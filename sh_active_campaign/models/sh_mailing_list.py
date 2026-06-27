# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields


class MailingList(models.Model):
    _inherit = 'mailing.list'

    sh_ac_list_id = fields.Char(
        string='AC List ID',
        copy=False,
        index=True,
        help='ActiveCampaign List ID linked to this mailing list.'
    )
