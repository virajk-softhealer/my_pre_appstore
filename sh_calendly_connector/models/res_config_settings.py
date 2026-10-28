# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging

from odoo import _, fields, models

from ..services.calendly_api import CalendlyApi


_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sh_calendly_client_id = fields.Char(related='company_id.sh_calendly_client_id', readonly=False)
    sh_calendly_client_secret = fields.Char(related='company_id.sh_calendly_client_secret', readonly=False)
    sh_calendly_webhook_signing_key = fields.Char(
        related='company_id.sh_calendly_webhook_signing_key', readonly=False,
    )
    sh_calendly_access_token = fields.Char(
        related='company_id.sh_calendly_access_token', readonly=True,
    )
    sh_calendly_refresh_token = fields.Char(
        related='company_id.sh_calendly_refresh_token', readonly=True,
    )
    sh_calendly_pat_token = fields.Char(
        related='company_id.sh_calendly_pat_token', readonly=False,
    )
    sh_calendly_auth_method = fields.Selection(
        related='company_id.sh_calendly_auth_method', readonly=False,
    )
    sh_calendly_token_expires_at = fields.Datetime(
        related='company_id.sh_calendly_token_expires_at', readonly=True,
    )
    sh_calendly_authenticated_user_uri = fields.Char(
        related='company_id.sh_calendly_authenticated_user_uri', readonly=True,
    )
    sh_calendly_connection_state = fields.Selection(
        related='company_id.sh_calendly_connection_state', readonly=True,
    )
    sh_calendly_organization_uri = fields.Char(
        related='company_id.sh_calendly_organization_uri', readonly=True,
    )
    sh_calendly_webhook_subscription_uri = fields.Char(
        related='company_id.sh_calendly_webhook_subscription_uri', readonly=True,
    )
    sh_calendly_last_sync_at = fields.Datetime(
        related='company_id.sh_calendly_last_sync_at', readonly=True,
    )
    sh_calendly_auto_sync_to_odoo = fields.Boolean(
        related='company_id.sh_calendly_auto_sync_to_odoo', readonly=False,
    )
    sh_calendly_auto_sync_event_types = fields.Boolean(
        related='company_id.sh_calendly_auto_sync_event_types', readonly=False,
    )
    sh_calendly_create_lead = fields.Boolean(
        related='company_id.sh_calendly_create_lead', readonly=False,
    )
    sh_calendly_auto_sync_to_calendly = fields.Boolean(
        related='company_id.sh_calendly_auto_sync_to_calendly', readonly=False,
    )
    sh_calendly_enable_queue = fields.Boolean(
        related='company_id.sh_calendly_enable_queue', readonly=False,
    )

    def _calendly_company(self):
        self.ensure_one()
        return self.company_id

    def action_calendly_connect(self):
        self.ensure_one()
        company = self._calendly_company()
        if company.sh_calendly_auth_method != 'oauth':
            return self._calendly_notification(
                _('Select OAuth to connect through Calendly authorization. PAT uses Test Connection.'),
                'warning',
            )
        if not company.sh_calendly_client_id or not company.sh_calendly_client_secret:
            _logger.warning('Calendly connection skipped: credentials missing, company_id=%s', company.id)
            return self._calendly_notification(_('Client ID and Client Secret are required.'), 'warning')
        return CalendlyApi(self.env, company).authorization_action()

    def action_calendly_register_webhook(self):
        self.ensure_one()
        company = self._calendly_company()
        api = CalendlyApi(self.env, company)
        if not api.register_webhook():
            return self._calendly_notification(
             _(
                    'Webhook registration failed. Verify the authentication credentials, '
                    'required scopes, signing key, and public callback URL.',
                ), 'warning',
            )
        return self._calendly_notification(
            _('Calendly webhook registered successfully. Automatic synchronization is ready.'),
            'success',
        )

    def action_calendly_test_connection(self):
        self.ensure_one()
        company = self._calendly_company()
        if company.sh_calendly_auth_method == 'pat' and not company.sh_calendly_pat_token:
            return self._calendly_notification(_('Personal Access Token is required.'), 'warning')
        if company.sh_calendly_auth_method == 'oauth' and (
            not company.sh_calendly_client_id or not company.sh_calendly_client_secret
        ):
            return self._calendly_notification(
                _('OAuth Client ID and Client Secret are required.'), 'warning',
            )
        if not CalendlyApi(self.env, company).test_connection():
            return self._calendly_notification(
                _('Connection test failed. Verify the current Calendly credentials.'), 'warning',
            )
        from ..services.calendly_sync import CalendlySyncService
        try:
            CalendlySyncService(self.env, company).sync_event_types()
        except Exception:
            _logger.exception('Calendly event type import failed during connection test: company_id=%s', company.id)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Calendly'),
                'message': _('The Calendly connection is working.'),
                'type': 'success',
                'sticky': False,
            },
        }

    @staticmethod
    def _calendly_notification(message, notification_type):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Calendly'),
                'message': message,
                'type': notification_type,
                'sticky': False,
            },
        }

    def action_calendly_disconnect(self):
        self.ensure_one()
        CalendlyApi(self.env, self._calendly_company()).disconnect()
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_calendly_sync_calendly_to_odoo(self):
        self.ensure_one()
        from ..services.calendly_sync import CalendlySyncService

        company = self._calendly_company()
        try:
            CalendlySyncService(self.env, company).poll_bookings()
        except Exception:
            _logger.exception('Manual Calendly synchronization failed: company_id=%s', company.id)
            return self._calendly_notification(
                _('Synchronization failed. Check the Odoo log for details.'), 'warning',
            )
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_calendly_sync_odoo_to_calendly(self):
        """Synchronize eligible Odoo Calendar meetings to Calendly immediately."""
        self.ensure_one()
        from ..services.calendly_sync import CalendlySyncService

        company = self._calendly_company()
        try:
            service = CalendlySyncService(self.env, company)
            handled_event_ids = service.process_pending_odoo_logs()
            service.push_odoo_changes(excluded_event_ids=handled_event_ids)
        except Exception:
            _logger.exception('Manual Odoo-to-Calendly synchronization failed: company_id=%s', company.id)
            return self._calendly_notification(
                _('Synchronization failed. Check the Odoo log for details.'), 'warning',
            )
        return self._calendly_notification(
            _('Odoo Calendar meetings synchronized to Calendly.'), 'success',
        )

    def action_calendly_sync_event_types(self):
        """Synchronize Calendly Event Types immediately from the settings page."""
        self.ensure_one()
        from ..services.calendly_sync import CalendlySyncService

        company = self._calendly_company()
        try:
            result = CalendlySyncService(self.env, company).sync_event_types()
        except Exception:
            _logger.exception('Manual Calendly Event Type synchronization failed: company_id=%s', company.id)
            return self._calendly_notification(
                _('Event Type synchronization failed. Check the Odoo log for details.'), 'warning',
            )
        return self._calendly_notification(
            _(
                'Event Types synchronized: %(created)s created, %(updated)s updated, '
                '%(deleted)s deleted.',
            ) % result,
            'success',
        )
