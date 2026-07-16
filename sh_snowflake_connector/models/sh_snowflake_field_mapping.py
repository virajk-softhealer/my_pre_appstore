# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import api, fields, models


class SnowflakeFieldMapping(models.Model):
    _name = "snowflake.field.mapping"
    _description = "Snowflake Field Mapping"
    _order = "sequence, id"

    table_config_id = fields.Many2one("snowflake.table.config", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="table_config_id.company_id", store=True, readonly=True, index=True)
    odoo_field_id = fields.Many2one(
        "ir.model.fields",
        required=True,
        ondelete="cascade",
        domain="[('model_id', '=', context.get('default_model_id'))]",
    )
    field_name = fields.Char(related="odoo_field_id.name", store=True, readonly=True)
    field_label = fields.Char(related="odoo_field_id.field_description", store=True, readonly=True)
    odoo_field_type = fields.Selection(related="odoo_field_id.ttype", store=True, readonly=True)
    name = fields.Char(compute="_compute_name", store=True)
    column_name = fields.Char(required=True)
    column_type = fields.Selection(
        [
            ("VARCHAR", "VARCHAR"),
            ("NUMBER", "NUMBER"),
            ("FLOAT", "FLOAT"),
            ("BOOLEAN", "BOOLEAN"),
            ("DATE", "DATE"),
            ("TIMESTAMP_NTZ", "TIMESTAMP"),
            ("BINARY", "BINARY"),
            ("VARIANT", "VARIANT"),
        ],
        default="VARCHAR",
        required=True,
    )
    column_length = fields.Integer(default=255)
    is_primary_key = fields.Boolean()
    is_included = fields.Boolean(default=True)
    column_sync_state = fields.Selection(
        [
            ("published", "Published"),
            ("not_published", "Not Published"),
        ],
        default="not_published",
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    @api.depends("field_label", "column_name")
    def _compute_name(self):
        for line in self:
            line.name = line.field_label or line.column_name or line.field_name or "/"

    @api.onchange("odoo_field_id")
    def _onchange_odoo_field_id(self):
        for line in self:
            if not line.odoo_field_id:
                continue
            line.column_name = line.odoo_field_id.name
            line.column_type = line.table_config_id._get_default_column_type(line.odoo_field_id.ttype)
            line.is_primary_key = line.odoo_field_id.name == "id"
            if not line.column_length:
                line.column_length = 255
