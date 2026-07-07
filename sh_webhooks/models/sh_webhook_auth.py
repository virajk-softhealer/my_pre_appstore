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
    ], string="Authentication Type", default='basic', required=True)

    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)

    state = fields.Selection([
        ('draft', 'Not Confirmed'),
        ('success', 'Confirmed'),
        ('failed', 'Failed')
    ], string="Status", default='draft')
    test_url = fields.Char("Test URL", help="URL to use for testing these credentials")

    def action_test_auth(self):
        self.ensure_one()
        if not self.test_url:
            raise UserError("Please provide a Test URL to verify the credentials.")

        try:
            headers = {}

            response = requests.get(
                self.test_url,
                headers=headers,
                timeout=10,
            )

            if 200 <= response.status_code < 300:
                self.write({'state': 'success'})
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                    'title': 'Success',
                    'message': 'Profile verified successfully (Status %s)' % response.status_code,
                    'type': 'success',
                    'next': {
                            'type': 'ir.actions.client',
                            'tag': 'soft_reload',
                        },
                }
            }
            else:
                self.write({'state': 'failed'})
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                    'title': 'Rejected',
                    'message': 'Server rejected profile verification (Status %s)' % response.status_code,
                    'type': 'danger',
                    'next': {
                            'type': 'ir.actions.client',
                            'tag': 'soft_reload',
                        },
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
                    'next': {
                            'type': 'ir.actions.client',
                            'tag': 'soft_reload',
                        },
                }
            }
