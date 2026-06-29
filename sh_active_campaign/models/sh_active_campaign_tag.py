# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields

# class ShActiveCampaignTag(models.Model):
#     _name = 'sh.active.campaign.tag'
#     _description = 'ActiveCampaign Tag'
#     _order = 'create_date desc'
#     _rec_name = 'sh_tag_name'
#
#     sh_tag_name = fields.Char(string='Tag Name', required=True, index=True)
#     sh_tag_type = fields.Char(string='Tag Type')  # 'contact' or 'deal'
#     sh_tag_id = fields.Char(string='Tag Id', index=True, copy=False)
#     create_date = fields.Datetime(string='Creation Date', readonly=True)
#     sh_company_id = fields.Many2one(
#         'res.company', string='Company',
#         default=lambda self: self.env.company
#     )
#     # Link to Odoo partner category for bidirectional sync
#     # sh_category_id = fields.Many2one(
#     #     'res.partner.category', string='Odoo Tag', ondelete='set null'
#     # )
