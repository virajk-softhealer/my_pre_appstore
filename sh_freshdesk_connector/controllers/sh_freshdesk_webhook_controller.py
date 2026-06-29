# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import hmac
import json
import logging
from urllib.parse import parse_qs
import traceback

from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)


class FreshdeskWebhookController(Controller):
    """Incoming Freshdesk ticket webhook endpoint."""

    _WEBHOOK_ID_HEADERS = (
        'X-Freshdesk-Ticket-Id',
        'X-Freshdesk-Object-Id',
        'X-Freshdesk-Resource-Id',
        'X-Freshdesk-Id',
        'X-Ticket-Id',
    )

    _SAFE_LOG_HEADERS = (
        'Content-Type',
        'User-Agent',
        'X-Forwarded-For',
        'X-Request-Id',
        'X-Freshdesk-Event',
        'X-Freshdesk-Ticket-Id',
        'X-Freshdesk-Object-Id',
        'X-Freshdesk-Resource-Id',
        'X-Freshdesk-Id',
        'X-Ticket-Id',
    )

    def _get_raw_payload(self):
        """Return the incoming body as text for logging and fallback parsing."""
        return request.httprequest.get_data(cache=True, as_text=True).strip()

    def _get_safe_headers(self):
        """Return a safe subset of headers for troubleshooting."""
        headers = {}
        for header_name in self._SAFE_LOG_HEADERS:
            value = request.httprequest.headers.get(header_name)
            if value:
                headers[header_name] = value
        return headers

    def _extract_ticket_identifier(self):
        """Extract a ticket id from the query string or well-known webhook headers."""
        for key in ('ticket_id', 'ticketId', 'id', 'resource_id', 'resourceId', 'object_id', 'objectId'):
            value = request.httprequest.args.get(key)
            if value not in (None, ''):
                return value

        for header_name in self._WEBHOOK_ID_HEADERS:
            value = request.httprequest.headers.get(header_name)
            if value not in (None, ''):
                return value

        return False

    def _parse_payload(self, raw_payload):
        """Parse the webhook payload using JSON, form data, and querystring fallbacks."""
        try:
            payload = request.get_json_data()
        except Exception:
            payload = None

        if payload not in (None, {}, [], ''):
            return payload

        form_payload = request.httprequest.form.to_dict(flat=True) if request.httprequest.form else {}
        values_payload = request.httprequest.values.to_dict(flat=True) if request.httprequest.values else {}
        if values_payload and not form_payload:
            form_payload = values_payload

        if form_payload:
            if 'payload' in form_payload:
                nested_payload = form_payload.get('payload')
                if nested_payload:
                    try:
                        return json.loads(nested_payload)
                    except Exception:
                        pass
            return form_payload

        if raw_payload:
            try:
                return json.loads(raw_payload)
            except Exception:
                parsed_qs = parse_qs(raw_payload, keep_blank_values=True)
                if parsed_qs:
                    flat_payload = {key: values[-1] for key, values in parsed_qs.items() if values}
                    if 'payload' in flat_payload:
                        try:
                            return json.loads(flat_payload['payload'])
                        except Exception:
                            pass
                    return flat_payload
                return raw_payload
        return payload

    def _log_webhook(self, handler, level, raw_payload, content_type, error_log=False):
        """Write one webhook log entry using the direct webhook context."""
        form_payload = request.httprequest.form.to_dict(flat=True) if request.httprequest.form else {}
        values_payload = request.httprequest.values.to_dict(flat=True) if request.httprequest.values else {}
        handler.env['sh.freshdesk.sync.log'].create_log(
            'helpdesk.ticket',
            False,
            level,
            json.dumps({
                'content_type': content_type,
                'raw_payload': raw_payload,
                'form_payload': form_payload,
                'values_payload': values_payload,
                'headers': self._get_safe_headers(),
            }),
            'freshdesk_to_odoo',
            error_log=error_log,
            operation='create',
        )

    @route(
        '/freshdesk/webhook/<int:company_id>/<string:secret>',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def sh_freshdesk_webhook(self, company_id, secret):

        response_headers = {'Content-Type': 'application/json'}
        company = request.env['res.company'].sudo().browse(company_id).exists()
        expected_secret = str(company.sh_freshdesk_webhook_secret or '').strip() if company else ''
        secret = str(secret or '').strip()

        if (
            not company
            or not company.sh_enable_freshdesk_webhook
            or not expected_secret
            or not hmac.compare_digest(secret, expected_secret)
        ):
            return request.make_json_response(
                {'status': 'error', 'message': 'Invalid or inactive Freshdesk webhook.'},
                headers=response_headers,
                status=404,
            )

        raw_payload = self._get_raw_payload()
        content_type = request.httprequest.headers.get('Content-Type')
        ticket_identifier = self._extract_ticket_identifier()
        handler = request.env['sh.freshdesk.webhook.handler'].sudo().with_context(
            allowed_company_ids=company.ids,
            sh_webhook_direct=True,
            skip_freshdesk_sync=True,
        )
        company = handler.env['res.company'].browse(company.id)

        payload = self._parse_payload(raw_payload)

        if payload in (None, {}, [], ''):
            if ticket_identifier:
                payload = {'id': ticket_identifier}
            else:
                self._log_webhook(
                    handler,
                    'error',
                    raw_payload,
                    content_type,
                    error_log='Freshdesk webhook payload is empty or invalid.',
                )
                return request.make_json_response(
                    {
                        'status': 'error',
                        'message': 'Invalid Freshdesk webhook payload.',
                    },
                    headers=response_headers,
                    status=400,
                )

        try:
            if isinstance(payload, dict) and ticket_identifier and not payload.get('id'):
                payload['id'] = ticket_identifier
            result = handler.process_payload(company, payload)
            return request.make_json_response(result, headers=response_headers, status=200)
        except Exception:
            _logger.exception('Freshdesk webhook processing failed for company %s', company.id)
            self._log_webhook(handler, 'error', raw_payload, content_type, error_log=traceback.format_exc())
            return request.make_json_response(
                {'status': 'error', 'message': 'Freshdesk webhook processing failed.'},
                headers=response_headers,
                status=500,
            )
