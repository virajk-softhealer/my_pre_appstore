# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging
import traceback
from odoo import models, fields, api, _
from odoo.tools import html2plaintext
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class ResPartner(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'sh.freshdesk.api']

    sh_freshdesk_id = fields.Char(string='Freshdesk ID', copy=False, index=True)
    sh_last_synced_from_fd = fields.Datetime(string='Last Synced (FD)', copy=False)

    def _prepare_freshdesk_contact_data(self):
        if self.is_company:
            return {
                'name': self.name,
                'note': html2plaintext(self.comment or ''),
            }
        return {
            'name': self.name,
            'email': self.email or '',
            'phone': self.phone or '',
            'description': html2plaintext(self.comment or ''),
            'address':self.street or '',
        }

    def action_sync_to_freshdesk(self):
        """
        Sync partners/companies to Freshdesk.

        Context flags used:
          - sh_is_sync_button: Called from a manual "Sync to Freshdesk" button on the record.
          - sh_is_crud_sync:   Called automatically by create/write/unlink ORM methods.
          - sh_is_create:      Sub-flag of crud: it was a create operation.
          - sh_is_update:      Sub-flag of crud: it was a write/update operation.
          - sh_bulk_sync:      Called inside Sync Now / Cron — suppress individual notifications.

        Logging: ALWAYS created for every outcome.
        Notifications:
          - Button click: return action dict (dialog) on success, raise ValidationError on failure.
          - CRUD auto-sync: Send bus notification only if company boolean is enabled.
          - Bulk sync (Sync Now / Cron): No notifications — only logs.
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        company = self.env.company
        is_button = self.env.context.get('sh_is_sync_button')
        is_crud = self.env.context.get('sh_is_crud_sync')
        is_bulk = self.env.context.get('sh_bulk_sync')
        is_create = self.env.context.get('sh_is_create')

        for partner in self:
            op = 'update' if partner.sh_freshdesk_id else 'create'
            try:
                # Validation: non-company contacts must have an email
                if not partner.email and not partner.is_company and (partner.sh_freshdesk_id or is_create):
                    reason = _('Contact sync skipped: email address is missing.')
                    sync_log.create_log(
                        model_name='res.partner', res_id=partner.id,
                        status='error', message=False,
                        direction='odoo_to_freshdesk', error_log=reason,
                        operation=op,
                    )
                    if is_button:
                        raise ValidationError(reason)
                    elif is_crud and not is_bulk:
                        self._send_sync_notification({
                            'type': 'danger',
                            'title': _('Sync Skipped'),
                            'message': _('%(name)s: Email is required to sync with Freshdesk.',
                                         name=partner.name),
                            'sticky': True,
                        })
                    continue  # Skip this partner, continue with rest

                data = partner._prepare_freshdesk_contact_data()
                endpoint = '/api/v2/companies/' if partner.is_company else '/api/v2/contacts/'

                if partner.sh_freshdesk_id:
                    response = partner._freshdesk_request('PUT', endpoint + partner.sh_freshdesk_id, payload=data, timeout=15)
                elif is_create:
                    response = partner._freshdesk_request('POST', endpoint, payload=data, timeout=15)
                else:
                    continue

                if response.status_code in (200, 201):
                    res_data = response.json()
                    partner.with_context(skip_freshdesk_sync=True).write({
                        'sh_freshdesk_id': str(res_data.get('id')),
                        'sh_last_synced_from_fd': fields.Datetime.now(),
                    })

                    # LOG: Always
                    sync_log.create_log('res.partner', partner.id, 'success', response.text, 'odoo_to_freshdesk', operation=op)

                    # NOTIFICATION: Only for button and crud — not for bulk/cron
                    if is_bulk:
                        continue

                    show_notification = False
                    if is_button:
                        show_notification = True
                    elif is_crud:
                        if self.env.context.get('sh_is_create') and company.sh_fd_partner_create:
                            show_notification = True
                        elif self.env.context.get('sh_is_update') and company.sh_fd_partner_update:
                            show_notification = True

                    if show_notification:
                        notification = {
                            'type': 'success',
                            'title': _('Success'),
                            'message': _('%(target)s Synced to Freshdesk Successfully!',
                                         target=_('Company') if partner.is_company else _('Contact')),
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
                    sync_log.create_log('res.partner', partner.id, 'error',response.text, 'odoo_to_freshdesk', error_log=error_msg, operation=op)

                    if is_button:
                        raise ValidationError(_("Freshdesk Error: %s") % error_msg)
                    elif is_crud and not is_bulk:
                        self._send_sync_notification({
                            'type': 'danger',
                            'title': _('Sync Error'),
                            'message': _('Failed to sync %(name)s to Freshdesk: %(err)s',
                                         name=partner.name, err=error_msg[:120]),
                            'sticky': True,
                        })

            except ValidationError as ve:
                if is_button:
                    raise ve
            except Exception:
                err = traceback.format_exc()
                # LOG: Always
                sync_log.create_log('res.partner', partner.id, 'error',
                                    'Odoo side Exception occurred.', 'odoo_to_freshdesk', error_log=err, operation=op)
                if is_button:
                    raise ValidationError(_("Internal Error: %s") % err)
                elif is_crud and not is_bulk:
                    self._send_sync_notification({
                        'type': 'danger',
                        'title': _('Sync Error'),
                        'message': _('Internal error syncing %(name)s to Freshdesk.',
                                     name=partner.name),
                        'sticky': True,
                    })

        return True

    def _send_sync_notification(self, params):
        """Helper to send realtime notification to current user via Bus."""
        if self.env.user:
            self.env['bus.bus']._sendone(self.env.user.partner_id, 'simple_notification', params)

    def action_delete_from_freshdesk(self):
        """
        Delete partners from Freshdesk.
        Logging: ALWAYS created.
        Notifications: Only for button and crud delete (when company boolean is enabled).
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        company = self.env.company
        is_button = self.env.context.get('sh_is_sync_button')
        is_crud = self.env.context.get('sh_is_crud_sync')
        is_bulk = self.env.context.get('sh_bulk_sync')

        for record in self.filtered(lambda x: x.sh_freshdesk_id):
            try:
                # Use correct endpoint: companies for is_company, contacts for persons
                endpoint = '/api/v2/companies/%s' if record.is_company else '/api/v2/contacts/%s'
                response = record._freshdesk_request('DELETE', endpoint % record.sh_freshdesk_id, timeout=10)
                if response.status_code == 204:
                    # LOG: Always
                    sync_log.create_log('res.partner', record.id, 'success',
                        response.text or 'Deleted successfully.', 'odoo_to_freshdesk', operation='delete')

                    # NOTIFICATION
                    show_notification = False
                    if not is_bulk:
                        if is_button:
                            show_notification = True
                        elif is_crud and company.sh_fd_partner_delete:
                            show_notification = True

                    if show_notification:
                        target_label = _('Company') if record.is_company else _('Contact')
                        notification = {
                            'type': 'success',
                            'title': _('Deleted'),
                            'message': _('%(target)s Deleted from Freshdesk Successfully!', target=target_label),
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
                    sync_log.create_log('res.partner', record.id, 'error', response.text, 'odoo_to_freshdesk', operation='delete', error_log=error_msg)

                    if is_button:
                        raise ValidationError(_("Freshdesk Deletion Error: %s") % error_msg)
                    elif is_crud and not is_bulk:
                        self._send_sync_notification({
                            'type': 'danger',
                            'title': _('Delete Error'),
                            'message': _('Failed to delete %(name)s from Freshdesk: %(err)s',
                                            name=record.name, err=error_msg[:120]),
                            'sticky': True,
                        })

            except ValidationError as ve:
                if is_button:
                    raise ve
            except Exception:
                err = traceback.format_exc()
                # LOG: Always
                sync_log.create_log('res.partner', record.id, 'error',
                                    'Odoo side Exception occurred.', 'odoo_to_freshdesk', error_log=err, operation='delete')
                if is_button:
                    raise ValidationError(_("Deletion Internal Error: %s") % err)
                elif is_crud and not is_bulk:
                    self._send_sync_notification({
                        'type': 'danger',
                        'title': _('Delete Error'),
                        'message': _('Internal error deleting %(name)s from Freshdesk.',
                                        name=record.name),
                        'sticky': True,
                    })

    @api.model_create_multi
    def create(self, vals_list):
        records = super(ResPartner, self.with_context(skip_freshdesk_sync=True)).create(vals_list)
        company = self.env.company
        if not self.env.context.get('skip_freshdesk_sync') and company.sh_fd_partner_create:
            records.with_context(
                skip_freshdesk_sync=True,
                sh_is_crud_sync=True,
                sh_is_create=True,
            ).action_sync_to_freshdesk()
        return records

    def write(self, vals):
        res = super().write(vals)
        company = self.env.company
        if not self.env.context.get('skip_freshdesk_sync') and company.sh_fd_partner_update:
            # Skip if only internal metadata fields are updated
            if all(k in ['sh_freshdesk_id', 'sh_last_synced_from_fd'] for k in vals.keys()):
                return res
            self.with_context(
                skip_freshdesk_sync=True,
                sh_is_crud_sync=True,
                sh_is_update=True,
            ).action_sync_to_freshdesk()
        return res

    def unlink(self):
        company = self.env.company
        if not self.env.context.get('skip_freshdesk_sync') and company.sh_fd_partner_delete:
            self.with_context(sh_is_crud_sync=True, sh_is_delete=True).action_delete_from_freshdesk()
        return super().unlink()

    def _prepare_freshdesk_webhook_contact_values(self, fd_data, is_company=False):
        """Build the partner values from a Freshdesk contact payload."""
        default_name = _('Freshdesk Company') if is_company else _('Freshdesk Contact')
        vals = {
            'name': fd_data.get('name') or fd_data.get('email') or default_name,
            'comment': fd_data.get('note') or fd_data.get('description') or '',
            'is_company': is_company,
            'sh_last_synced_from_fd': fields.Datetime.now(),
        }
        if not is_company:
            vals.update({
                'email': fd_data.get('email') or '',
                'phone': fd_data.get('phone') or fd_data.get('mobile') or '',
                'street': fd_data.get('address') or '',
                'website': fd_data.get('website') or '',
                'city': fd_data.get('city') or '',
                'zip': fd_data.get('zip') or fd_data.get('postal_code') or '',
            })

        if 'company_name' in self._fields and fd_data.get('company_name'):
            vals['company_name'] = fd_data.get('company_name')
        # Freshdesk language/time zone values are not always valid Odoo locale/timezone codes.
        # Keeping them out of the webhook payload avoids validation errors during contact sync.
        # if 'lang' in self._fields and fd_data.get('language'):
        #     vals['lang'] = fd_data.get('language')
        # if 'tz' in self._fields and fd_data.get('time_zone'):
        #     vals['tz'] = fd_data.get('time_zone')
        if 'function' in self._fields and fd_data.get('job_title'):
            vals['function'] = fd_data.get('job_title')

        country = fd_data.get('country') or {}
        if 'country_id' in self._fields and isinstance(country, dict) and country.get('name'):
            country_rec = self.env['res.country'].sudo().search([('name', '=', country.get('name'))], limit=1)
            if country_rec:
                vals['country_id'] = country_rec.id

        state = fd_data.get('state') or {}
        if 'state_id' in self._fields and isinstance(state, dict) and state.get('name'):
            state_domain = [('name', '=', state.get('name'))]
            if isinstance(country, dict) and country.get('name'):
                country_rec = self.env['res.country'].sudo().search([('name', '=', country.get('name'))], limit=1)
                if country_rec:
                    state_domain.append(('country_id', '=', country_rec.id))
            state_rec = self.env['res.country.state'].sudo().search(state_domain, limit=1)
            if state_rec:
                vals['state_id'] = state_rec.id

        return vals

    def _find_existing_freshdesk_partner(self, fd_data, is_company=False):
        """Find the most likely partner for a Freshdesk contact payload."""
        partner_obj = self.sudo()
        fd_id = str(fd_data.get('id') or '').strip()
        email = (fd_data.get('email') or '').strip()
        phone = (fd_data.get('phone') or fd_data.get('mobile') or '').strip()
        name = (fd_data.get('name') or '').strip()

        if fd_id:
            partner = partner_obj.search([
                ('sh_freshdesk_id', '=', fd_id),
                ('is_company', '=', is_company),
            ], limit=1)
            if partner:
                return partner

        if email:
            partner = partner_obj.search([
                ('email', '=', email),
                ('is_company', '=', is_company),
            ], limit=1)
            if partner:
                return partner

        if phone:
            partner = partner_obj.search([
                ('phone', '=', phone),
                ('is_company', '=', is_company),
            ], limit=1)
            if partner:
                return partner

        if name and email:
            partner = partner_obj.search([
                ('name', '=', name),
                ('email', '=', email),
                ('is_company', '=', is_company),
            ], limit=1)
            if partner:
                return partner

        if name:
            partner = partner_obj.search([
                ('name', '=', name),
                ('is_company', '=', is_company),
            ], limit=1)
            if partner:
                return partner

        return self.browse()

    def _is_webhook_company_payload(self, fd_data):
        """Return True when the webhook payload clearly looks like a company payload."""
        if not isinstance(fd_data, dict):
            return False

        if fd_data.get('is_company') is True:
            return True

        company_name = (fd_data.get('company_name') or '').strip()
        email = (fd_data.get('email') or '').strip()
        phone = (fd_data.get('phone') or fd_data.get('mobile') or '').strip()
        name = (fd_data.get('name') or '').strip()
        company_markers = any(
            fd_data.get(key) not in (False, None, '')
            for key in ('company_name', 'company', 'company_id', 'org_company_id', 'domains', 'note')
        )
        person_markers = bool(email or phone)

        if company_markers and not person_markers:
            return True
        if company_name and not person_markers and name == company_name:
            return True
        return False


    def _prepare_freshdesk_webhook_person_values(self, fd_data):
        """Prepare webhook values for a person/contact partner."""
        return self._prepare_freshdesk_webhook_contact_values(fd_data, is_company=False)

    def _prepare_freshdesk_webhook_company_values(self, fd_data):
        """Prepare webhook values for a company partner."""
        vals = self._prepare_freshdesk_webhook_contact_values(fd_data, is_company=True)
        vals['is_company'] = True
        if fd_data.get('company_name') and not vals.get('name'):
            vals['name'] = fd_data.get('company_name')
        return vals

    def _get_webhook_contact_id(self, fd_data):
        """Return a contact/requester ID only when the payload is clearly contact-shaped."""
        if not isinstance(fd_data, dict):
            return False

        requester = fd_data.get('requester') if isinstance(fd_data.get('requester'), dict) else {}
        explicit_id = (
            requester.get('id')
            or fd_data.get('requester_id')
            or fd_data.get('contact_id')
            or fd_data.get('ticket_requester_id')
            or fd_data.get('ticket_contact_id')
        )
        if explicit_id not in (False, None, ''):
            return str(explicit_id)

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
        contact_keys = {'email', 'phone', 'mobile', 'name', 'requester_email', 'requester_phone', 'requester_name'}
        has_ticket_marker = any(fd_data.get(key) not in (False, None, '') for key in ticket_keys)
        has_contact_marker = any(fd_data.get(key) not in (False, None, '') for key in contact_keys)
        if has_ticket_marker and not has_contact_marker:
            return False

        fd_id = fd_data.get('id')
        return str(fd_id) if fd_id not in (False, None, '') else False


    def _write_freshdesk_partner_diff(self, partner, vals):
        """Write only changed fields to avoid noisy rewrites."""
        write_vals = {}
        for field_name, new_value in (vals or {}).items():
            if field_name not in partner._fields:
                continue

            current_value = partner[field_name]
            field_type = partner._fields[field_name].type
            if field_type == 'many2one':
                current_value = current_value.id if current_value else False
            elif field_type in ('char', 'text', 'selection', 'html', 'datetime'):
                current_value = current_value or False
                new_value = new_value or False

            if current_value != new_value:
                write_vals[field_name] = new_value

        if write_vals:
            partner.with_context(skip_freshdesk_sync=True).write(write_vals)
        return partner

    @api.model
    def sync_partners_from_freshdesk(self, is_manual=False):
        """
        Sync contacts from Freshdesk to Odoo.
        is_manual=True  → Manual "Sync Now" button (uses date range filter).
        is_manual=False → Cron job (uses last_sync_date for incremental sync).
        Logging is ALWAYS performed.
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        page = 1
        per_page = 100
        company = self.env.company.sudo()
        sync_limit = company.sh_fd_contact_sync_limit
        total_synced = 0
        from_cron = bool(self.env.context.get('cron_id'))
        _logger.info('Starting Freshdesk Contact Sync (is_manual=%s, limit=%s)', is_manual, sync_limit)

        while True:
            params = self._prepare_updated_since_params(page=page, manual=is_manual)
            end_date = params.pop('sh_end_date', None)

            _logger.info('Fetching Freshdesk Contacts - Page %s', page)
            try:
                response = self._freshdesk_request('GET', '/api/v2/contacts', params=params)
            except Exception:
                _logger.error('Error fetching contacts page %s: %s', page, traceback.format_exc())
                break

            if response.status_code != 200:
                _logger.error('Freshdesk API error (contacts) Status %s: %s', response.status_code, response.text)
                break

            contacts = response.json()
            if not contacts or not isinstance(contacts, list):
                _logger.info('No more contacts on page %s. Sync done.', page)
                break

            _logger.info('Processing %s contacts from page %s', len(contacts), page)
            processed_in_batch = 0
            for contact in contacts:
                if sync_limit > 0 and total_synced >= sync_limit:
                    _logger.info('Sync limit %s reached. Stopping.', sync_limit)
                    break

                try:
                    if end_date:
                        fd_updated_at = contact.get('updated_at')
                        if fd_updated_at and str(fd_updated_at) > str(end_date):
                            continue

                    fd_id = str(contact.get('id'))
                    email = contact.get('email')

                    partner = self.search([('sh_freshdesk_id', '=', fd_id), ('is_company', '=', False)], limit=1)
                    if not partner and email:
                        partner = self.search([('email', '=', email), ('is_company', '=', False)], limit=1)

                    vals = {
                        'name': contact.get('name') or 'Freshdesk Contact',
                        'email': email,
                        'phone': contact.get('phone') or '',
                        'comment': contact.get('description') or '',
                        'street': contact.get('address') or '',
                        'website': contact.get('website') or '',
                        'city': contact.get('city') or '',
                        'zip': contact.get('zip') or contact.get('postal_code') or '',
                        'is_company': False,
                        'sh_freshdesk_id': fd_id,
                        'sh_last_synced_from_fd': fields.Datetime.now(),
                    }

                    if partner:
                        partner.with_context(skip_freshdesk_sync=True).write(vals)
                        sync_log.create_log('res.partner', partner.id, 'success', json.dumps(contact), 'freshdesk_to_odoo', operation='update')
                    else:
                        new_partner = self.with_context(skip_freshdesk_sync=True).create(vals)
                        sync_log.create_log('res.partner', new_partner.id, 'success', json.dumps(contact), 'freshdesk_to_odoo', operation='create')

                    total_synced += 1
                    processed_in_batch += 1
                except Exception:
                    _logger.exception('Freshdesk contact sync failed on page %s for contact %s', page, contact.get('id'))
                    if not from_cron:
                        raise

            _logger.debug('total_synced so far: %s', total_synced)
            if from_cron and processed_in_batch:
                if not self.env['ir.cron']._commit_progress(processed=processed_in_batch):
                    break
            if sync_limit > 0 and total_synced >= sync_limit:
                break
            if len(contacts) < per_page:
                _logger.info('Page %s not full (%s/%s). Done.', page, len(contacts), per_page)
                break
            page += 1

        self.env.company.sudo().write({'sh_freshdesk_last_contact_sync_date': fields.Datetime.now()})
        return True

    def sync_companies_from_freshdesk(self, is_manual=False):
        """
        Sync companies from Freshdesk to Odoo.
        Logging is ALWAYS performed.
        """
        sync_log = self.env['sh.freshdesk.sync.log']
        page = 1
        per_page = 100
        company = self.env.company.sudo()
        sync_limit = company.sh_fd_company_sync_limit
        total_synced = 0
        from_cron = bool(self.env.context.get('cron_id'))
        _logger.info('Starting Freshdesk Company Sync (is_manual=%s, limit=%s)', is_manual, sync_limit)

        while True:
            params = self.with_context(fetch_companies=True)._prepare_updated_since_params(page=page, manual=is_manual)
            end_date = params.pop('sh_end_date', None)

            _logger.info('Fetching Freshdesk Companies - Page %s', page)
            try:
                response = self._freshdesk_request('GET', '/api/v2/companies', params=params)
            except Exception:
                _logger.error('Error fetching companies page %s: %s', page, traceback.format_exc())
                break

            if response.status_code != 200:
                _logger.error('Freshdesk API error (companies) Status %s: %s', response.status_code, response.text)
                break

            companies_data = response.json()
            if not companies_data or not isinstance(companies_data, list):
                _logger.info('No more companies on page %s. Sync done.', page)
                break

            _logger.info('Processing %s companies from page %s', len(companies_data), page)
            processed_in_batch = 0
            for co_data in companies_data:
                if sync_limit > 0 and total_synced >= sync_limit:
                    _logger.info('Sync limit %s reached. Stopping.', sync_limit)
                    break

                try:
                    if end_date:
                        fd_updated_at = co_data.get('updated_at')
                        if fd_updated_at and str(fd_updated_at) > str(end_date):
                            continue

                    fd_id = str(co_data.get('id'))
                    partner = self.search([('sh_freshdesk_id', '=', fd_id), ('is_company', '=', True)], limit=1)
                    if not partner:
                        partner = self.search([('sh_freshdesk_id', '=', fd_id)], limit=1)

                    vals = {
                        'name': co_data.get('name') or 'Freshdesk Company',
                        'comment': co_data.get('note') or co_data.get('description') or '',
                        'is_company': True,
                        'sh_freshdesk_id': fd_id,
                        'sh_last_synced_from_fd': fields.Datetime.now(),
                    }

                    if partner:
                        if not partner.is_company:
                            vals['is_company'] = True
                        partner.with_context(skip_freshdesk_sync=True).write(vals)
                        sync_log.create_log('res.partner', partner.id, 'success', json.dumps(co_data), 'freshdesk_to_odoo', operation='update')
                    else:
                        new_partner = self.with_context(skip_freshdesk_sync=True).create(vals)
                        sync_log.create_log('res.partner', new_partner.id, 'success', json.dumps(co_data), 'freshdesk_to_odoo', operation='create')

                    total_synced += 1
                    processed_in_batch += 1
                except Exception:
                    _logger.exception('Freshdesk company sync failed on page %s for company %s', page, co_data.get('id'))
                    if not from_cron:
                        raise

            if from_cron and processed_in_batch:
                if not self.env['ir.cron']._commit_progress(processed=processed_in_batch):
                    break
            if sync_limit > 0 and total_synced >= sync_limit:
                break
            if len(companies_data) < per_page:
                _logger.info('Page %s not full (%s/%s). Done.', page, len(companies_data), per_page)
                break
            page += 1

        self.env.company.sudo().write({'sh_freshdesk_last_company_sync_date': fields.Datetime.now()})
        return True

    @api.model
    def process_freshdesk_webhook(self, fd_data, operation, resource_type='contact'):
        sync_log = self.env['sh.freshdesk.sync.log']
        skip_webhook_log = self.env.context.get('sh_skip_webhook_log')
        is_company = resource_type == 'company' or self._is_webhook_company_payload(fd_data)
        fd_data = dict(fd_data or {})
        fd_id = self._get_webhook_contact_id(fd_data)
        if not fd_id:
            raise ValidationError(_("Freshdesk webhook payload does not contain an ID."))

        partner = self._find_existing_freshdesk_partner(fd_data, is_company=is_company)
        if is_company and not partner:
            fd_id_search = str(fd_data.get('id') or '').strip()
            if fd_id_search:
                partner = self.sudo().search([('sh_freshdesk_id', '=', fd_id_search)], limit=1)
        if is_company:
            vals = self._prepare_freshdesk_webhook_company_values(fd_data)
        else:
            vals = self._prepare_freshdesk_webhook_person_values(fd_data)
        vals['sh_freshdesk_id'] = fd_id
        vals['sh_last_synced_from_fd'] = fields.Datetime.now()

        if operation == 'delete':
            if partner:
                partner_id = partner.id
                partner.with_context(skip_freshdesk_sync=True).unlink()
                if not skip_webhook_log:
                    sync_log.create_log(
                        'res.partner',
                        partner_id,
                        'success',
                        json.dumps(fd_data),
                        'freshdesk_to_odoo',
                        operation='delete',
                    )
                return self.browse()
            if not skip_webhook_log:
                sync_log.create_log(
                    'res.partner',
                    False,
                    'success',
                    json.dumps({'payload': fd_data, 'result': 'delete skipped; record not found'}),
                    'freshdesk_to_odoo',
                    operation='delete',
                )
            return self.browse()

        if partner:
            self._write_freshdesk_partner_diff(partner, vals)
            logged_operation = 'update'
            record = partner
        else:
            record = self.with_context(skip_freshdesk_sync=True).create(vals)
            logged_operation = 'create'

        if not skip_webhook_log:
            sync_log.create_log(
                'res.partner',
                record.id,
                'success',
                json.dumps(fd_data),
                'freshdesk_to_odoo',
                operation=logged_operation,
            )
        return record
