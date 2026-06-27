# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging
import requests
from odoo import models, fields, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sh_ac_configuration = fields.Boolean(
        related='company_id.sh_ac_configuration',
        readonly=False,
        string='Active Campaign Configuration',
    )
    sh_ac_url = fields.Char(
        related='company_id.sh_ac_url',
        readonly=False,
        string='URL',
    )
    sh_ac_api_key = fields.Char(
        related='company_id.sh_ac_api_key',
        readonly=False,
        string='API Key',
    )
    sh_ac_access_token = fields.Char(
        related='company_id.sh_ac_access_token',
        readonly=True,
        string='Webhook Access Token',
    )
    sh_ac_enable_webhook_processing = fields.Boolean(
        related='company_id.sh_ac_enable_webhook_processing',
        readonly=False,
        string='Enable Webhook Processing',
    )
    sh_ac_webhook_url = fields.Char(
        related='company_id.sh_ac_webhook_url',
        readonly=True,
        string='Webhook URL',
    )
    sh_ac_webhook_url_warning = fields.Char(
        related='company_id.sh_ac_webhook_url_warning',
        readonly=True,
        string='Webhook URL Warning',
    )

    def action_generate_sh_ac_webhook_token(self):
        """Regenerate the webhook token from the settings screen."""
        self.ensure_one()
        self.company_id.action_generate_sh_ac_webhook_token()
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    def action_test_connection(self):
        """Test ActiveCampaign API connection and return result in wizard."""
        self.ensure_one()
        company = self.env.company
        url = self.sh_ac_url
        api_key = self.sh_ac_api_key
        message = ''
        is_success = False

        try:
            if not url or not api_key:
                message = _('Please fill in the URL and API Key before testing the connection.')
            else:
                endpoint = url.rstrip('/') + '/api/3/tags?limit=1'
                headers = {
                    'Api-Token': api_key,
                    'Accept': 'application/json',
                }
                response = requests.get(endpoint, headers=headers, timeout=10)
                if response.status_code == 200:
                    message = _('Connection Successful! ActiveCampaign API is reachable.')
                    is_success = True
                elif response.status_code == 401:
                    message = _('Connection Failed! Invalid API Key. Please check your credentials.')
                else:
                    message = _('Connection Failed! HTTP Status: %s') % response.status_code
        except requests.exceptions.ConnectionError:
            message = _('Connection Error: Unable to reach the ActiveCampaign server. Check your URL.')
        except requests.exceptions.Timeout:
            message = _('Connection Timeout: The ActiveCampaign server took too long to respond.')
        except Exception as e:
            _logger.error('AC connection test error: %s', str(e))
            message = _('Unexpected Error: %s') % str(e)

        wizard = self.env['sh.ac.message.wizard'].create({
            'message': message,
            'is_success': is_success,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Connection Test Result'),
            'res_model': 'sh.ac.message.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }
