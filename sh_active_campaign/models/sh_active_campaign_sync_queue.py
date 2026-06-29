# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class ShActiveCampaignSyncQueue(models.Model):
    """Queue records for deferred ActiveCampaign synchronization jobs."""

    _name = 'sh.active.campaign.sync.queue'
    _description = 'ActiveCampaign Sync Queue'
    _order = 'create_date desc'
    _rec_name = 'sh_sync_key'

    _JOB_META = {
        'contacts_import': {'entity': 'contact', 'operation': 'import', 'method': '_sync_contacts_from_ac'},
        'contacts_export': {'entity': 'contact', 'operation': 'export', 'method': '_export_contacts_to_ac'},
        'list_contacts_import': {'entity': 'list_contact', 'operation': 'import', 'method': '_sync_list_contacts_from_ac'},
        'lists_import': {'entity': 'campaign', 'operation': 'import', 'method': '_sync_lists_from_ac'},
        'tags_import': {'entity': 'tag', 'operation': 'import', 'method': '_sync_tags_from_ac'},
        'tags_export': {'entity': 'tag', 'operation': 'export', 'method': '_export_tags_to_ac'},
        'deals_import': {'entity': 'deal', 'operation': 'import', 'method': '_sync_deals_from_ac'},
        'deals_export': {'entity': 'deal', 'operation': 'export', 'method': '_export_deals_to_ac'},
        'forms_import': {'entity': 'form', 'operation': 'import', 'method': '_sync_forms_from_ac'},
    }

    sh_sync_key = fields.Selection([
        ('contacts_import', 'Contacts Import'),
        ('contacts_export', 'Contacts Export'),
        ('list_contacts_import', 'List Contacts Import'),
        ('lists_import', 'Lists Import'),
        ('tags_import', 'Tags Import'),
        ('tags_export', 'Tags Export'),
        ('deals_import', 'Deals Import'),
        ('deals_export', 'Deals Export'),
        ('forms_import', 'Forms Import'),
    ], string='Sync Job', required=True, readonly=True)
    sh_state = fields.Selection([
        ('draft', 'Pending'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', required=True, readonly=True)
    sh_payload = fields.Text(string='Snapshot Payload', readonly=True)
    sh_error_message = fields.Text(string='Last Error Message', readonly=True)
    sh_last_attempt_date = fields.Datetime(string='Last Attempt Date', readonly=True)
    sh_dashboard_id = fields.Many2one(
        'sh.active.campaign.dashboard',
        string='Dashboard',
        required=True,
        readonly=True,
        ondelete='cascade',
    )
    sh_company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        readonly=True,
    )

    @api.model
    def _get_job_meta(self, sync_key):
        return self._JOB_META.get(sync_key, {})

    @api.model
    def add_to_queue(self, sync_key, dashboard_id, payload=False, error_message=False, company_id=False):
        """Create or refresh a queue job for the given dashboard sync request."""
        meta = self._get_job_meta(sync_key)
        if not meta:
            _logger.warning('Unknown ActiveCampaign queue job key: %s', sync_key)
            return False

        dashboard = self.env['sh.active.campaign.dashboard'].sudo().browse(dashboard_id)
        if not dashboard.exists():
            _logger.warning('Cannot queue ActiveCampaign job %s because dashboard %s was not found.', sync_key, dashboard_id)
            return False

        company_id = company_id or dashboard.sh_company_id.id
        if payload and not isinstance(payload, str):
            payload = json.dumps(payload)

        domain = [
            ('sh_sync_key', '=', sync_key),
            ('sh_dashboard_id', '=', dashboard.id),
            ('sh_company_id', '=', company_id),
            ('sh_state', 'in', ['draft', 'failed']),
        ]
        existing = self.search(domain, limit=1)
        vals = {
            'sh_sync_key': sync_key,
            'sh_dashboard_id': dashboard.id,
            'sh_company_id': company_id,
            'sh_payload': payload,
            'sh_error_message': error_message,
            'sh_state': 'draft',
        }
        if existing:
            existing.write(vals)
            return existing
        return self.create(vals)

    @api.model
    def process_queue(self, batch_size=100):
        """Cron job that processes pending and failed ActiveCampaign jobs."""
        from_cron = bool(self.env.context.get('cron_id'))
        if from_cron:
            self.env['ir.cron']._commit_progress(remaining=self.search_count([('sh_state', 'in', ['draft', 'failed'])]))

        while True:
            pending_queue = self.search([('sh_state', 'in', ['draft', 'failed'])], order='create_date asc')
            queue_records = pending_queue.try_lock_for_update(limit=batch_size)
            if not queue_records:
                if not pending_queue:
                    _logger.info('ActiveCampaign Sync Queue: no jobs to process.')
                break

            self.process_queue_records(queue_records)

            if from_cron:
                time_left = self.env['ir.cron']._commit_progress(len(queue_records))
                if not time_left:
                    break
            else:
                self.env.cr.commit()
        return True

    def action_retry(self):
        """Reset selected queue records to pending so the cron can retry them."""

        records = self.filtered(lambda l:l.sh_state not in ('done'))
        
        if records:
            records.write({'sh_state': 'draft', 'sh_error_message': False})

            return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Queue Reset'),
                            'message': _('Selected queue records have been reset to Pending.'),
                            'type': 'success',
                            'sticky': False,
                        },
                    }

    def process_queue_records(self, queue_records):
        """Execute each queued sync job and update its queue state."""
        for queue_rec in queue_records:
            try:
                queue_rec.write({'sh_last_attempt_date': fields.Datetime.now()})

                meta = self._get_job_meta(queue_rec.sh_sync_key)
                if not meta:
                    queue_rec.write({
                        'sh_state': 'failed',
                        'sh_error_message': _('Unsupported queue job: %s') % queue_rec.sh_sync_key,
                    })
                    continue

                payload = {}
                if queue_rec.sh_payload:
                    try:
                        payload = json.loads(queue_rec.sh_payload)
                    except Exception:
                        payload = {}

                original_dashboard = queue_rec.sh_dashboard_id
                dashboard = original_dashboard
                if not dashboard.exists():
                    dashboard = self.env['sh.active.campaign.dashboard'].sudo().search(
                        [('sh_company_id', '=', queue_rec.sh_company_id.id)],
                        limit=1,
                    )
                if not dashboard:
                    dashboard = self.env['sh.active.campaign.dashboard'].sudo().create({
                        'sh_company_id': queue_rec.sh_company_id.id,
                    })

                try:
                    # Prefer the dashboard's live settings at execution time so scheduled jobs
                    # follow the current date-range configuration instead of the enqueue snapshot.
                    queue_snapshot = payload
                    if original_dashboard and original_dashboard.exists():
                        queue_snapshot = original_dashboard._get_queue_payload_snapshot()
                    if not queue_snapshot:
                        queue_snapshot = payload
                    result = dashboard.with_company(queue_rec.sh_company_id).with_context(
                        is_cron=True,
                        sh_from_queue_cron=True,
                        sh_queue_snapshot=queue_snapshot,
                    )._run_queued_sync_job(queue_rec.sh_sync_key)

                    if result is False:
                        queue_rec.write({
                            'sh_state': 'failed',
                            'sh_error_message': _('Queued sync job returned no result.'),
                        })
                    else:
                        queue_rec.write({
                            'sh_state': 'done',
                            'sh_error_message': False,
                        })
                except Exception as exc:
                    _logger.exception('Failed to process ActiveCampaign queue job %s: %s', queue_rec.id, exc)
                    queue_rec.write({
                        'sh_state': 'failed',
                        'sh_error_message': str(exc),
                    })
            except Exception as exc:
                _logger.exception('Queue record %s failed before execution: %s', queue_rec.id, exc)
                try:
                    queue_rec.write({
                        'sh_state': 'failed',
                        'sh_error_message': str(exc),
                    })
                except Exception:
                    _logger.exception('Unable to mark queue record %s as failed.', queue_rec.id)
