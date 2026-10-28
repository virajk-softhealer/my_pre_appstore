# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging
import urllib.parse
from datetime import datetime, timezone

from odoo import _, fields

from .calendly_api import CalendlyApi


_logger = logging.getLogger(__name__)


class CalendlySyncService:
    """Shared idempotent inbound and outbound synchronization service."""

    def __init__(self, env, company):
        self.env = env
        self.company = company
        self.api = CalendlyApi(env, company)
        self._connection_checked = False
        self._connection_available = False

    def _log(self, values):
        values.setdefault('company_id', self.company.id)
        if not values.get('event_type_id') and values.get('calendar_event_id'):
            calendar_event = self.env['calendar.event'].browse(values['calendar_event_id']).exists()
            if calendar_event:
                values['event_type_id'] = calendar_event.sh_calendly_event_type_id.id
        if values.get('event_type', '').startswith('event_type.') and values.get('external_event_id'):
            domain = [
                ('company_id', '=', self.company.id),
                ('external_event_id', '=', values['external_event_id']),
                ('operation', '=', values.get('operation')),
                ('event_type', '=', values.get('event_type')),
            ]
            existing = self.env['sh.calendly.sync.log'].search(
                domain + [('status', '=', 'pending')], limit=1,
            ) or self.env['sh.calendly.sync.log'].search(domain, limit=1)
            if existing:
                if existing.status == 'pending' and values.get('status') != 'pending':
                    existing.write(values)
                    return existing
                if existing.status == 'pending' and values.get('operation') == 'create':
                    if values.get('response'):
                        existing.write({'response': values['response']})
                    return existing
                if self._event_type_log_fingerprint(existing.response) == self._event_type_log_fingerprint(
                    values.get('response'),
                ):
                    return existing
        elif values.get('source') in ('webhook', 'scheduled_poll') and values.get('external_event_id'):
            domain = [
                ('company_id', '=', self.company.id),
                ('external_event_id', '=', values['external_event_id']),
                ('operation', '=', values.get('operation')),
            ]
            if values.get('external_invitee_id'):
                domain.append(('external_invitee_id', '=', values['external_invitee_id']))
            existing = self.env['sh.calendly.sync.log'].search(
                domain + [('status', '=', 'pending')], limit=1,
            ) or self.env['sh.calendly.sync.log'].search(domain, limit=1)
            if existing:
                if existing.status == 'pending' and values.get('status') != 'pending':
                    existing.write(values)
                    return existing
                if values.get('operation') != 'update':
                    return existing
                if self._response_fingerprint(existing.response) == self._response_fingerprint(
                    values.get('response'),
                ):
                    return existing
        elif values.get('source') == 'odoo_action' and values.get('calendar_event_id'):
            existing = self.env['sh.calendly.sync.log'].search([
                ('company_id', '=', self.company.id),
                ('source', '=', 'odoo_action'),
                ('calendar_event_id', '=', values['calendar_event_id']),
                ('operation', '=', values.get('operation')),
            ], limit=1)
            if existing:
                if existing.status == 'pending' and values.get('status') != 'pending':
                    existing.write(values)
                return existing
        return self.env['sh.calendly.sync.log'].create(values)

    def _check_connection(self, operation):
        """Validate live Calendly credentials before a synchronization operation."""
        if self._connection_checked:
            return self._connection_available
        self._connection_checked = True
        if self.api.validate_connection():
            self._connection_available = True
            return True
        _logger.warning(
            'Calendly synchronization skipped: company_id=%s operation=%s credentials_invalid=True',
            self.company.id, operation,
        )
        return False

    @staticmethod
    def _parse_calendly_datetime(value):
        if not value:
            return False
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(
                timezone.utc,
            ).replace(tzinfo=None)
        except (TypeError, ValueError):
            _logger.warning('Calendly timestamp could not be parsed: value_type=%s', type(value).__name__)
            return False

    def sync_event_types(self):
        """Reconcile active Calendly Event Types with the shared Odoo records."""
        if not self._check_connection('event_type_sync'):
            return {'created': 0, 'updated': 0, 'deleted': 0}
        event_types = self.api.list_event_types()
        if self.company.sh_calendly_enable_queue:
            return self._queue_event_type_sync(event_types)
        event_type_model = self.env['sh.calendly.event.type']
        remote_uris = {event_type.get('uri') for event_type in event_types if event_type.get('uri')}
        existing_records = event_type_model.search([])
        deleted_records = existing_records.filtered(lambda record: record.uri not in remote_uris)
        deleted_count = len(deleted_records)
        if deleted_records:
            deleted_records.unlink()

        created_count = 0
        updated_count = 0
        for event_type in event_types:
            uri = event_type.get('uri')
            record = event_type_model.search([('uri', '=', uri)], limit=1) if uri else event_type_model
            if record:
                updated_count += 1
            else:
                created_count += 1
            self._upsert_event_type(event_type)
        return {
            'created': created_count,
            'updated': updated_count,
            'deleted': deleted_count,
        }

    _EVENT_TYPE_SYNC_FIELDS = ('name', 'scheduling_url', 'duration', 'locations', 'pooling_type')

    @classmethod
    def _event_type_values_changed(cls, record, values):
        return any(getattr(record, field) != values[field] for field in cls._EVENT_TYPE_SYNC_FIELDS)

    @staticmethod
    def _event_type_log_fingerprint(response):
        """Return a stable fingerprint for the meaningful Event Type payload fields."""
        if not isinstance(response, dict):
            return False
        resource = response.get('payload') if isinstance(response.get('payload'), dict) else response
        payload = {
            key: resource.get(key)
            for key in (
                'uri', 'name', 'scheduling_url', 'duration', 'locations', 'pooling_type',
                'created_at', 'updated_at',
            )
            if key in resource
        }
        return json.dumps(payload, sort_keys=True, default=str) if payload else False

    @staticmethod
    def _response_fingerprint(response):
        if not isinstance(response, dict):
            return False
        return json.dumps(response, sort_keys=True, default=str)

    def _find_outbound_echo(self, event_id, operation):
        if not event_id or operation not in ('create', 'delete'):
            return self.env['sh.calendly.sync.log']
        return self.env['sh.calendly.sync.log'].search([
            ('company_id', '=', self.company.id),
            ('sync_direction', '=', 'odoo_to_calendly'),
            ('source', '=', 'odoo_action'),
            ('external_event_id', '=', event_id),
            ('operation', '=', operation),
            ('status', 'in', ('pending', 'success')),
        ], limit=1)

    def queue_webhook_payload(self, payload, source='webhook'):
        """Store an inbound Calendly payload for later queue processing."""
        event_name = payload.get('event') or payload.get('event_type')
        resource = payload.get('payload') if isinstance(payload.get('payload'), dict) else {}
        uri = resource.get('uri') if resource else False
        if event_name == 'event_type.updated' and uri and not self.env['sh.calendly.event.type'].search(
            [('uri', '=', uri)], limit=1,
        ):
            event_name = 'event_type.created'
            payload = dict(payload, event=event_name)
        if event_name == 'event_type.created' and uri:
            existing_create = self.env['sh.calendly.sync.log'].search([
                ('company_id', '=', self.company.id),
                ('external_event_id', '=', uri),
                ('event_type', '=', 'event_type.created'),
                ('operation', '=', 'create'),
                ('status', '=', 'pending'),
            ], limit=1)
            if existing_create:
                existing_create.write({'response': payload})
                self.env['sh.calendly.sync.queue'].add_to_queue(existing_create)
                return existing_create
        invitee = resource.get('invitee') if isinstance(resource.get('invitee'), dict) else resource
        scheduled_event_value = resource.get('scheduled_event') or resource.get('event')
        scheduled_event = (
            scheduled_event_value if isinstance(scheduled_event_value, dict) else {}
        )
        scheduled_event_id = (
            scheduled_event.get('uri')
            or scheduled_event_value
            if isinstance(scheduled_event_value, str)
            else scheduled_event.get('uri')
        )
        echo_operation = {'invitee.created': 'create', 'invitee.canceled': 'delete'}.get(event_name)
        echo_log = self._find_outbound_echo(
            scheduled_event_id or resource.get('uri') if resource else False,
            echo_operation,
        )
        if echo_log:
            return echo_log
        log = self._log({
            'source': source,
            'event_type': event_name,
            'operation': {
                'event_type.created': 'create', 'event_type.updated': 'update',
                'event_type.deleted': 'delete', 'invitee.created': 'create',
                'invitee.updated': 'update', 'invitee.canceled': 'delete',
            }.get(event_name, 'create'),
            'sync_direction': 'calendly_to_odoo',
            'external_event_id': scheduled_event_id or resource.get('uri') if resource else False,
            'external_invitee_id': invitee.get('uri') if invitee else False,
            'invitee_email': invitee.get('email') if invitee else False,
            'status': 'pending', 'response': payload,
            'message': _('Calendly synchronization is pending the queue.'),
        })
        self.env['sh.calendly.sync.queue'].add_to_queue(log)
        return log

    def _queue_event_type_sync(self, event_types):
        """Queue Event Type reconciliation without changing Odoo records immediately."""
        unique_event_types = {
            event_type.get('uri'): event_type for event_type in event_types if event_type.get('uri')
        }
        event_type_model = self.env['sh.calendly.event.type']
        existing_records = event_type_model.search([])
        deleted_records = existing_records.filtered(lambda record: record.uri not in unique_event_types)
        for record in deleted_records:
            self.queue_webhook_payload({
                'event': 'event_type.deleted', 'payload': {'uri': record.uri},
            }, source='odoo_action')
        created_count = updated_count = 0
        for event_type in unique_event_types.values():
            record = event_type_model.search([('uri', '=', event_type['uri'])], limit=1)
            if record:
                values = self._event_type_values(event_type)
                if not self._event_type_values_changed(record, values):
                    continue
                operation = 'updated'
                updated_count += 1
            else:
                operation = 'created'
                created_count += 1
            self.queue_webhook_payload({
                'event': 'event_type.%s' % operation, 'payload': event_type,
            }, source='odoo_action')
        return {'created': created_count, 'updated': updated_count, 'deleted': len(deleted_records)}

    def process_queued_log(self, log):
        """Process a queued log without creating a second synchronization log."""
        if log.event_type and log.event_type.startswith('event_type.'):
            payload = log.response if isinstance(log.response, dict) else {}
            resource = payload.get('payload') if isinstance(payload.get('payload'), dict) else payload
            uri = resource.get('uri') if isinstance(resource, dict) else log.external_event_id
            event_name = payload.get('event') or log.event_type
            if event_name == 'event_type.deleted' or log.operation == 'delete':
                self.env['sh.calendly.event.type'].search([('uri', '=', uri)]).unlink()
                log.write({'status': 'success', 'message': _('Event Type deleted from Odoo.')})
                return True
            if not isinstance(resource, dict) or not uri:
                log.write({'status': 'failed', 'message': _('Event Type queue payload is incomplete.')})
                return False
            record = self.env['sh.calendly.event.type'].search([('uri', '=', uri)], limit=1)
            if not record and event_name == 'event_type.updated':
                log.write({'operation': 'create', 'event_type': 'event_type.created'})
            record = self._upsert_event_type(resource)
            log.write({
                'status': 'success', 'event_type_id': record.id,
                'message': _('Event Type synchronized to Odoo from the queue.'),
            })
            return True
        if log.sync_direction == 'calendly_to_odoo' and isinstance(log.response, dict):
            self.process_webhook(log.response, source=log.source)
            return log.status in ('success', 'skipped')
        if log.sync_direction == 'odoo_to_calendly' and log.operation == 'create':
            event = log.calendar_event_id.exists()
            if not event:
                log.write({'status': 'skipped', 'message': _('The Odoo meeting no longer exists.')})
                return True
            self.push_event(event)
            return log.status in ('success', 'skipped')
        self.retry_log(log)
        return log.status in ('success', 'skipped')

    def _upsert_event_type(self, event_type):
        uri = event_type.get('uri')
        if not uri:
            return self.env['sh.calendly.event.type']
        values = self._event_type_values(event_type)
        record = self.env['sh.calendly.event.type'].search([('uri', '=', uri)], limit=1)
        if record:
            record.write(values)
        else:
            record = self.env['sh.calendly.event.type'].create(values)
        return record

    def _event_type_values(self, event_type):
        uri = event_type.get('uri')
        return {
            'name': event_type.get('name') or uri.rsplit('/', 1)[-1],
            'uri': uri,
            'scheduling_url': event_type.get('scheduling_url'),
            'duration': event_type.get('duration') or 0,
            'locations': event_type.get('locations') or False,
            'pooling_type': event_type.get('pooling_type') or False,
            'calendly_updated_at': self._parse_calendly_datetime(event_type.get('updated_at')),
        }

    def process_event_type_webhook(self, payload):
        if not self._check_connection('event_type_webhook'):
            return 'failed'
        event_name = payload.get('event') or payload.get('event_type')
        operation = {
            'event_type.created': 'create',
            'event_type.updated': 'update',
            'event_type.deleted': 'delete',
        }.get(event_name)
        resource = payload.get('payload') or {}
        uri = resource.get('uri') if isinstance(resource, dict) else resource
        event_type_record = self.env['sh.calendly.event.type'].search(
            [('uri', '=', uri)], limit=1,
        ) if uri else self.env['sh.calendly.event.type']
        if event_name == 'event_type.updated' and event_type_record:
            if not isinstance(resource, dict) or not resource.get('name'):
                resource = self.api.get_event_type(uri) or resource
            if not isinstance(resource, dict):
                resource = {}
            resource.setdefault('uri', uri)
            values = self._event_type_values(resource)
            tracked_fields = ('name', 'scheduling_url', 'duration', 'locations', 'pooling_type')
            if all(getattr(event_type_record, field) == values[field] for field in tracked_fields):
                self._upsert_event_type(resource)
                return 'success'
        log = self.env['sh.calendly.sync.log'].search([
            ('company_id', '=', self.company.id),
            ('source', '=', 'webhook'),
            ('external_event_id', '=', uri),
            ('operation', '=', operation),
        ], limit=1) if uri else self.env['sh.calendly.sync.log']
        if not log:
            log = self._log({
                'source': 'webhook',
                'event_type': event_name,
                'operation': operation,
                'sync_direction': 'calendly_to_odoo',
                'external_event_id': uri,
                'status': 'pending',
                'response': payload,
            })
        try:
            event_type_record = False
            if event_name != 'event_type.deleted' and uri:
                event_type_record = event_type_record or self._event_type_record(uri)
                log.write({'event_type_id': event_type_record.id})
            if event_name == 'event_type.deleted':
                if uri:
                    self.env['sh.calendly.event.type'].search([('uri', '=', uri)]).unlink()
                log.write({'status': 'success', 'message': _('Event Type deleted from Odoo.')})
                return 'success'
            if not isinstance(resource, dict) or not uri:
                log.write({'status': 'failed', 'message': _('Event Type webhook payload is incomplete.')})
                return 'failed'
            if not resource.get('name'):
                resource = self.api.get_event_type(uri)
            status = 'success' if self._upsert_event_type(resource) else 'failed'
            event_type_record = self._event_type_record(uri)
            log.write({
                'status': status,
                'event_type_id': event_type_record.id,
                'message': (
                    _('Event Type synchronized to Odoo.')
                    if status == 'success' else _('Event Type synchronization failed.')
                ),
            })
            return status
        except Exception:
            _logger.exception(
                'Calendly Event Type webhook synchronization failed: company_id=%s event_type=%s uri=%s',
                self.company.id, event_name, uri,
            )
            log.write({'status': 'failed', 'message': _('Event Type synchronization failed.')})
            return 'failed'

    def _event_type_record(self, uri):
        if not uri:
            return self.env['sh.calendly.event.type']
        event_type_model = self.env['sh.calendly.event.type']
        record = event_type_model.search([
            ('uri', '=', uri),
        ], limit=1)
        if not record:
            record = event_type_model.create({'name': uri.rsplit('/', 1)[-1], 'uri': uri})
        return record

    def process_webhook(self, payload, source='webhook'):
        event_type = payload.get('event') or payload.get('event_type')
        operation = {
            'invitee.created': 'create',
            'invitee.updated': 'update',
            'invitee.canceled': 'delete',
        }.get(event_type, 'create')
        webhook_payload = payload.get('payload') or {}
        # Calendly's invitee webhook payload is the invitee object itself. Some integrations may instead
        # provide nested invitee/scheduled_event URI values, so support both representations.
        invitee = webhook_payload.get('invitee') or webhook_payload or payload.get('invitee') or {}
        scheduled_event = (
            webhook_payload.get('scheduled_event') or webhook_payload.get('event')
            or payload.get('scheduled_event') or {}
        )
        invitee_uri = invitee if isinstance(invitee, str) else invitee.get('uri') or invitee.get('id')
        event_uri = (
            scheduled_event if isinstance(scheduled_event, str)
            else scheduled_event.get('uri') or scheduled_event.get('id')
        )
        invitee = invitee if isinstance(invitee, dict) else {}
        scheduled_event = scheduled_event if isinstance(scheduled_event, dict) else {}
        invitee_id = invitee.get('uri') or invitee.get('id') or invitee_uri
        event_id = scheduled_event.get('uri') or scheduled_event.get('id') or event_uri
        email = invitee.get('email')
        echo_operation = {
            'invitee.created': 'create',
            'invitee.canceled': 'delete',
        }.get(event_type)
        echo_log = self._find_outbound_echo(event_id, echo_operation)
        if echo_log:
            return echo_log
        if not self._check_connection('calendly_to_odoo_sync'):
            return self._log({
                'source': source,
                'event_type': event_type,
                'operation': operation,
                'sync_direction': 'calendly_to_odoo',
                'invitee_email': email,
                'external_event_id': event_id,
                'external_invitee_id': invitee_id,
                'status': 'failed',
                'message': _('Calendly credential validation failed.'),
                'response': payload,
            })
        existing = self.env['sh.calendly.sync.log'].search([
            ('company_id', '=', self.company.id),
            ('external_event_id', '=', event_id),
            ('external_invitee_id', '=', invitee_id),
            ('status', '=', 'success'),
        ], limit=1) if event_id else False
        if existing and event_type in ('invitee.created', 'invitee.updated'):
            same_payload = self._response_fingerprint(existing.response) == self._response_fingerprint(payload)
            if event_type == 'invitee.created' or same_payload:
                return existing
        log = self._log({
            'source': source,
            'event_type': event_type,
            'operation': operation,
            'sync_direction': 'calendly_to_odoo',
            'invitee_email': email,
            'external_event_id': event_id,
            'external_invitee_id': invitee_id,
            'status': 'pending',
            'response': payload,
        })
        try:
            if event_type == 'invitee.canceled':
                self._cancel_inbound(event_id, log)
            elif event_type in ('invitee.created', 'invitee.updated'):
                if invitee_uri and not invitee.get('email'):
                    invitee = self.api.get_invitee(invitee_uri)
                if event_uri and not scheduled_event.get('start_time'):
                    scheduled_event = self.api.get_scheduled_event(event_uri)
                self._create_inbound(invitee, scheduled_event, log)
            else:
                log.write({'status': 'skipped', 'message': _('Unsupported Calendly event type.')})
        except Exception as error:
            _logger.exception(
                'Calendly synchronization failed: company_id=%s event_id=%s invitee_id=%s',
                self.company.id, event_id, invitee_id,
            )
            log.write({'status': 'failed', 'message': _('Synchronization failed; see the Odoo log for details.')})
        return log

    def _get_partner(self, email, name):
        email = (email or '').strip()
        if not email:
            _logger.error('Calendly invitee email missing: company_id=%s', self.company.id)
            return False, False
        partner = self.env['res.partner'].search([
            ('email', '=ilike', email), '|', ('company_id', '=', False), ('company_id', '=', self.company.id),
        ], limit=1)
        if partner:
            return partner, False
        return self.env['res.partner'].create({
            'name': name or email,
            'email': email,
            'company_id': self.company.id,
        }), True

    def _create_inbound(self, invitee, scheduled_event, log):
        event_uri = scheduled_event.get('uri') or scheduled_event.get('id')
        invitee_uri = invitee.get('uri') or invitee.get('id')
        email = invitee.get('email')
        if not event_uri or not invitee_uri or not email:
            _logger.error(
                'Calendly booking payload incomplete: company_id=%s event_id=%s invitee_id=%s',
                self.company.id, event_uri, invitee_uri,
            )
            log.write({'status': 'failed', 'message': _('Calendly booking payload is incomplete.')})
            return False
        partner, contact_created = self._get_partner(email, invitee.get('name'))
        if not partner:
            log.write({'status': 'failed', 'message': _('Calendly invitee contact could not be resolved.')})
            return False
        partners = partner
        guests_created = False
        for guest in scheduled_event.get('event_guests') or []:
            guest_email = guest.get('email') if isinstance(guest, dict) else guest
            guest_partner, guest_created = self._get_partner(guest_email, guest_email)
            if guest_partner:
                partners |= guest_partner
                guests_created = guests_created or guest_created
        start = self._parse_datetime(scheduled_event.get('start_time'))
        end = self._parse_datetime(scheduled_event.get('end_time'))
        if not start or not end:
            log.write({'status': 'failed', 'message': _('Calendly booking time is invalid or missing.')})
            return False
        location_data = scheduled_event.get('location') or {}
        location = (
            location_data.get('location')
            if location_data.get('type') == 'physical'
            else False
        )
        vals = {
            'name': scheduled_event.get('name') or _('Calendly Meeting'),
            'start': start,
            'stop': end,
            'partner_ids': [(6, 0, partners.ids)],
            'sh_calendly_event_id': event_uri,
            'sh_calendly_event_type': scheduled_event.get('event_type'),
            'sh_calendly_status': 'validated',
            'location': location,
        }
        event_type_record = self._event_type_record(scheduled_event.get('event_type'))
        if event_type_record:
            vals['sh_calendly_event_type_id'] = event_type_record.id
        meeting = self.env['calendar.event'].with_context(skip_calendly_sync=True).search([
            ('sh_calendly_event_id', '=', event_uri),
        ], limit=1)
        if meeting:
            meeting.write(vals)
        else:
            meeting = self.env['calendar.event'].with_context(skip_calendly_sync=True).create(vals)
        lead = self.env['crm.lead']
        if self.company.sh_calendly_create_lead:
            lead = lead.search([
                ('partner_id', '=', partner.id), ('company_id', '=', self.company.id),
            ], order='id desc', limit=1)
            if not lead:
                lead = self.env['crm.lead'].create({
                    'name': vals['name'], 'partner_id': partner.id, 'company_id': self.company.id,
                    'description': _('Created from a Calendly booking.'),
                })
        log.write({
            'status': 'success', 'calendar_event_id': meeting.id, 'lead_id': lead.id,
            'event_type_id': meeting.sh_calendly_event_type_id.id,
            'message': _(
                'Created or updated the Calendar meeting%s; %s contact was used.'
            ) % (
                _(' and CRM lead') if lead else '',
                _('new') if contact_created or guests_created else _('existing'),
            ),
        })

    def _cancel_inbound(self, event_id, log):
        meeting = self.env['calendar.event'].with_context(active_test=False).search([
            ('sh_calendly_event_id', '=', event_id),
        ], limit=1)
        if meeting:
            meeting.with_context(skip_calendly_sync=True).write({
                'active': False,
                'sh_calendly_status': 'canceled',
            })
            meeting.message_post(body=_('The Calendly booking was canceled.'))
        log.write({
            'status': 'success', 'calendar_event_id': meeting.id if meeting else False,
            'message': _('Canceled and archived the linked Calendar meeting; CRM lead was kept active.'),
        })

    def _parse_datetime(self, value):
        if not value:
            _logger.error('Calendly booking time missing: company_id=%s', self.company.id)
            return False
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            _logger.exception('Calendly booking time invalid: company_id=%s value_type=%s', self.company.id, type(value).__name__)
            return False

    def poll_bookings(self, automatic=False):
        _logger.info('Calendly polling started: company_id=%s', self.company.id)
        if automatic and not self.company.sh_calendly_auto_sync_to_odoo:
            return
        if not self._check_connection('calendly_to_odoo_poll'):
            return
        events = self.api.list_events()
        _logger.info(
            'Calendly polling fetched events: company_id=%s event_count=%s',
            self.company.id, len(events),
        )
        processed_events = 0
        processed_invitees = 0
        for event in events:
            event_uri = event.get('uri')
            if not event_uri:
                _logger.warning(
                    'Calendly polling skipped event without URI: company_id=%s', self.company.id,
                )
                continue
            processed_events += 1
            try:
                invitees = []
                page_token = False
                while True:
                    query = {'count': 100}
                    if page_token:
                        query['page_token'] = page_token
                    invitee_path = (
                        f'/scheduled_events/{event_uri.rsplit("/", 1)[-1]}/invitees?'
                        f'{urllib.parse.urlencode(query)}'
                    )
                    invitee_payload = self.api._request(invitee_path) or {}
                    invitees.extend(invitee_payload.get('collection', []))
                    next_page_token = (invitee_payload.get('pagination') or {}).get('next_page_token')
                    if not next_page_token or next_page_token == page_token:
                        break
                    page_token = next_page_token
                _logger.info(
                    'Calendly polling fetched invitees: company_id=%s event_uri=%s invitee_count=%s',
                    self.company.id, event_uri, len(invitees),
                )
                for invitee in invitees:
                    processed_invitees += 1
                    payload = {
                        'event': 'invitee.canceled' if event.get('status') == 'canceled' else 'invitee.created',
                        'payload': {'invitee': invitee, 'scheduled_event': event},
                    }
                    if self.company.sh_calendly_enable_queue:
                        self.queue_webhook_payload(payload, source='scheduled_poll')
                    else:
                        self.process_webhook(payload, source='scheduled_poll')
            except Exception:
                _logger.exception(
                    'Calendly polling failed: company_id=%s event_uri=%s',
                    self.company.id, event_uri,
                )
        self.company.write({'sh_calendly_last_sync_at': fields.Datetime.now()})
        _logger.info(
            'Calendly polling finished: company_id=%s processed_events=%s processed_invitees=%s',
            self.company.id, processed_events, processed_invitees,
        )

    def push_event(self, event):
        if not self._check_connection('odoo_to_calendly_sync'):
            return False
        event_type_record = event.sh_calendly_event_type_id
        event_type_uri = event_type_record.uri or event.sh_calendly_event_type
        if not event_type_uri or not event.start or not event.partner_ids:
            return
        partner_emails = [
            partner.email.strip()
            for partner in event.partner_ids
            if partner.email and partner.email.strip()
        ]
        email = partner_emails[0] if partner_emails else False
        event_guests = partner_emails[1:]
        primary_partner = next(
            (partner for partner in event.partner_ids if partner.email and partner.email.strip()),
            False,
        )
        invitee_timezone = (
            (primary_partner.tz if primary_partner else False)
            or event.user_id.tz
            or self.env.user.tz
            or 'UTC'
        )
        if not email:
            return
        operation = 'delete' if not event.active else (
            'update' if event.sh_calendly_event_id else 'create'
        )
        event_type_data = {
            'locations': event_type_record.locations,
            'pooling_type': event_type_record.pooling_type,
        }
        if not event_type_data['locations']:
            event_type_data = self.api.get_event_type(event_type_uri)
            if event_type_data:
                event_type_record.write({
                    'locations': event_type_data.get('locations') or False,
                    'pooling_type': event_type_data.get('pooling_type') or False,
                })
        locations = event_type_data.get('locations') or []
        invitee_location = False
        if event_type_data.get('pooling_type') != 'round_robin' and len(locations) == 1:
            invitee_location = locations[0]
        calendly_response = False
        try:
            if not event.active and event.sh_calendly_event_id:
                calendly_response = self.api.cancel_event(event.sh_calendly_event_id)
                if calendly_response is None:
                    _logger.error(
                        'Calendly cancellation failed: company_id=%s event_id=%s',
                        self.company.id, event.sh_calendly_event_id,
                    )
                    self._log({
                        'source': 'odoo_action', 'event_type': 'calendar.event', 'operation': 'delete',
                        'sync_direction': 'odoo_to_calendly',
                        'invitee_email': email, 'external_event_id': event.sh_calendly_event_id,
                        'status': 'failed', 'calendar_event_id': event.id,
                        'response': calendly_response,
                        'message': _('Cancellation failed; see the Odoo log for details.'),
                    })
                    return
                event.with_context(skip_calendly_sync=True).write({'sh_calendly_status': 'canceled'})
            elif not event.sh_calendly_event_id:
                calendly_response, api_error = self.api.create_invitee(
                    event_type_uri,
                    email,
                    fields.Datetime.to_datetime(event.start),
                    timezone=invitee_timezone,
                    event_guests=event_guests,
                    invitee_name=primary_partner.name or email,
                    location=invitee_location,
                )
                resource = (calendly_response or {}).get('resource') if isinstance(calendly_response, dict) else False
                if not resource or not resource.get('event'):
                    _logger.error(
                        'Calendly invitee creation failed: company_id=%s event_id=%s',
                        self.company.id, event.id,
                    )
                    self._log({
                        'source': 'odoo_action', 'event_type': 'calendar.event', 'operation': 'create',
                        'sync_direction': 'odoo_to_calendly',
                        'invitee_email': email, 'status': 'failed', 'calendar_event_id': event.id,
                        'response': calendly_response,
                        'api_error': api_error,
                        'message': api_error or _('Calendly booking creation failed.'),
                    })
                    return
                event.with_context(skip_calendly_sync=True).write({
                    'sh_calendly_event_id': resource.get('event'),
                    'sh_calendly_status': 'validated',
                })
            if operation != 'update':
                self._log({
                    'source': 'odoo_action',
                    'event_type': 'calendar.event',
                    'operation': operation,
                    'sync_direction': 'odoo_to_calendly',
                    'invitee_email': email, 'external_event_id': event.sh_calendly_event_id,
                    'status': 'success',
                    'calendar_event_id': event.id, 'response': calendly_response,
                    'message': _('Odoo change synchronized to Calendly.'),
                })
        except Exception:
            _logger.exception(
                'Odoo-to-Calendly synchronization failed: company_id=%s event_id=%s',
                self.company.id, event.id,
            )
            if operation != 'update':
                self._log({
                    'source': 'odoo_action',
                    'event_type': 'calendar.event',
                    'operation': operation,
                    'sync_direction': 'odoo_to_calendly',
                    'invitee_email': email, 'external_event_id': event.sh_calendly_event_id,
                    'status': 'failed',
                    'calendar_event_id': event.id,
                    'response': calendly_response,
                    'message': _('Synchronization failed; see the Odoo log for details.'),
                })

    def queue_pending_event(self, event):
        operation = 'delete' if not event.active else (
            'create' if not event.sh_calendly_event_id else 'update'
        )
        if operation == 'update':
            return False
        event_type_record = event.sh_calendly_event_type_id
        event_type_uri = event_type_record.uri or event.sh_calendly_event_type
        if not event_type_uri or (operation == 'create' and (not event.start or not event.partner_ids)):
            return False
        partner = next((partner for partner in event.partner_ids if partner.email), False)
        if operation == 'create' and not partner:
            return False
        log = self._log({
            'source': 'odoo_action',
            'event_type': 'calendar.event',
            'operation': operation,
            'sync_direction': 'odoo_to_calendly',
            'invitee_email': partner.email if partner else False,
            'external_event_id': event.sh_calendly_event_id,
            'status': 'pending',
            'calendar_event_id': event.id,
            'message': _('Calendly synchronization is pending the manual Odoo to Calendly sync.'),
        })
        if self.company.sh_calendly_enable_queue:
            self.env['sh.calendly.sync.queue'].add_to_queue(log)
        return True

    def push_odoo_changes(self, excluded_event_ids=None):
        excluded_event_ids = excluded_event_ids or set()
        events = self.env['calendar.event'].search([
            '|',
            ('sh_calendly_event_type_id', '!=', False),
            ('sh_calendly_event_type', '!=', False),
        ], limit=100)
        for event in events:
            event_company = event.user_id.company_id or self.env.company
            if event_company != self.company or event.id in excluded_event_ids:
                continue
            if self.company.sh_calendly_enable_queue:
                self.queue_pending_event(event)
            else:
                self.push_event(event)

    def process_pending_odoo_logs(self):
        handled_event_ids = set()
        pending_create_logs = self.env['sh.calendly.sync.log'].search([
            ('company_id', '=', self.company.id),
            ('source', '=', 'odoo_action'),
            ('sync_direction', '=', 'odoo_to_calendly'),
            ('operation', '=', 'create'),
            ('status', '=', 'pending'),
            ('calendar_event_id', '!=', False),
        ])
        for log in pending_create_logs:
            event = self.env['calendar.event'].browse(log.calendar_event_id.id).exists()
            if event:
                handled_event_ids.add(event.id)
                if self.company.sh_calendly_enable_queue:
                    self.env['sh.calendly.sync.queue'].add_to_queue(log)
                else:
                    self.push_event(event)
            else:
                log.write({'status': 'skipped', 'message': _('The Odoo meeting no longer exists.')})

        logs = self.env['sh.calendly.sync.log'].search([
            ('company_id', '=', self.company.id),
            ('source', '=', 'odoo_action'),
            ('sync_direction', '=', 'odoo_to_calendly'),
            ('operation', '=', 'delete'),
            ('status', '=', 'pending'),
            ('external_event_id', '!=', False),
        ])
        for log in logs:
            if self.company.sh_calendly_enable_queue:
                self.env['sh.calendly.sync.queue'].add_to_queue(log)
            else:
                self.retry_log(log)
        return handled_event_ids

    def process_pending_delete_logs(self):
        """Keep the existing helper name for callers outside the settings action."""
        return self.process_pending_odoo_logs()

    def retry_log(self, log):
        if (
            log.sync_direction == 'odoo_to_calendly'
            and log.operation == 'delete'
            and log.external_event_id
        ):
            try:
                if not self._check_connection('odoo_to_calendly_delete_retry'):
                    log.write({
                        'status': 'failed',
                        'message': _('Calendly connection is unavailable; retry again later.'),
                    })
                    return log
                response = self.api.cancel_event(log.external_event_id)
                log.write({
                    'status': 'success' if response is not None else 'failed',
                    'response': response,
                    'message': (
                        _('Calendly meeting canceled during retry.')
                        if response is not None else
                        _('Calendly meeting cancellation failed during retry.')
                    ),
                })
            except Exception:
                _logger.exception(
                    'Odoo-to-Calendly delete retry failed: company_id=%s log_id=%s',
                    self.company.id, log.id,
                )
                log.write({
                    'status': 'failed',
                    'message': _('Calendly meeting cancellation failed during retry.'),
                })
            return log
        if log.sync_direction == 'calendly_to_odoo' and log.operation in ('create', 'delete'):
            event_type = 'invitee.canceled' if log.operation == 'delete' else 'invitee.created'
            self.process_webhook({
                'event': event_type,
                'payload': {
                    'invitee': {'uri': log.external_invitee_id, 'email': log.invitee_email},
                    'scheduled_event': {'uri': log.external_event_id},
                },
            })
