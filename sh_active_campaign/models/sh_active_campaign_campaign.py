# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields

class ShActiveCampaignCampaign(models.Model):
    _name = 'sh.active.campaign.campaign'
    _description = 'ActiveCampaign Campaign'
    _order = 'create_date desc'
    _rec_name = 'sh_campaign_name'

    sh_campaign_name = fields.Char(string='Campaign Name', required=True, index=True)
    sh_ac_campaign_id = fields.Char(
        string='AC Campaign ID', index=True, copy=False,
        help='Unique campaign ID from ActiveCampaign (used as external key).'
    )
    sh_campaign_lists = fields.Integer(
        string="Campaign's Lists",
        help='Number of mailing lists this campaign is associated with.'
    )
    sh_campaign_type = fields.Selection([
        ('single', 'Single'),
        ('split', 'Split'),
        ('automated', 'Automated'),
        ('auto_responder', 'Auto Responder'),
        ('split_test_auto', 'Split Test Auto'),
    ], string='Campaign Type', default='single')
    sh_campaign_status = fields.Selection([
        ('draft', 'Draft'),
        ('scheduled', 'Scheduled'),
        ('sending', 'Sending'),
        ('completed', 'Completed'),
        ('paused', 'Paused'),
    ], string='Campaign Status', default='draft')
    sh_company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company
    )
