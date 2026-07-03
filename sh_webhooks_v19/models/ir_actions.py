# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models,fields

class IrActionsServer(models.Model):
    _inherit = 'ir.actions.server'

    sh_is_webhook = fields.Boolean(default=False)
    
    def create_action(self):
        """ Create a contextual action for each server action. """
        action=super().create_action()
        if self.env.context.get('webhook'):
            for rec in self:
                rec.update({
                    'sh_is_webhook': True,
                })
        return action