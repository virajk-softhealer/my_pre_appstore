# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import fields, models


class FreshdeskUserGuide(models.Model):
    _name = 'sh.freshdesk.user.guide'
    _description = 'Freshdesk User Guide'
    _order = 'id desc'

    name = fields.Char(string='Title', required=True, readonly=True)
    guide_html = fields.Html(string='Guide Content', readonly=True)
