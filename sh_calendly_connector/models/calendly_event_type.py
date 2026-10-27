# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import api, fields, models


class CalendlyEventType(models.Model):
    _name = 'sh.calendly.event.type'
    _description = 'Calendly Event Type'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    # _order = 'name, id'
    # _rec_name = 'name'

    name = fields.Char(required=True, index=True, tracking=True)
    uri = fields.Char(string='Calendly URI', required=True, index=True, copy=False, tracking=True)
    sh_calendly_event_type_uri_id = fields.Char(
        compute='_compute_sh_calendly_event_type_uri_id',
        string='Event Type Id',
        help='The Calendly Event Type identifier without its API URL.',
    )
    scheduling_url = fields.Char(copy=False)
    duration = fields.Integer(copy=False, tracking=True)
    locations = fields.Json(copy=False)
    pooling_type = fields.Char(copy=False)
    calendly_updated_at = fields.Datetime(copy=False, tracking=True)

    @api.depends('uri')
    def _compute_sh_calendly_event_type_uri_id(self):
        for event_type in self:
            event_type.sh_calendly_event_type_uri_id = (
                event_type.uri.rsplit('/', 1)[-1] if event_type.uri else False
            )

    _sql_constraints = [
        (
            'sh_calendly_event_type_uri_uniq',
            'unique(uri)',
            'A Calendly Event Type URI must be unique.',
        ),
    ]
