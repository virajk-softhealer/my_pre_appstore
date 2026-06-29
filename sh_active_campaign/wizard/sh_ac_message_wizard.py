# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields

class ShACMessageWizard(models.TransientModel):
    _name = 'sh.ac.message.wizard'
    _description = 'ActiveCampaign Message Wizard'

    message = fields.Text(string='Message', readonly=True)
    is_success = fields.Boolean(string='Is Success', readonly=True)
