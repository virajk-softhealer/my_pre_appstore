# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields, api, _

class ShWebhookLog(models.Model):
    _name = 'sh.webhook.log'
    _description = 'Webhook Log'
    _order = 'create_date desc'
    _rec_name = 'method'

    direction = fields.Selection([
        ('outbound', 'Outbound'),
        ('inbound', 'Inbound')
    ], string="Direction", default='outbound', required=True, help="Specifies if the webhook is being sent from Odoo (Outbound) or received by Odoo (Inbound).")
    
    webhook_id = fields.Many2one('sh.webhook.action', string="Outbound Webhook", ondelete='cascade', help="The outbound webhook configuration that triggered this log.")
    incoming_webhook_id = fields.Many2one('sh.webhook.incoming', string="Inbound Webhook", ondelete='cascade', help="The inbound webhook configuration that received this request.")
    
    res_model = fields.Char("Related Model", help="The technical name of the Odoo model (e.g., res.partner) associated with this webhook.")
    res_ids = fields.Text("Record IDs", help="The database IDs of the records that were processed in this webhook call.")
    
    method = fields.Char("Method", help="The HTTP method used for the request (usually POST).")
    request_header = fields.Text("Request Header", help="The full JSON set of HTTP headers sent with the request.")
    request_body = fields.Text("Request Body", help="The actual data payload sent to the external server.")
    response_header = fields.Text("Response Header", help="The HTTP headers returned by the external server.")
    response_body = fields.Text("Response Body", help="The data or message returned by the external server after the call.")
    status_code = fields.Integer("Status Code", help="The HTTP response status code (e.g., 200: Success, 404: Not Found, 500: Server Error).")
    state = fields.Selection([
        ('pending', 'Pending'),
        ('success', 'Success'),
        ('error', 'Error')
    ], string="Status", default='pending', help="Current state of the webhook: 'Pending' means it is waiting for the background Cron, 'Success' means it was delivered, and 'Error' means it failed.")
    error_msg = fields.Text("Error Message", help="If the webhook failed, the technical error message will be shown here.")
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company, help="The company associated with this webhook log.")

    @api.model
    def _gc_webhook_logs(self):
        """ Delete logs older than 30 days using raw SQL for performance """
        self.env.cr.execute("""
            DELETE FROM sh_webhook_log 
            WHERE create_date < (now() - interval '30 days')
        """)

    def action_retry(self):
        """Reset selected queue items back to pending."""
        for record in self:
            record.write({
                'state': 'pending',
                'error_msg': False,
                'status_code': False,
            })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Queue Reset'),
                'message': _('Selected queue records have been reset to Pending.'),
                'sticky': False,
                'type': 'success',
                'next': {
                    'type': 'ir.actions.client',
                    'tag': 'reload',
                },
            }
        }
