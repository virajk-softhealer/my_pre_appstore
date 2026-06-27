# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.
from odoo import _, fields, models
from odoo.exceptions import ValidationError


class ShQueue(models.Model):
    """A model to manage and process queue entries for third-party integrations."""
    _name = 'sh.queue'
    _description = 'Sh Queue management'

    name = fields.Char(
        string='Name',
        required=True,
        readonly=True,
    )

    remote_id = fields.Char(
        string='Remote Id',
        readonly=True
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('error', 'Error'),
        ('done', 'Done'),
        ('delete','Deleted'),
    ], string="State", default='draft')
    note = fields.Char('Note/Error',readonly=True)
    res_model = fields.Char('Res Model',readonly=True)
    res_id = fields.Char('Res id',readonly=True)

    json_data = fields.Text(
        string='Json Data',
        readonly=True,
    )

    queue_type = fields.Selection(
        string='Queue Type',
        selection=[],
        readonly=True,
    )

    sh_integration_base_id = fields.Many2one(
        string='Connector',
        comodel_name='sh.integration.config',
        readonly=True,
    )


    def _mass_action_import_from_queue(self):
        """A placeholder method for mass-importing records from the queue."""
        print("\n\n\n\t--------------> 41 SUPER",)

    def view_record(self):
        """Opens the record associated with the queue entry in a new window."""
        if self.res_model and self.res_id:
            return {
                'name': _('sh_queue_record_view'),
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'target': 'current',
                'res_model': self.res_model,
                'res_id':int(self.res_id),
                'domain': [('id', 'in', int(self.res_id))],
                'context': {'edit': True, 'create': True, 'search_default_active': 1},
            }
        else:
            raise ValidationError("res_model & res_id missing !")
