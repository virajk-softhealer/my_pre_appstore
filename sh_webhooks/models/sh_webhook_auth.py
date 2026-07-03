# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields
from odoo.exceptions import UserError
import requests

class ShWebhookAuth(models.Model):
    _name = 'sh.webhook.auth'
    _description = 'Webhook Authentication'

    name = fields.Char("Name", required=True)
    auth_type = fields.Selection([
        ('basic', 'Basic Authentication'),
        ('bearer', 'Bearer Token'),
        ('apikey', 'API Key'),
    ], string="Authentication Type", default='basic', required=True)

    # Basic Auth
    username = fields.Char("Username")
    password = fields.Char("Password")

    # Bearer Token
    token = fields.Char("Token")

    # API Key
    api_key_name = fields.Char("API Key Name", default="X-API-Key")
    api_key_value = fields.Char("API Key Value")
    api_key_location = fields.Selection([
        ('header', 'Header'),
        ('query', 'Query String'),
    ], string="API Key Location", default='header')

    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)

    state = fields.Selection([
        ('draft', 'Not Confirmed'),
        ('success', 'Confirmed'),
        ('failed', 'Failed')
    ], string="Status", default='draft')
    test_url = fields.Char("Test URL", help="URL to use for testing these credentials")

    def _get_auth_obj(self):
        """ Returns the authentication parameters for requests """
        self.ensure_one()
        if self.auth_type == 'basic':
            from requests.auth import HTTPBasicAuth
            return HTTPBasicAuth(self.username, self.password)
        return None

    def action_test_auth(self):
        self.ensure_one()
        if not self.test_url:
            
            raise UserError("Please provide a Test URL to verify the credentials.")
        
        
        try:
            headers = {}
            params = {}
            auth = self._get_auth_obj()
            
            if self.auth_type == 'bearer':
                headers['Authorization'] = f'Bearer {self.token}'
            elif self.auth_type == 'apikey':
                if self.api_key_location == 'header':
                    headers[self.api_key_name] = self.api_key_value
                else:
                    params[self.api_key_name] = self.api_key_value

            response = requests.get(
                self.test_url, 
                headers=headers, 
                params=params, 
                auth=auth, 
                timeout=10
            )

            if 200 <= response.status_code < 300:
                self.write({'state': 'success'})
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Success',
                        'message': 'Credentials verified successfully (Status %s)' % response.status_code,
                        'type': 'success',
                    }
                }
            else:
                self.write({'state': 'failed'})
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Rejected',
                        'message': 'Server rejected credentials (Status %s)' % response.status_code,
                        'type': 'danger',
                    }
                }
        except Exception as e:
            self.write({'state': 'failed'})
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Error',
                    'message': str(e),
                    'type': 'danger',
                }
            }
