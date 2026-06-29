# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import json
import logging
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class SHFreshdeskSyncQueue(models.Model):
    _name = 'sh.freshdesk.sync.queue'
    _description = 'Freshdesk Queue Logs'
    _order = 'create_date desc'
    _rec_name = 'res_model_id'

    res_model_id = fields.Many2one('ir.model', string='Model', required=True, readonly=True, ondelete='cascade')
    res_id = fields.Integer(string='Record ID', readonly=True)
    direction = fields.Selection([
        ('odoo_to_freshdesk', 'Odoo -> Freshdesk'),
        ('freshdesk_to_odoo', 'Freshdesk -> Odoo'),
    ], string='Direction', required=True, readonly=True)
    operation = fields.Selection([
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
    ], string='Operation', readonly=True)
    state = fields.Selection([
        ('draft', 'Pending'),
        ('done', 'Done'),
        ('failed', 'Failed'),
    ], string='Status', default='draft', required=True)
    error_message = fields.Text(string='Last Error Message', readonly=True)
    payload = fields.Text(string='Raw Payload', readonly=True)
    last_attempt_date = fields.Datetime(string='Last Attempt Date', readonly=True)
    company_id = fields.Many2one(
        'res.company',
        string='Company',
        default=lambda self: self.env.company,
        required=True,
        readonly=True,
    )
    partner_id = fields.Many2one('res.partner', string='Contact', compute='_compute_record_links', store=True)
    ticket_id = fields.Many2one('helpdesk.ticket', string='Ticket', compute='_compute_record_links', store=True)

    @api.depends('res_model_id', 'res_id')
    def _compute_record_links(self):
        for queue in self:
            partner = False
            ticket = False
            if queue.res_model_id and queue.res_id:
                if queue.res_model_id.model == 'res.partner':
                    partner = queue.res_id
                elif queue.res_model_id.model == 'helpdesk.ticket':
                    ticket = queue.res_id
            queue.partner_id = partner
            queue.ticket_id = ticket

    @api.model
    def add_to_queue(self, model_name, res_id, direction, operation=False, error_log=False, payload=False, company_id=False):
        """
        Creates a new queue entry or updates an existing pending/failed entry for the same record.
        """
        if not model_name and direction == 'freshdesk_to_odoo' and payload:
            try:
                payload_data = json.loads(payload)
                handler = self.env['sh.freshdesk.webhook.handler']
                resource_type, action, data = handler._normalize_payload(payload_data)
                if resource_type == 'ticket':
                    model_name = 'helpdesk.ticket'
                else:
                    model_name = 'res.partner'
                if not operation:
                    operation = action
            except Exception as e:
                _logger.warning("Failed to resolve model from payload in add_to_queue: %s", str(e))

        if not model_name:
            _logger.warning("Cannot add to queue: model_name is missing and could not be resolved.")
            return False

        model = self.env['ir.model'].sudo().search([('model', '=', model_name)], limit=1)
        if not model:
            _logger.warning("Cannot add to queue: ir.model entry not found for model_name %s.", model_name)
            return False

        from odoo.tools import html2plaintext
        clean_error = html2plaintext(error_log) if error_log else ''

        # Search for a pending/failed duplicate to update instead of creating a duplicate
        fd_payload_id = False
        if not res_id and payload:
            try:
                payload_data = json.loads(payload)
                handler = self.env['sh.freshdesk.webhook.handler']
                resource_type, action, data = handler._normalize_payload(payload_data)
                fd_payload_id = str(data.get('id'))
            except Exception:
                pass

        domain = [
            ('res_model_id', '=', model.id),
            ('direction', '=', direction),
            ('state', 'in', ['draft', 'failed']),
            ('company_id', '=', company_id or self.env.company.id),
        ]
        if res_id:
            domain.append(('res_id', '=', res_id))
        else:
            domain.append(('res_id', '=', False))

        existing = self.search(domain, limit=1)

        if not existing and fd_payload_id and not res_id:
            all_empty_res = self.search([
                ('res_model_id', '=', model.id),
                ('direction', '=', direction),
                ('state', 'in', ['draft', 'failed']),
                ('res_id', '=', False),
                ('company_id', '=', company_id or self.env.company.id),
            ])
            for item in all_empty_res:
                if item.payload:
                    try:
                        p_data = json.loads(item.payload)
                        handler = self.env['sh.freshdesk.webhook.handler']
                        _, _, d = handler._normalize_payload(p_data)
                        if str(d.get('id')) == fd_payload_id:
                            existing = item
                            break
                    except Exception:
                        pass

        if existing:
            existing.write({
                'operation': operation or existing.operation,
                'error_message': clean_error,
                'payload': payload or existing.payload,
                'state': 'draft',
            })
        else:
            self.create({
                'res_model_id': model.id,
                'res_id': res_id,
                'direction': direction,
                'operation': operation,
                'error_message': clean_error,
                'payload': payload,
                'state': 'draft',
                'company_id': company_id or self.env.company.id,
            })
        return True

    @api.model
    def process_queue(self, batch_size=100):
        """
        Cron job method to process pending/failed sync queue items in batches.
        """
        records = self.search([('state', 'in', ['draft', 'failed'])], limit=batch_size)
        if not records:
            _logger.info("Freshdesk Queue Logs: No records to process.")
            return True
        self.process_queue_records(records)
        return True

    def action_retry(self):
        """
        Manually retry selected queue items.
        """
        for record in self:
            record.write({'state': 'draft'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Queue Reset'),
                'message': _('Selected queue records have been reset to Pending.'),
                'sticky': False,
                'type': 'success',
            }
        }

    def process_queue_records(self, queue_records):
        """
        Processes a set of queue records, updating states and handling exceptions.
        """
        _logger.info("Freshdesk Queue Logs: Processing %s records...", len(queue_records))
        for queue_rec in queue_records:
            queue_rec.write({
                'last_attempt_date': fields.Datetime.now(),
            })
            self.env.cr.commit()

            model_name = queue_rec.res_model_id.model
            res_id = queue_rec.res_id

            try:
                ctx = {
                    'sh_bulk_sync': True,
                    'skip_freshdesk_sync': False,
                    'allowed_company_ids': queue_rec.company_id.ids,
                    'sh_from_queue_cron': True,
                }
                if queue_rec.operation == 'create':
                    ctx['sh_is_create'] = True
                elif queue_rec.operation == 'update':
                    ctx['sh_is_update'] = True
                elif queue_rec.operation == 'delete':
                    ctx['sh_is_delete'] = True

                start_time = fields.Datetime.now()

                if queue_rec.direction == 'odoo_to_freshdesk':
                    record = self.env[model_name].sudo().with_company(queue_rec.company_id).browse(res_id)
                    if not record.exists():
                        if queue_rec.operation == 'delete' and queue_rec.payload:
                            try:
                                delete_payload = json.loads(queue_rec.payload)
                            except Exception:
                                delete_payload = {}
                            fd_delete_id = str(delete_payload.get('id') or '').strip()
                            if fd_delete_id:
                                if model_name == 'res.partner':
                                    endpoint = '/api/v2/companies/%s' if delete_payload.get('is_company') else '/api/v2/contacts/%s'
                                else:
                                    endpoint = '/api/v2/tickets/%s'
                                delete_response = self.env[model_name].sudo().with_company(queue_rec.company_id)._freshdesk_request(
                                    'DELETE',
                                    endpoint % fd_delete_id,
                                    timeout=10,
                                )
                                if delete_response.status_code in (200, 202, 204):
                                    self.env['sh.freshdesk.sync.log'].create_log(
                                        model_name,
                                        res_id,
                                        'success',
                                        delete_response.text or 'Deleted successfully.',
                                        'odoo_to_freshdesk',
                                        operation='delete',
                                    )
                                    queue_rec.write({
                                        'state': 'done',
                                        'error_message': False,
                                    })
                                    self.env.cr.commit()
                                    continue
                                error_msg = self.env[model_name]._extract_error_message(delete_response)
                                raise ValidationError(_("Freshdesk Deletion Error: %s") % error_msg)
                        queue_rec.write({
                            'state': 'failed',
                            'error_message': _("Record ID %s in model %s does not exist in Odoo.") % (res_id, model_name)
                        })
                        self.env.cr.commit()
                        continue

                    record_ctx = record.with_context(ctx)
                    if model_name == 'res.partner':
                        if queue_rec.operation == 'delete':
                            record_ctx.action_delete_from_freshdesk()
                        else:
                            record_ctx.action_sync_to_freshdesk()
                    elif model_name == 'helpdesk.ticket':
                        if queue_rec.operation == 'delete':
                            record_ctx.action_delete_from_freshdesk()
                        else:
                            record_ctx.action_sync_ticket_to_freshdesk()

                elif queue_rec.direction == 'freshdesk_to_odoo':
                    if not res_id:
                        if queue_rec.payload:
                            payload_data = json.loads(queue_rec.payload)
                            if model_name == 'res.partner':
                                resource_type = 'company' if self.env['res.partner']._is_webhook_company_payload(payload_data) else 'contact'
                                result = self.env['res.partner'].with_company(queue_rec.company_id).with_context(
                                    sh_from_queue_cron=True,
                                    sh_webhook_direct=True,
                                ).process_freshdesk_webhook(
                                    payload_data,
                                    queue_rec.operation or 'update',
                                    resource_type=resource_type,
                                )
                            else:
                                result = self.env['sh.freshdesk.webhook.handler'].with_company(queue_rec.company_id).with_context(
                                    sh_from_queue_cron=True
                                ).process_payload(
                                    queue_rec.company_id, payload_data
                                )
                            record_id = False
                            if isinstance(result, dict):
                                if result.get('status') == 'success':
                                    record_id = result.get('record_id')
                            elif getattr(result, 'id', False):
                                record_id = result.id
                            if record_id:
                                queue_rec.write({'res_id': record_id})
                                res_id = record_id
                        else:
                            queue_rec.write({
                                'state': 'failed',
                                'error_message': _("No Odoo record ID and no payload data to retry sync.")
                            })
                            self.env.cr.commit()
                            continue
                    else:
                        record = self.env[model_name].sudo().with_company(queue_rec.company_id).browse(res_id)
                        if not record.exists():
                            queue_rec.write({
                                'state': 'failed',
                                'error_message': _("Odoo record ID %s in model %s does not exist.") % (res_id, model_name)
                            })
                            self.env.cr.commit()
                            continue

                        if not record.sh_freshdesk_id:
                            queue_rec.write({
                                'state': 'failed',
                                'error_message': _("Record exists but lacks Freshdesk ID.")
                            })
                            self.env.cr.commit()
                            continue

                        if model_name == 'res.partner':
                            endpoint = '/api/v2/companies/%s' if record.is_company else '/api/v2/contacts/%s'
                            response = record._freshdesk_request('GET', endpoint % record.sh_freshdesk_id, timeout=10)
                            if response.status_code == 200:
                                self.env['res.partner'].with_context(sh_from_queue_cron=True).process_freshdesk_webhook(
                                    response.json(),
                                    queue_rec.operation or 'update',
                                    'company' if record.is_company else 'contact'
                                )
                            else:
                                error_msg = record._extract_error_message(response)
                                raise ValidationError(_("Freshdesk API Error: %s") % error_msg)

                        elif model_name == 'helpdesk.ticket':
                            response = record._freshdesk_request('GET', '/api/v2/tickets/%s' % record.sh_freshdesk_id, timeout=10)
                            if response.status_code == 200:
                                self.env['helpdesk.ticket'].with_context(sh_from_queue_cron=True).process_freshdesk_webhook(
                                    response.json(),
                                    queue_rec.operation or 'update'
                                )
                            else:
                                error_msg = record._extract_error_message(response)
                                raise ValidationError(_("Freshdesk API Error: %s") % error_msg)

                # Query the latest log generated for this record during this execution
                log = self.env['sh.freshdesk.sync.log'].search([
                    ('res_model.model', '=', model_name),
                    ('res_id', '=', res_id),
                    ('direction', '=', queue_rec.direction),
                    ('operation', '=', queue_rec.operation),
                    ('sync_date', '>=', start_time),
                ], order='sync_date desc', limit=1)

                if log:
                    if log.status == 'success':
                        queue_rec.write({
                            'state': 'done',
                            'error_message': False,
                        })
                    else:
                        from odoo.tools import html2plaintext
                        queue_rec.write({
                            'state': 'failed',
                            'error_message': html2plaintext(log.error_log or log.message) or _("Sync failed."),
                        })
                else:
                    queue_rec.write({
                        'state': 'done',
                        'error_message': False,
                    })

            except Exception as e:
                _logger.exception("Failed to process queue record %s: %s", queue_rec.id, str(e))
                queue_rec.write({
                    'state': 'failed',
                    'error_message': str(e),
                })

            self.env.cr.commit()


