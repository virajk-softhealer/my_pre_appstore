# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields


class ResPartnerCategory(models.Model):
    _inherit = 'res.partner.category'

    sh_ac_tag_id = fields.Char(
        string='AC Tag ID',
        copy=False,
        index=True,
        help='ActiveCampaign Tag ID linked to this Odoo tag/category.'
    )
