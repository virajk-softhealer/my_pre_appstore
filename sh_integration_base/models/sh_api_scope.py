from odoo import _, api, fields, models

class ShApiScope(models.Model):
    _name = 'sh.api.scope'
    _description = "Manage the scope for api"

    name = fields.Char(readonly=True)
    code = fields.Char(
        string='code',readonly=True
    )
    is_active = fields.Boolean(default=True,readonly=True)
    api_type = fields.Selection(selection=[],string='Api Type',readonly=True)

    
    