class FreshdeskSyncLogInherit(models.Model):
    _inherit = 'sh.freshdesk.sync.log'

    @api.model
    def create_log(self, model_name, res_id, status, message, direction='odoo_to_freshdesk', error_log=False, operation=False):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron') and not self.env.context.get('sh_webhook_direct'):
            if status == 'error':
                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name=model_name,
                    res_id=res_id,
                    direction=direction,
                    operation=operation,
                    error_log=error_log,
                    payload=message if direction == 'freshdesk_to_odoo' else False,
                    company_id=company.id,
                )
            return False

        res = super(FreshdeskSyncLogInherit, self).create_log(
            model_name=model_name,
            res_id=res_id,
            status=status,
            message=message,
            direction=direction,
            error_log=error_log,
            operation=operation
        )
        return res


class ResCompany(models.Model):
    _inherit = 'res.company'

    sh_enable_sync_queue = fields.Boolean(string='Enable Queue Logs', default=False)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    sh_enable_sync_queue = fields.Boolean(related='company_id.sh_enable_sync_queue', readonly=False)

    def _update_cron_from_settings(self):
        super(ResConfigSettings, self)._update_cron_from_settings()
        self.ensure_one()
        queue_cron = self.env.ref('sh_freshdesk_connector.ir_cron_sh_fd_sync_queue_process', raise_if_not_found=False)
        if queue_cron:
            queue_cron.write({
                'active': bool(self.sh_enable_sync_queue),
            })


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def action_sync_to_freshdesk(self):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            for partner in self:
                op = 'update' if partner.sh_freshdesk_id else 'create'
                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='res.partner',
                    res_id=partner.id,
                    direction='odoo_to_freshdesk',
                    operation=op,
                )
            if self.env.context.get('sh_is_sync_button'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Added to Queue'),
                        'message': _('Records have been added to the sync queue.'),
                        'sticky': False,
                        'type': 'info',
                    }
                }
            return True
        return super(ResPartner, self).action_sync_to_freshdesk()

    def action_delete_from_freshdesk(self):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            for record in self:
                payload = json.dumps({
                    'id': record.sh_freshdesk_id,
                    'name': record.name,
                    'is_company': bool(record.is_company),
                    'model': 'res.partner',
                })
                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='res.partner',
                    res_id=record.id,
                    direction='odoo_to_freshdesk',
                    operation='delete',
                    payload=payload,
                )
            if self.env.context.get('sh_is_sync_button'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Added to Queue'),
                        'message': _('Records have been added to the sync queue for deletion.'),
                        'sticky': False,
                        'type': 'info',
                    }
                }
            return True
        return super(ResPartner, self).action_delete_from_freshdesk()

    @api.model
    def sync_partners_from_freshdesk(self, is_manual=False):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            self._queue_partners_from_freshdesk(is_manual=is_manual)
            return True
        return super(ResPartner, self).sync_partners_from_freshdesk(is_manual=is_manual)

    def _queue_partners_from_freshdesk(self, is_manual=False):
        page = 1
        per_page = 100
        company = self.env.company.sudo()
        sync_limit = company.sh_fd_contact_sync_limit
        total_synced = 0

        while True:
            params = self._prepare_updated_since_params(page=page, manual=is_manual)
            end_date = params.pop('sh_end_date', None)

            try:
                response = self._freshdesk_request('GET', '/api/v2/contacts', params=params)
            except Exception:
                break

            if response.status_code != 200:
                break

            contacts = response.json()
            if not contacts or not isinstance(contacts, list):
                break

            for contact in contacts:
                if sync_limit > 0 and total_synced >= sync_limit:
                    break

                if end_date:
                    fd_updated_at = contact.get('updated_at')
                    if fd_updated_at and str(fd_updated_at) > str(end_date):
                        continue

                fd_id = str(contact.get('id'))
                email = contact.get('email')

                partner = self.search([('sh_freshdesk_id', '=', fd_id), ('is_company', '=', False)], limit=1)
                if not partner and email:
                    partner = self.search([('email', '=', email), ('is_company', '=', False)], limit=1)

                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='res.partner',
                    res_id=partner.id if partner else False,
                    direction='freshdesk_to_odoo',
                    operation='update' if partner else 'create',
                    payload=json.dumps(contact),
                    company_id=company.id,
                )
                total_synced += 1

            if sync_limit > 0 and total_synced >= sync_limit:
                break
            if len(contacts) < per_page:
                break
            page += 1

        self.env.company.sudo().write({'sh_freshdesk_last_contact_sync_date': fields.Datetime.now()})
        return True

    @api.model
    def sync_companies_from_freshdesk(self, is_manual=False):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            self._queue_companies_from_freshdesk(is_manual=is_manual)
            return True
        return super(ResPartner, self).sync_companies_from_freshdesk(is_manual=is_manual)

    def _queue_companies_from_freshdesk(self, is_manual=False):
        page = 1
        per_page = 100
        company = self.env.company.sudo()
        sync_limit = company.sh_fd_company_sync_limit
        total_synced = 0

        while True:
            params = self.with_context(fetch_companies=True)._prepare_updated_since_params(page=page, manual=is_manual)
            end_date = params.pop('sh_end_date', None)

            try:
                response = self._freshdesk_request('GET', '/api/v2/companies', params=params)
            except Exception:
                break

            if response.status_code != 200:
                break

            companies_data = response.json()
            if not companies_data or not isinstance(companies_data, list):
                break

            for co_data in companies_data:
                if sync_limit > 0 and total_synced >= sync_limit:
                    break

                if end_date:
                    fd_updated_at = co_data.get('updated_at')
                    if fd_updated_at and str(fd_updated_at) > str(end_date):
                        continue

                fd_id = str(co_data.get('id'))
                partner = self.search([('sh_freshdesk_id', '=', fd_id), ('is_company', '=', True)], limit=1)

                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='res.partner',
                    res_id=partner.id if partner else False,
                    direction='freshdesk_to_odoo',
                    operation='update' if partner else 'create',
                    payload=json.dumps(co_data),
                    company_id=company.id,
                )
                total_synced += 1

            if sync_limit > 0 and total_synced >= sync_limit:
                break
            if len(companies_data) < per_page:
                break
            page += 1

        self.env.company.sudo().write({'sh_freshdesk_last_company_sync_date': fields.Datetime.now()})
        return True


