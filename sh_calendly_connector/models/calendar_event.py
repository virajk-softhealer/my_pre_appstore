# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging

from odoo import _, api, fields, models


_logger = logging.getLogger(__name__)


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    sh_calendly_event_id = fields.Char(index=True, copy=False,string="Calendly Event Id")
    sh_calendly_event_uuid = fields.Char(
        compute='_compute_sh_calendly_event_uuid',
        string='Calendly Event Id',
        help='The Calendly scheduled event identifier without its API URL.',
    )
    sh_calendly_event_type = fields.Char(index=True, copy=False, string="Calendly Event Type")
    sh_calendly_event_type_id = fields.Many2one(
        'sh.calendly.event.type', string='Calendly Event Type', index=True, copy=False,
    )
    sh_calendly_status = fields.Selection(
        selection=[
            ('active', 'Active'),
            ('canceled', 'Canceled'),
            ('validated', 'Validated'),
        ],
        default='active',
        copy=False,
        # tracking=True,
        string="Calendly Status",
    )

    @api.depends('sh_calendly_event_id')
    def _compute_sh_calendly_event_uuid(self):
        for event in self:
            event.sh_calendly_event_uuid = (
                event.sh_calendly_event_id.rsplit('/', 1)[-1]
                if event.sh_calendly_event_id else False
            )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._push_calendly_changes()
        return records

    def write(self, vals):
        result = super().write(vals)
        if set(vals) & {'name', 'start', 'stop', 'partner_ids', 'active'}:
            self._push_calendly_changes()
        return result

    def unlink(self):
        """Cancel linked Calendly bookings before deleting Odoo meetings."""
        from ..services.calendly_sync import CalendlySyncService

        for event in self:
            if not event.sh_calendly_event_id:
                continue
            company = event.user_id.company_id or self.env.company
            service = CalendlySyncService(self.env, company)
            if not company.sh_calendly_auto_sync_to_calendly:
                service._log({
                    'source': 'odoo_action',
                    'event_type': 'calendar.event',
                    'operation': 'delete',
                    'sync_direction': 'odoo_to_calendly',
                    'invitee_email': event.partner_ids[:1].email,
                    'external_event_id': event.sh_calendly_event_id,
                    'status': 'pending',
                    'calendar_event_id': event.id,
                    'message': _('Calendly cancellation is pending the manual Odoo to Calendly sync.'),
                })
                continue
            try:
                if not service._check_connection('odoo_to_calendly_delete'):
                    continue
                response = service.api.cancel_event(event.sh_calendly_event_id)
                service._log({
                    'source': 'odoo_action',
                    'event_type': 'calendar.event',
                    'operation': 'delete',
                    'sync_direction': 'odoo_to_calendly',
                    'invitee_email': event.partner_ids[:1].email,
                    'external_event_id': event.sh_calendly_event_id,
                    'status': 'success' if response is not None else 'failed',
                    'calendar_event_id': event.id,
                    'response': response,
                    'message': _(
                        'Calendly meeting canceled because the Odoo meeting was deleted.'
                    ) if response is not None else _(
                        'Calendly meeting cancellation failed.'
                    ),
                })
            except Exception:
                _logger.exception(
                    'Calendly cancellation on Odoo deletion failed: company_id=%s event_id=%s',
                    company.id, event.id,
                )
        return super().unlink()

    def _push_calendly_changes(self):
        """Send only explicitly eligible Odoo events; inbound writes are skipped."""
        if self.env.context.get('skip_calendly_sync'):
            return
        from ..services.calendly_sync import CalendlySyncService

        for event in self:
            company = event.user_id.company_id or self.env.company
            service = CalendlySyncService(self.env, company)
            if company.sh_calendly_auto_sync_to_calendly:
                service.push_event(event)
            else:
                service.queue_pending_event(event)

    def action_calendly_retry(self):
        self.ensure_one()
        if not self.sh_calendly_event_id:
            return False
        self._push_calendly_changes()
        return True
