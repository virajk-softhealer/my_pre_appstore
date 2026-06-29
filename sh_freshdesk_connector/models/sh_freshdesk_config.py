# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import base64
import json
import logging
import time
from datetime import datetime, timezone

import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class SHFreshdeskAPI(models.AbstractModel):
    _name = 'sh.freshdesk.api'
    _description = 'Freshdesk API Helper'

    @api.model
    def get_freshdesk_client(self):
        company = self.env.company.sudo()
        domain = company.sh_freshdesk_domain
        api_key = company.sh_freshdesk_api_key

        if not domain or not api_key:
            raise UserError(_('Please configure Freshdesk Domain and API Key first in Settings.'))

        if not domain.startswith('http'):
            domain = 'https://' + domain
        domain = domain.rstrip('/')

        auth_str = '%s:%s' % (api_key, 'X')
        encoded_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Basic %s' % encoded_auth,
        }
        return domain, headers

    @api.model
    def _freshdesk_request(self, method, endpoint, payload=None, params=None, files=None, timeout=20):
        domain, headers = self.get_freshdesk_client()
        url = '%s%s' % (domain, endpoint)

        # If uploading files, 'Content-Type' should not be 'application/json'
        # requests will set the correct multipart/form-data content type with boundary.
        if files:
            headers.pop('Content-Type', None)

        for attempt in range(3):
            response = requests.request(
                method,
                url,
                headers=headers,
                data=json.dumps(payload) if payload is not None and not files else payload,
                params=params,
                files=files,
                timeout=timeout,
            )
            if response.status_code != 429:
                return response
            retry_after = response.headers.get('Retry-After')
            wait_time = int(retry_after) if retry_after and retry_after.isdigit() else (attempt + 1) * 2
            _logger.warning('Freshdesk rate limit hit for %s %s. Retrying in %s seconds.', method, endpoint, wait_time)
            time.sleep(wait_time)

        return response
    @api.model
    def _extract_error_message(self, response):
        """ Extract a human readable message from Freshdesk error response """
        # if not response:
        #     return _("No response from Freshdesk.")

        error_msg = getattr(response, 'text', str(response))
        try:
            data = response.json()
            if isinstance(data, dict):
                # case 1: errors list (common in validation failures)
                errors = data.get('errors')
                if errors and isinstance(errors, list):
                    msg_list = []
                    for err in errors:
                        if isinstance(err, dict) and err.get('message'):
                            msg_list.append(err['message'])
                        elif isinstance(err, str):
                            msg_list.append(err)
                    if msg_list:
                        return " | ".join(msg_list)

                # case 2: simple message
                if data.get('message'):
                    return data['message']

                # case 3: description
                if data.get('description'):
                    return data['description']
        except Exception:
            pass

        return error_msg

    @api.model
    def _to_iso8601_datetime(self, value):
        if not value:
            return False

        if isinstance(value, datetime):
            dt = value
        else:
            raw_value = str(value).strip()
            try:
                dt = fields.Datetime.to_datetime(raw_value)
            except Exception:
                try:
                    normalized = raw_value.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(normalized)
                except Exception:
                    _logger.warning('Invalid datetime value for Freshdesk sync parameter: %s', raw_value)
                    return False

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    @api.model
    def _prepare_updated_since_params(self, page=None, manual=False):
        company = self.env.company.sudo()
        start_date = company.sh_sync_start_date
        end_date = company.sh_sync_end_date

        # Choose specific last sync date based on the calling model
        last_sync = False
        if self._name == 'res.partner':
            if self.env.context.get('fetch_companies'):
                last_sync = company.sh_freshdesk_last_company_sync_date
            else:
                last_sync = company.sh_freshdesk_last_contact_sync_date
        elif self._name == 'helpdesk.ticket':
            last_sync = company.sh_freshdesk_last_ticket_sync_date

        params = {'per_page': 100}
        if page:
            params['page'] = page

        if manual and start_date:
            iso_start = self._to_iso8601_datetime(start_date)
            if iso_start:
                params['updated_since'] = iso_start
        elif last_sync:
            iso_last_sync = self._to_iso8601_datetime(last_sync)
            if iso_last_sync:
                params['updated_since'] = iso_last_sync

        if manual and end_date:
            iso_end = self._to_iso8601_datetime(end_date)
            if iso_end:
                params['sh_end_date'] = iso_end

        return params


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sh_freshdesk_domain = fields.Char(related='company_id.sh_freshdesk_domain', readonly=False)
    sh_freshdesk_api_key = fields.Char(related='company_id.sh_freshdesk_api_key', readonly=False)
    sh_freshdesk_last_contact_sync_date = fields.Datetime(related='company_id.sh_freshdesk_last_contact_sync_date', readonly=False)
    sh_freshdesk_last_company_sync_date = fields.Datetime(related='company_id.sh_freshdesk_last_company_sync_date', readonly=False)
    sh_freshdesk_last_ticket_sync_date = fields.Datetime(related='company_id.sh_freshdesk_last_ticket_sync_date', readonly=False)
    sh_fd_contact_sync_limit = fields.Integer(related='company_id.sh_fd_contact_sync_limit', readonly=False)
    # Added field 'sh_fd_company_sync_limit'
    sh_fd_company_sync_limit = fields.Integer(related='company_id.sh_fd_company_sync_limit', readonly=False)
    sh_fd_ticket_sync_limit = fields.Integer(related='company_id.sh_fd_ticket_sync_limit', readonly=False)

    sh_enable_partner_sync = fields.Boolean(related='company_id.sh_enable_partner_sync', readonly=False)
    sh_enable_ticket_sync = fields.Boolean(related='company_id.sh_enable_ticket_sync', readonly=False)
    sh_import_conversations = fields.Boolean(related='company_id.sh_import_conversations', readonly=False)
    sh_import_time_entries = fields.Boolean(related='company_id.sh_import_time_entries', readonly=False)

    sh_manual_action = fields.Boolean(related='company_id.sh_manual_action', readonly=False)
    sh_automated_action = fields.Boolean(related='company_id.sh_automated_action', readonly=False)
    sh_fd_partner_create = fields.Boolean(related='company_id.sh_fd_partner_create', readonly=False)
    sh_fd_partner_update = fields.Boolean(related='company_id.sh_fd_partner_update', readonly=False)
    sh_fd_partner_delete = fields.Boolean(related='company_id.sh_fd_partner_delete', readonly=False)
    sh_fd_ticket_create = fields.Boolean(related='company_id.sh_fd_ticket_create', readonly=False)
    sh_fd_ticket_update = fields.Boolean(related='company_id.sh_fd_ticket_update', readonly=False)
    sh_fd_ticket_delete = fields.Boolean(related='company_id.sh_fd_ticket_delete', readonly=False)

    sh_cron_interval_number = fields.Integer(related='company_id.sh_cron_interval_number', readonly=False)
    sh_cron_interval_type = fields.Selection(related='company_id.sh_cron_interval_type', readonly=False)

    sh_sync_start_date = fields.Datetime(related='company_id.sh_sync_start_date', readonly=False)
    sh_sync_end_date = fields.Datetime(related='company_id.sh_sync_end_date', readonly=False)
    sh_enable_freshdesk_webhook = fields.Boolean(related='company_id.sh_enable_freshdesk_webhook', readonly=False)
    sh_freshdesk_webhook_secret = fields.Char(related='company_id.sh_freshdesk_webhook_secret', readonly=True)
    sh_freshdesk_webhook_team_id = fields.Many2one(
        related='company_id.sh_freshdesk_webhook_team_id',
        readonly=False,
    )
    sh_freshdesk_webhook_url = fields.Char(related='company_id.sh_freshdesk_webhook_url', readonly=True)
    sh_fd_webhook_ticket_create = fields.Boolean(related='company_id.sh_fd_webhook_ticket_create', readonly=False)
    sh_fd_webhook_ticket_update = fields.Boolean(related='company_id.sh_fd_webhook_ticket_update', readonly=False)

    @api.onchange('sh_manual_action')
    def _onchange_sh_manual_action(self):
        if self.sh_manual_action:
            self.sh_automated_action = False

    @api.onchange('sh_automated_action')
    def _onchange_sh_automated_action(self):
        if self.sh_automated_action:
            self.sh_manual_action = False

    def _update_cron_from_settings(self):
        self.ensure_one()
        partner_cron = self.env.ref('sh_freshdesk_connector.ir_cron_sh_fd_partner_sync', raise_if_not_found=False)
        ticket_cron = self.env.ref('sh_freshdesk_connector.ir_cron_sh_fd_ticket_sync', raise_if_not_found=False)
        auto_enabled = bool(self.sh_automated_action)

        if partner_cron:
            partner_cron.write({
                'active': auto_enabled and self.sh_enable_partner_sync,
                'interval_number': max(self.sh_cron_interval_number, 1),
                'interval_type': self.sh_cron_interval_type or 'hours',
            })
        if ticket_cron:
            ticket_cron.write({
                'active': auto_enabled and self.sh_enable_ticket_sync,
                'interval_number': max(self.sh_cron_interval_number, 1),
                'interval_type': self.sh_cron_interval_type or 'hours',
            })

    def action_save_auto_setting(self):
        self.ensure_one()
        self.write({
            'sh_automated_action': True,
            'sh_manual_action': False,
        })
        self._update_cron_from_settings()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Auto Setting Saved'),
                'message': _('Automated synchronization settings were applied.'),
                'sticky': False,
                'type': 'success',
            }
        }

    def _assert_freshdesk_connection(self):
        domain, headers = self.env['sh.freshdesk.api'].get_freshdesk_client()
        try:
            response = requests.get('%s/api/v2/ticket_fields' % domain, headers=headers, timeout=10)
        except requests.exceptions.RequestException as exc:
            raise UserError(_('Freshdesk connection error: %s') % str(exc))

        if response.status_code != 200:
            raise UserError(
                _('Freshdesk connection failed. Status: %s\nResponse: %s') % (response.status_code, response.text)
            )

    def action_sync_now(self):
        self.ensure_one()
        if not self.sh_manual_action:
            raise UserError(_('Enable Manual Action to run Sync immediately.'))
        if not self.sh_sync_start_date or not self.sh_sync_end_date:
            raise UserError(_('Please set From Date and To Date for manual synchronization.'))
        if self.sh_sync_start_date > self.sh_sync_end_date:
            raise UserError(_('From Date must be less than or equal to To Date.'))

        self._assert_freshdesk_connection()
        
        # Pass sh_bulk_sync=True to suppress per-record notifications (single summary shown after).
        # is_manual=True makes sync methods use the manual date range from settings.
        bulk_env = self.with_context(sh_bulk_sync=True).env

        if self.sh_enable_partner_sync:
            bulk_env['res.partner'].sync_partners_from_freshdesk(is_manual=True)
            bulk_env['res.partner'].sync_companies_from_freshdesk(is_manual=True)
        if self.sh_enable_ticket_sync:
            bulk_env['helpdesk.ticket'].sync_tickets_from_freshdesk(is_manual=True)

        now = fields.Datetime.now()
        company = self.company_id.sudo()
        if self.sh_enable_partner_sync:
            company.write({
                'sh_freshdesk_last_contact_sync_date': now,
                'sh_freshdesk_last_company_sync_date': now,
            })
        if self.sh_enable_ticket_sync:
            company.write({'sh_freshdesk_last_ticket_sync_date': now})

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Synchronization Complete'),
                'message': _('Selected data has been synchronized with Freshdesk.'),
                'sticky': False,
                'type': 'success',
            }
        }
        return True

    def action_build_connection(self):
        self.ensure_one()
        self._assert_freshdesk_connection()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Connection Successful'),
                'message': _('Successfully connected to Freshdesk.'),
                'sticky': False,
                'type': 'success',
            }
        }

    def action_generate_freshdesk_webhook_secret(self):
        self.ensure_one()
        self.company_id.action_generate_freshdesk_webhook_secret()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhook Secret Updated'),
                'message': _('Freshdesk webhook URL has been regenerated. Update the URL in Freshdesk.'),
                'sticky': False,
                'type': 'success',
            }
        }

    @api.model
    def get_freshdesk_flow_config(self):
        company = self.env.company.sudo()
        return {
            'sync_contacts': company.sh_enable_partner_sync,
            'sync_tickets': company.sh_enable_ticket_sync,
            'import_conversations': company.sh_import_conversations,
            'import_time_entries': company.sh_import_time_entries,
            'manual_action': company.sh_manual_action,
            'automated_action': company.sh_automated_action,
            'contact_sync_limit': company.sh_fd_contact_sync_limit,
            'company_sync_limit': company.sh_fd_company_sync_limit,
            'ticket_sync_limit': company.sh_fd_ticket_sync_limit,
            'from_date': fields.Datetime.to_string(company.sh_sync_start_date) if company.sh_sync_start_date else '',
            'to_date': fields.Datetime.to_string(company.sh_sync_end_date) if company.sh_sync_end_date else '',
        }

    @api.model
    def test_freshdesk_connection(self):
        try:
            self._assert_freshdesk_connection()
            return {'ok': True, 'message': _('Connection successful.')}
        except UserError as exc:
            return {'ok': False, 'message': str(exc)}

    def set_values(self):
        res = super().set_values()
        self._update_cron_from_settings()
        return res
