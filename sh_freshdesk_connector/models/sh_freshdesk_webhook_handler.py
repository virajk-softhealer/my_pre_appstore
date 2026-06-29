# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json

from odoo import _, models


class SHFreshdeskWebhookHandler(models.AbstractModel):
    _name = 'sh.freshdesk.webhook.handler'
    _description = 'Freshdesk Webhook Handler'

    def _fetch_ticket_from_freshdesk(self, ticket_id):
        """Fetch the latest Freshdesk ticket payload when only the ID is known."""
        if not ticket_id:
            return {}

        ticket_model = self.env['helpdesk.ticket']
        try:
            response = ticket_model._freshdesk_request(
                'GET',
                '/api/v2/tickets/%s' % ticket_id,
                params={'include': 'description,requester'},
                timeout=20,
            )
        except Exception:
            return {}

        if response.status_code != 200:
            return {}

        try:
            data = response.json()
        except Exception:
            return {}

        return data if isinstance(data, dict) else {}

    def _fetch_contact_from_freshdesk(self, contact_id):
        """Fetch the Freshdesk requester/contact payload when available."""
        if not contact_id:
            return {}

        ticket_model = self.env['helpdesk.ticket']
        try:
            response = ticket_model._freshdesk_request(
                'GET',
                '/api/v2/contacts/%s' % contact_id,
                timeout=20,
            )
        except Exception:
            return {}

        if response.status_code != 200:
            return {}

        try:
            data = response.json()
        except Exception:
            return {}

        return data if isinstance(data, dict) else {}

    def _fetch_company_from_freshdesk(self, company_id):
        """Fetch the Freshdesk company payload when the requester belongs to a company."""
        if not company_id:
            return {}

        ticket_model = self.env['helpdesk.ticket']
        try:
            response = ticket_model._freshdesk_request(
                'GET',
                '/api/v2/companies/%s' % company_id,
                timeout=20,
            )
        except Exception:
            return {}

        if response.status_code != 200:
            return {}

        try:
            data = response.json()
        except Exception:
            return {}

        return data if isinstance(data, dict) else {}

    def _search_contact_ids_by_name(self, contact_name):
        """Resolve candidate contact IDs from a contact name using Freshdesk autocomplete."""
        contact_name = (contact_name or '').strip()
        if not contact_name:
            return []

        ticket_model = self.env['helpdesk.ticket']
        try:
            response = ticket_model._freshdesk_request(
                'GET',
                '/api/v2/contacts/autocomplete',
                params={'term': contact_name},
                timeout=20,
            )
        except Exception:
            return []

        if response.status_code != 200:
            return []

        try:
            data = response.json()
        except Exception:
            return []

        if not isinstance(data, list):
            return []

        ids = []
        for item in data:
            if not isinstance(item, dict):
                continue
            item_id = item.get('id')
            item_name = (item.get('name') or '').strip()
            if item_id not in (False, None, ''):
                if item_name.lower() == contact_name.lower():
                    return [item_id]
                ids.append(item_id)
        return ids

    def _fetch_contact_from_name(self, contact_name):
        """Fetch full contact data by looking up the contact name first."""
        for contact_id in self._search_contact_ids_by_name(contact_name):
            contact_data = self._fetch_contact_from_freshdesk(contact_id)
            if contact_data:
                return contact_data
        return {}

    def _hydrate_requester_contact(self, data, requester_id):
        """Attach the full requester/contact payload to the webhook data."""
        if not requester_id:
            return data

        hydrated = dict(data or {})
        contact_data = self._fetch_contact_from_freshdesk(requester_id)
        if not contact_data:
            return hydrated

        requester = hydrated.get('requester') if isinstance(hydrated.get('requester'), dict) else {}
        requester = dict(requester)
        requester.update(contact_data)

        hydrated['requester'] = requester
        hydrated.setdefault('requester_id', requester_id)
        hydrated.setdefault('requester_email', contact_data.get('email'))
        hydrated.setdefault('requester_name', contact_data.get('name'))
        hydrated.setdefault('requester_phone', contact_data.get('phone') or contact_data.get('mobile'))
        hydrated.setdefault('email', contact_data.get('email'))
        hydrated.setdefault('name', contact_data.get('name'))
        hydrated.setdefault('phone', contact_data.get('phone'))
        hydrated.setdefault('company_name', contact_data.get('company_name'))
        hydrated.setdefault('address', contact_data.get('address'))
        hydrated.setdefault('city', contact_data.get('city'))
        hydrated.setdefault('zip', contact_data.get('zip') or contact_data.get('postal_code'))
        hydrated.setdefault('website', contact_data.get('website'))
        hydrated.setdefault('description', contact_data.get('description'))

        company_id = contact_data.get('company_id')
        if company_id:
            company_data = self._fetch_company_from_freshdesk(company_id)
            if company_data:
                hydrated.setdefault('company_id', company_id)
                hydrated.setdefault('company_name', company_data.get('name'))
                hydrated.setdefault('company', company_data)
                hydrated.setdefault('company_note', company_data.get('note'))
                hydrated.setdefault('company_domain', company_data.get('domains'))
                hydrated.setdefault('company_description', company_data.get('description'))

        return hydrated

    def _merge_ticket_and_requester(self, data, ticket_id):
        """Fetch the ticket and embedded requester/contact data once and merge it."""
        if not ticket_id:
            return data

        merged = dict(data or {})
        remote_ticket = self._fetch_ticket_from_freshdesk(ticket_id)
        if remote_ticket:
            # Prefer the freshly fetched Freshdesk ticket as the current source of truth.
            # Some webhook payloads carry stale values for fields like priority/status.
            merged = dict(merged)
            merged.update(remote_ticket)
            if not merged.get('id'):
                merged['id'] = ticket_id

        requester = merged.get('requester') if isinstance(merged.get('requester'), dict) else {}
        requester_id = merged.get('requester_id') or requester.get('id')
        if requester_id:
            merged = self._hydrate_requester_contact(merged, requester_id)
            merged['requester_id'] = requester_id

        return merged

    def _sync_requester_from_ticket_payload(self, company, data):
        """Sync the requester/contact before ticket creation or update."""
        requester = data.get('requester') if isinstance(data.get('requester'), dict) else {}
        requester_id = (
            data.get('requester_id')
            or requester.get('id')
            or data.get('ticket_requester_id')
            or data.get('ticket_contact_id')
            or data.get('contact_id')
        )
        requester_name = (
            requester.get('name')
            or data.get('requester_name')
            or data.get('ticket_requester_name')
            or data.get('ticket_contact_name')
            or data.get('name')
            or data.get('contact_name')
        )
        if requester_id and (not requester or not requester.get('email')):
            requester_contact = self._fetch_contact_from_freshdesk(requester_id)
            if requester_contact:
                requester = dict(requester or {})
                requester.update(requester_contact)
                data['requester'] = requester

        if not requester_id:
            requester_contact = self._fetch_contact_from_name(requester_name)
            if requester_contact:
                requester_id = requester_contact.get('id')
                if requester_id:
                    data['requester_id'] = requester_id
                    requester = dict(requester or {})
                    requester.update(requester_contact)
                    data['requester'] = requester
                    data['requester_name'] = requester_contact.get('name') or requester_name
                    data['requester_email'] = requester_contact.get('email') or data.get('requester_email')
                    data['requester_phone'] = requester_contact.get('phone') or requester_contact.get('mobile') or data.get('requester_phone')
            if not requester_id:
                return False

        requester_payload = dict(requester or {})
        requester_payload['id'] = requester_payload.get('id') or requester_id
        requester_payload.setdefault('name', data.get('requester_name') or requester_name)
        requester_payload.setdefault('email', (
            data.get('requester_email')
            or data.get('ticket_requester_email')
            or data.get('ticket_contact_email')
            or data.get('email')
            or data.get('contact_email')
        ))
        requester_payload.setdefault('phone', (
            data.get('requester_phone')
            or data.get('ticket_requester_phone')
            or data.get('ticket_contact_phone')
            or data.get('phone')
            or data.get('contact_phone')
        ))

        if not any(requester_payload.get(key) for key in ('name', 'email', 'phone')):
            return False

        partner = self.env['res.partner'].with_company(company).with_context(skip_freshdesk_sync=True).process_freshdesk_webhook(
            requester_payload,
            'update',
            resource_type='contact',
        )
        return partner or False


    def _coerce_payload(self, payload):
        """Return the first JSON object found in the webhook payload."""
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return next((item for item in payload if isinstance(item, dict)), {})
        if not isinstance(payload, str) or not payload.strip():
            return {}

        try:
            return self._coerce_payload(json.loads(payload))
        except Exception:
            return {}

    def _unwrap_webhook_payload(self, payload):
        """Return the first nested ticket wrapper found in the payload."""
        payload = self._coerce_payload(payload)
        if not isinstance(payload, dict):
            return {}

        current = payload
        for key in ('payload', 'freshdesk_webhook', 'ticket', 'data', 'object'):
            value = current.get(key)
            if isinstance(value, dict):
                current = value
        return current

    def _extract_ticket_data(self, payload):
        """Extract the ticket node from known Freshdesk wrappers."""
        payload = self._unwrap_webhook_payload(payload)
        if not payload:
            return {}
        return payload

    def _get_payload_value(self, payload, field_names):
        """Return the first non-empty value from a list of field names."""
        for field_name in field_names:
            value = payload.get(field_name)
            if value not in (False, None, ''):
                return value
        return False

    def _extract_ticket_id(self, payload):
        """Return the best ticket identifier from the raw or extracted payload."""
        candidates = ['id', 'ticket_id', 'ticketId', 'resource_id', 'object_id', 'freshdesk_ticket_id']

        for source in (self._extract_ticket_data(payload), self._unwrap_webhook_payload(payload), self._coerce_payload(payload)):
            if not isinstance(source, dict):
                continue
            for field_name in candidates:
                value = source.get(field_name)
                if value not in (False, None, ''):
                    return value
        return False

    def _extract_action(self, payload):
        """Normalize Freshdesk event names to create, update, or delete."""
        action = self._get_payload_value(payload, ['action', 'event_action'])
        if not action:
            event = self._get_payload_value(payload, ['event', 'event_type', 'type'])
            if event:
                action = str(event).replace(':', '.').split('.')[-1]

        action = str(action or '').lower()
        if action in ('created', 'new'):
            return 'create'
        if action in ('updated', 'modified'):
            return 'update'
        if action in ('deleted', 'removed'):
            return 'delete'
        return action

    def _infer_resource_type(self, payload):
        """Infer whether the payload is ticket, company, or contact shaped."""
        data = self._extract_ticket_data(payload)
        if not isinstance(data, dict):
            return 'ticket'

        ticket_keys = {
            'subject',
            'description',
            'description_text',
            'status',
            'priority',
            'tags',
            'ticket_subject',
            'ticket_description',
            'ticket_status',
            'ticket_priority',
            'ticket_tags',
        }
        company_keys = {
            'company_id',
            'company_name',
            'company',
            'domains',
            'org_company_id',
            'note',
        }
        contact_keys = {
            'requester_id',
            'contact_id',
            'email',
            'phone',
            'mobile',
            'requester_email',
            'requester_phone',
            'requester_name',
            'contact_name',
        }

        has_ticket = any(data.get(key) not in (False, None, '') for key in ticket_keys)
        has_company = any(data.get(key) not in (False, None, '') for key in company_keys)
        has_contact = any(data.get(key) not in (False, None, '') for key in contact_keys)

        if has_ticket and not has_company and not has_contact:
            return 'ticket'
        if has_company and not has_ticket:
            return 'company'
        if has_contact and not has_ticket:
            return 'contact'
        if has_company and has_contact and not has_ticket:
            return 'company'
        return 'ticket'

    def _normalize_payload(self, payload):
        """Keep a compact compatibility wrapper for queue and sync helpers."""
        payload = self._coerce_payload(payload)
        data = self._extract_ticket_data(payload)
        action = self._extract_action(payload)
        ticket_id = self._extract_ticket_id(payload)
        resource_type = self._infer_resource_type(payload)

        if ticket_id and not data.get('id'):
            data = dict(data)
            data['id'] = ticket_id
        return resource_type, action, data

    def _is_event_enabled(self, company, action):
        field_name = 'sh_fd_webhook_ticket_%s' % action
        return bool(getattr(company, field_name, False))

    def _create_skip_log(self, action, payload, reason):
        self.env['sh.freshdesk.sync.log'].create_log(
            'helpdesk.ticket',
            False,
            'success',
            json.dumps({'payload': payload, 'result': 'skipped', 'reason': reason}),
            'freshdesk_to_odoo',
            operation=action if action in ('create', 'update', 'delete') else False,
        )

    def process_payload(self, company, payload):
        payload = self._coerce_payload(payload)
        data = self._extract_ticket_data(payload)
        action = self._extract_action(payload)
        ticket_id = self._extract_ticket_id(payload)
        existing_ticket = False

        if ticket_id:
            existing_ticket = self.env['helpdesk.ticket'].search([
                ('sh_freshdesk_id', '=', str(ticket_id)),
            ], limit=1)

        if ticket_id:
            action = 'update' if existing_ticket else 'create'
        elif action not in ('create', 'update'):
            reason = _('Unsupported Freshdesk ticket webhook event: %s') % (action or 'unknown')
            self._create_skip_log(action, payload, reason)
            return {'status': 'skipped', 'message': reason}

        if ticket_id and not data.get('id'):
            data = dict(data)
            data['id'] = ticket_id

        if ticket_id:
            data = self._merge_ticket_and_requester(data, ticket_id)

        if not data.get('id'):
            reason = _('Freshdesk ticket webhook payload does not contain a ticket ID.')
            self._create_skip_log(action, payload, reason)
            return {'status': 'skipped', 'message': reason}

        requester_partner = self._sync_requester_from_ticket_payload(company, data)

        if not self._is_event_enabled(company, action):
            reason = _('Freshdesk webhook event is disabled in settings.')
            self._create_skip_log(action, payload, reason)
            return {'status': 'skipped', 'message': reason}

        ticket_model = self.env['helpdesk.ticket'].with_company(company)
        if requester_partner:
            ticket_model = ticket_model.with_context(sh_webhook_partner_id=requester_partner.id)
        record = ticket_model.process_freshdesk_webhook(data, action)

        return {
            'status': 'success',
            'model': 'helpdesk.ticket',
            'record_id': record.id if record else False,
            'action': action,
            'resource_type': 'ticket',
        }
