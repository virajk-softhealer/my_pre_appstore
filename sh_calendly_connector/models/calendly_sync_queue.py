# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import html
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


class CalendlySyncQueue(models.Model):
    _name = 'sh.calendly.sync.queue'
    _description = 'Calendly Synchronization Queue'
    _order = 'create_date desc, id desc'

    sync_log_id = fields.Many2one(
        comodel_name='sh.calendly.sync.log', required=True, readonly=True,
        ondelete='cascade', index=True,
    )
    direction = fields.Selection(related='sync_log_id.sync_direction', store=True, readonly=True)
    operation = fields.Selection(related='sync_log_id.operation', store=True, readonly=True)
    event_type = fields.Char(related='sync_log_id.event_type', store=True, readonly=True)
    sync_model = fields.Selection(
        [('calendar_event', 'Calendar Meeting'), ('event_type', 'Event Type')],
        compute='_compute_sync_model', store=True, readonly=True,string="Type"
    )
    state = fields.Selection(
        [('draft', 'Pending'), ('done', 'Done'), ('failed', 'Failed')],
        default='draft', required=True, copy=False, index=True,
    )
    error_message = fields.Text(readonly=True)
    payload = fields.Text(readonly=True)
    payload_display = fields.Html(compute='_compute_payload_display', readonly=True)
    last_attempt_date = fields.Datetime(readonly=True,string="Date")
    company_id = fields.Many2one(
        comodel_name='res.company', required=True, readonly=True,
        default=lambda self: self.env.company, ondelete='restrict', index=True,
    )

    @api.depends('payload')
    def _compute_payload_display(self):
        for queue in self:
            payload = self._payload_from_sync_log(queue.sync_log_id)
            if queue.payload:
                try:
                    payload = json.loads(queue.payload)
                except (TypeError, ValueError):
                    payload = queue.payload
                if payload == {}:
                    payload = self._payload_from_sync_log(queue.sync_log_id)
            queue.payload_display = '<pre>%s</pre>' % html.escape(
                json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            )

    @api.depends('event_type')
    def _compute_sync_model(self):
        for queue in self:
            queue.sync_model = (
                'event_type' if queue.event_type and queue.event_type.startswith('event_type.')
                else 'calendar_event'
            )

    @api.model
    def add_to_queue(self, sync_log):
        """Create or reset one queue item for a pending or failed synchronization log."""
        sync_log.ensure_one()
        sync_log = sync_log.exists()
        if not sync_log or sync_log.status not in ('pending', 'failed'):
            raise UserError(_('Only pending or failed synchronization logs can be queued.'))
        queue = self.search([
            ('sync_log_id', '=', sync_log.id), ('state', 'in', ('draft', 'failed')),
        ], limit=1)
        values = {
            'sync_log_id': sync_log.id,
            'state': 'draft',
            'error_message': False,
            'payload': json.dumps(sync_log.response or self._payload_from_sync_log(sync_log), default=str),
            'company_id': sync_log.company_id.id,
        }
        if queue:
            queue.write(values)
        else:
            queue = self.create(values)
        return queue

    @api.model
    def _payload_from_sync_log(self, sync_log):
        """Build a useful payload when an outbound log has no API response."""
        payload = {
            'model': 'calendar.event',
            'operation': sync_log.operation,
            'external_event_id': sync_log.external_event_id,
        }
        event = sync_log.calendar_event_id.exists()
        if event:
            payload.update({
                'record_id': event.id,
                'name': event.name,
                'start': event.start,
                'stop': event.stop,
                'active': event.active,
                'event_type': event.sh_calendly_event_type_id.uri or event.sh_calendly_event_type,
                'attendees': [partner.email for partner in event.partner_ids if partner.email],
            })
        return payload

    @api.model
    def process_queue(self, batch_size=100):
        """Process pending and failed queue items in a bounded batch."""
        records = self.search([('state', 'in', ('draft', 'failed'))], limit=batch_size)
        self.process_queue_records(records)
        return True

    def action_retry(self):
        """Reset failed queue items so the scheduled worker can process them again."""
        failed_records = self.filtered(lambda record: record.state == 'failed')
        if not failed_records:
            raise UserError(_('Only failed queue records can be retried.'))
        failed_records.write({'state': 'draft', 'error_message': False})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('Queue Reset'),
                'message': _('%s Calendly queue record(s) moved to Pending.') % len(failed_records),
                'type': 'success', 'sticky': False,
            },
        }

    def process_queue_records(self, queue_records):
        """Process queue records and retain errors for administrator retry."""
        from ..services.calendly_sync import CalendlySyncService

        for queue in queue_records:
            if not queue.sync_log_id.exists():
                queue.write({
                    'state': 'failed',
                    'error_message': _('The linked synchronization log no longer exists.'),
                    'last_attempt_date': fields.Datetime.now(),
                })
                continue
            queue.write({'last_attempt_date': fields.Datetime.now()})
            try:
                log = queue.sync_log_id
                log.write({'status': 'pending', 'message': False})
                CalendlySyncService(self.env, queue.company_id).process_queued_log(log)
                if log.status in ('success', 'skipped'):
                    queue.write({'state': 'done', 'error_message': False})
                else:
                    if log.status == 'pending':
                        log.write({'status': 'failed', 'message': _('Synchronization failed during queue processing.')})
                    queue.write({
                        'state': 'failed',
                        'error_message': log.message or _('Synchronization failed.'),
                    })
            except Exception as error:
                _logger.exception('Calendly queue processing failed: queue_id=%s', queue.id)
                log.write({
                    'status': 'failed',
                    'message': _('Synchronization failed; see the Odoo log for details.'),
                })
                queue.write({'state': 'failed', 'error_message': str(error)})
