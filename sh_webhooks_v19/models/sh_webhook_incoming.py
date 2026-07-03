# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval, wrap_module
from types import SimpleNamespace
import uuid
import json
import logging

_logger = logging.getLogger(__name__)

class ShWebhookIncoming(models.Model):
    _name = 'sh.webhook.incoming'
    _description = 'Incoming Webhook Route'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char("Name", required=True, tracking=True, help="A unique name to identify this inbound webhook route.")
    token = fields.Char("Token", required=True, default=lambda self: str(uuid.uuid4()), copy=False, help="A unique security token used in the URL. If this is leaked, you can generate a new one.")
    active = fields.Boolean(default=True, help="If unchecked, this webhook route will be disabled and ignore all incoming requests.")
    
    model_id = fields.Many2one('ir.model', string="Model", tracking=True, help="The Odoo model that the incoming data will interact with (Optional).")
    model_name = fields.Char(related='model_id.model', string="Model Name", readonly=True)
    
    method = fields.Selection([
        ('GET', 'GET'),
        ('POST', 'POST'),
    ], string="Method", default='POST', required=True, help="The HTTP method used by the sender. POST is the standard for data, while GET is sometimes used for service verification (Challenges).")
    
    code = fields.Text("Python Code", default="""# Available variables:
#  - env: Odoo environment
#  - model: Odoo model (if specified)
#  - request: The Odoo request object
#  - payload: The JSON/Form payload received
#  - headers: The HTTP headers
#  - log: log function
#  - result: dict to return as JSON response

# Example:
# model.create({'name': payload.get('name')})
# result.update({'status': 'ok'})
""", required=True, help="The Python code that runs when this webhook is called. You can use variables like 'payload' to access the incoming data.")

    url = fields.Char("Webhook URL", compute='_compute_url', store=False, help="The unique URL that external systems should call to send data to Odoo.")

    @api.depends('token')
    def _compute_url(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        for rec in self:
            rec.url = f"{base_url}/sh_webhooks/incoming/{rec.token}"

    def process_request(self, request_obj, payload, headers):
        """ Process the incoming request using safe_eval """
        self.ensure_one()
        result = {'status': 'success'}
        eval_context = {
            'env': self.env,
            'model': self.env[self.model_name] if self.model_name else None,
            'request': request_obj,
            'payload': payload,
            'headers': headers,
            'datetime': fields.Datetime,
            'date': fields.Date,
            'json': wrap_module(json, ['loads', 'dumps', 'JSONEncoder', 'JSONDecoder']),
            'log': _logger.info,
            'result': result,
        }
        try:
            safe_eval(self.code, eval_context, mode='exec')
            state = 'error' if result.get('status') == 'error' else 'success'
            self._log_request(request_obj, payload, headers, result, state=state)
            return result
        except Exception as e:
            _logger.error("Incoming Webhook Error: %s", str(e))
            self._log_request(request_obj, payload, headers, result, error=e, state='error')
            return {'status': 'error', 'message': str(e)}

    def _build_request_proxy(self, log):
        """Build a minimal request-like object for queued inbound retries."""
        headers = json.loads(log.request_header or '{}')
        payload = json.loads(log.request_body or '{}')
        request_proxy = SimpleNamespace(
            httprequest=SimpleNamespace(
                method=log.method or 'POST',
                headers=headers,
                content_type='application/json',
            ),
            params=payload,
        )
        return request_proxy, payload, headers

    def _process_inbound_webhook_queue(self):
        """Process pending inbound webhook logs."""
        logs_to_process = self.env['sh.webhook.log'].search([
            ('direction', '=', 'inbound'),
            ('incoming_webhook_id', '!=', False),
            ('state', '=', 'pending'),
        ], limit=100)

        for log in logs_to_process:
            webhook = log.incoming_webhook_id
            try:
                request_proxy, payload, headers = self._build_request_proxy(log)
                result = {'status': 'success'}
                eval_context = {
                    'env': self.env,
                    'model': self.env[webhook.model_name] if webhook.model_name else None,
                    'request': request_proxy,
                    'payload': payload,
                    'headers': headers,
                    'datetime': fields.Datetime,
                    'date': fields.Date,
                    'json': wrap_module(json, ['loads', 'dumps', 'JSONEncoder', 'JSONDecoder']),
                    'log': _logger.info,
                    'result': result,
                }

                with self.env.cr.savepoint():
                    safe_eval(webhook.code, eval_context, mode='exec')

                state = 'error' if result.get('status') == 'error' else 'success'
                log.write({
                    'response_body': json.dumps(result),
                    'status_code': 200 if state == 'success' else 500,
                    'state': state,
                    'error_msg': False if state == 'success' else log.error_msg,
                })
            except Exception as e:
                _logger.error("Queued Incoming Webhook Error: %s", str(e))
                log.write({
                    'response_body': json.dumps({'status': 'error', 'message': str(e)}),
                    'status_code': 500,
                    'state': 'error',
                    'error_msg': str(e),
                })
            self.env.cr.commit()

    def _log_request(self, request_obj, payload, headers, result, error=None, state='success'):
        def json_safe(data):
            try:
                return json.dumps(data)
            except:
                return str(data)

        log_vals = {
            'direction': 'inbound',
            'incoming_webhook_id': self.id,
            'method': request_obj.httprequest.method,
            'request_header': json_safe(dict(headers)),
            'request_body': json_safe(payload),
            'response_body': json_safe(result),
            'status_code': 200 if state == 'success' else 500,
            'state': state,
            'company_id': self.env.company.id,
        }
        if error:
            log_vals['error_msg'] = str(error)
        self.env['sh.webhook.log'].sudo().create(log_vals)
