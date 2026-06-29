# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import fields, models


class ShActiveCampaignUserGuide(models.Model):
    _name = 'sh.active.campaign.user.guide'
    _description = 'Active Campaign User Guide'
    _order = 'id desc'

    name = fields.Char(string='Title', required=True, default='Active Campaign User Guide')
    sh_content = fields.Html(string='Guide Content', sanitize=True, required=True)
