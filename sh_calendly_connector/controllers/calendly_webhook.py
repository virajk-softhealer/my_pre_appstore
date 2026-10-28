# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import hashlib
import hmac
import json
import logging
import time

from odoo import http
from odoo.http import request

from ..services.calendly_sync import CalendlySyncService


_logger = logging.getLogger(__name__)


class CalendlyWebhookController(http.Controller):
    @http.route('/calendly/webhook/<int:company_id>', type='http', auth='public', methods=['POST'], csrf=False)
    def calendly_webhook(self, company_id, **kwargs):
        company = request.env['res.company'].sudo().browse(company_id).exists()
        if not company or not company.sh_calendly_webhook_signing_key:
            return request.make_json_response({'error': 'not_configured'}, status=404)
        raw_body = request.httprequest.get_data(cache=True)
        if len(raw_body) > 1024 * 1024:
            return request.make_json_response({'error': 'payload_too_large'}, status=413)
        signature = request.httprequest.headers.get('Calendly-Webhook-Signature', '')
        if not self._verify_signature(raw_body, signature, company.sh_calendly_webhook_signing_key):
            return request.make_json_response({'error': 'invalid_signature'}, status=401)
        try:
            payload = json.loads(raw_body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return request.make_json_response({'error': 'invalid_json'}, status=400)
        event_type = payload.get('event') or payload.get('event_type')
        event_type_events = ('event_type.created', 'event_type.updated', 'event_type.deleted')
        if event_type not in ('invitee.created', 'invitee.updated', 'invitee.canceled') + event_type_events:
            return request.make_json_response({'status': 'ignored'}, status=200)
        auto_sync = (
            company.sh_calendly_auto_sync_event_types
            if event_type in event_type_events
            else company.sh_calendly_auto_sync_to_odoo
        )
        if company.sh_calendly_enable_queue:
            service = CalendlySyncService(company.env, company)
            service.queue_webhook_payload(payload)
            return request.make_json_response({'status': 'deferred'}, status=200)
        if not auto_sync:
            # The corresponding manual or scheduled flow owns synchronization when direct processing is disabled.
            return request.make_json_response({'status': 'deferred'}, status=200)
        # The signed webhook is the authorization boundary for this public callback. The sync must run with
        # elevated ORM access because Calendly has no Odoo user session, while the company and payload are
        # restricted to the verified company endpoint above.
        service = CalendlySyncService(company.env, company)
        if event_type in event_type_events:
            status = service.process_event_type_webhook(payload)
            return request.make_json_response({'status': status}, status=200 if status == 'success' else 422)
        log = service.process_webhook(payload)
        return request.make_json_response({'status': log.status}, status=200 if log.status != 'failed' else 422)

    @staticmethod
    def _verify_signature(raw_body, signature, signing_key):
        values = dict(item.split('=', 1) for item in signature.split(',') if '=' in item)
        timestamp = values.get('t')
        provided = values.get('v1')
        if not timestamp or not provided:
            return False
        try:
            if abs(time.time() - int(timestamp)) > 300:
                return False
        except ValueError:
            return False
        signed_payload = f'{timestamp}.{raw_body.decode("utf-8")}'.encode('utf-8')
        expected = hmac.new(signing_key.encode('utf-8'), signed_payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    @http.route('/calendly/oauth/callback', type='http', auth='user', methods=['GET'], csrf=False)
    def calendly_oauth_callback(self, code=None, state=None, **kwargs):
        if not code or not state:
            return request.not_found()
        company = request.env.company
        expected = request.env['ir.config_parameter'].sudo().get_param(
            f'sh_calendly.oauth_state.{company.id}',
        )
        if not expected or not hmac.compare_digest(expected, state):
            return request.not_found()
        from ..services.calendly_api import CalendlyApi
        if not CalendlyApi(request.env, company).exchange_code(code):
            _logger.error('Calendly OAuth callback failed: company_id=%s', company.id)
            return request.redirect('/odoo/settings?calendly_oauth=failed')
        return request.redirect('/odoo/settings?calendly_oauth=connected')
