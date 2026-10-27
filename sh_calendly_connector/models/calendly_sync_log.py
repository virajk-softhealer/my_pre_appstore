# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import html
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CalendlySyncLog(models.Model):
    _name = 'sh.calendly.sync.log'
    _description = 'Calendly Synchronization Log'
    _order = 'date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    date = fields.Datetime(default=fields.Datetime.now, required=True, index=True, tracking=True)
    source = fields.Selection(
        selection=[
            ('webhook', 'Webhook'),
            ('scheduled_poll', 'Scheduled Poll'),
            ('odoo_action', 'Odoo Action'),
        ], required=True, index=True, tracking=True,
    )
    event_type = fields.Char(index=True, tracking=True)
    operation = fields.Selection(
        selection=[
            ('create', 'Create'),
            ('update', 'Update'),
            ('delete', 'Delete'),
        ], index=True, tracking=True,
    )
    sync_direction = fields.Selection(
        selection=[
            ('calendly_to_odoo', 'Calendly to Odoo'),
            ('odoo_to_calendly', 'Odoo to Calendly'),
        ], string="Direction",index=True, tracking=True,
    )
    invitee_email = fields.Char(index=True)
    event_type_id = fields.Many2one(
        'sh.calendly.event.type',
        string='Event Type',
        readonly=True,
        ondelete='set null',
        index=True,
    )
    status = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('success', 'Success'),
            ('failed', 'Failed'),
            ('skipped', 'Skipped'),
        ], required=True, default='pending', index=True, tracking=True,
    )
    calendar_event_id = fields.Many2one('calendar.event', ondelete='set null', index=True)
    lead_id = fields.Many2one('crm.lead', ondelete='set null', index=True)
    message = fields.Text()
    api_error = fields.Text(string='API Error', copy=False, readonly=True)
    response = fields.Json(copy=False, readonly=True)
    response_display = fields.Html(compute='_compute_response_display', readonly=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True, default=lambda self: self.env.company,
        ondelete='restrict',
    )
    external_event_id = fields.Char(index=True, copy=False)
    external_invitee_id = fields.Char(index=True, copy=False)

    @api.constrains('company_id', 'lead_id')
    def _check_company_links(self):
        for record in self:
            if record.lead_id and record.lead_id.company_id and record.lead_id.company_id != record.company_id:
                raise UserError(_('The lead must belong to the log company.'))

    @api.depends('response')
    def _compute_response_display(self):
        for record in self:
            if not record.response:
                record.response_display = False
                continue
            formatted_response = json.dumps(record.response, indent=2, ensure_ascii=False, default=str)
            record.response_display = '<pre>%s</pre>' % html.escape(formatted_response)

    def action_retry_failed(self):
        from ..services.calendly_sync import CalendlySyncService

        for log in self:
            if log.status != 'failed':
                continue
            log.write({'status': 'pending'})
            CalendlySyncService(self.env, log.company_id).retry_log(log)
        return True
