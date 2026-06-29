# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging
from odoo import models, fields, api, tools

_logger = logging.getLogger(__name__)


class FreshdeskSyncLog(models.Model):
    _name = 'sh.freshdesk.sync.log'
    _description = 'Freshdesk Sync Log'
    _order = 'sync_date desc'
    _rec_name = 'res_model'

    res_model = fields.Many2one('ir.model', string='Model', readonly=True)
    res_id = fields.Integer(string='Record ID', readonly=True)
    status = fields.Selection([
        ('success', 'Success'),
        ('error', 'Error'),
    ], string='Status', readonly=True)
    message = fields.Html(string='Response', readonly=True)
    error_log = fields.Html(string='Error Log', readonly=True)
    direction = fields.Selection([
        ('odoo_to_freshdesk', 'Odoo -> Freshdesk'),
        ('freshdesk_to_odoo', 'Freshdesk -> Odoo'),
    ], string='Direction', readonly=True)
    operation = fields.Selection([
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
    ], string='Operation', readonly=True)
    sync_date = fields.Datetime(string='Sync DateTime', default=fields.Datetime.now, readonly=True)
    sh_formatted_message = fields.Text(string='Formatted Message', compute='_compute_formatted_message')
    partner_id = fields.Many2one('res.partner', string='Contact', compute='_compute_record_links', store=True)
    ticket_id = fields.Many2one('helpdesk.ticket', string='Ticket', compute='_compute_record_links', store=True)

    @api.depends('res_model', 'res_id')
    def _compute_record_links(self):
        for log in self:
            partner = False
            ticket = False
            if log.res_model and log.res_id:
                if log.res_model.model == 'res.partner':
                    partner = log.res_id
                elif log.res_model.model == 'helpdesk.ticket':
                    ticket = log.res_id
            log.partner_id = partner
            log.ticket_id = ticket

    @api.depends('message')
    def _compute_formatted_message(self):
        for record in self:
            formatted = False
            if record.message:
                try:
                    # Strip HTML if any (though usually it's raw JSON text)
                    raw_text = tools.html2plaintext(record.message) if record.message else ""
                    json_data = json.loads(raw_text)
                    formatted = json.dumps(json_data, indent=4)
                except Exception:
                    # Fallback to original if not JSON
                    formatted = tools.html2plaintext(record.message)
            record.sh_formatted_message = formatted
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        readonly=True,
    )

    @api.model
    def create_log(self, model_name, res_id, status, message, direction='odoo_to_freshdesk', error_log=False, operation=False):
        model = self.env['ir.model'].sudo().search([('model', '=', model_name)], limit=1)
        if error_log:
            error_log = "<pre style='white-space: pre-wrap; word-break: break-all; color: red;'>%s</pre>" % error_log
        self.sudo().create({
            'res_model': model.id if model else False,
            'res_id': res_id,
            'status': status,
            'message': message,
            'direction': direction,
            'error_log': error_log,
            'operation': operation,
        })

    @api.model
    def action_clean_old_logs(self):
        """
        Delete sh.freshdesk.sync.log records older than 30 days.
        Uses direct SQL for performance — avoids loading records into memory.
        Runs via daily cron: 'Clean Contact/Ticket Logs'.
        """
        _logger.info('Freshdesk Log Cleanup: Deleting records older than 30 days...')
        self.env.cr.execute(
            "DELETE FROM sh_freshdesk_sync_log WHERE sync_date < NOW() - INTERVAL '30 days'"
        )
        deleted = self.env.cr.rowcount
        _logger.info('Freshdesk Log Cleanup: Deleted %s old log record(s).', deleted)
        return True