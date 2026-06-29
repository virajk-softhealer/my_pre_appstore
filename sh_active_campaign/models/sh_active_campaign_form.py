# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields

class ShActiveCampaignForm(models.Model):
    _name = 'sh.active.campaign.form'
    _description = 'ActiveCampaign Web Form'
    _order = 'sh_creation_date desc'
    _rec_name = 'sh_form_name'

    sh_ac_form_id = fields.Char(
        string='Ac Form ID', index=True, copy=False,
        help='Unique form ID from ActiveCampaign.'
    )
    sh_form_name = fields.Char(string='Form Name', index=True)
    sh_creation_date = fields.Date(string='Creation Date')
    sh_actions = fields.Char(string='Actions')
    sh_webform = fields.Char(string='Webform', help='Webform identifier/URL from AC.')
    sh_type = fields.Char(string='Type', help='Form type from ActiveCampaign.')
    sh_email = fields.Char(string='Email')
    sh_tag_id = fields.Many2one(
        'res.partner.category', string='Tag',
        help='Tag assigned to contacts who submit this form.'
    )
    sh_list_id = fields.Many2one(
        'mailing.list', string='List',
        help='Mailing list that form submissions are added to.'
    )
    sh_pipeline_id = fields.Many2one(
        'crm.stage', string='Pipeline/Stage',
        help='CRM pipeline stage associated with this form.'
    )
    sh_stage = fields.Char(string='Stage')
    sh_deal_currency = fields.Many2one(
        'res.currency', string='Deal Currency'
    )
    sh_deal_value = fields.Float(string='Deal Value')
    sh_deal_title = fields.Char(string='Deal Title')
    sh_company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company
    )
    sh_parent_form_id = fields.Many2one(
        'sh.active.campaign.form', string='Parent Form',
        ondelete='cascade', index=True
    )
    sh_action_ids = fields.One2many(
        'sh.active.campaign.form', 'sh_parent_form_id',
        string='Actions'
    )

