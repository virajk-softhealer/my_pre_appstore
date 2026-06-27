# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging
import requests
from datetime import datetime, timezone
from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

class ShActiveCampaignDashboard(models.Model):
    _name = 'sh.active.campaign.dashboard'
    _description = 'ActiveCampaign Sync Dashboard'
    _rec_name = 'sh_company_id'

    sh_company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company
    )

    # --- Contacts ---
    sh_sync_contacts_import = fields.Boolean(
        string='Sync Contacts From AC to Odoo'
    )
    sh_sync_contacts_export = fields.Boolean(
        string='Sync Contacts From Odoo to AC'
    )

    # --- List Contacts ---
    sh_sync_list_contacts_import = fields.Boolean(
        string='Sync List Contacts From AC to Odoo'
    )

    # --- Lists / Campaigns ---
    sh_sync_lists_import = fields.Boolean(
        string='Sync Lists/Campaigns From AC to Odoo'
    )

    # --- Tags ---
    sh_sync_tags_import = fields.Boolean(
        string='Sync Tags From AC to Odoo'
    )
    sh_sync_tags_export = fields.Boolean(
        string='Sync Tags From Odoo to AC'
    )

    # --- Deals ---
    sh_sync_deals_import = fields.Boolean(
        string='Sync Deals From AC to Odoo'
    )
    sh_sync_deals_export = fields.Boolean(
        string='Sync Deals From Odoo to AC'
    )

    # --- Forms ---
    sh_sync_forms_import = fields.Boolean(
        string='Sync Forms From AC to Odoo'
    )

    # --- Date Range & Scheduler ---
    sh_date_range = fields.Boolean(string='Date Range')
    sh_date_from = fields.Datetime(string='From')
    sh_date_to = fields.Datetime(string='To')
    sh_auto_scheduler = fields.Boolean(string='Auto Scheduler')
    sh_ac_enable_queue_processing = fields.Boolean(string='Enable Queue Processing')

    def _is_queue_processing_enabled(self):
        """Return True when ActiveCampaign sync requests should be queued."""
        self.ensure_one()
        return bool(self.sh_ac_enable_queue_processing) and not self.env.context.get('sh_from_queue_cron')

    def _queue_sync_job(self, sync_key):
        """Create or refresh a queued sync job for the current dashboard."""
        self.env['sh.active.campaign.sync.queue'].sudo().add_to_queue(
            sync_key=sync_key,
            dashboard_id=self.id,
            payload=self._get_queue_payload_snapshot(),
            company_id=self.sh_company_id.id,
        )

    def _get_queue_payload_snapshot(self):
        """Snapshot the current sync window so queued jobs reuse the same filter."""
        self.ensure_one()
        return {
            'sh_date_range': bool(self.sh_date_range),
            'sh_date_from': fields.Datetime.to_string(self.sh_date_from) if self.sh_date_from else False,
            'sh_date_to': fields.Datetime.to_string(self.sh_date_to) if self.sh_date_to else False,
        }

    def _get_sync_date_window(self):
        """Return the effective date range for direct or queued sync processing."""
        self.ensure_one()
        queue_snapshot = self.env.context.get('sh_queue_snapshot') or {}
        if queue_snapshot.get('sh_date_range'):
            return (
                True,
                fields.Datetime.from_string(queue_snapshot['sh_date_from']) if queue_snapshot.get('sh_date_from') else False,
                fields.Datetime.from_string(queue_snapshot['sh_date_to']) if queue_snapshot.get('sh_date_to') else False,
                True,
            )
        return self.sh_date_range, self.sh_date_from, self.sh_date_to, False

    @staticmethod
    def _parse_active_campaign_datetime(value):
        """Convert an ActiveCampaign timestamp to a naive UTC datetime."""
        if not value:
            return False
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip().replace('Z', '+00:00')
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return False
        if dt.tzinfo:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def _is_datetime_in_sync_window(record_dt, date_from, date_to):
        """Return True when a datetime falls inside the selected sync window."""
        if not record_dt:
            return False
        if date_from and record_dt < date_from:
            return False
        if date_to and record_dt > date_to:
            return False
        return True

    def _get_active_campaign_list_datetime(self, record):
        """Extract the best available timestamp from an ActiveCampaign list payload."""
        for key in ('udate', 'updated_at', 'updatedAt', 'cdate', 'created_at', 'createdAt'):
            record_dt = self._parse_active_campaign_datetime(record.get(key))
            if record_dt:
                return record_dt
        return False

    def _is_ac_record_in_date_window(self, record, date_from, date_to, candidate_keys):
        """Return True when an AC record falls inside the selected sync window."""
        if not (date_from or date_to):
            return True
        for key in candidate_keys:
            record_dt = self._parse_active_campaign_datetime(record.get(key))
            if record_dt:
                return self._is_datetime_in_sync_window(record_dt, date_from, date_to)
        # Preserve existing flow if AC does not expose a usable timestamp.
        return True

    def _run_queued_sync_job(self, sync_key):
        """Dispatch a queued ActiveCampaign sync job to the matching private method."""
        self.ensure_one()
        job_map = {
            'contacts_import': self._sync_contacts_from_ac,
            'contacts_export': self._export_contacts_to_ac,
            'list_contacts_import': self._sync_list_contacts_from_ac,
            'lists_import': self._sync_lists_from_ac,
            'tags_import': self._sync_tags_from_ac,
            'tags_export': self._export_tags_to_ac,
            'deals_import': self._sync_deals_from_ac,
            'deals_export': self._export_deals_to_ac,
            'forms_import': self._sync_forms_from_ac,
        }
        method = job_map.get(sync_key)
        if not method:
            raise UserError(_('Unsupported ActiveCampaign queue job: %s') % sync_key)
        return method()

    def action_run_sync(self):
        """Dispatch sync operations based on selected entity checkboxes."""
        self.ensure_one()
        company = self.sh_company_id
        if not company.sh_ac_configuration or not company.sh_ac_url or not company.sh_ac_api_key:
            raise UserError(_('Please configure ActiveCampaign credentials in Settings first.'))

        sync_direction = self.env.context.get('sync_direction')
        client = self._get_ac_client(company)
        if not self._is_queue_processing_enabled():
            self._cleanup_stale_active_campaign_records(client)

        # Core logic for each sync will be implemented in following phases
        if sync_direction != 'export' and self.sh_sync_tags_import:
            self._sync_tags_from_ac()
        if sync_direction != 'export' and self.sh_sync_contacts_import:
            self._sync_contacts_from_ac()
        if sync_direction != 'export' and self.sh_sync_lists_import:
            self._sync_lists_from_ac()
        if sync_direction != 'export' and self.sh_sync_list_contacts_import:
            self._sync_list_contacts_from_ac()
        if sync_direction != 'export' and self.sh_sync_deals_import:
            self._sync_deals_from_ac()
        if sync_direction != 'export' and self.sh_sync_forms_import:
            self._sync_forms_from_ac()
        if sync_direction != 'import' and self.sh_sync_contacts_export:
            self._export_contacts_to_ac()
        if sync_direction != 'import' and self.sh_sync_tags_export:
            self._export_tags_to_ac()
        if sync_direction != 'import' and self.sh_sync_deals_export:
            self._export_deals_to_ac()


    def _cleanup_stale_active_campaign_records(self, client):
        """Cleanup stale contact, deal, tag, list, form, and campaign IDs in Odoo that have been deleted in ActiveCampaign."""
        company = self.sh_company_id
        
        # 1. Cleanup stale Contacts/Partners
        try:
            active_contact_ids = set()
            offset, limit = 0, 100
            while True:
                data = client.get('/api/3/contacts', params={'limit': limit, 'offset': offset})
                contacts = data.get('contacts', [])
                if not contacts:
                    break
                for c in contacts:
                    active_contact_ids.add(str(c['id']))
                offset += limit
            
            stale_partners = self.env['res.partner'].search([
                ('sh_ac_contact_id', '!=', False),
                '|', ('company_id', '=', company.id), ('company_id', '=', False)
            ])
            for partner in stale_partners:
                if partner.sh_ac_contact_id not in active_contact_ids:
                    partner.sudo().write({
                        'sh_ac_contact_id': False,
                    })
        except Exception as e:
            _logger.error('Failed to cleanup stale contacts: %s', str(e))

        # 2. Cleanup stale Deals/Leads
        try:
            active_deal_ids = set()
            offset, limit = 0, 100
            while True:
                data = client.get('/api/3/deals', params={'limit': limit, 'offset': offset})
                deals = data.get('deals', [])
                if not deals:
                    break
                for d in deals:
                    active_deal_ids.add(str(d['id']))
                offset += limit
            
            stale_leads = self.env['crm.lead'].search([
                ('sh_ac_deal_id', '!=', False),
                '|', ('company_id', '=', company.id), ('company_id', '=', False)
            ])
            for lead in stale_leads:
                if lead.sh_ac_deal_id not in active_deal_ids:
                    lead.write({
                        'sh_ac_deal_id': False,
                        'sh_is_ac_lead': False
                    })
        except Exception as e:
            _logger.error('Failed to cleanup stale deals: %s', str(e))

        # 3. Cleanup stale Tags/Categories
        try:
            active_tag_ids = set()
            offset, limit = 0, 100
            while True:
                data = client.get('/api/3/tags', params={'limit': limit, 'offset': offset})
                tags = data.get('tags', [])
                if not tags:
                    break
                for t in tags:
                    active_tag_ids.add(str(t['id']))
                offset += limit
            
            stale_categories = self.env['res.partner.category'].search([
                ('sh_ac_tag_id', '!=', False)
            ])
            for category in stale_categories:
                if category.sh_ac_tag_id not in active_tag_ids:
                    category.write({
                        'sh_ac_tag_id': False
                    })
        except Exception as e:
            _logger.error('Failed to cleanup stale tags: %s', str(e))

        # 4. Cleanup stale Lists
        try:
            active_list_ids = set()
            offset, limit = 0, 100
            while True:
                data = client.get('/api/3/lists', params={'limit': limit, 'offset': offset})
                lists = data.get('lists', [])
                if not lists:
                    break
                for l in lists:
                    active_list_ids.add(str(l['id']))
                offset += limit
            
            stale_lists = self.env['mailing.list'].search([
                ('sh_ac_list_id', '!=', False)
            ])
            for lst in stale_lists:
                if lst.sh_ac_list_id not in active_list_ids:
                    lst.write({
                        'sh_ac_list_id': False
                    })
        except Exception as e:
            _logger.error('Failed to cleanup stale lists: %s', str(e))

        # 5. Cleanup stale Forms
        try:
            active_form_ids = set()
            offset, limit = 0, 100
            while True:
                data = client.get('/api/3/forms', params={'limit': limit, 'offset': offset})
                forms = data.get('forms', [])
                if not forms:
                    break
                for f in forms:
                    active_form_ids.add(str(f['id']))
                offset += limit
            
            stale_forms = self.env['sh.active.campaign.form'].search([
                ('sh_ac_form_id', '!=', False),
                '|', ('sh_company_id', '=', company.id), ('sh_company_id', '=', False)
            ])
            for form in stale_forms:
                if form.sh_ac_form_id not in active_form_ids:
                    form.write({
                        'sh_ac_form_id': False
                    })
        except Exception as e:
            _logger.error('Failed to cleanup stale forms: %s', str(e))

        # 6. Cleanup stale Campaigns
        try:
            active_campaign_ids = set()
            offset, limit = 0, 100
            while True:
                data = client.get('/api/3/campaigns', params={'limit': limit, 'offset': offset})
                campaigns = data.get('campaigns', [])
                if not campaigns:
                    break
                for cp in campaigns:
                    active_campaign_ids.add(str(cp['id']))
                offset += limit
            
            stale_campaigns = self.env['sh.active.campaign.campaign'].search([
                ('sh_ac_campaign_id', '!=', False),
                '|', ('sh_company_id', '=', company.id), ('sh_company_id', '=', False)
            ])
            for camp in stale_campaigns:
                if camp.sh_ac_campaign_id not in active_campaign_ids:
                    camp.write({
                        'sh_ac_campaign_id': False
                    })
        except Exception as e:
            _logger.error('Failed to cleanup stale campaigns: %s', str(e))

    def action_save_configuration(self):
        """Save config and activate/deactivate cron based on auto scheduler."""
        self.ensure_one()
        if self.sh_company_id.sh_ac_configuration and not self.sh_company_id.sh_ac_access_token:
            self.sh_company_id.sh_ac_access_token = self.sh_company_id._sh_ac_generate_token()
        import_cron = self.env.ref('sh_active_campaign.sh_ac_cron_import', raise_if_not_found=False)
        export_cron = self.env.ref('sh_active_campaign.sh_ac_cron_export', raise_if_not_found=False)
        if self.sh_auto_scheduler:
            if import_cron:
                import_cron.active = True
            if export_cron:
                export_cron.active = True
        else:
            if import_cron:
                import_cron.active = False
            if export_cron:
                export_cron.active = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _('Configuration saved!'),
                'type': 'success',
                'sticky': False,
            }
        }

    # API Engine methods to be added here
    def _get_ac_client(self, company):
        """Return a simple API client wrapper for ActiveCampaign V3."""
        import requests

        class ACClient:
            def __init__(self, base_url, api_key):
                self.base_url = base_url.rstrip('/')
                self.headers = {
                    'Api-Token': api_key,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                }

            def get(self, endpoint, params=None):
                url = self.base_url + endpoint
                resp = requests.get(url, headers=self.headers, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()

            def post(self, endpoint, json=None):
                url = self.base_url + endpoint
                resp = requests.post(url, headers=self.headers, json=json, timeout=30)
                resp.raise_for_status()
                return resp.json()

            def put(self, endpoint, json=None):
                url = self.base_url + endpoint
                resp = requests.put(url, headers=self.headers, json=json, timeout=30)
                resp.raise_for_status()
                return resp.json()

            def delete(self, endpoint, params=None):
                url = self.base_url + endpoint
                resp = requests.delete(url, headers=self.headers, params=params, timeout=30)
                resp.raise_for_status()
                if resp.text:
                    return resp.json()
                return {}

        if not company.sh_ac_configuration or not company.sh_ac_url or not company.sh_ac_api_key:
            raise UserError(_('ActiveCampaign is not configured for company: %s') % company.name)
        return ACClient(company.sh_ac_url, company.sh_ac_api_key)

    # Sync methods skeletons
    def _sync_tags_from_ac(self):
        """Import all tags from ActiveCampaign into res.partner.category."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('tags_import')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        offset, limit = 0, 100
        processed = 0
        failed = 0
        use_date_range, date_from, date_to, _from_queue_snapshot = self._get_sync_date_window()
        date_params = {}
        if use_date_range:
            if date_from:
                date_params['filters[updated_after]'] = date_from.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            if date_to:
                date_params['filters[updated_before]'] = date_to.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        
        try:
            while True:
                params = {'limit': limit, 'offset': offset, **date_params}
                data = client.get('/api/3/tags', params=params)
                tags = data.get('tags', [])
                if use_date_range:
                    tags = [
                        tag for tag in tags
                        if self._is_ac_record_in_date_window(
                            tag,
                            date_from,
                            date_to,
                            ('udate', 'updated_at', 'updatedAt', 'cdate', 'created_at', 'createdAt'),
                        )
                    ]
                if not tags:
                    break
                
                for tag in tags:
                    try:
                        tag_id = str(tag['id'])
                        tag_name = tag.get('tag', '')
                        existing = self.env['res.partner.category'].search(
                            [('sh_ac_tag_id', '=', tag_id)], 
                            limit=1
                        )
                        if not existing and tag_name:
                            existing = self.env['res.partner.category'].search(
                                [('name', '=ilike', tag_name)],
                                limit=1
                            )
                        vals = {
                            'name': tag_name,
                            'sh_ac_tag_id': tag_id,
                        }
                        if existing:
                            existing.with_context(sh_skip_ac_tag_sync=True).write(vals)
                        else:
                            self.env['res.partner.category'].with_context(sh_skip_ac_tag_sync=True).create(vals)
                        processed += 1
                    except Exception as e:
                        failed += 1
                        _logger.error('Failed to sync AC tag %s: %s', tag.get('id'), str(e))
                
                self.env.cr.commit()
                offset += limit
                
            status = 'success' if failed == 0 else ('partial' if processed > 0 else 'failed')
            message = _('Successfully synced %s tags.') % processed
            
        except Exception as e:
            status = 'failed'
            message = _('Sync failed: %s') % str(e)

        self.env['sh.active.campaign.log'].create({
            'sh_entity': 'tag',
            'sh_operation': 'import',
            'sh_status': status,
            'sh_records_processed': processed,
            'sh_records_failed': failed,
            'sh_message': message,
            'sh_company_id': company.id,
        })

    def _get_or_create_ac_tag(self, client, category):
        """Find an existing ActiveCampaign tag by name/ID, or create it if missing."""
        if category.sh_ac_tag_id:
            return category.sh_ac_tag_id
        
        payload = {
            'tag': {
                'tag': category.name,
                'tagType': 'contact',
                'description': 'Exported from Odoo'
            }
        }
        try:
            resp = client.post('/api/3/tags', json=payload)
            if resp.get('tag', {}).get('id'):
                # sudo is required to write back external tag IDs to res.partner.category
                category.with_context(sh_skip_ac_tag_sync=True).sudo().write({'sh_ac_tag_id': str(resp['tag']['id'])})
                return category.sh_ac_tag_id
        except Exception as e:
            # If tag already exists in AC, search for it
            try:
                search_resp = client.get('/api/3/tags', params={'search': category.name})
                for t in search_resp.get('tags', []):
                    if t.get('tag', '').strip().lower() == category.name.strip().lower():
                        # sudo is required to write back external tag IDs to res.partner.category
                        category.with_context(sh_skip_ac_tag_sync=True).sudo().write({'sh_ac_tag_id': str(t['id'])})
                        return category.sh_ac_tag_id
            except Exception as ex:
                _logger.error('Failed to search or create AC tag %s: %s', category.name, str(ex))
        return None

    def _get_or_create_odoo_tag(self, client, ac_tag_id):
        """Find or create an Odoo res.partner.category tag representing a given AC tag ID."""
        # sudo is required to search res.partner.category for sync mapping
        existing = self.env['res.partner.category'].sudo().search([('sh_ac_tag_id', '=', str(ac_tag_id))], limit=1)
        if existing:
            return existing
        
        try:
            data = client.get(f'/api/3/tags/{ac_tag_id}')
            if data and 'tag' in data:
                tag_name = data['tag'].get('tag')
                if tag_name:
                    # Check if there is an Odoo tag with the same name first to avoid duplicates
                    # sudo is required to search res.partner.category
                    odoo_tag = self.env['res.partner.category'].sudo().search([('name', '=ilike', tag_name)], limit=1)
                    if odoo_tag:
                        # sudo is required to write the mapping back to res.partner.category
                        odoo_tag.sudo().write({'sh_ac_tag_id': str(ac_tag_id)})
                        return odoo_tag
                    else:
                        # sudo is required to create a new res.partner.category tag
                        return self.env['res.partner.category'].sudo().create({
                            'name': tag_name,
                            'sh_ac_tag_id': str(ac_tag_id)
                        })
        except Exception as e:
            _logger.error('Failed to fetch tag details for tag ID %s from AC: %s', ac_tag_id, str(e))
        return self.env['res.partner.category']

    def _sync_contact_tags_to_ac(self, client, partner):
        """Synchronize tags from Odoo contact to ActiveCampaign contact."""
        if not partner.sh_ac_contact_id:
            return
        
        try:
            # 1. Fetch current tags associated with the contact in AC
            data = client.get(f'/api/3/contacts/{partner.sh_ac_contact_id}/contactTags', params={'limit': 100})
            ac_contact_tags = [
                item for item in data.get('contactTags', [])
                if isinstance(item, dict) and item.get('id')
            ]
        except Exception as e:
            _logger.error('Failed to fetch contact tags for AC contact %s: %s', partner.sh_ac_contact_id, str(e))
            ac_contact_tags = []

        # 2. Add Odoo tags to AC if they are missing and collect the desired AC tag IDs.
        desired_ac_tag_ids = set()
        for category in partner.category_id:
            ac_tag_id = self._get_or_create_ac_tag(client, category)
            if ac_tag_id:
                desired_ac_tag_ids.add(str(ac_tag_id))
            if ac_tag_id and all(str(item.get('tag')) != str(ac_tag_id) for item in ac_contact_tags):
                try:
                    payload = {
                        'contactTag': {
                            'contact': partner.sh_ac_contact_id,
                            'tag': ac_tag_id
                        }
                    }
                    client.post('/api/3/contactTags', json=payload)
                except Exception as e:
                    _logger.error('Failed to add tag %s to AC contact %s: %s', ac_tag_id, partner.sh_ac_contact_id, str(e))

        # 3. Remove AC tags that are no longer present on the Odoo contact.
        for item in ac_contact_tags:
            ac_tag_id = str(item.get('tag') or '')
            if ac_tag_id and ac_tag_id not in desired_ac_tag_ids:
                try:
                    client.delete('/api/3/contactTags/%s' % item['id'])
                except Exception as e:
                    _logger.error(
                        'Failed to remove tag %s from AC contact %s: %s',
                        ac_tag_id,
                        partner.sh_ac_contact_id,
                        str(e),
                    )

    def _sync_contact_tags_from_ac(self, client, partner):
        """Synchronize tags from ActiveCampaign contact to Odoo contact."""
        if not partner.sh_ac_contact_id:
            return
        
        try:
            # 1. Fetch current tags associated with the contact in AC
            data = client.get(f'/api/3/contacts/{partner.sh_ac_contact_id}/contactTags', params={'limit': 100})
            ac_tags = data.get('contactTags', [])
        except Exception as e:
            _logger.error('Failed to fetch contact tags for AC contact %s: %s', partner.sh_ac_contact_id, str(e))
            ac_tags = []

        # 2. Map AC tag IDs to Odoo tag records
        odoo_tag_ids = []
        for item in ac_tags:
            ac_tag_id = item.get('tag')
            if ac_tag_id:
                odoo_tag = self._get_or_create_odoo_tag(client, ac_tag_id)
                if odoo_tag:
                    odoo_tag_ids.append(odoo_tag.id)

        commands = []
        if odoo_tag_ids:
            # Add missing ActiveCampaign-linked tags without touching local-only tags.
            for tag_id in odoo_tag_ids:
                if tag_id not in partner.category_id.ids:
                    commands.append((4, tag_id))

        # Remove tags that no longer exist in ActiveCampaign, but only for AC-linked categories.
        current_ac_categories = partner.category_id.filtered(lambda cat: cat.sh_ac_tag_id)
        ac_tag_id_set = {str(item.get('tag')) for item in ac_tags if item.get('tag')}
        for category in current_ac_categories:
            if category.sh_ac_tag_id not in ac_tag_id_set:
                commands.append((3, category.id))

        if commands:
            # sudo is required to modify res.partner category_id field
            partner.sudo().write({'category_id': commands})

    def _sync_contacts_from_ac(self):
        """Import contacts from AC into res.partner (batch 100, commit per batch)."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('contacts_import')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        offset, limit = 0, 100
        processed = 0
        failed = 0
        
        is_cron = self.env.context.get('is_cron')
        use_date_range, date_from, date_to, from_queue_snapshot = self._get_sync_date_window()
        date_params = {}
        if use_date_range and date_from and (not is_cron or from_queue_snapshot):
            date_params['filters[updated_after]'] = date_from.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        else:
            last_log = self.env['sh.active.campaign.log'].search([
                ('sh_entity', '=', 'contact'),
                ('sh_operation', '=', 'import'),
                ('sh_status', '=', 'success'),
                ('sh_company_id', '=', company.id)
            ], limit=1, order='create_date desc')
            if last_log:
                date_params['filters[updated_after]'] = last_log.create_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            
        if use_date_range and date_to and (not is_cron or from_queue_snapshot):
            date_params['filters[updated_before]'] = date_to.strftime('%Y-%m-%dT%H:%M:%S+00:00')

        error_details = []
        try:
            while True:
                params = {'limit': limit, 'offset': offset, **date_params}
                data = client.get('/api/3/contacts', params=params)
                contacts = data.get('contacts', [])
                if not contacts:
                    break
                
                for contact in contacts:
                    try:
                        email = contact.get('email', '')
                        contact_id = str(contact['id'])
                        
                        # Search by AC ID first, then Email
                        existing = self.env['res.partner'].search([('sh_ac_contact_id', '=', contact_id)], limit=1)
                        if not existing and email:
                            existing = self.env['res.partner'].search([('email', '=', email)], limit=1)
                            
                        vals = {
                            'name': (contact.get('firstName', '') + ' ' + contact.get('lastName', '')).strip() or email or 'AC Contact ' + contact_id,
                            'email': email,
                            'phone': contact.get('phone', ''),
                            'sh_ac_contact_id': contact_id,
                        }
                        if existing:
                            existing.write(vals)
                        else:
                            existing = self.env['res.partner'].create(vals)
                        self._sync_contact_tags_from_ac(client, existing)
                        processed += 1
                    except Exception as e:
                        failed += 1
                        reason = str(e)
                        contact_info = (contact.get('firstName', '') + ' ' + contact.get('lastName', '')).strip() or contact.get('email') or contact.get('id') or 'Unknown'
                        timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        error_details.append(f"[{timestamp}] Contact '{contact_info}' was skipped during AC → Odoo sync because: {reason}")
                        _logger.error('Failed to sync AC contact %s: %s', contact.get('id'), str(e))
                
                self.env.cr.commit()
                offset += limit
                
            status = 'success' if failed == 0 else ('partial' if processed > 0 else 'failed')
            message = _('Successfully synced %s contacts.') % processed
            if error_details:
                message += '\n\n' + _('Failure Details:\n') + '\n'.join(error_details)
            
        except Exception as e:
            status = 'failed'
            message = _('Sync failed: %s') % str(e)

        self.env['sh.active.campaign.log'].create({
            'sh_entity': 'contact',
            'sh_operation': 'import',
            'sh_status': status,
            'sh_records_processed': processed,
            'sh_records_failed': failed,
            'sh_message': message,
            'sh_company_id': company.id,
        })

    def _sync_lists_from_ac(self):
        """Import campaigns and lists from ActiveCampaign."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('lists_import')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        processed = 0
        use_date_range, date_from, date_to, _from_queue_snapshot = self._get_sync_date_window()
        date_params = {}
        if use_date_range:
            if date_from:
                date_params['filters[updated_after]'] = date_from.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            if date_to:
                date_params['filters[updated_before]'] = date_to.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        
        try:
            # 1. Import Campaigns
            offset, limit = 0, 100
            while True:
                params = {'limit': limit, 'offset': offset, **date_params}
                data = client.get('/api/3/campaigns', params=params)
                campaigns = data.get('campaigns', [])
                if use_date_range:
                    campaigns = [
                        cp for cp in campaigns
                        if self._is_ac_record_in_date_window(
                            cp,
                            date_from,
                            date_to,
                            ('udate', 'updated_at', 'updatedAt', 'cdate', 'created_at', 'createdAt'),
                        )
                    ]
                if not campaigns:
                    break
                for cp in campaigns:
                    cp_id = str(cp['id'])
                    existing = self.env['sh.active.campaign.campaign'].search(
                        [('sh_ac_campaign_id', '=', cp_id), ('sh_company_id', '=', company.id)], 
                        limit=1
                    )
                    
                    # Map ActiveCampaign V3 Campaign Status values
                    # 0=draft, 1=scheduled, 2=sending, 3=completed, 4=paused, 5=paused, 6=sending
                    status_map = {
                        '0': 'draft',
                        '1': 'scheduled',
                        '2': 'sending',
                        '3': 'completed',
                        '4': 'paused',
                        '5': 'paused',
                        '6': 'sending',
                    }
                    ac_status = str(cp.get('status', '0'))
                    mapped_status = status_map.get(ac_status, 'draft')

                    # Map ActiveCampaign V3 Campaign Type values
                    type_map = {
                        'single': 'single',
                        'split': 'split',
                        'automated': 'automated',
                        'autoresponder': 'auto_responder',
                        'split_test_auto': 'split_test_auto',
                    }
                    ac_type = str(cp.get('type', 'single'))
                    mapped_type = type_map.get(ac_type, 'single')
                    
                    vals = {
                        'sh_campaign_name': cp.get('name', ''),
                        'sh_ac_campaign_id': cp_id,
                        'sh_campaign_type': mapped_type,
                        'sh_campaign_status': mapped_status,
                        'sh_company_id': company.id,
                    }
                    if existing:
                        existing.write(vals)
                    else:
                        self.env['sh.active.campaign.campaign'].create(vals)
                    processed += 1
                self.env.cr.commit()
                offset += limit

            self.env['sh.active.campaign.log'].create({
                'sh_entity': 'campaign',
                'sh_operation': 'import',
                'sh_status': 'success',
                'sh_records_processed': processed,
                'sh_message': _('Imported %s campaigns.') % processed,
                'sh_company_id': company.id,
            })
        except Exception as e:
            _logger.error('AC Campaign Sync Error: %s', str(e))
            self.env['sh.active.campaign.log'].create({
                'sh_entity': 'campaign',
                'sh_operation': 'import',
                'sh_status': 'failed',
                'sh_records_processed': processed,
                'sh_records_failed': 0,
                'sh_message': _('Sync failed: %s') % str(e),
                'sh_company_id': company.id,
            })

    def _sync_list_contacts_from_ac(self):
        """Import lists from ActiveCampaign when 'List Contacts' is checked."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('list_contacts_import')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        processed = 0
        failed = 0
        use_date_range, date_from, date_to, from_queue_snapshot = self._get_sync_date_window()
        
        try:
            # Import Lists -> mailing.list
            offset, limit = 0, 100
            while True:
                params = {'limit': limit, 'offset': offset}
                data = client.get('/api/3/lists', params=params)
                lists = data.get('lists', [])
                if not lists:
                    break

                for lst in lists:
                    try:
                        list_dt = self._get_active_campaign_list_datetime(lst)
                        if use_date_range and date_from and (not self.env.context.get('is_cron') or from_queue_snapshot):
                            if not self._is_datetime_in_sync_window(list_dt, date_from, date_to):
                                continue

                        lst_id = str(lst['id'])
                        existing = self.env['mailing.list'].search([('sh_ac_list_id', '=', lst_id)], limit=1)
                        vals = {'name': lst.get('name', ''), 'sh_ac_list_id': lst_id}
                        if existing:
                            existing.write(vals)
                        else:
                            self.env['mailing.list'].create(vals)
                        processed += 1
                    except Exception as e:
                        failed += 1
                        _logger.error('Failed to sync AC list: %s', str(e))
                self.env.cr.commit()
                offset += limit
            
            status = 'success' if failed == 0 else ('partial' if processed > 0 else 'failed')
            message = _('Successfully synced %s lists.') % processed
            
        except Exception as e:
            status = 'failed'
            message = _('Sync failed: %s') % str(e)
            
        self.env['sh.active.campaign.log'].create({
            'sh_entity': 'list_contact',
            'sh_operation': 'import',
            'sh_status': status,
            'sh_records_processed': processed,
            'sh_records_failed': failed,
            'sh_message': message,
            'sh_company_id': company.id,
        })

    def _resolve_and_link_ac_contact(self, client, contact_id):
        """
        Helper to resolve an ActiveCampaign contact ID to an Odoo res.partner.
        If the contact does not exist in Odoo, it will fetch details from AC,
        search by email, or create a new partner, ensuring proper bi-directional mapping.
        """
        if not contact_id:
            return False

        # 1. Search for existing partner by AC ID
        partner = self.env['res.partner'].sudo().search([('sh_ac_contact_id', '=', str(contact_id))], limit=1)
        if partner:
            return partner

        # 2. Fetch contact details from AC
        try:
            resp = client.get(f'/api/3/contacts/{contact_id}')
            contact_data = resp.get('contact', {})
            if contact_data:
                email = contact_data.get('email', '')
                first_name = contact_data.get('firstName', '')
                last_name = contact_data.get('lastName', '')
                phone = contact_data.get('phone', '')
                name = (first_name + ' ' + last_name).strip() or email or f'AC Contact {contact_id}'

                # Search by email next
                if email:
                    partner = self.env['res.partner'].sudo().search([('email', '=', email)], limit=1)

                vals = {
                    'name': name,
                    'email': email,
                    'phone': phone,
                    'sh_ac_contact_id': str(contact_id),
                }

                if partner:
                    partner.sudo().write(vals)
                else:
                    partner = self.env['res.partner'].sudo().create(vals)

                return partner
        except Exception as e:
            _logger.error('Failed to resolve AC contact ID %s: %s', contact_id, str(e))

        return False

    def _sync_deals_from_ac(self):
        """Import deals from ActiveCampaign into crm.lead."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('deals_import')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        offset, limit = 0, 100
        processed = 0
        
        is_cron = self.env.context.get('is_cron')
        use_date_range, date_from, date_to, from_queue_snapshot = self._get_sync_date_window()
        date_params = {}
        if use_date_range and date_from and (not is_cron or from_queue_snapshot):
            date_params['filters[updated_after]'] = date_from.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        else:
            last_log = self.env['sh.active.campaign.log'].search([
                ('sh_entity', '=', 'deal'),
                ('sh_operation', '=', 'import'),
                ('sh_status', '=', 'success'),
                ('sh_company_id', '=', company.id)
            ], limit=1, order='create_date desc')
            if last_log:
                date_params['filters[updated_after]'] = last_log.create_date.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            
        if use_date_range and date_to and (not is_cron or from_queue_snapshot):
            date_params['filters[updated_before]'] = date_to.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        
        try:
            while True:
                params = {'limit': limit, 'offset': offset, **date_params}
                data = client.get('/api/3/deals', params=params)
                deals = data.get('deals', [])
                if not deals:
                    break
                
                for deal in deals:
                    deal_id = str(deal['id'])
                    existing = self.env['crm.lead'].search([('sh_ac_deal_id', '=', deal_id)], limit=1)
                    
                    # Simple stage mapping (can be enhanced with a real mapping table)
                    stage_name = deal.get('status', 'To Contact') # Status can be '0' (open), '1' (won), '2' (lost)
                    stage = self.env['crm.stage'].search([('name', 'ilike', stage_name)], limit=1)
                    
                    # Resolve and link contact
                    partner = False
                    ac_contact_id = deal.get('contact', '')
                    if ac_contact_id:
                        partner = self._resolve_and_link_ac_contact(client, ac_contact_id)
                    
                    vals = {
                        'name': deal.get('title', 'AC Deal ' + deal_id),
                        'expected_revenue': float(deal.get('value', 0)) / 100,
                        'sh_ac_deal_id': deal_id,
                        'sh_ac_contact_id': str(ac_contact_id) if ac_contact_id else False,
                        'sh_is_ac_lead': True,
                        'type': 'opportunity',
                    }
                    if partner:
                        vals['partner_id'] = partner.id
                        if partner.email:
                            vals['email_from'] = partner.email
                        if partner.phone:
                            vals['phone'] = partner.phone
                            
                    if stage:
                        vals['stage_id'] = stage.id
                        
                    if existing:
                        existing.write(vals)
                    else:
                        self.env['crm.lead'].create(vals)
                    processed += 1
                
                self.env.cr.commit()
                offset += limit
                
            self.env['sh.active.campaign.log'].create({
                'sh_entity': 'deal',
                'sh_operation': 'import',
                'sh_status': 'success',
                'sh_records_processed': processed,
                'sh_message': _('Successfully synced %s deals.') % processed,
                'sh_company_id': company.id,
            })
        except Exception as e:
            _logger.error('AC Deal Sync Error: %s', str(e))
            self.env['sh.active.campaign.log'].create({
                'sh_entity': 'deal',
                'sh_operation': 'import',
                'sh_status': 'failed',
                'sh_records_processed': processed,
                'sh_records_failed': 0,
                'sh_message': _('Sync failed: %s') % str(e),
                'sh_company_id': company.id,
            })

    def _sync_forms_from_ac(self):
        """Import web forms from ActiveCampaign into sh.active.campaign.form."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('forms_import')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        processed = 0
        use_date_range, date_from, date_to, _from_queue_snapshot = self._get_sync_date_window()
        date_params = {}
        if use_date_range:
            if date_from:
                date_params['filters[updated_after]'] = date_from.strftime('%Y-%m-%dT%H:%M:%S+00:00')
            if date_to:
                date_params['filters[updated_before]'] = date_to.strftime('%Y-%m-%dT%H:%M:%S+00:00')
        
        try:
            # Purge any legacy / orphan action records from previous syncs to clean up the UI
            self.env['sh.active.campaign.form'].sudo().search([
                ('sh_ac_form_id', '=', False),
                ('sh_parent_form_id', '=', False)
            ]).unlink()

            data = client.get('/api/3/forms', params=date_params)
            forms = data.get('forms', [])
            if use_date_range:
                forms = [
                    form for form in forms
                    if self._is_ac_record_in_date_window(
                        form,
                        date_from,
                        date_to,
                        ('udate', 'updated_at', 'updatedAt', 'cdate', 'created_at', 'createdAt'),
                    )
                ]
            
            # Fetch deal stages from ActiveCampaign to resolve group and stage titles
            ac_stages = []
            try:
                stages_data = client.get('/api/3/dealStages')
                ac_stages = stages_data.get('dealStages', [])
            except Exception as e:
                _logger.error('Failed to fetch AC deal stages: %s', str(e))
                
            for form in forms:
                form_id = str(form['id'])
                cdate = form.get('cdate', '')
                if cdate:
                    cdate = cdate.split('T')[0]
                
                existing = self.env['sh.active.campaign.form'].search(
                    [('sh_ac_form_id', '=', form_id), ('sh_company_id', '=', company.id)], 
                    limit=1
                )
                
                vals = {
                    'sh_ac_form_id': form_id,
                    'sh_form_name': form.get('name', ''),
                    'sh_creation_date': cdate or False,
                    'sh_company_id': company.id,
                }
                
                if existing:
                    existing.write(vals)
                    parent_record = existing
                else:
                    parent_record = self.env['sh.active.campaign.form'].create(vals)
                
                processed += 1
                
                # Retrieve and sync actions nested inside the form
                actions = form.get('actiondata', {}).get('actions', [])
                if len(actions) == 1:
                    parent_record.sh_actions = "1 record"
                else:
                    parent_record.sh_actions = f"{len(actions)} records"
                    
                # Clean up existing sub-actions for this form to avoid duplicates on sync
                self.env['sh.active.campaign.form'].sudo().search([
                    ('sh_parent_form_id', '=', parent_record.id),
                    ('sh_company_id', '=', company.id)
                ]).unlink()
                
                # Populate child action records
                action_webform_ref = f"ac.webform,{parent_record.id}"
                for action in actions:
                    action_vals = {
                        'sh_ac_form_id': False,
                        'sh_form_name': False,
                        'sh_creation_date': cdate or False,
                        'sh_actions': 'No records',
                        'sh_webform': action_webform_ref,
                        'sh_parent_form_id': parent_record.id,
                        'sh_type': action.get('type', ''),
                        'sh_email': action.get('email') or action.get('fromemail') or '',
                        'sh_company_id': company.id,
                    }
                    
                    action_type = action.get('type', '')
                    
                    # 1. Map Tag
                    if action_type == 'add-a-tag':
                        tag_name = action.get('tag')
                        if tag_name:
                            category = self.env['res.partner.category'].sudo().search([('name', '=', tag_name)], limit=1)
                            if not category:
                                category = self.env['res.partner.category'].sudo().create({'name': tag_name})
                            action_vals['sh_tag_id'] = category.id
                            
                    # 2. Map Mailing List
                    elif action_type in ('subscribe-to-list', 'subscribe-to-sms-list'):
                        ac_list_id = str(action.get('list', ''))
                        if ac_list_id:
                            list_domain = [('sh_ac_list_id', '=', ac_list_id)]
                            if 'company_id' in self.env['mailing.list']._fields:
                                list_domain.append(('company_id', '=', company.id))
                            list_rec = self.env['mailing.list'].search(list_domain, limit=1)
                            action_vals['sh_list_id'] = list_rec.id if list_rec else False
                            
                    # 3. Map Deal Creation
                    elif action_type == 'add-to-deal':
                        stage_id = str(action.get('stage', ''))
                        stage_title = ''
                        for s in ac_stages:
                            if str(s.get('id')) == stage_id:
                                stage_title = s.get('title')
                                break
                        action_vals['sh_stage'] = stage_title or stage_id
                        
                        if stage_title:
                            stage_rec = self.env['crm.stage'].search([('name', 'ilike', stage_title)], limit=1)
                            action_vals['sh_pipeline_id'] = stage_rec.id if stage_rec else False
                            
                        currency_name = str(action.get('currency', 'USD')).upper()
                        currency_rec = self.env['res.currency'].search([('name', '=', currency_name)], limit=1)
                        action_vals['sh_deal_currency'] = currency_rec.id if currency_rec else False
                        
                        action_vals['sh_deal_value'] = float(action.get('value', 0.0))
                        action_vals['sh_deal_title'] = action.get('deal_title') or action.get('title') or action.get('dealTitle') or ''
                        
                    self.env['sh.active.campaign.form'].sudo().create(action_vals)
            
            self.env.cr.commit()
            
            self.env['sh.active.campaign.log'].create({
                'sh_entity': 'form',
                'sh_operation': 'import',
                'sh_status': 'success',
                'sh_records_processed': processed,
                'sh_message': _('Imported %s forms.') % processed,
                'sh_company_id': company.id,
            })
        except Exception as e:
            _logger.error('AC Form Sync Error: %s', str(e))
            self.env['sh.active.campaign.log'].create({
                'sh_entity': 'form',
                'sh_operation': 'import',
                'sh_status': 'failed',
                'sh_records_processed': processed,
                'sh_records_failed': 0,
                'sh_message': _('Sync failed: %s') % str(e),
                'sh_company_id': company.id,
            })

    def _export_specific_partner(self, partner):
        """Helper to export a single partner to AC."""
        client = self._get_ac_client(self.sh_company_id)
        try:
            if partner.sh_ac_contact_id:
                try:
                    client.get('/api/3/contacts/%s' % partner.sh_ac_contact_id)
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 404:
                        partner.sudo().write({
                            'sh_ac_contact_id': False,
                        })
                    else:
                        raise e

            payload = {
                'contact': {
                    'email': partner.email,
                    'firstName': partner.name.split(' ')[0] if partner.name else '',
                    'lastName': ' '.join(partner.name.split(' ')[1:]) if partner.name else '',
                    'phone': partner.phone or '',
                }
            }
            resp = client.post('/api/3/contact/sync', json=payload)
            if resp.get('contact', {}).get('id'):
                # sudo is required to write ActiveCampaign contact details to the partner record
                partner.sudo().write({
                    'sh_ac_contact_id': str(resp['contact']['id']),
                })
                self._sync_contact_tags_to_ac(client, partner)
        except Exception as e:
            _logger.error('Failed to export specific partner %s: %s', partner.email, str(e))

    def _export_contacts_to_ac(self):
        """Export Odoo partners to ActiveCampaign contacts."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('contacts_export')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        processed = 0
        failed = 0
        
        is_cron = self.env.context.get('is_cron')
        use_date_range, date_from, date_to, from_queue_snapshot = self._get_sync_date_window()
        domain = [
            ('email', '!=', False),
            ('type', '=', 'contact'),
            ('company_id', 'in', [company.id, False])
        ]
        if use_date_range and (not is_cron or from_queue_snapshot):
            if date_from:
                domain.append(('write_date', '>=', date_from))
            if date_to:
                domain.append(('write_date', '<=', date_to))
        else:
            # For export, if cron is running, we could use the last log's date
            last_log = self.env['sh.active.campaign.log'].search([
                ('sh_entity', '=', 'contact'),
                ('sh_operation', '=', 'export'),
                ('sh_status', '=', 'success'),
                ('sh_company_id', '=', company.id)
            ], limit=1, order='create_date desc')
            if last_log:
                domain.append(('write_date', '>=', last_log.create_date))
            else:
                domain.append(('sh_ac_contact_id', '=', False))
                
        error_details = []
        partners = self.env['res.partner'].search(domain)
        
        for partner in partners:
            try:
                if partner.sh_ac_contact_id:
                    try:
                        client.get('/api/3/contacts/%s' % partner.sh_ac_contact_id)
                    except requests.exceptions.HTTPError as e:
                        if e.response is not None and e.response.status_code == 404:
                            partner.sudo().write({
                                'sh_ac_contact_id': False,
                            })
                        else:
                            raise e

                payload = {
                    'contact': {
                        'email': partner.email,
                        'firstName': partner.name.split(' ')[0] if partner.name else '',
                        'lastName': ' '.join(partner.name.split(' ')[1:]) if partner.name else '',
                        'phone': partner.phone or '',
                    }
                }
                resp = client.post('/api/3/contact/sync', json=payload)
                if resp.get('contact', {}).get('id'):
                    partner.sh_ac_contact_id = str(resp['contact']['id'])
                    self._sync_contact_tags_to_ac(client, partner)
                    processed += 1
            except Exception as e:
                failed += 1
                reason = str(e)
                contact_info = partner.name or partner.email or f"ID: {partner.id}"
                timestamp = fields.Datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                error_details.append(f"[{timestamp}] Contact '{contact_info}' was skipped during Odoo → AC sync because: {reason}")
                _logger.error('Failed to export contact %s: %s', partner.email, str(e))
        
        self.env.cr.commit()
        
        status = 'success' if failed == 0 else ('partial' if processed > 0 else 'failed')
        message = _('Exported %s contacts.') % processed
        if error_details:
            message += '\n\n' + _('Failure Details:\n') + '\n'.join(error_details)

        self.env['sh.active.campaign.log'].create({
            'sh_entity': 'contact',
            'sh_operation': 'export',
            'sh_status': status,
            'sh_records_processed': processed,
            'sh_records_failed': failed,
            'sh_message': message,
            'sh_company_id': company.id,
        })

    def _export_tags_to_ac(self):
        """Export Odoo partner categories to ActiveCampaign tags."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('tags_export')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        processed = 0
        failed = 0
        
        is_cron = self.env.context.get('is_cron')
        use_date_range, date_from, date_to, from_queue_snapshot = self._get_sync_date_window()
        domain = []
        if use_date_range and (not is_cron or from_queue_snapshot):
            if date_from:
                domain.append(('write_date', '>=', date_from))
            if date_to:
                domain.append(('write_date', '<=', date_to))
        else:
            last_log = self.env['sh.active.campaign.log'].search([
                ('sh_entity', '=', 'tag'),
                ('sh_operation', '=', 'export'),
                ('sh_status', '=', 'success'),
                ('sh_company_id', '=', company.id)
            ], limit=1, order='create_date desc')
            if last_log:
                domain.append(('write_date', '>=', last_log.create_date))
            else:
                domain.append(('sh_ac_tag_id', '=', False))
                
        categories = self.env['res.partner.category'].search(domain)
        
        for category in categories:
            try:
                payload = {
                    'tag': {
                        'tag': category.name,
                        'tagType': 'contact',
                        'description': 'Exported from Odoo'
                    }
                }
                try:
                    resp = client.post('/api/3/tags', json=payload)
                    if resp.get('tag', {}).get('id'):
                        category.sh_ac_tag_id = str(resp['tag']['id'])
                        processed += 1
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 422:
                        # Tag already exists in ActiveCampaign, retrieve its ID
                        search_resp = client.get('/api/3/tags', params={'search': category.name})
                        found_tag = None
                        for t in search_resp.get('tags', []):
                            if t.get('tag', '').strip().lower() == category.name.strip().lower():
                                found_tag = t
                                break
                        if found_tag:
                            category.sh_ac_tag_id = str(found_tag['id'])
                            processed += 1
                            continue
                    raise e
            except Exception as e:
                failed += 1
                _logger.error('Failed to export tag %s: %s', category.name, str(e))
        
        self.env.cr.commit()
        
        self.env['sh.active.campaign.log'].create({
            'sh_entity': 'tag',
            'sh_operation': 'export',
            'sh_status': 'success' if failed == 0 else 'partial',
            'sh_records_processed': processed,
            'sh_records_failed': failed,
            'sh_message': _('Exported %s tags.') % processed,
            'sh_company_id': company.id,
        })
        return True

    def _export_deals_to_ac(self):
        """Export Odoo CRM opportunities to ActiveCampaign as deals."""
        self.ensure_one()
        if self._is_queue_processing_enabled():
            self._queue_sync_job('deals_export')
            return True
        company = self.sh_company_id
        client = self._get_ac_client(company)
        processed = 0
        failed = 0
        
        is_cron = self.env.context.get('is_cron')
        use_date_range, date_from, date_to, from_queue_snapshot = self._get_sync_date_window()
        domain = [
            ('type', '=', 'opportunity'),
        ]
        if use_date_range and (not is_cron or from_queue_snapshot):
            if date_from:
                domain.append(('write_date', '>=', date_from))
            if date_to:
                domain.append(('write_date', '<=', date_to))
        else:
            last_log = self.env['sh.active.campaign.log'].search([
                ('sh_entity', '=', 'deal'),
                ('sh_operation', '=', 'export'),
                ('sh_status', '=', 'success'),
                ('sh_company_id', '=', company.id)
            ], limit=1, order='create_date desc')
            if last_log:
                domain.append(('write_date', '>=', last_log.create_date))
            else:
                domain.append(('sh_ac_deal_id', '=', False))
                
        leads = self.env['crm.lead'].search(domain)
        
        # Fetch deal stages from ActiveCampaign to resolve group and stage IDs
        ac_stages = []
        try:
            stages_data = client.get('/api/3/dealStages')
            ac_stages = stages_data.get('dealStages', [])
        except Exception as e:
            _logger.error('Failed to fetch AC deal stages: %s', str(e))
            
        # Fetch available users/owners from ActiveCampaign
        ac_owner_id = '1' # Default fallback
        try:
            users_data = client.get('/api/3/users')
            ac_users = users_data.get('users', [])
            if ac_users:
                ac_owner_id = str(ac_users[0]['id'])
        except Exception as e:
            _logger.error('Failed to fetch AC users: %s', str(e))
            
        for lead in leads:
            try:
                # Find AC contact ID
                partner = lead.partner_id
                
                # If partner is not set, try to resolve by email_from or create a new partner
                if not partner:
                    email = lead.email_from
                    if email:
                        # 1. Search for existing partner by email
                        partner = self.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
                        if not partner:
                            # 2. Create new partner using lead information
                            partner = self.env['res.partner'].sudo().create({
                                'name': lead.contact_name or lead.name or email,
                                'email': email,
                                'phone': lead.phone,
                            })
                        # Link partner back to the lead
                        lead.sudo().write({'partner_id': partner.id})

                if partner and partner.sh_ac_contact_id:
                    try:
                        client.get('/api/3/contacts/%s' % partner.sh_ac_contact_id)
                    except requests.exceptions.HTTPError as e:
                        if e.response is not None and e.response.status_code == 404:
                            partner.sudo().write({
                                'sh_ac_contact_id': False,
                            })
                        else:
                            raise e
                
                if partner and not partner.sh_ac_contact_id:
                    # Sync this partner to AC first
                    self._export_specific_partner(partner)
                
                ac_contact_id = partner.sh_ac_contact_id if partner else False
                if not ac_contact_id:
                    failed += 1
                    continue
                
                # Determine AC stage and group
                ac_stage_id = '1'
                ac_group_id = '1'
                if lead.stage_id and ac_stages:
                    for s in ac_stages:
                        if s.get('title', '').strip().lower() == lead.stage_id.name.strip().lower():
                            ac_stage_id = str(s['id'])
                            ac_group_id = str(s.get('group', '1'))
                            break
                    else:
                        # Fallback to the first available AC stage
                        ac_stage_id = str(ac_stages[0]['id'])
                        ac_group_id = str(ac_stages[0].get('group', '1'))
                    
                payload = {
                    'deal': {
                        'title': lead.name,
                        'value': int((lead.expected_revenue or 0) * 100),
                        'currency': lead.company_currency.name or 'USD',
                        'contact': ac_contact_id,
                        'status': 0, # Open
                        'stage': ac_stage_id,
                        'group': ac_group_id,
                        'owner': ac_owner_id,
                    }
                }
                resp = False
                if lead.sh_ac_deal_id:
                    try:
                        resp = client.put('/api/3/deals/%s' % lead.sh_ac_deal_id, json=payload)
                    except requests.exceptions.HTTPError as e:
                        if e.response is not None and e.response.status_code == 404:
                            lead.write({
                                'sh_ac_deal_id': False,
                                'sh_is_ac_lead': False
                            })
                            resp = client.post('/api/3/deals', json=payload)
                        else:
                            raise e
                else:
                    resp = client.post('/api/3/deals', json=payload)
                if resp and resp.get('deal', {}).get('id'):
                    lead.sh_ac_deal_id = str(resp['deal']['id'])
                    lead.sh_is_ac_lead = True
                    processed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                _logger.error('Failed to export deal %s: %s', lead.name, str(e))
        
        self.env.cr.commit()
        
        self.env['sh.active.campaign.log'].create({
            'sh_entity': 'deal',
            'sh_operation': 'export',
            'sh_status': 'success' if failed == 0 else 'partial',
            'sh_records_processed': processed,
            'sh_records_failed': failed,
            'sh_message': _('Exported %s deals, failed %s.') % (processed, failed),
            'sh_company_id': company.id,
        })
        return True


    # --- Webhook Handlers ---
    #
    # Webhooks are intentionally handled separately from scheduled sync jobs.
    # They should only upsert the single record that changed in ActiveCampaign
    # and then reuse the same Odoo models and mappings that cron uses.
    def _webhook_get_payload_data(self, payload, key):
        """Return the most likely payload segment for a webhook entity."""
        self.ensure_one()
        if not isinstance(payload, dict):
            return {}
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        for suffix in ('_data', 'Data'):
            nested = payload.get(f'{key}{suffix}')
            if isinstance(nested, dict):
                return nested
        nested_payload = payload.get('data')
        if isinstance(nested_payload, dict):
            nested = nested_payload.get(key)
            if isinstance(nested, dict):
                return nested
        return payload

    def _webhook_get_value(self, payload, *keys, default=''):
        """Return the first matching scalar value across common ActiveCampaign key styles."""
        self.ensure_one()
        if not isinstance(payload, dict):
            return default
        candidates = []
        for key in keys:
            candidates.extend([
                key,
                key.replace('_', ''),
                key.replace('_', ' '),
                key.replace('_', '-'),
                ''.join(part.capitalize() if idx else part for idx, part in enumerate(key.split('_'))),
            ])
        for candidate in candidates:
            value = payload.get(candidate)
            if value not in (None, '', []):
                return value
        return default

    def _webhook_get_string(self, payload, *keys, default=''):
        """Return a string value from the first matching webhook key."""
        value = self._webhook_get_value(payload, *keys, default=default)
        if value in (None, False):
            return default
        return str(value).strip()

    def _webhook_collect_tags(self, payload):
        """Collect tag values from webhook payloads in list, dict, or string form."""
        self.ensure_one()
        if not isinstance(payload, dict):
            return []
        contact_data = self._webhook_get_payload_data(payload, 'contact')
        raw_values = [
            payload.get('tags'),
            payload.get('tag'),
            payload.get('tag_ids'),
            payload.get('tagIds'),
            payload.get('contact_tags'),
            payload.get('contactTags'),
            contact_data.get('tags'),
            contact_data.get('tag'),
            contact_data.get('tag_ids'),
            contact_data.get('tagIds'),
            contact_data.get('contact_tags'),
            contact_data.get('contactTags'),
        ]
        tag_values = []
        for raw_value in raw_values:
            if not raw_value:
                continue
            if isinstance(raw_value, dict):
                tag_values.append(raw_value)
                continue
            if isinstance(raw_value, list):
                tag_values.extend(raw_value)
                continue
            if isinstance(raw_value, str):
                parts = [part.strip() for part in raw_value.split(',') if part.strip()]
                if len(parts) > 1:
                    tag_values.extend(parts)
                else:
                    tag_values.append(raw_value.strip())
                continue
            tag_values.append(raw_value)
        return tag_values

    @staticmethod
    def _webhook_normalize_ac_tag_id(value):
        """Return a normalized ActiveCampaign tag id or False when the value is not an id."""
        if value in (None, False, ''):
            return False
        value = str(value).strip()
        return value if value.isdigit() else False

    def _webhook_resolve_ac_tag_id(self, client, tag_data):
        """Resolve a real ActiveCampaign tag id from webhook data or by searching AC."""
        self.ensure_one()
        if not isinstance(tag_data, dict):
            tag_data = {'tag': tag_data}
        ac_tag_id = self._webhook_normalize_ac_tag_id(tag_data.get('id') or tag_data.get('tag_id') or tag_data.get('tagId'))
        if ac_tag_id:
            return ac_tag_id

        tag_name = str(tag_data.get('name') or tag_data.get('tag') or '').strip()
        if not tag_name:
            return False

        try:
            search_resp = client.get('/api/3/tags', params={'search': tag_name})
            for tag in search_resp.get('tags', []):
                if str(tag.get('tag', '')).strip().lower() == tag_name.lower():
                    return self._webhook_normalize_ac_tag_id(tag.get('id'))
        except Exception as exc:
            _logger.error('Failed to resolve AC tag id for %s: %s', tag_name, str(exc))
        return False

    def _webhook_resolve_tag_categories(self, client, tag_payloads):
        """Resolve ActiveCampaign tag payloads to Odoo partner categories."""
        self.ensure_one()
        categories = self.env['res.partner.category'].sudo()
        resolved_categories = self.env['res.partner.category']
        for tag_data in tag_payloads:
            if not tag_data:
                continue
            if not isinstance(tag_data, dict):
                tag_data = {'tag': tag_data}
            ac_tag_id = self._webhook_resolve_ac_tag_id(client, tag_data)
            tag_name = str(tag_data.get('name') or tag_data.get('tag') or '').strip()
            category = False
            if ac_tag_id:
                category = categories.search([('sh_ac_tag_id', '=', ac_tag_id)], limit=1)
                if not category:
                    try:
                        category = self._get_or_create_odoo_tag(client, ac_tag_id)
                    except Exception:
                        category = False
            if not category and tag_name:
                category = categories.search([('name', '=ilike', tag_name)], limit=1)
                if not category:
                    category = categories.with_context(sh_skip_ac_tag_sync=True).create({'name': tag_name})
                if ac_tag_id:
                    category.with_context(sh_skip_ac_tag_sync=True).sudo().write({'sh_ac_tag_id': ac_tag_id})
            if category:
                resolved_categories |= category
        return resolved_categories

    def _webhook_collect_numeric_tag_ids(self, payload):
        """Collect only numeric ActiveCampaign tag ids from webhook payloads."""
        self.ensure_one()
        if not isinstance(payload, dict):
            return []
        contact_data = self._webhook_get_payload_data(payload, 'contact')
        raw_values = [
            payload.get('tags'),
            payload.get('tag'),
            payload.get('tag_ids'),
            payload.get('tagIds'),
            payload.get('contact_tags'),
            payload.get('contactTags'),
            contact_data.get('tags'),
            contact_data.get('tag'),
            contact_data.get('tag_ids'),
            contact_data.get('tagIds'),
            contact_data.get('contact_tags'),
            contact_data.get('contactTags'),
        ]
        tag_ids = []
        for raw_value in raw_values:
            if not raw_value:
                continue
            if isinstance(raw_value, dict):
                candidate = self._webhook_normalize_ac_tag_id(raw_value.get('id') or raw_value.get('tag') or raw_value.get('tag_id'))
                if candidate:
                    tag_ids.append(candidate)
                continue
            if isinstance(raw_value, list):
                for item in raw_value:
                    if isinstance(item, dict):
                        candidate = self._webhook_normalize_ac_tag_id(item.get('id') or item.get('tag') or item.get('tag_id'))
                        if candidate:
                            tag_ids.append(candidate)
                    else:
                        candidate = self._webhook_normalize_ac_tag_id(item)
                        if candidate:
                            tag_ids.append(candidate)
                continue
            candidate = self._webhook_normalize_ac_tag_id(raw_value)
            if candidate:
                tag_ids.append(candidate)
        return list(dict.fromkeys(tag_ids))

    def _webhook_resolve_tag_categories_from_ids(self, client, tag_ids):
        """Resolve numeric ActiveCampaign tag ids to Odoo partner categories."""
        self.ensure_one()
        categories = self.env['res.partner.category'].sudo()
        resolved_categories = self.env['res.partner.category']
        for ac_tag_id in tag_ids:
            if not ac_tag_id:
                continue
            category = categories.search([('sh_ac_tag_id', '=', ac_tag_id)], limit=1)
            if not category:
                try:
                    category = self._get_or_create_odoo_tag(client, ac_tag_id)
                except Exception as exc:
                    _logger.warning('Failed to resolve AC tag %s into Odoo: %s', ac_tag_id, str(exc))
                    category = False
            if category:
                resolved_categories |= category
        return resolved_categories

    def _webhook_fetch_contact_tag_payloads(self, client, ac_contact_id):
        """Fetch the current AC contact tag payloads and flag whether the result is authoritative."""
        self.ensure_one()
        if not ac_contact_id:
            return [], False
        try:
            resp = client.get(f'/api/3/contacts/{ac_contact_id}/contactTags', params={'limit': 100})
            return resp.get('contactTags', []) or [], True
        except Exception as exc:
            _logger.warning('Failed to fetch AC contact tags for %s: %s', ac_contact_id, str(exc))
        return [], False

    def _webhook_fetch_deal_payload(self, client, ac_deal_id):
        """Fetch the latest ActiveCampaign deal payload for webhook hydration."""
        self.ensure_one()
        if not ac_deal_id:
            return {}
        try:
            resp = client.get(f'/api/3/deals/{ac_deal_id}')
            return resp.get('deal') or {}
        except Exception as exc:
            _logger.warning('Failed to fetch AC deal %s: %s', ac_deal_id, str(exc))
        return {}

    @staticmethod
    def _webhook_parse_amount(amount_value):
        """Convert an AC deal amount into Odoo currency units."""
        if amount_value in (None, ''):
            return 0.0
        try:
            return float(amount_value) / 100.0
        except (TypeError, ValueError):
            return 0.0

    def _webhook_append_description(self, lead, note_text):
        """Replace the standard CRM description field with the latest webhook note."""
        self.ensure_one()
        note_text = (note_text or '').strip()
        if not lead or not note_text:
            return False

        # Public webhook requests need sudo when updating standard CRM fields.
        lead.sudo().write({'description': note_text})
        lead.sudo().message_post(body=note_text)
        return True

    def _webhook_find_partner(self, payload):
        """Find the Odoo contact linked to a webhook payload."""
        self.ensure_one()
        partner = False
        contact_data = self._webhook_get_payload_data(payload, 'contact')
        ac_contact_id = str(
            contact_data.get('id')
            or payload.get('contact_id')
            or payload.get('contactId')
            or payload.get('id')
            or ''
        ).strip()
        email = (contact_data.get('email') or payload.get('email') or '').strip()
        if ac_contact_id:
            partner = self.env['res.partner'].sudo().search(
                [('sh_ac_contact_id', '=', ac_contact_id)],
                limit=1,
            )
        if not partner and email:
            partner = self.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
        return partner, contact_data, ac_contact_id, email

    def _webhook_sync_contact_tags(self, payload, partner):
        """Sync webhook tag payloads onto an Odoo partner category relation."""
        self.ensure_one()
        if not partner:
            return False
        client = self._get_ac_client(self.sh_company_id)
        _, _, ac_contact_id, _ = self._webhook_find_partner(payload)
        tag_payloads, is_authoritative = self._webhook_fetch_contact_tag_payloads(client, ac_contact_id)
        if tag_payloads:
            categories = self._webhook_resolve_tag_categories_from_ids(
                client,
                [self._webhook_normalize_ac_tag_id(item.get('tag') or item.get('id') or item.get('tag_id'))
                 for item in tag_payloads if isinstance(item, dict)],
            )
        else:
            numeric_tag_ids = self._webhook_collect_numeric_tag_ids(payload)
            is_authoritative = False
            if not numeric_tag_ids:
                _logger.warning(
                    'Skipping ActiveCampaign tag sync for contact %s because no numeric tag IDs were available.',
                    ac_contact_id or partner.id,
                )
                return False
            categories = self._webhook_resolve_tag_categories_from_ids(client, numeric_tag_ids)

        if not categories:
            return False

        commands = []
        for category in categories:
            if category.id not in partner.category_id.ids:
                commands.append((4, category.id))
        if is_authoritative:
            current_ac_categories = partner.category_id.filtered(lambda cat: cat.sh_ac_tag_id)
            for category in current_ac_categories:
                if category.id not in categories.ids:
                    commands.append((3, category.id))
        if commands:
            # Public webhook requests need sudo when modifying M2M relations.
            partner.sudo().write({'category_id': commands})
        return True

    def _webhook_handle_contact(self, payload):
        """Create or update a partner from ActiveCampaign contact webhooks."""
        self.ensure_one()
        partner, contact_data, ac_contact_id, email = self._webhook_find_partner(payload)
        first_name = self._webhook_get_string(contact_data, 'first_name', 'firstName')
        last_name = self._webhook_get_string(contact_data, 'last_name', 'lastName')
        name_value = self._webhook_get_string(contact_data, 'name')
        name = (
            name_value
            or ' '.join(filter(None, [first_name, last_name])).strip()
            or self._webhook_get_string(payload, 'name', 'full_name')
            or (partner.name if partner else '')
            or email
            or f'AC Contact {ac_contact_id or "Unknown"}'
        )
        phone = self._webhook_get_string(contact_data, 'phone', 'phone_number') or self._webhook_get_string(
            payload, 'phone', 'phone_number'
        )
        note = self._webhook_get_string(contact_data, 'note', 'notes') or self._webhook_get_string(
            payload, 'note', 'notes'
        )
        vals = {'name': name}
        if email:
            vals['email'] = email
        if phone:
            vals['phone'] = phone
        if note:
            vals['comment'] = note
        if ac_contact_id:
            vals['sh_ac_contact_id'] = ac_contact_id
        if partner:
            # Public webhook requests need sudo when writing business records.
            partner.sudo().write(vals)
        else:
            partner = self.env['res.partner'].sudo().create(vals)
        self._webhook_sync_contact_tags(payload, partner)
        return partner

    def _webhook_handle_deal(self, payload):
        """Create or update a CRM opportunity from ActiveCampaign deal webhooks."""
        self.ensure_one()
        client = self._get_ac_client(self.sh_company_id)
        deal_data = self._webhook_get_payload_data(payload, 'deal')
        deal_id = str(
            deal_data.get('id')
            or payload.get('deal_id')
            or payload.get('dealId')
            or payload.get('id')
            or ''
        ).strip()
        deal_payload = {}
        if deal_id:
            deal_payload = self._webhook_fetch_deal_payload(client, deal_id)
        merged_deal_data = dict(deal_data or {})
        if deal_payload:
            merged_deal_data.update(deal_payload)

        deal_title = (
            merged_deal_data.get('title')
            or merged_deal_data.get('name')
            or payload.get('title')
            or payload.get('name')
            or f'AC Deal {deal_id or "Unknown"}'
        )
        contact_data = {}
        if isinstance(merged_deal_data.get('contact'), dict):
            contact_data = merged_deal_data.get('contact') or {}
        elif isinstance(deal_payload.get('contact'), dict):
            contact_data = deal_payload.get('contact') or {}
        elif isinstance(deal_data.get('contact'), dict):
            contact_data = deal_data.get('contact') or {}

        contact_id = (
            merged_deal_data.get('contact')
            or merged_deal_data.get('contact_id')
            or merged_deal_data.get('contactId')
            or payload.get('contact_id')
            or payload.get('contactId')
        )
        partner = False
        if contact_id:
            if isinstance(contact_id, dict):
                contact_id = contact_id.get('id') or contact_id.get('contact_id') or contact_id.get('contactId')
            partner = self._resolve_and_link_ac_contact(client, contact_id)
        else:
            email = (
                contact_data.get('email')
                or payload.get('email')
                or merged_deal_data.get('email')
                or ''
            ).strip()
            if email:
                partner = self.env['res.partner'].sudo().search([('email', '=', email)], limit=1)
                if not partner:
                    partner = self.env['res.partner'].sudo().create({
                        'name': contact_data.get('name') or ' '.join(filter(None, [
                            contact_data.get('first_name') or contact_data.get('firstName'),
                            contact_data.get('last_name') or contact_data.get('lastName'),
                        ])).strip() or email,
                        'email': email,
                        'phone': contact_data.get('phone') or contact_data.get('phone_number') or '',
                    })

        contact_name = (
            self._webhook_get_string(contact_data, 'name')
            or ' '.join(filter(None, [
                self._webhook_get_string(contact_data, 'first_name', 'firstName'),
                self._webhook_get_string(contact_data, 'last_name', 'lastName'),
            ])).strip()
            or (partner.name if partner else '')
            or self._webhook_get_string(payload, 'contact_name', 'contactName')
        )
        contact_email = (
            self._webhook_get_string(contact_data, 'email')
            or self._webhook_get_string(payload, 'email')
            or (partner.email if partner else '')
        )
        contact_phone = (
            self._webhook_get_string(contact_data, 'phone', 'phone_number')
            or self._webhook_get_string(payload, 'phone', 'phone_number')
            or (partner.phone if partner else '')
        )
        deal_note = (
            self._webhook_get_string(merged_deal_data, 'description', 'note', 'notes')
            or self._webhook_get_string(payload, 'description', 'note', 'notes')
        )

        vals = {
            'name': deal_title,
            'type': 'opportunity',
            'sh_is_ac_lead': True,
        }
        if deal_id:
            vals['sh_ac_deal_id'] = deal_id
        if contact_id:
            vals['sh_ac_contact_id'] = str(contact_id)
        if contact_name:
            vals['contact_name'] = contact_name
        if contact_email:
            vals['email_from'] = contact_email
        if contact_phone:
            vals['phone'] = contact_phone
        vals['expected_revenue'] = self._webhook_parse_amount(
            merged_deal_data.get('value') or payload.get('value') or merged_deal_data.get('deal_value')
        )
        if partner:
            vals['partner_id'] = partner.id

        stage_title = (
            self._webhook_get_string(merged_deal_data, 'stageTitle', 'stage_title')
            or self._webhook_get_string(payload, 'stageTitle', 'stage_title')
        )
        stage_id = merged_deal_data.get('stage') or payload.get('stage')
        if isinstance(stage_id, dict):
            stage_id = stage_id.get('id') or stage_id.get('stage_id') or stage_id.get('stageId')
        if not stage_title and stage_id:
            try:
                stage_resp = client.get(f'/api/3/stages/{stage_id}')
                stage_title = (stage_resp.get('stage') or {}).get('title') or stage_title
            except Exception as exc:
                _logger.warning('Failed to fetch AC stage %s for deal %s: %s', stage_id, deal_id or 'unknown', str(exc))
        if stage_title:
            stage = self.env['crm.stage'].search([('name', 'ilike', stage_title)], limit=1)
            if stage:
                vals['stage_id'] = stage.id

        existing = self.env['crm.lead'].sudo().search([('sh_ac_deal_id', '=', deal_id)], limit=1) if deal_id else False
        if existing:
            existing.sudo().write(vals)
            if deal_note:
                self._webhook_append_description(existing, deal_note)
            return existing
        lead = self.env['crm.lead'].sudo().create(vals)
        if deal_note:
            self._webhook_append_description(lead, deal_note)
        return lead

    def _webhook_handle_deal_note(self, payload):
        """Append an ActiveCampaign deal note to the matching CRM opportunity."""
        self.ensure_one()
        client = self._get_ac_client(self.sh_company_id)
        note_data = self._webhook_get_payload_data(payload, 'note')
        deal_data = self._webhook_get_payload_data(payload, 'deal')
        deal_id = str(
            deal_data.get('id')
            or note_data.get('deal')
            or note_data.get('deal_id')
            or note_data.get('dealId')
            or payload.get('deal_id')
            or payload.get('dealId')
            or payload.get('deal')
            or ''
        ).strip()
        note_text = (
            note_data.get('text')
            or note_data.get('note')
            or payload.get('note')
            or payload.get('text')
            or ''
        )
        if not note_text:
            return False

        lead = self.env['crm.lead'].sudo().search([('sh_ac_deal_id', '=', deal_id)], limit=1) if deal_id else False
        if not lead and deal_id:
            lead = self._webhook_handle_deal(payload)
        if not lead:
            return False

        self._webhook_append_description(lead, note_text)
        return lead

    def _webhook_handle_tag(self, payload):
        """Create or update a partner tag mapping from ActiveCampaign webhooks."""
        self.ensure_one()
        client = self._get_ac_client(self.sh_company_id)
        partner, _, _, _ = self._webhook_find_partner(payload)
        if not partner:
            return False

        tag_data = self._webhook_get_payload_data(payload, 'tag')
        ac_tag_id = self._webhook_resolve_ac_tag_id(client, {
            'id': tag_data.get('id') or payload.get('tag_id') or payload.get('tagId'),
            'tag': tag_data.get('tag') or payload.get('tag_name'),
            'name': tag_data.get('name') or payload.get('tag_name'),
        })
        tag_name = tag_data.get('name') or tag_data.get('tag') or payload.get('tag_name')
        if not ac_tag_id and not tag_name:
            return False

        category = False
        if ac_tag_id:
            category = self._get_or_create_odoo_tag(client, ac_tag_id)
        elif tag_name:
            category = self.env['res.partner.category'].sudo().search([('name', '=ilike', tag_name)], limit=1)
            if not category:
                category = self.env['res.partner.category'].with_context(sh_skip_ac_tag_sync=True).sudo().create({'name': tag_name})
            if category and not category.sh_ac_tag_id:
                resolved_ac_tag_id = self._webhook_resolve_ac_tag_id(client, {'name': tag_name, 'tag': tag_name})
                if resolved_ac_tag_id:
                    category.with_context(sh_skip_ac_tag_sync=True).sudo().write({'sh_ac_tag_id': resolved_ac_tag_id})

        if not category:
            return False

        event_name = str(payload.get('event') or payload.get('type') or '').lower()
        command = [(4, category.id)]
        if 'removed' in event_name or 'delete' in event_name:
            command = [(3, category.id)]

        # Public webhook requests need sudo when modifying partner tags.
        partner.sudo().write({'category_id': command})
        return True

    def _webhook_handle_list(self, payload):
        """Create or update a mailing list from an ActiveCampaign webhook."""
        self.ensure_one()
        list_data = self._webhook_get_payload_data(payload, 'list')
        list_id = str(
            list_data.get('id')
            or payload.get('list_id')
            or payload.get('listId')
            or payload.get('list')
            or ''
        ).strip()
        list_name = (
            list_data.get('name')
            or payload.get('name')
            or f'AC List {list_id or "Unknown"}'
        )

        vals = {'name': list_name}
        if list_id:
            vals['sh_ac_list_id'] = list_id

        existing = self.env['mailing.list'].sudo().search([('sh_ac_list_id', '=', list_id)], limit=1) if list_id else False
        if not existing and list_name:
            existing = self.env['mailing.list'].sudo().search([('name', '=', list_name)], limit=1)

        if existing:
            existing.sudo().write(vals)
            return existing
        return self.env['mailing.list'].sudo().create(vals)

    def _process_webhook_event(self, event_name, payload):
        """Route a webhook event to the matching handler."""
        self.ensure_one()
        event_name = (event_name or '').strip().lower()
        if event_name in (
            'contact_add',
            'contact_sync',
            'contact_update',
            'subscribe',
            'update',
            'unsubscribe',
            'subscriber_note',
        ):
            return self._webhook_handle_contact(payload)
        if event_name in ('deal_add', 'deal_sync', 'deal_update'):
            return self._webhook_handle_deal(payload)
        if event_name in ('deal_note_add', 'deal_note_update'):
            return self._webhook_handle_deal_note(payload)
        if event_name in ('contact_tag_added', 'contact_tag_removed'):
            return self._webhook_handle_tag(payload)
        if event_name == 'list_add':
            return self._webhook_handle_list(payload)
        _logger.info('Ignoring unsupported ActiveCampaign webhook event: %s', event_name or 'unknown')
        return False

    @api.model
    def _cron_run_import(self):
        """Scheduled action: run import for all companies with auto_scheduler enabled."""
        dashboards = self.search([('sh_auto_scheduler', '=', True)])
        for dashboard in dashboards:
            try:
                dashboard.with_context(is_cron=True, sync_direction='import').action_run_sync()
            except Exception as e:
                _logger.error('AC Cron Import failed for company %s: %s',
                              dashboard.sh_company_id.name, str(e))
                continue

    @api.model
    def _cron_run_export(self):
        """Scheduled action: run export for all companies with auto_scheduler enabled."""
        dashboards = self.search([('sh_auto_scheduler', '=', True)])
        for dashboard in dashboards:
            try:
                # Currently action_run_sync does both, but we can separate if needed
                dashboard.with_context(is_cron=True,sync_direction='export').action_run_sync()
            except Exception as e:
                _logger.error('AC Cron Export failed for company %s: %s',
                              dashboard.sh_company_id.name, str(e))
                continue

    @api.model
    def action_open_dashboard(self):
        """Open or create the dashboard record for the current company to avoid draft state on load."""
        company_id = self.env.company.id
        record = self.search([('sh_company_id', '=', company_id)], limit=1)
        if not record:
            record = self.create({'sh_company_id': company_id})
        
        action = self.env.ref('sh_active_campaign.sh_ac_dashboard_action').read()[0]
        action['res_id'] = record.id
        return action
