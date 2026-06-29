# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import secrets
from odoo import api, models, fields


class ResCompany(models.Model):
    _inherit = 'res.company'

    sh_ac_configuration = fields.Boolean(
        string='Active Campaign Configuration',
        help='Enable to configure ActiveCampaign integration for this company.'
    )
    sh_ac_url = fields.Char(
        string='URL',
        help='Your ActiveCampaign API URL. Example: https://youraccountname.api-us1.com'
    )
    sh_ac_api_key = fields.Char(
        string='API Key',
        help='Your ActiveCampaign API Key. Found under Settings > Developer in ActiveCampaign.'
    )
    sh_ac_enable_queue_processing = fields.Boolean(
        string='Enable Queue Processing',
        help='When enabled, ActiveCampaign sync requests are queued and processed asynchronously by cron.'
    )
    sh_ac_enable_webhook_processing = fields.Boolean(
        string='Enable Webhook Processing',
        help='When enabled, ActiveCampaign webhook callbacks are accepted and processed in real time.'
    )
    sh_ac_access_token = fields.Char(
        string='Webhook Access Token',
        copy=False,
        readonly=True,
        default=lambda self: secrets.token_urlsafe(32),
        help='Secure token used to authenticate incoming webhooks from ActiveCampaign.'
    )
    sh_ac_webhook_url = fields.Char(
        string='Webhook URL',
        compute='_compute_sh_ac_webhook_url',
        readonly=True,
    )
    sh_ac_webhook_url_warning = fields.Char(
        string='Webhook URL Warning',
        compute='_compute_sh_ac_webhook_url',
        readonly=True,
    )

    @api.depends('sh_ac_access_token', 'sh_ac_enable_webhook_processing')
    def _compute_sh_ac_webhook_url(self):
        """Build the public webhook URL and warning for the current company."""
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for company in self:
            token = company.sh_ac_access_token
            enabled = bool(company.sh_ac_enable_webhook_processing)
            if base_url and token and enabled:
                company.sh_ac_webhook_url = f'{base_url}/sh_active_campaign/webhook/{company.id}/{token}'
                if (
                    'localhost' in base_url
                    or base_url.startswith('http://127.0.0.1')
                    or base_url.startswith('https://127.0.0.1')
                    or not base_url.startswith('https://')
                ):
                    company.sh_ac_webhook_url_warning = (
                        'ActiveCampaign needs a public HTTPS URL. Localhost and plain HTTP URLs will fail.'
                    )
                else:
                    company.sh_ac_webhook_url_warning = False
            else:
                company.sh_ac_webhook_url = False
                company.sh_ac_webhook_url_warning = False

    def _sh_ac_generate_token(self):
        """Generate a secure random access token for webhook authentication."""
        return secrets.token_urlsafe(32)

    def action_generate_sh_ac_webhook_token(self):
        """Regenerate the webhook token used by ActiveCampaign callbacks."""
        for company in self.sudo():
            company.sh_ac_access_token = company._sh_ac_generate_token()
        return True
