# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging
import traceback

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class HelpdeskTicket(models.Model):
    _name = 'helpdesk.ticket'
    _inherit = ['helpdesk.ticket', 'sh.freshdesk.api']

    sh_freshdesk_id = fields.Char(string='Freshdesk Ticket ID', copy=False, index=True)
    sh_freshdesk_status = fields.Selection([
        ('2', 'Open'),
        ('3', 'Pending'),
        ('4', 'Resolved'),
        ('5', 'Closed'),
        ('6', 'Waiting on Customer'),
        ('7', 'Waiting on Third Party'),
        ('8', 'Pending (Custom)'),
    ], string='Freshdesk Status', readonly=True,tracking=True,default='2')
    sh_freshdesk_priority = fields.Selection([
        ('1', 'Low'),
        ('2', 'Medium'),
        ('3', 'High'),
        ('4', 'Urgent'),
    ], string='Freshdesk Priority', readonly=True,tracking=True,default='1')
    sh_last_synced_from_fd = fields.Datetime(string='Last Synced (FD)', readonly=True, copy=False)
    sh_freshdesk_webhook_payload = fields.Text(
        string='Freshdesk Webhook Payload',
        readonly=True,
        copy=False,
    )

    def _get_webhook_helpdesk_team(self, company):
        """Return the configured webhook team or the first available team."""
        team = company.sh_freshdesk_webhook_team_id
        if team:
            return team

        team_model = self.env['helpdesk.team'].sudo()
        domain = []
        if 'company_id' in team_model._fields:
            domain = ['|', ('company_id', '=', False), ('company_id', '=', company.id)]

        team = team_model.search(domain, limit=1)
        if not team:
            team = team_model.search([], limit=1)
        if not team:
            raise ValidationError(_('Please configure at least one Helpdesk Team for Freshdesk webhook tickets.'))
        return team

    def _is_webhook_company_like_contact(self, fd_contact):
        """Return True when a requester/contact payload looks like a company."""
        if not isinstance(fd_contact, dict):
            return False

        if fd_contact.get('is_company') is True:
            return True

        company_name = (fd_contact.get('company_name') or '').strip()
        email = (fd_contact.get('email') or '').strip()
        phone = (fd_contact.get('phone') or fd_contact.get('mobile') or '').strip()
        company_markers = any(
            fd_contact.get(key) not in (False, None, '')
            for key in ('company_name', 'company')
        )
        person_markers = bool(email or phone)
        if company_markers and not person_markers:
            return True
        return bool(company_name and not person_markers and fd_contact.get('name', '').strip() == company_name)


    def _is_ticket_like_webhook_payload(self, fd_data):
        """Return True when the payload clearly looks like a ticket payload."""
        if not isinstance(fd_data, dict):
            return False

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
        contact_keys = {'email', 'phone', 'mobile', 'requester_email', 'requester_phone', 'requester_name', 'requester'}

        has_ticket_marker = any(fd_data.get(key) not in (False, None, '') for key in ticket_keys)
        has_contact_marker = any(fd_data.get(key) not in (False, None, '') for key in contact_keys)
        return has_ticket_marker and not has_contact_marker

    def _get_webhook_requester_id(self, fd_data):
        """Return a requester/contact ID only when it is clearly not a ticket ID."""
        requester = fd_data.get('requester') if isinstance(fd_data.get('requester'), dict) else {}
        requester_id = (
            requester.get('id')
            or fd_data.get('requester_id')
            or fd_data.get('ticket_requester_id')
            or fd_data.get('ticket_contact_id')
            or fd_data.get('contact_id')
        )
        if requester_id in (False, None, ''):
            return False

        ticket_id = fd_data.get('id') or fd_data.get('ticket_id')
        if ticket_id and str(requester_id) == str(ticket_id) and self._is_ticket_like_webhook_payload(fd_data):
            return False
        return str(requester_id)

    def _get_webhook_partner_values(self, fd_data):
        """Build partner values from the webhook payload only."""
        requester = fd_data.get('requester') if isinstance(fd_data.get('requester'), dict) else {}
        requester_id = self._get_webhook_requester_id(fd_data)
        candidate_email = (
            fd_data.get('requester_email')
            or requester.get('email')
            or fd_data.get('email')
            or fd_data.get('from_email')
            or fd_data.get('reply_email')
            or fd_data.get('ticket_contact_email')
            or fd_data.get('ticket_requester_email')
            or fd_data.get('contact_email')
        )
        candidate_name = (
            fd_data.get('requester_name')
            or requester.get('name')
            or fd_data.get('name')
            or fd_data.get('contact_name')
            or fd_data.get('ticket_contact_name')
            or fd_data.get('ticket_requester_name')
            or fd_data.get('from_name')
            or fd_data.get('created_by')
        )
        candidate_phone = (
            fd_data.get('phone')
            or requester.get('phone')
            or fd_data.get('requester_phone')
            or fd_data.get('ticket_requester_phone')
            or fd_data.get('ticket_contact_phone')
            or fd_data.get('contact_phone')
        )

        return {
            'requester_id': requester_id or False,
            'email': candidate_email and candidate_email.strip() or False,
            'name': candidate_name and candidate_name.strip() or False,
            'phone': candidate_phone and str(candidate_phone).strip() or False,
        }

    def _get_webhook_contact_values(self, fd_contact, requester_id=False):
        """Map Freshdesk contact data into Odoo partner values."""
        if not isinstance(fd_contact, dict):
            return {}

        address = fd_contact.get('address') or {}
        country = fd_contact.get('country') or {}
        state = fd_contact.get('state') or {}
        name = fd_contact.get('name') or fd_contact.get('email') or _('Freshdesk Contact')
        vals = {
            'name': name,
            'email': fd_contact.get('email') or '',
            'phone': fd_contact.get('phone') or '',
            'comment': fd_contact.get('description') or '',
            'street': address if isinstance(address, str) else (address.get('line1') or address.get('street') or ''),
            'street2': address.get('line2') if isinstance(address, dict) else '',
            'city': fd_contact.get('city') or (address.get('city') if isinstance(address, dict) else '') or '',
            'zip': fd_contact.get('zip') or fd_contact.get('postal_code') or (address.get('zip') if isinstance(address, dict) else '') or '',
            'website': fd_contact.get('website') or '',
            'is_company': False,
            'sh_last_synced_from_fd': fields.Datetime.now(),
        }
        if requester_id:
            vals['sh_freshdesk_id'] = str(requester_id)
        if 'company_name' in self.env['res.partner']._fields and fd_contact.get('company_name'):
            vals['company_name'] = fd_contact.get('company_name')
        # Freshdesk language/time zone are intentionally not mapped here.
        # Odoo rejects invalid locale values such as "en", which can break webhook sync.
        # if 'lang' in self.env['res.partner']._fields and fd_contact.get('language'):
        #     vals['lang'] = fd_contact.get('language')
        # if 'tz' in self.env['res.partner']._fields and fd_contact.get('time_zone'):
        #     vals['tz'] = fd_contact.get('time_zone')
        if 'function' in self.env['res.partner']._fields and fd_contact.get('job_title'):
            vals['function'] = fd_contact.get('job_title')
        if 'country_id' in self.env['res.partner']._fields and isinstance(country, dict) and country.get('name'):
            country_rec = self.env['res.country'].sudo().search([('name', '=', country.get('name'))], limit=1)
            if country_rec:
                vals['country_id'] = country_rec.id
        if 'state_id' in self.env['res.partner']._fields and isinstance(state, dict) and state.get('name'):
            state_domain = [('name', '=', state.get('name'))]
            if isinstance(country, dict) and country.get('name'):
                country_rec = self.env['res.country'].sudo().search([('name', '=', country.get('name'))], limit=1)
                if country_rec:
                    state_domain.append(('country_id', '=', country_rec.id))
            state_rec = self.env['res.country.state'].sudo().search(state_domain, limit=1)
            if state_rec:
                vals['state_id'] = state_rec.id
        return vals

    def _sync_webhook_partner_from_contact(self, partner, fd_contact, requester_id=False):
        """Fill missing partner contact data from Freshdesk contact details."""
        if not partner or not isinstance(fd_contact, dict):
            return partner

        contact_vals = self._get_webhook_contact_values(fd_contact, requester_id=requester_id)
        vals = {}

        def _set_if_changed(field_name, new_value):
            if field_name not in partner._fields:
                return
            if new_value in (False, None, ''):
                return
            current_value = partner[field_name]
            if partner._fields[field_name].type == 'many2one':
                current_value = current_value.id if current_value else False
            elif partner._fields[field_name].type in ('char', 'text', 'selection', 'html'):
                current_value = current_value or False
                new_value = str(new_value)
            if current_value != new_value:
                vals[field_name] = new_value

        if requester_id:
            _set_if_changed('sh_freshdesk_id', str(requester_id))
        _set_if_changed('name', contact_vals.get('name'))
        _set_if_changed('email', contact_vals.get('email'))
        _set_if_changed('phone', contact_vals.get('phone'))
        _set_if_changed('comment', contact_vals.get('comment'))
        _set_if_changed('street', contact_vals.get('street'))
        _set_if_changed('street2', contact_vals.get('street2'))
        _set_if_changed('city', contact_vals.get('city'))
        _set_if_changed('zip', contact_vals.get('zip'))
        _set_if_changed('website', contact_vals.get('website'))
        _set_if_changed('company_name', contact_vals.get('company_name'))
        # Do not push Freshdesk language/time zone into Odoo contacts from webhook sync.
        # _set_if_changed('lang', contact_vals.get('lang'))
        # _set_if_changed('tz', contact_vals.get('tz'))
        _set_if_changed('function', contact_vals.get('function'))
        if contact_vals.get('country_id'):
            _set_if_changed('country_id', contact_vals.get('country_id'))
        if contact_vals.get('state_id'):
            _set_if_changed('state_id', contact_vals.get('state_id'))
        if vals:
            vals['sh_last_synced_from_fd'] = fields.Datetime.now()
            partner.with_context(skip_freshdesk_sync=True).write(vals)
        return partner

    def _find_webhook_partner_from_contact(self, fd_contact, requester_id=False):
        """Return the best matching partner for a Freshdesk contact payload."""
        partner_obj = self.env['res.partner'].sudo()

        if requester_id:
            partner = partner_obj.search([
                ('sh_freshdesk_id', '=', str(requester_id)),
                ('is_company', '=', False),
            ], limit=1)
            if partner:
                return partner

        email = (fd_contact.get('email') or '').strip() if isinstance(fd_contact, dict) else ''
        phone = (fd_contact.get('phone') or fd_contact.get('mobile') or '').strip() if isinstance(fd_contact, dict) else ''
        name = (fd_contact.get('name') or '').strip() if isinstance(fd_contact, dict) else ''

        if email:
            partner = partner_obj.search([
                ('email', '=', email),
                ('is_company', '=', False),
            ], limit=1)
            if partner:
                return partner

        if phone:
            partner = partner_obj.search([
                ('phone', '=', phone),
                ('is_company', '=', False),
            ], limit=1)
            if partner:
                return partner

        if name and email:
            partner = partner_obj.search([
                ('name', '=', name),
                ('email', '=', email),
                ('is_company', '=', False),
            ], limit=1)
            if partner:
                return partner

        return self.env['res.partner']

    def _find_webhook_partner_by_name(self, name):
        """Return an existing partner by exact Freshdesk contact name."""
        name = (name or '').strip()
        if not name:
            return self.env['res.partner']

        partner_obj = self.env['res.partner'].sudo()
        partner = partner_obj.search([
            ('name', '=', name),
            ('is_company', '=', False),
        ], limit=1)
        if partner:
            return partner

        partner = partner_obj.search([
            ('name', 'ilike', name),
            ('is_company', '=', False),
        ], limit=1)
        return partner or self.env['res.partner']

    def _get_or_create_webhook_requester(self, fd_data):
        """Find or create a partner from webhook payload fields."""
        partner_obj = self.env['res.partner'].sudo()
        sync_log = self.env['sh.freshdesk.sync.log']
        partner_values = self._get_webhook_partner_values(fd_data)
        requester_id = partner_values['requester_id']
        email = partner_values['email']
        name = partner_values['name']
        phone = partner_values['phone']
        fd_contact = fd_data.get('requester') if isinstance(fd_data.get('requester'), dict) else {}

        if fd_contact and not fd_contact.get('id') and requester_id:
            fd_contact = dict(fd_contact)
            fd_contact['id'] = requester_id

        existing_partner_id = self.env.context.get('sh_webhook_partner_id')
        if existing_partner_id:
            existing_partner = partner_obj.browse(existing_partner_id).exists()
            if existing_partner:
                if not fd_contact and requester_id:
                    fd_contact = self.env['sh.freshdesk.webhook.handler']._fetch_contact_from_freshdesk(requester_id)
                if fd_contact:
                    self._sync_webhook_partner_from_contact(
                        existing_partner,
                        fd_contact,
                        requester_id=requester_id,
                    )
                return existing_partner

        if requester_id:
            partner = partner_obj.search([
                ('sh_freshdesk_id', '=', requester_id),
                ('is_company', '=', False),
            ], limit=1)
            if not fd_contact:
                fd_contact = self.env['sh.freshdesk.webhook.handler']._fetch_contact_from_freshdesk(requester_id)
            if fd_contact:
                fd_contact = dict(fd_contact)
                fd_contact['id'] = fd_contact.get('id') or requester_id
                resource_type = 'company' if self._is_webhook_company_like_contact(fd_contact) else 'contact'
                partner = self.env['res.partner'].with_context(
                    skip_freshdesk_sync=True,
                    sh_skip_webhook_log=True,
                ).process_freshdesk_webhook(
                    fd_contact,
                    'update',
                    resource_type=resource_type,
                )
                return partner
            if partner:
                return partner

        if email:
            partner = partner_obj.search([('email', '=', email), ('is_company', '=', False)], limit=1)
            if partner:
                if requester_id and not partner.sh_freshdesk_id:
                    partner.with_context(skip_freshdesk_sync=True).write({'sh_freshdesk_id': requester_id})
                if not fd_contact and requester_id:
                    fd_contact = self.env['sh.freshdesk.webhook.handler']._fetch_contact_from_freshdesk(requester_id)
                self._sync_webhook_partner_from_contact(partner, fd_contact, requester_id=requester_id)
                return partner

        if fd_contact:
            partner = self._find_webhook_partner_from_contact(fd_contact, requester_id=requester_id)
            if partner:
                self._sync_webhook_partner_from_contact(partner, fd_contact, requester_id=requester_id)
                return partner

        if name and not requester_id and not email:
            fd_contact = self.env['sh.freshdesk.webhook.handler']._fetch_contact_from_name(name)
            if fd_contact:
                fd_contact = dict(fd_contact)
                fd_contact['id'] = fd_contact.get('id')
                resource_type = 'company' if self._is_webhook_company_like_contact(fd_contact) else 'contact'
                partner = self.env['res.partner'].with_context(
                    skip_freshdesk_sync=True,
                    sh_skip_webhook_log=True,
                ).process_freshdesk_webhook(
                    fd_contact,
                    'update',
                    resource_type=resource_type,
                )
                return partner
            partner = self._find_webhook_partner_by_name(name)
            if partner:
                return partner

        if not (requester_id or email or name or phone):
            return self.env['res.partner']

        vals = {
            'name': name or email or _('Freshdesk Contact'),
            'is_company': False,
            'sh_last_synced_from_fd': fields.Datetime.now(),
        }
        if email:
            vals['email'] = email
        if phone:
            vals['phone'] = phone
        if requester_id:
            vals['sh_freshdesk_id'] = requester_id

        partner = partner_obj.with_context(skip_freshdesk_sync=True).create(vals)
        sync_log.create_log(
            'res.partner',
            partner.id,
            'success',
            json.dumps({'source': 'webhook_payload', 'payload': fd_data}),
            'freshdesk_to_odoo',
            operation='create',
        )
        return partner

    def _get_webhook_ticket_tags(self, fd_data):
        """Return the raw tag list from the webhook payload."""
        tags = fd_data.get('tags')
        if tags in (False, None, ''):
            return []
        if isinstance(tags, list):
            return [tag for tag in tags if tag not in (False, None, '')]
        if isinstance(tags, str):
            return [tag.strip() for tag in tags.split(',') if tag.strip()]
        return [str(tags).strip()] if str(tags).strip() else []

    def _get_webhook_tag_commands(self, fd_data):
        """Convert Freshdesk tags into Odoo helpdesk tag commands when possible."""
        if 'tag_ids' not in self._fields:
            return False

        tags = self._get_webhook_ticket_tags(fd_data)
        if not tags:
            return False

        try:
            tag_model = self.env['helpdesk.tag'].sudo()
        except Exception:
            return False

        tag_ids = []
        for tag_name in tags:
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            tag = tag_model.search([('name', '=', tag_name)], limit=1)
            if not tag:
                tag = tag_model.create({'name': tag_name})
            tag_ids.append(tag.id)

        return [(6, 0, tag_ids)] if tag_ids else False

    def _normalize_webhook_status(self, value):
        """Convert Freshdesk status labels or values to the stored selection key."""
        if value in (False, None, ''):
            return '2'
        if isinstance(value, bool):
            return '2'

        raw_value = str(value).strip()
        mapping = {
            'open': '2',
            'pending': '3',
            'resolved': '4',
            'closed': '5',
            'waiting on customer': '6',
            'waiting on third party': '7',
            'pending (custom)': '8',
        }
        if raw_value.lower() in mapping:
            return mapping[raw_value.lower()]

        if raw_value in ('2', '3', '4', '5', '6', '7', '8'):
            return raw_value
        return '2'

    def _normalize_webhook_priority(self, value):
        """Convert Freshdesk priority labels or values to the stored selection key."""
        if value in (False, None, ''):
            return '1'
        if isinstance(value, bool):
            return '1'

        raw_value = str(value).strip()
        mapping = {
            'low': '1',
            'medium': '2',
            'high': '3',
            'urgent': '4',
        }
        if raw_value.lower() in mapping:
            return mapping[raw_value.lower()]

        if raw_value in ('1', '2', '3', '4'):
            return raw_value
        return '1'

    def _enrich_webhook_payload(self, fd_data):
        """Fetch Freshdesk ticket data when the wrapper only contains partial info."""
        payload = dict(fd_data or {})
        ticket_id = payload.get('id')
        if ticket_id and (
            not payload.get('requester_id')
            or not payload.get('tags')
            or payload.get('status') in (None, '', False)
            or payload.get('priority') in (None, '', False)
        ):
            remote_data = self.env['sh.freshdesk.webhook.handler']._fetch_ticket_from_freshdesk(ticket_id)
            if remote_data:
                # Prefer the latest Freshdesk ticket record over webhook fields.
                # Webhook payloads can be partial or can lag behind the updated ticket state.
                merged = dict(payload)
                merged.update(remote_data)
                payload = merged
        return payload

    def _unwrap_webhook_payload(self, fd_data):
        """Return the nested ticket object from known webhook wrappers."""
        if not isinstance(fd_data, dict):
            return {}

        current = fd_data
        for key in ('payload', 'freshdesk_webhook', 'ticket', 'data', 'object'):
            value = current.get(key)
            if isinstance(value, dict):
                current = value
        return current

    def _normalize_webhook_ticket_data(self, fd_data):
        """Map webhook wrapper keys to the Freshdesk ticket schema."""
        data = self._unwrap_webhook_payload(fd_data)
        normalized = dict(data)

        alias_map = {
            'ticket_id': 'id',
            'ticket_subject': 'subject',
            'ticket_description': 'description',
            'ticket_description_text': 'description_text',
            'ticket_status': 'status',
            'ticket_priority': 'priority',
            'ticket_tags': 'tags',
            'ticket_requester_id': 'requester_id',
            'ticket_requester_email': 'email',
            'ticket_requester_name': 'name',
            'ticket_requester_phone': 'phone',
            'ticket_contact_id': 'requester_id',
            'ticket_contact_email': 'email',
            'ticket_contact_name': 'name',
            'ticket_contact_phone': 'phone',
        }
        for alias, canonical in alias_map.items():
            if canonical not in normalized and data.get(alias) not in (False, None, ''):
                normalized[canonical] = data.get(alias)

        requester = normalized.get('requester')
        if isinstance(requester, dict):
            requester_alias_map = {
                'id': 'requester_id',
                'email': 'requester_email',
                'name': 'requester_name',
                'phone': 'requester_phone',
                'mobile': 'requester_phone',
            }
            for source, canonical in requester_alias_map.items():
                if canonical not in normalized and requester.get(source) not in (False, None, ''):
                    normalized[canonical] = requester.get(source)

        return normalized

    def _prepare_webhook_ticket_values(self, fd_data, ticket=False):
        """Prepare Odoo values from a normalized Freshdesk ticket payload."""
        fd_data = self._enrich_webhook_payload(fd_data)
        partner = self._get_or_create_webhook_requester(fd_data)
        if not partner and ticket and ticket.partner_id:
            partner = ticket.partner_id
        tag_commands = self._get_webhook_tag_commands(fd_data)
        vals = {
            'name': fd_data.get('subject') or _('No Subject'),
            'description': fd_data.get('description') or fd_data.get('description_text') or '',
            'sh_freshdesk_id': str(fd_data.get('id')) if fd_data.get('id') else False,
            'sh_freshdesk_status': self._normalize_webhook_status(fd_data.get('status', 2)),
            'sh_freshdesk_priority': self._normalize_webhook_priority(fd_data.get('priority', 1)),
            'sh_last_synced_from_fd': fields.Datetime.now(),
            'sh_freshdesk_webhook_payload': json.dumps(fd_data, indent=4, sort_keys=True, ensure_ascii=False),
        }
        if partner:
            vals['partner_id'] = partner.id
        elif ticket and ticket.partner_id:
            vals['partner_id'] = ticket.partner_id.id
        elif not ticket:
            vals['partner_id'] = False

        if tag_commands:
            vals['tag_ids'] = tag_commands
        return vals

    def _prepare_webhook_ticket_update_values(self, ticket, fd_data):
        """Only keep webhook values that actually changed on the ticket."""
        vals = self._prepare_webhook_ticket_values(fd_data, ticket=ticket)
        if not ticket:
            return vals

        write_vals = {}
        for field_name, new_value in vals.items():
            if field_name == 'tag_ids':
                if new_value and ticket.tag_ids.ids != new_value[0][2]:
                    write_vals[field_name] = new_value
                continue

            if field_name not in ticket._fields:
                write_vals[field_name] = new_value
                continue

            current_value = ticket[field_name]
            if ticket._fields[field_name].type == 'many2one':
                current_value = current_value.id if current_value else False
            elif ticket._fields[field_name].type in ('char', 'text', 'selection', 'html', 'datetime'):
                current_value = current_value or False
                new_value = new_value or False

            if current_value != new_value:
                write_vals[field_name] = new_value

        return write_vals

    def _get_freshdesk_status(self):
        stage_name = (self.stage_id.name or '').lower()
        if self.stage_id and self.stage_id.fold:
            return 5
        if 'pending' in stage_name:
            return 3
        if 'resolve' in stage_name or 'done' in stage_name:
            return 4
        return 2

    def _get_freshdesk_priority(self):
        pr = (self.priority or '0').strip()
        mapping = {'0': 1, '1': 2, '2': 3, '3': 4}
        return mapping.get(pr, 1)

    def _prepare_freshdesk_ticket_data(self):
        data = {
            'subject': self.name or 'No Subject',
            'description': self.description or self.name or 'No Description',
            'status': int(self.sh_freshdesk_status),
            'priority': int(self.sh_freshdesk_priority),
            # 'requester_id':False
        }
        if self.partner_id:
            if self.partner_id.is_company:
                if self.partner_email:
                    data['email'] = self.partner_email
                elif self.partner_id.email:
                    data['email'] = self.partner_id.email
            elif self.partner_id.sh_freshdesk_id:
                data['requester_id'] = int(self.partner_id.sh_freshdesk_id)
            elif self.partner_email:
                data['email'] = self.partner_email
        elif self.partner_email:
            data['email'] = self.partner_email
        return data

    def action_sync_ticket_to_freshdesk(self, is_manual=False):
        """
        Sync helpdesk tickets to Freshdesk.

        Context flags:
          - sh_is_sync_button: Manual "Sync to Freshdesk" button on the record form.
          - sh_is_crud_sync:   Auto-triggered by create/write/unlink ORM.
          - sh_is_create/sh_is_update: Sub-flags for CRUD type.
          - sh_bulk_sync:      Called by Sync Now / Cron — suppress notifications.

        is_manual (arg): True when called from action_sync_now (Sync Now button in settings).

        Logging: ALWAYS created.
        Notifications:
          - Button: return action on success, raise ValidationError on failure.
          - CRUD: bus notification if company boolean is enabled.
          - Bulk/Cron: no notifications, only logs.
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        company = self.env.company
        is_button = self.env.context.get('sh_is_sync_button')
        is_crud = self.env.context.get('sh_is_crud_sync')
        is_bulk = self.env.context.get('sh_bulk_sync')
        is_create = self.env.context.get('sh_is_create')
        is_update = self.env.context.get('sh_is_update')

        for ticket in self:
            op = 'update' if ticket.sh_freshdesk_id else 'create'
            try:
                data = ticket._prepare_freshdesk_ticket_data()
                if ticket.sh_freshdesk_id:
                    response = ticket._freshdesk_request(
                        'PUT', '/api/v2/tickets/%s' % ticket.sh_freshdesk_id, payload=data, timeout=10
                    )
                    
                elif is_create:
                    response = ticket._freshdesk_request('POST', '/api/v2/tickets', payload=data, timeout=10)
                else:
                    continue

                if response.status_code in (200, 201):
                    res_data = response.json()
                    ticket.with_context(skip_freshdesk_sync=True).write({
                        'sh_freshdesk_id': str(res_data.get('id')),
                        'sh_freshdesk_status': str(res_data.get('status', 2)),
                        'sh_freshdesk_priority': str(res_data.get('priority', 1)),
                        'sh_last_synced_from_fd': fields.Datetime.now(),
                    })

                    # Update partner's Freshdesk ID and Sync date if Freshdesk auto-created/linked it
                    fd_requester_id = res_data.get('requester_id')
                    if fd_requester_id and ticket.partner_id and not ticket.partner_id.sh_freshdesk_id:
                        ticket.partner_id.with_context(skip_freshdesk_sync=True).write({
                            'sh_freshdesk_id': str(fd_requester_id),
                            'sh_last_synced_from_fd': fields.Datetime.now(),
                        })
                        # Log contact creation (since it did not exist before and was created now)
                        sync_log.create_log('res.partner', ticket.partner_id.id, 'success', response.text, 'odoo_to_freshdesk', operation='create')

                    # LOG: Always
                    sync_log.create_log('helpdesk.ticket', ticket.id, 'success', response.text, 'odoo_to_freshdesk', operation=op)

                    # NOTIFICATION: Not for bulk/cron
                    if is_bulk:
                        continue

                    show_notification = False
                    if is_button:
                        show_notification = True
                    elif is_crud:
                        if is_create and company.sh_fd_ticket_create:
                            show_notification = True
                        elif is_update and company.sh_fd_ticket_update:
                            show_notification = True

                    if show_notification:
                        notification = {
                            'type': 'success',
                            'title': _('Success'),
                            'message': _('Ticket Synced to Freshdesk Successfully!'),
                            'sticky': False,
                        }
                        if is_button:
                            return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': notification}
                        else:
                            # sticky=True for CRUD: bus notifications may arrive after page reload
                            notification['sticky'] = True
                            self._send_sync_notification(notification)

                else:
                    error_msg = self._extract_error_message(response)

                    # LOG: Always
                    sync_log.create_log('helpdesk.ticket', ticket.id, 'error',
                                        response.text, 'odoo_to_freshdesk', error_log=error_msg, operation=op)

                    if is_button:
                        raise ValidationError(_("Freshdesk Error: %s") % error_msg)
                    elif is_crud and not is_bulk:
                        self._send_sync_notification({
                            'type': 'danger',
                            'title': _('Sync Error'),
                            'message': _('Failed to sync ticket %(name)s to Freshdesk: %(err)s',
                                         name=ticket.name, err=error_msg[:120]),
                            'sticky': True,
                        })

            except ValidationError as ve:
                if is_button:
                    raise ve
            except Exception:
                err = traceback.format_exc()
                # LOG: Always
                sync_log.create_log('helpdesk.ticket', ticket.id, 'error',
                                    'Odoo side Exception occurred.', 'odoo_to_freshdesk', error_log=err, operation=op)
                if is_button:
                    raise ValidationError(_("Internal Error: %s") % err)
                elif is_crud and not is_bulk:
                    self._send_sync_notification({
                        'type': 'danger',
                        'title': _('Sync Error'),
                        'message': _('Internal error syncing ticket %(name)s to Freshdesk.',
                                     name=ticket.name),
                        'sticky': True,
                    })

        return False

    def _send_sync_notification(self, params):
        """Helper to send realtime notification to current user via Bus."""
        if self.env.user:
            self.env['bus.bus']._sendone(self.env.user.partner_id, 'simple_notification', params)

    def action_delete_from_freshdesk(self):
        """
        Delete tickets from Freshdesk.
        Logging: ALWAYS created.
        Notifications: Only for button and crud delete (when company boolean is enabled).
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        company = self.env.company
        is_button = self.env.context.get('sh_is_sync_button')
        is_crud = self.env.context.get('sh_is_crud_sync')
        is_bulk = self.env.context.get('sh_bulk_sync')

        for record in self:
            if record.sh_freshdesk_id:
                try:
                    response = record._freshdesk_request('DELETE', '/api/v2/tickets/%s' % record.sh_freshdesk_id, timeout=10)
                    if response.status_code == 204:
                        # LOG: Always
                        sync_log.create_log('helpdesk.ticket', record.id, 'success',
                                            response.text or 'Deleted successfully.', 'odoo_to_freshdesk', operation='delete')

                        # NOTIFICATION
                        show_notification = False
                        if not is_bulk:
                            if is_button:
                                show_notification = True
                            elif is_crud and company.sh_fd_ticket_delete:
                                show_notification = True

                        if show_notification:
                            notification = {
                                'type': 'success',
                                'title': _('Deleted'),
                                'message': _('Ticket Deleted from Freshdesk Successfully!'),
                                'sticky': False,
                            }
                            if is_button:
                                return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': notification}
                            else:
                                # sticky=True for CRUD: bus notifications may arrive after page reload
                                notification['sticky'] = True
                                self._send_sync_notification(notification)
                    else:
                        error_msg = self._extract_error_message(response)
                        # LOG: Always
                        sync_log.create_log('helpdesk.ticket', record.id, 'error', response.text, 'odoo_to_freshdesk', operation='delete', error_log=error_msg)

                        if is_button:
                            raise ValidationError(_("Freshdesk Deletion Error: %s") % error_msg)
                        elif is_crud and not is_bulk:
                            self._send_sync_notification({
                                'type': 'danger',
                                'title': _('Delete Error'),
                                'message': _('Failed to delete ticket %(name)s from Freshdesk: %(err)s',
                                             name=record.name, err=error_msg[:120]),
                                'sticky': True,
                            })

                except ValidationError as ve:
                    if is_button:
                        raise ve
                except Exception:
                    err = traceback.format_exc()
                    # LOG: Always
                    sync_log.create_log('helpdesk.ticket', record.id, 'error',
                                        'Odoo side Exception occurred.', 'odoo_to_freshdesk', error_log=err, operation='delete')
                    if is_button:
                        raise ValidationError(_("Ticket Deletion Internal Error: %s") % err)
                    elif is_crud and not is_bulk:
                        self._send_sync_notification({
                            'type': 'danger',
                            'title': _('Delete Error'),
                            'message': _('Internal error deleting ticket %(name)s from Freshdesk.',
                                         name=record.name),
                            'sticky': True,
                        })

    @api.model_create_multi
    def create(self, vals_list):
        records = super(HelpdeskTicket, self.with_context(skip_freshdesk_sync=True)).create(vals_list)
        company = self.env.company
        if not self.env.context.get('skip_freshdesk_sync') and company.sh_fd_ticket_create:
            records.with_context(
                skip_freshdesk_sync=True,
                sh_is_crud_sync=True,
                sh_is_create=True,
            ).action_sync_ticket_to_freshdesk()
        return records

    def write(self, vals):
        res = super().write(vals)
        company = self.env.company
        if not self.env.context.get('skip_freshdesk_sync') and company.sh_fd_ticket_update:
            sync_skip_fields = ['sh_freshdesk_id', 'sh_freshdesk_status', 'sh_freshdesk_priority', 'sh_last_synced_from_fd', 'access_token']
            if all(k in sync_skip_fields for k in vals.keys()):
                return res
            self.with_context(
                skip_freshdesk_sync=True,
                sh_is_crud_sync=True,
                sh_is_update=True,
            ).action_sync_ticket_to_freshdesk()
        return res

    def unlink(self):
        company = self.env.company
        if not self.env.context.get('skip_freshdesk_sync') and company.sh_fd_ticket_delete:
            self.with_context(sh_is_crud_sync=True, sh_is_delete=True).action_delete_from_freshdesk()
        return super().unlink()

    def _get_or_create_freshdesk_requester(self, requester_id):
        """
        Resolve a Freshdesk requester into an Odoo contact.

        The contact is always hydrated from Freshdesk first so we keep the
        requester data in sync instead of creating a reduced duplicate record.
        """
        if not requester_id:
            return self.env['res.partner']

        try:
            response = self._freshdesk_request('GET', '/api/v2/contacts/%s' % requester_id, params={'include': 'description'})
            if response.status_code == 200 and isinstance(response.json(), dict):
                fd_contact = response.json()
                fd_contact['id'] = fd_contact.get('id') or requester_id
                partner = self.env['res.partner'].with_context(skip_freshdesk_sync=True).process_freshdesk_webhook(
                    fd_contact,
                    'update',
                    resource_type='contact',
                )
                return partner
        except Exception as exc:
            _logger.error('Failed to auto-create requester %s during ticket sync: %s', requester_id, str(exc))

        return self.env['res.partner']

    @api.model
    def _map_freshdesk_priority_to_odoo(self, freshdesk_priority):
        mapping = {1: '0', 2: '1', 3: '2', 4: '3'}
        return mapping.get(int(freshdesk_priority or 1), '0')

    @api.model
    def process_freshdesk_webhook(self, fd_data, operation):
        sync_log = self.env['sh.freshdesk.sync.log']
        fd_data = self._normalize_webhook_ticket_data(fd_data)
        fd_id = fd_data.get('id')
        if not fd_id:
            raise ValidationError(_("Freshdesk webhook payload does not contain an ID."))

        fd_id = str(fd_id)
        ticket = self.search([('sh_freshdesk_id', '=', fd_id)], limit=1)

        if operation == 'delete':
            if ticket:
                ticket_id = ticket.id
                ticket.with_context(skip_freshdesk_sync=True).unlink()
                sync_log.create_log(
                    'helpdesk.ticket',
                    ticket_id,
                    'success',
                    json.dumps(fd_data),
                    'freshdesk_to_odoo',
                    operation='delete',
                )
                return self.browse()
            sync_log.create_log(
                'helpdesk.ticket',
                False,
                'success',
                json.dumps({'payload': fd_data, 'result': 'delete skipped; record not found'}),
                'freshdesk_to_odoo',
                operation='delete',
            )
            return self.browse()

        vals = self._prepare_webhook_ticket_values(fd_data, ticket=ticket)
        if not ticket:
            vals['team_id'] = self._get_webhook_helpdesk_team(self.env.company).id
        else:
            vals = self._prepare_webhook_ticket_update_values(ticket, fd_data)

        if ticket:
            ticket.with_context(skip_freshdesk_sync=True).write(vals)
            logged_operation = 'update'
            record = ticket
        else:
            record = self.with_context(skip_freshdesk_sync=True).create(vals)
            logged_operation = 'create'

        sync_log.create_log(
            'helpdesk.ticket',
            record.id,
            'success',
            json.dumps(fd_data),
            'freshdesk_to_odoo',
            operation=logged_operation,
        )
        return record

    @api.model
    def sync_tickets_from_freshdesk(self, is_manual=False):
        """
        Sync tickets from Freshdesk to Odoo.
        is_manual=True  → Manual "Sync Now" (uses date range filter).
        is_manual=False → Cron job (incremental sync from last_sync_date).
        Logging is ALWAYS performed.
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        page = 1
        per_page = 100
        company = self.env.company.sudo()
        sync_limit = company.sh_fd_ticket_sync_limit
        total_synced = 0
        from_cron = bool(self.env.context.get('cron_id'))
        _logger.info('Starting Freshdesk Ticket Sync (is_manual=%s, limit=%s)', is_manual, sync_limit)

        while True:
            params = self._prepare_updated_since_params(page=page, manual=is_manual)
            end_date = params.pop('sh_end_date', None)
            params['include'] = 'description'

            _logger.info('Fetching Freshdesk Tickets - Page %s', page)
            try:
                response = self._freshdesk_request('GET', '/api/v2/tickets', params=params)
            except Exception:
                _logger.error('Error fetching tickets page %s: %s', page, traceback.format_exc())
                break

            if response.status_code != 200:
                _logger.error('Freshdesk API error (tickets) Status %s: %s', response.status_code, response.text)
                break

            fd_tickets = response.json()
            if not fd_tickets or not isinstance(fd_tickets, list):
                _logger.info('No more tickets on page %s. Sync done.', page)
                break

            _logger.info('Processing %s tickets from page %s', len(fd_tickets), page)
            processed_in_batch = 0
            for fd_ticket in fd_tickets:
                if sync_limit > 0 and total_synced >= sync_limit:
                    _logger.info('Sync limit %s reached. Stopping.', sync_limit)
                    break

                try:
                    if end_date:
                        fd_updated_at = fd_ticket.get('updated_at')
                        if fd_updated_at and str(fd_updated_at) > str(end_date):
                            continue

                    fd_id = str(fd_ticket.get('id'))
                    ticket = self.search([('sh_freshdesk_id', '=', fd_id)], limit=1)

                    # Find or Create Related Contact (Requester)
                    requester_id = fd_ticket.get('requester_id')
                    partner = self._get_or_create_freshdesk_requester(requester_id)
                    tag_commands = self._get_webhook_tag_commands(fd_ticket)

                    vals = {
                        'name': fd_ticket.get('subject') or 'No Subject',
                        'description': fd_ticket.get('description') or '',
                        'sh_freshdesk_id': fd_id,
                        'sh_freshdesk_status': self._normalize_webhook_status(fd_ticket.get('status', 2)),
                        'sh_freshdesk_priority': self._normalize_webhook_priority(fd_ticket.get('priority', 1)),
                        # 'priority': self._map_freshdesk_priority_to_odoo(fd_ticket.get('priority')),
                        'partner_id': partner.id if partner else False,
                        'sh_last_synced_from_fd': fields.Datetime.now(),
                    }
                    if tag_commands:
                        vals['tag_ids'] = tag_commands

                    if ticket:
                        ticket.with_context(skip_freshdesk_sync=True).write(vals)
                        sync_log.create_log('helpdesk.ticket', ticket.id, 'success', json.dumps(fd_ticket), 'freshdesk_to_odoo', operation='update')
                    else:
                        new_ticket = self.with_context(skip_freshdesk_sync=True).create(vals)
                        sync_log.create_log('helpdesk.ticket', new_ticket.id, 'success', json.dumps(fd_ticket), 'freshdesk_to_odoo', operation='create')

                    total_synced += 1
                    processed_in_batch += 1
                except Exception:
                    _logger.exception('Freshdesk ticket sync failed on page %s for ticket %s', page, fd_ticket.get('id'))
                    if not from_cron:
                        raise

            if from_cron and processed_in_batch:
                if not self.env['ir.cron']._commit_progress(processed=processed_in_batch):
                    break
            if sync_limit > 0 and total_synced >= sync_limit:
                break
            if len(fd_tickets) < per_page:
                _logger.info('Page %s not full (%s/%s). Done.', page, len(fd_tickets), per_page)
                break
            page += 1

        self.env.company.sudo().write({'sh_freshdesk_last_ticket_sync_date': fields.Datetime.now()})
        return True
