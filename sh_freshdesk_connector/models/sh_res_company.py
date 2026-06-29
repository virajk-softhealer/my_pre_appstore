# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import uuid

from odoo import fields, models, api, _
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    sh_freshdesk_domain = fields.Char(string='Freshdesk Domain')
    sh_freshdesk_api_key = fields.Char(string='API Key')
    sh_freshdesk_last_contact_sync_date = fields.Datetime(string='Last Contact Sync Date')
    sh_freshdesk_last_company_sync_date = fields.Datetime(string='Last Company Sync Date')
    sh_freshdesk_last_ticket_sync_date = fields.Datetime(string='Last Ticket Sync Date')
    sh_fd_contact_sync_limit = fields.Integer(string='Contact Sync Limit', default=0)
    sh_fd_company_sync_limit = fields.Integer(string='Company Sync Limit', default=0)
    sh_fd_ticket_sync_limit = fields.Integer(string='Ticket Sync Limit', default=0)

    sh_enable_partner_sync = fields.Boolean(string='Sync Contacts', default=False)
    sh_enable_ticket_sync = fields.Boolean(string='Sync Tickets', default=False)
    sh_import_conversations = fields.Boolean(string='Import Conversations', default=False)
    sh_import_time_entries = fields.Boolean(string='Import Time Entries', default=False)

    sh_manual_action = fields.Boolean(string='Manual Action', default=False)
    sh_automated_action = fields.Boolean(string='Automated Action', default=False)
    sh_fd_partner_create = fields.Boolean(string='Contact Create', default=False)
    sh_fd_partner_update = fields.Boolean(string='Contact Update', default=False)
    sh_fd_partner_delete = fields.Boolean(string='Contact Delete', default=False)
    sh_fd_ticket_create = fields.Boolean(string='Ticket Create', default=False)
    sh_fd_ticket_update = fields.Boolean(string='Ticket Update', default=False)
    sh_fd_ticket_delete = fields.Boolean(string='Ticket Delete', default=False)

    sh_cron_interval_number = fields.Integer(string='Interval Number', default=1)
    sh_cron_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], string='Interval Type', default='hours')

    sh_sync_start_date = fields.Datetime(string='From Date')
    sh_sync_end_date = fields.Datetime(string='To Date')

    sh_enable_freshdesk_webhook = fields.Boolean(string='Enable Freshdesk Webhook', default=False)
    sh_freshdesk_webhook_secret = fields.Char(
        string='Freshdesk Webhook Secret',
        default=lambda self: uuid.uuid4().hex,
        copy=False,
        readonly=True,
    )
    sh_freshdesk_webhook_team_id = fields.Many2one(
        'helpdesk.team',
        string='Webhook Default Helpdesk Team',
        ondelete='set null',
    )
    sh_freshdesk_webhook_url = fields.Char(
        string='Freshdesk Webhook URL',
        compute='_compute_sh_freshdesk_webhook_url',
    )
    sh_fd_webhook_ticket_create = fields.Boolean(string='Webhook Ticket Create', default=True)
    sh_fd_webhook_ticket_update = fields.Boolean(string='Webhook Ticket Update', default=True)

    def _compute_sh_freshdesk_webhook_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        for company in self:
            if base_url and company.sh_freshdesk_webhook_secret:
                company.sh_freshdesk_webhook_url = '%s/freshdesk/webhook/%s/%s' % (
                    base_url,
                    company.id,
                    company.sh_freshdesk_webhook_secret,
                )
            else:
                company.sh_freshdesk_webhook_url = False

    def action_generate_freshdesk_webhook_secret(self):
        for company in self.sudo():
            company.sh_freshdesk_webhook_secret = uuid.uuid4().hex
        return True

    @api.constrains('sh_fd_contact_sync_limit', 'sh_fd_company_sync_limit', 'sh_fd_ticket_sync_limit', 'sh_cron_interval_number')
    def _check_positive_values(self):
        for record in self:
            if record.sh_fd_contact_sync_limit < 0:
                raise ValidationError(_("Contact Sync Limit cannot be negative."))
            if record.sh_fd_company_sync_limit < 0:
                raise ValidationError(_("Company Sync Limit cannot be negative."))
            if record.sh_fd_ticket_sync_limit < 0:
                raise ValidationError(_("Ticket Sync Limit cannot be negative."))
            if record.sh_cron_interval_number <= 0:
                raise ValidationError(_("Interval Number must be greater than zero."))
