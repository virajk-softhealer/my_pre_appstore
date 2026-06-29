# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging
import pprint
import re
from urllib.parse import parse_qs

from werkzeug.exceptions import BadRequest, Unauthorized

from odoo import http
from odoo.http import request
from odoo.tools import consteq

_logger = logging.getLogger(__name__)


class ShActiveCampaignWebhookController(http.Controller):
    """Public webhook receiver for ActiveCampaign real-time events."""

    _webhook_url = '/sh_active_campaign/webhook/<int:company_id>/<string:token>'

    @http.route(
        [_webhook_url, '/sh_active_campaign/webhook/<int:company_id>'],
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False,
        save_session=False,
    )
    def sh_active_campaign_webhook(self, company_id, token=None, **kwargs):
        """Receive an ActiveCampaign webhook and dispatch it to the dashboard."""
        company = request.env['res.company'].sudo().browse(company_id).exists()
        if not company or not company.sh_ac_configuration or not company.sh_ac_enable_webhook_processing:
            return request.not_found()

        token = token or request.params.get('token') or kwargs.get('token')
        self._verify_webhook_token(company, token)

        try:
            payload = self._parse_webhook_payload()
        except Exception as exc:
            _logger.warning('Invalid ActiveCampaign webhook payload for company %s: %s', company.id, exc)
            raise BadRequest() from exc

        event_name = self._get_event_name(payload)
        _logger.info(
            'Received ActiveCampaign webhook for company %s, event %s:\n%s',
            company.id,
            event_name or 'unknown',
            pprint.pformat(payload),
        )

        dashboard = request.env['sh.active.campaign.dashboard'].sudo().search(
            [('sh_company_id', '=', company.id)],
            limit=1,
        )
        if not dashboard:
            # Webhooks must work even before the dashboard is manually opened.
            dashboard = request.env['sh.active.campaign.dashboard'].sudo().create({
                'sh_company_id': company.id,
            })

        dashboard.with_company(company).sudo()._process_webhook_event(event_name, payload)
        return request.make_response('OK', headers=[('Content-Type', 'text/plain; charset=utf-8')])

    @staticmethod
    def _split_form_key(key):
        """Split bracket-notation keys like contact[fields][*] into path parts."""
        parts = [part for part in re.split(r'\[|\]', key) if part]
        return parts or [key]

    def _assign_nested_value(self, container, path, value):
        """Assign a value into a nested dict path created from bracket notation."""
        current = container
        for part in path[:-1]:
            node = current.get(part)
            if not isinstance(node, dict):
                node = {}
                current[part] = node
            current = node

        leaf = path[-1]
        existing = current.get(leaf)
        if existing is None:
            current[leaf] = value
            return
        if isinstance(existing, list):
            if isinstance(value, list):
                existing.extend(item for item in value if item not in existing)
            elif value not in existing:
                existing.append(value)
            return
        if existing != value:
            current[leaf] = [existing] + (value if isinstance(value, list) else [value])

    def _normalize_payload_mapping(self, payload):
        """Normalize payload values and expand bracket-notation keys into nested dicts."""
        normalized = {}
        for key, value in (payload or {}).items():
            if isinstance(value, list) and len(value) == 1:
                value = value[0]
            if '[' in key and ']' in key:
                self._assign_nested_value(normalized, self._split_form_key(key), value)
            else:
                normalized[key] = value
        return normalized

    def _parse_webhook_payload(self):
        """Parse webhook data from JSON, form-encoded data, or raw querystring payloads."""
        raw_payload = request.httprequest.get_data(cache=True, as_text=True) or ''

        try:
            payload = request.get_json_data()
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload:
            return self._normalize_payload_mapping(payload)

        form_payload = request.httprequest.form.to_dict(flat=False) if request.httprequest.form else {}
        if form_payload:
            return self._normalize_payload_mapping(form_payload)

        values_payload = request.httprequest.values.to_dict(flat=False) if request.httprequest.values else {}
        if values_payload:
            return self._normalize_payload_mapping(values_payload)

        if raw_payload:
            try:
                raw_json = json.loads(raw_payload)
            except Exception:
                raw_json = None
            if isinstance(raw_json, dict):
                return self._normalize_payload_mapping(raw_json)

            parsed_qs = parse_qs(raw_payload, keep_blank_values=True)
            if parsed_qs:
                return self._normalize_payload_mapping(parsed_qs)

        return {}

    @staticmethod
    def _get_event_name(payload):
        """Extract the webhook event name from the incoming payload."""
        for key in ('event', 'eventType', 'event_type', 'event_name', 'type'):
            value = payload.get(key)
            if value:
                return str(value).strip().lower()
        return ''

    @staticmethod
    def _verify_webhook_token(company, received_token):
        """Validate the per-company webhook token before processing."""
        if not received_token:
            _logger.warning('Received ActiveCampaign webhook without token for company %s.', company.id)
            raise Unauthorized()
        expected_token = company.sh_ac_access_token or ''
        if not expected_token or not consteq(str(expected_token), str(received_token)):
            _logger.warning('Received ActiveCampaign webhook with invalid token for company %s.', company.id)
            raise Unauthorized()
