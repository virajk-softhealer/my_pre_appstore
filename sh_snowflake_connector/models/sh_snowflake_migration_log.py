# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import fields, models


class SnowflakeMigrationLog(models.Model):
    _name = "snowflake.migration.log"
    _description = "Snowflake Migration Log"
    _rec_name = "table_config_id"
    _order = "date desc, id desc"

    table_config_id = fields.Many2one("snowflake.table.config", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="table_config_id.company_id", store=True, readonly=True, index=True)
    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    added_columns = fields.Text()
    removed_columns = fields.Text()
    altered_columns = fields.Text()
    status = fields.Selection([("success", "Success"), ("failed", "Failed")], required=True)
    error_message = fields.Text()
