# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields, api


class ResPartner(models.Model):
    _inherit = 'res.partner'

    sh_ac_contact_id = fields.Integer(
        string='ActiveCampaign Contact ID',
        copy=False,
        help='External contact ID from ActiveCampaign used for sync mapping.'
    )

    sh_ac_synced = fields.Boolean(
        string='ActiveCampaign Synced',
        compute='_compute_sh_ac_synced',
        store=True,
        help='Indicates whether the contact has been synced with ActiveCampaign.'
    )

    @api.depends('sh_ac_contact_id')
    def _compute_sh_ac_synced(self):
        """Set sh_ac_synced to True when an ActiveCampaign contact ID exists."""
        for rec in self:
            rec.sh_ac_synced = bool(rec.sh_ac_contact_id)
   
