# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields, api

class ShActiveCampaignLog(models.Model):
    _name = 'sh.active.campaign.log'
    _description = 'ActiveCampaign Sync Log'
    _order = 'create_date desc'
    _rec_name ="sh_entity"

    sh_entity = fields.Selection([
        ('contact', 'Contact'),
        ('list_contact', 'List Contact'),
        ('list', 'List'),
        ('campaign', 'Campaign'),
        ('tag', 'Tag'),
        ('deal', 'Deal'),
        ('form', 'Form'),
    ], string='Entity', required=True)
    sh_operation = fields.Selection([
        ('import', 'Import (AC → Odoo)'),
        ('export', 'Export (Odoo → AC)'),
    ], string='Operation', required=True)
    sh_status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('partial', 'Partial'),
    ], string='Status', required=True)
    sh_records_processed = fields.Integer(string='Records Processed')
    sh_records_failed = fields.Integer(string='Records Failed')
    sh_message = fields.Text(string='Message / Error')
    sh_company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company
    )

    @api.model
    def _cron_cleanup_logs(self):
        """Scheduled action: Delete sync logs older than 30 days, processed company-wise."""
        limit_date = fields.Datetime.subtract(fields.Datetime.now(), days=30)
        
        # 1. Active company-specific log cleanup
        for company in self.env.companies:
            old_logs = self.search([
                ('sh_company_id', '=', company.id),
                ('create_date', '<', limit_date)
            ])
            if old_logs:
                old_logs.unlink()
