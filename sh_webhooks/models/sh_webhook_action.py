# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval, wrap_module
import requests
import json
import logging
import odoo
import odoo.modules.registry
import time

_logger = logging.getLogger(__name__)

class ShWebhookAction(models.Model):
    _name = 'sh.webhook.action'
    _description = 'Webhook Action'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char("Action Name", required=True, tracking=True, help="Enter a descriptive name for this webhook integration.")
    model_id = fields.Many2one('ir.model', string="Model", required=True, ondelete='cascade', tracking=True, help="Select the Odoo model that will trigger this webhook (e.g., Contact, Sale Order).")
    model_name = fields.Char(related='model_id.model', string="Model Name", readonly=True)
    url = fields.Char("Webhook URL", required=True, tracking=True, help="The external endpoint URL where the data will be sent.")
    server_action_id = fields.Many2one('ir.actions.server', string="Server Action", ondelete='cascade')
    method = fields.Selection([
        ('POST', 'POST'),
    ], string="Method", default='POST', required=True, help="The HTTP request method. POST is the universal standard for webhook data synchronization.")
    timeout = fields.Integer("Timeout", default=25, help="Maximum time (in seconds) to wait for the external server to respond.")
    run_type = fields.Selection([
        ('single', 'Single'),
        ('multi', 'Multi')
    ], string="Run Type", default='multi', required=True, help="Single: Send one request per record. Multi: Send one request containing all records in a list.")
    send_type = fields.Selection([
        ('immediately', 'Immediately'),
        # ('post_commit', 'Post Commit'),
        ('cron', 'Cron Job')
    ], string="Send Type", default='immediately', required=True, help="Immediately: Send during the save process. Cron Job: Send in the background every 5 minutes (Recommended for high performance).")
    field_ids = fields.Many2many('ir.model.fields', string="Fields", help="Choose specific fields to trigger the webhook. If empty, any change to the record will trigger it.")
    
    adapt_url = fields.Boolean("Adapt URL", help="Enable this to modify the URL dynamically using Python code.")
    adapt_headers = fields.Boolean("Adapt Headers", help="Enable this to add dynamic headers using Python code.")
    apply_auth = fields.Boolean("Apply Authentication", help="Enable this to use a pre-configured Authentication profile (like Basic or API Key).")
    adapt_payload = fields.Boolean("Adapt Payload", help="Enable this to customize the JSON structure of the sent data using Python code.")
    process_response = fields.Boolean("Process Response", help="Enable this to run Python code based on the response received from the external server.")
    verify_ssl = fields.Boolean("Verify SSL", default=True, help="If enabled, Odoo will check the SSL certificate of the target server. Disable for local/untrusted testing.")
    sudo_fields = fields.Boolean("Sudo Fields", help="If enabled, Odoo will fetch the record data with full system permissions, bypassing record rules.")
    
    auth_id = fields.Many2one('sh.webhook.auth', string="Authentication", tracking=True, help="Select the authentication profile to use for this webhook.")
    header_ids = fields.One2many('sh.webhook.header', 'webhook_id', string="Header Values", help="Add custom static HTTP headers here.")
    
    code_url = fields.Text("URL Code", help="Python code to compute the final URL. Use 'url' variable.")
    code_headers = fields.Text("Headers Code", help="Python code to compute extra headers. Use 'headers' dictionary.")
    code_payload = fields.Text("Payload Code", help="Python code to compute the data payload. Use 'payload' variable.")
    code_response = fields.Text("Response Code", help="Python code to process the server's response. Available: 'response', 'records', 'env', 'json'.")
    code_error = fields.Text("Error Code", help="Python code to run if the request fails. Available: 'error', 'records', 'env'.")
    
    company_id = fields.Many2one(
        'res.company', 
        string='Company', 
        required=True, 
        default=lambda self: self.env.company
    )
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._update_server_action()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(f in vals for f in ['name', 'model_id']):
            for rec in self:
                rec._update_server_action()
        return res

    def _update_server_action(self):
        """ Create or update the linked server action """
        for rec in self:
            action_vals = {
                'name': f"Webhook: {rec.name}",
                'model_id': rec.model_id.id,
                'state': 'code',
                'code': f"model.browse(env.context.get('active_ids', [])).env['sh.webhook.action'].browse({rec.id}).trigger_webhook(records)",
            }
            if rec.server_action_id:
                rec.server_action_id.write(action_vals)
            else:
                rec.server_action_id = self.env['ir.actions.server'].create(action_vals)
            
    def create_contextual_action(self):
        """ Create a contextual action in the model's 'Action' menu """
        for rec in self:
            if rec.server_action_id:
                rec.server_action_id.create_action()

    def remove_contextual_action(self):
        """ Remove the contextual action """
        for rec in self:
            if rec.server_action_id:
                rec.server_action_id.unlink_action()

    def action_preview(self):
        """ Open a wizard to select records for previewing the webhook """
        return {
            'name': _('Select Records to Test'),
            'type': 'ir.actions.act_window',
            'res_model': 'sh.webhook.test.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_webhook_id': self.id, 'default_model_id': self.model_id.id}
        }

    def _get_eval_context(self, records=None):
        """ Prepare evaluation context for Python code """
        return {
            'env': self.env,
            'model': self.env[self.model_name],
            'records': records,
            'record': records[0] if records and len(records) == 1 else None,
            'datetime': fields.Datetime,
            'date': fields.Date,
            'time': wrap_module(time, ['time', 'sleep', 'strftime', 'strptime']),
            'json': wrap_module(json, ['loads', 'dumps', 'JSONEncoder', 'JSONDecoder']),
            'requests': wrap_module(requests, ['get', 'post', 'put', 'patch', 'delete', 'request', 'Response']),
            'log': _logger.info,
            'UserError': ValidationError,
        }

    def _prepare_payload(self, records):
        """ Prepare the payload for the webhook """
        payload = []
        for rec in records:
            fields_to_read = self.field_ids.mapped('name') or ['id']
            record_to_read = rec.sudo() if self.sudo_fields else rec
            rec_data = record_to_read.read(fields_to_read)[0]
            payload.append(rec_data)
        
        final_payload = payload if self.run_type == 'multi' else (payload[0] if payload else {})

        if self.adapt_payload and self.code_payload:
            eval_context = self._get_eval_context(records)
            eval_context['payload'] = final_payload
            safe_eval(self.code_payload, eval_context, mode='exec')
            return eval_context.get('payload', final_payload)
        
        return final_payload

    def _normalize_headers(self, headers):
        """Return a requests-safe header mapping."""
        normalized_headers = {}
        for key, value in (headers or {}).items():
            if key in (None, False):
                continue

            header_key = str(key).strip()
            if not header_key:
                continue

            if value in (None, False):
                value = ''

            if not isinstance(value, (str, bytes)):
                value = str(value)

            normalized_headers[header_key] = value

        return normalized_headers

    def _prepare_headers(self, records):
        """ Prepare the headers for the webhook """
        headers = self._normalize_headers({header.key: header.value for header in self.header_ids})
        if self.adapt_headers and self.code_headers:
            eval_context = self._get_eval_context(records)
            eval_context['headers'] = headers
            safe_eval(self.code_headers, eval_context, mode='exec')
            headers = eval_context.get('headers', headers)
        return self._normalize_headers(headers)

    def _prepare_url(self, records):
        """ Prepare the URL for the webhook """
        url = self.url
        if self.adapt_url and self.code_url:
            eval_context = self._get_eval_context(records)
            eval_context['url'] = url
            safe_eval(self.code_url, eval_context, mode='exec')
            url = eval_context.get('url', url)
        return url

    def trigger_webhook(self, records):
        """ Main method to trigger the webhook call """
        if not records:
            return
        
        if self.run_type == 'single':
            for rec in records:
                self._trigger_execution(rec)
        else:
            self._trigger_execution(records)

    def _trigger_execution(self, records):
        """ Internal helper to handle the sending logic (immediate, post-commit, etc.) """
        if self.send_type == 'immediately':
            self._send_request(records)
        # elif self.send_type == 'post_commit':
        #     # Use post-commit logic (currently on hold)
        #     record_ids = records.ids
        #     self.env.cr.postcommit.add(lambda: self._send_request_with_new_env(record_ids))
        elif self.send_type == 'cron':
            # Create a pending log entry to be processed by the background Cron
            url = self._prepare_url(records)
            headers = self._prepare_headers(records)
            payload = self._prepare_payload(records)
            self._log_request(url, headers, payload, records=records, state='pending')

    def _process_webhook_queue(self):
        """ Background task to process pending or failed webhooks with retries """
        # Search for Pending OR Error logs that have tried less than 3 times
        logs_to_process = self.env['sh.webhook.log'].search([
            ('webhook_id', '!=', False),
            '|',
            ('state', '=', 'pending'),
            '&', ('state', '=', 'error'), ('retry_count', '<', 3)
        ], limit=100)
        
        for log in logs_to_process:
            webhook = log.webhook_id
            try:
                # Re-apply authentication if needed
                headers = json.loads(log.request_header or '{}')
                payload = json.loads(log.request_body or '{}')
                auth = None
                if webhook.apply_auth and webhook.auth_id:
                    if webhook.auth_id.auth_type == 'basic':
                        auth = (webhook.auth_id.username, webhook.auth_id.password)
                    elif webhook.auth_id.auth_type == 'bearer':
                        headers['Authorization'] = f"Bearer {webhook.auth_id.token}"

                response = requests.request(
                    method=webhook.method,
                    url=log.url,
                    headers=headers,
                    auth=auth,
                    json=payload if webhook.method in ['POST', 'PUT', 'PATCH'] else None,
                    params=payload if webhook.method == 'GET' else None,
                    timeout=webhook.timeout,
                    verify=webhook.verify_ssl
                )
                
                # Re-evaluate process response if configured
                if webhook.process_response and webhook.code_response:
                    if log.res_ids and log.res_model:
                        id_list = [int(i) for i in log.res_ids.split(',')]
                        records = self.env[log.res_model].browse(id_list)
                    else:
                        records = self.env.user
                    
                    eval_context = webhook._get_eval_context(records)
                    eval_context['response'] = response
                    with self.env.cr.savepoint():
                        safe_eval(webhook.code_response, eval_context, mode='exec')

                log.write({
                    'response_header': json.dumps(dict(response.headers)),
                    'response_body': response.text,
                    'status_code': response.status_code,
                    'state': 'success' if 200 <= response.status_code < 300 else 'error',
                    'error_msg': False if 200 <= response.status_code < 300 else log.error_msg,
                    'retry_count': log.retry_count + (1 if response.status_code >= 300 else 0)
                })
            except Exception as e:
                log.write({
                    'state': 'error',
                    'error_msg': str(e),
                    'retry_count': log.retry_count + 1
                })
            # Commit after each one to ensure progress is saved
            self.env.cr.commit()

    # def _send_request_with_new_env(self, record_ids):
    #     """ Send request with a fresh environment (for post-commit) """
    #     db_name = self.env.cr.dbname
    #     registry = odoo.modules.registry.Registry(db_name)
    #     with registry.cursor() as new_cr:
    #         new_env = odoo.api.Environment(new_cr, self.env.uid, self.env.context)
    #         # We must browse the action in the NEW environment
    #         self.with_env(new_env)._send_request(new_env[self.model_name].browse(record_ids))
    #         new_cr.commit()

    def _send_request(self, records):
        """ Perform the actual HTTP request """
        url = self.url
        headers = {}
        payload = {}
        try:
            url = self._prepare_url(records)
            headers = self._prepare_headers(records)
            payload = self._prepare_payload(records)
            
            # Apply Authentication
            auth = None
            if self.apply_auth and self.auth_id:
                if self.auth_id.auth_type == 'basic':
                    auth = (self.auth_id.username, self.auth_id.password)
                elif self.auth_id.auth_type == 'bearer':
                    headers['Authorization'] = f"Bearer {self.auth_id.token}"
                elif self.auth_id.auth_type == 'apikey':
                    if self.auth_id.api_key_location == 'header':
                        headers[self.auth_id.api_key_name] = self.auth_id.api_key_value
                    else:
                        if self.method == 'GET':
                            payload[self.auth_id.api_key_name] = self.auth_id.api_key_value
                        else:
                            # Add to URL params if not GET
                            url += ('?' if '?' not in url else '&') + f"{self.auth_id.api_key_name}={self.auth_id.api_key_value}"

            response = requests.request(
                method=self.method,
                url=url,
                headers=headers,
                auth=auth,
                json=payload if self.method in ['POST', 'PUT', 'PATCH'] else None,
                params=payload if self.method == 'GET' else None,
                timeout=self.timeout,
                verify=self.verify_ssl
            )
            
            if self.process_response and self.code_response:
                eval_context = self._get_eval_context(records)
                eval_context['response'] = response
                try:
                    with self.env.cr.savepoint():
                        safe_eval(self.code_response, eval_context, mode='exec')
                except Exception as e:
                    # If script fails, we log it and raise it to the outer block to ensure log state is 'error'
                    _logger.error("Webhook Process Response Error: %s", str(e))
                    raise e
            
            self._log_request(url, headers, payload, records=records, response=response)
                
        except Exception as e:
            _logger.error("Webhook Error: %s", str(e))
            self._log_request(url, headers, payload, records=records, error=e)
            if self.code_error:
                eval_context = self._get_eval_context(records)
                eval_context['error'] = e
                safe_eval(self.code_error, eval_context, mode='exec')

    def _log_request(self, url, headers, payload, records=None, response=None, error=None, state=None):
        """ Log the webhook attempt """
        res_ids = False
        if records:
            res_ids = ','.join(map(str, records.ids))

        log_vals = {
            'webhook_id': self.id,
            'url': url,
            'method': self.method,
            'request_header': json.dumps(headers),
            'request_body': json.dumps(payload),
            'company_id': self.company_id.id,
            'res_model': self.model_name,
            'res_ids': res_ids,
            'state': state or 'pending' if not response and not error else 'success' if response and 200 <= response.status_code < 300 else 'error',
        }
        if response is not None:
            log_vals.update({
                'response_header': json.dumps(dict(response.headers)),
                'response_body': response.text,
                'status_code': response.status_code,
            })
        if error:
            log_vals.update({
                'state': 'error',
                'error_msg': str(error),
            })
        self.env['sh.webhook.log'].sudo().create(log_vals)

class ShWebhookHeader(models.Model):
    _name = 'sh.webhook.header'
    _description = 'Webhook Header'

    webhook_id = fields.Many2one('sh.webhook.action', string="Webhook", required=True, ondelete='cascade')
    key = fields.Char("Key", required=True)
    value = fields.Char("Value", required=True)
