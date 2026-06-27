# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies.

from odoo import models,fields,api
from odoo.exceptions import ValidationError


class BaseLog(models.Model):
    _name = 'sh.integration.log'
    _description = 'Log'
    _order = 'id desc'


    name = fields.Char(string="Reference",default=lambda self:('New'),copy=False, readonly=True)
    description = fields.Text("Description",readonly=True)
    company_id=fields.Many2one("res.company",
                            string="Company",
                            default=lambda self: self.env.user.company_id,
                            readonly=True
                            )
    # datetime = fields.Datetime("Date & Time")
    state = fields.Selection([('success','Success'),('error','Failed'),('info','Information')],readonly=True,string="State")
    config_id = fields.Many2one("sh.integration.config", string="Integration Config",readonly=True)
    response=fields.Text("Response",readonly=True)
    
    log_type = fields.Selection(
        string='Log Type',
        selection=[('auth', 'Authorize'), ('token', 'Token')],
        readonly=True,
    )
    
    def action_open_log_record(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Log Record',
            'view_mode': 'form',
            'res_model': 'sh.integration.log',
            'res_id': self.id,
            'target': 'new',  
        }
        
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', ('New')) == ('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('sh.log.seq')
        res = super(BaseLog, self).create(vals_list)
        return res