# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging
from datetime import timedelta

from odoo import fields, models


_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = 'res.company'

    sh_calendly_client_id = fields.Char(copy=False)
    sh_calendly_client_secret = fields.Char(copy=False)
    sh_calendly_webhook_signing_key = fields.Char(copy=False)
    # OAuth credentials are managed by the OAuth callback and never edited in settings.
    sh_calendly_access_token = fields.Char(copy=False)
    sh_calendly_refresh_token = fields.Char(copy=False)
    sh_calendly_token_expires_at = fields.Datetime(copy=False)
    sh_calendly_pat_token = fields.Char(copy=False)
    sh_calendly_auth_method = fields.Selection(
        selection=[
            ('oauth', 'OAuth'),
            ('pat', 'Personal Access Token'),
        ],
        default='oauth',
        required=True,
        copy=False,
    )
    sh_calendly_authenticated_user_uri = fields.Char(copy=False)
    sh_calendly_organization_uri = fields.Char(copy=False)
    sh_calendly_webhook_subscription_uri = fields.Char(copy=False)
    sh_calendly_connection_state = fields.Selection(
        selection=[
            ('disconnected', 'Disconnected'),
            ('connected', 'Connected'),
            ('error', 'Error'),
            ('revoked', 'Revoked'),
        ],
        default='disconnected',
        copy=False,
    )
    sh_calendly_last_sync_at = fields.Datetime(copy=False)
    sh_calendly_auto_sync_to_odoo = fields.Boolean(
        string='Automatic Calendly → Odoo Sync',
        default=True,
    )
    sh_calendly_auto_sync_event_types = fields.Boolean(
        string='Automatic Calendly Event Type Sync',
        default=False,
    )
    sh_calendly_create_lead = fields.Boolean(
        string='Create CRM Lead for Calendly Bookings',
        default=False,
    )
    sh_calendly_auto_sync_to_calendly = fields.Boolean(
        string='Automatic Odoo → Calendly Sync',
        default=False,
    )

    def cron_poll_bookings(self):
        """Poll connected companies whose direct Calendly sync is disabled."""
        from ..services.calendly_sync import CalendlySyncService

        companies = self.env['res.company'].search([
            ('sh_calendly_auto_sync_to_odoo', '=', False),
        ])
        _logger.info(
            'Calendly poll cron started: connected_companies=%s company_ids=%s',
            len(companies), companies.ids,
        )
        for company in companies:
            _logger.info(
                'Calendly poll cron processing company_id=%s name=%s auto_sync_to_odoo=%s',
                company.id, company.display_name, company.sh_calendly_auto_sync_to_odoo,
            )
            try:
                CalendlySyncService(self.env, company).poll_bookings(automatic=True)
            except Exception:
                _logger.exception(
                    'Calendly poll failed: company_id=%s. The cron continued; '
                    'see the Odoo log for the failure reason.',
                    company.id,
                )
        _logger.info('Calendly poll cron finished: processed_companies=%s', len(companies))

    def cron_push_odoo_changes(self):
        """Push eligible Calendar events for companies using scheduled mode."""
        from ..services.calendly_sync import CalendlySyncService

        for company in self.env['res.company'].search([
            ('sh_calendly_auto_sync_to_calendly', '=', False),
        ]):
            service = CalendlySyncService(self.env, company)
            if service._check_connection('odoo_to_calendly_poll'):
                service.push_odoo_changes()

    def cron_cleanup_sync_logs(self):
        """Keep the fixed 30-day Sync Log history."""
        cutoff = fields.Datetime.now() - timedelta(days=30)
        self.env['sh.calendly.sync.log'].search([
            ('date', '<', cutoff),
        ], limit=5000).unlink()
