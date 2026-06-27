# # -*- coding: utf-8 -*-
# # Copyright (C) Softhealer Technologies.

# TODO: Remove at the end, if india mart module is update according the new changes of this module.
# sh.lead.log is replace with sh.integration.log

# from odoo import models, fields, api


# # TODO: Remove this model instead use sh.integration.log
# class BaseLog(models.Model):
#      _name = 'sh.lead.log'
#      _description = 'Helps you to maintain the activity done'
#      _order = 'create_date desc'

#      name = fields.Char("Name")
#      error = fields.Char("Message")
#      company_id=fields.Many2one("res.company",string="Company")
#      datetime = fields.Datetime("Date & Time")
#      state = fields.Selection([('success','Success'),('error','Failed')],  string="State")
#      config_id = fields.Many2one("sh.integration.config", string="Integration Config")  
#      response=fields.Char("Response")

#      def action_open_log_record(self):
#           self.ensure_one()
#           return {
#                'type': 'ir.actions.act_window',
#                'name': 'Log Record',
#                'view_mode': 'form',
#                'res_model': 'sh.lead.log',
#                'res_id': self.id,
#                'target': 'new',  
#           }
