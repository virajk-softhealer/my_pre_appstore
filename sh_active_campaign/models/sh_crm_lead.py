# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields


class CrmLead(models.Model):
    _inherit = 'crm.lead'

    sh_ac_deal_id = fields.Integer(
        string='AC Deal ID',
        copy=False,
        index=True,
        help='ActiveCampaign Deal ID linked to this CRM opportunity.'
    )
    sh_ac_contact_id = fields.Integer(
        string='AC Contact ID',
        copy=False,
        index=True,
        help='ActiveCampaign Contact ID associated with this deal.'
    )
   
    sh_is_ac_lead = fields.Boolean(
        string='Active Campaign Lead',
        default=False,
        copy=False,
        help='Marks this lead/opportunity as synced with ActiveCampaign.'
    )