class HelpdeskTicket(models.Model):
    _inherit = 'helpdesk.ticket'

    def action_sync_ticket_to_freshdesk(self, is_manual=False):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            for ticket in self:
                op = 'update' if ticket.sh_freshdesk_id else 'create'
                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='helpdesk.ticket',
                    res_id=ticket.id,
                    direction='odoo_to_freshdesk',
                    operation=op,
                )
            if self.env.context.get('sh_is_sync_button'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Added to Queue'),
                        'message': _('Tickets have been added to the sync queue.'),
                        'sticky': False,
                        'type': 'info',
                    }
                }
            return True
        return super(HelpdeskTicket, self).action_sync_ticket_to_freshdesk(is_manual=is_manual)

    def action_delete_from_freshdesk(self):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            for record in self:
                payload = json.dumps({
                    'id': record.sh_freshdesk_id,
                    'name': record.name,
                    'model': 'helpdesk.ticket',
                })
                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='helpdesk.ticket',
                    res_id=record.id,
                    direction='odoo_to_freshdesk',
                    operation='delete',
                    payload=payload,
                )
            if self.env.context.get('sh_is_sync_button'):
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Added to Queue'),
                        'message': _('Tickets have been added to the sync queue for deletion.'),
                        'sticky': False,
                        'type': 'info',
                    }
                }
            return True
        return super(HelpdeskTicket, self).action_delete_from_freshdesk()

    @api.model
    def sync_tickets_from_freshdesk(self, is_manual=False):
        company = self.env.company
        if company.sh_enable_sync_queue and not self.env.context.get('sh_from_queue_cron'):
            self._queue_tickets_from_freshdesk(is_manual=is_manual)
            return True
        return super(HelpdeskTicket, self).sync_tickets_from_freshdesk(is_manual=is_manual)

    def _queue_tickets_from_freshdesk(self, is_manual=False):
        page = 1
        per_page = 100
        company = self.env.company.sudo()
        sync_limit = company.sh_fd_ticket_sync_limit
        total_synced = 0

        while True:
            params = self._prepare_updated_since_params(page=page, manual=is_manual)
            end_date = params.pop('sh_end_date', None)
            params['include'] = 'description'

            try:
                response = self._freshdesk_request('GET', '/api/v2/tickets', params=params)
            except Exception:
                break

            if response.status_code != 200:
                break

            fd_tickets = response.json()
            if not fd_tickets or not isinstance(fd_tickets, list):
                break

            for fd_ticket in fd_tickets:
                if sync_limit > 0 and total_synced >= sync_limit:
                    break

                if end_date:
                    fd_updated_at = fd_ticket.get('updated_at')
                    if fd_updated_at and str(fd_updated_at) > str(end_date):
                        continue

                fd_id = str(fd_ticket.get('id'))
                ticket = self.search([('sh_freshdesk_id', '=', fd_id)], limit=1)

                self.env['sh.freshdesk.sync.queue'].sudo().add_to_queue(
                    model_name='helpdesk.ticket',
                    res_id=ticket.id if ticket else False,
                    direction='freshdesk_to_odoo',
                    operation='update' if ticket else 'create',
                    payload=json.dumps(fd_ticket),
                    company_id=company.id,
                )
                total_synced += 1

            if sync_limit > 0 and total_synced >= sync_limit:
                break
            if len(fd_tickets) < per_page:
                break
            page += 1

        self.env.company.sudo().write({'sh_freshdesk_last_ticket_sync_date': fields.Datetime.now()})
        return True
