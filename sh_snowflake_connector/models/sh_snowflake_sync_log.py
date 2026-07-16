# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import fields, models


class SnowflakeSyncLog(models.Model):
    _name = "snowflake.sync.log"
    _description = "Snowflake Sync Log"
    _rec_name = "table_config_id"
    _order = "date desc, id desc"

    table_config_id = fields.Many2one("snowflake.table.config", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="table_config_id.company_id", store=True, readonly=True, index=True)
    date = fields.Datetime(default=fields.Datetime.now, readonly=True)
    sync_type = fields.Selection([("manual", "Manual"), ("scheduled", "Scheduled")], required=True)
    new_record_count = fields.Integer()
    updated_record_count = fields.Integer()
    status = fields.Selection([("success", "Success"), ("failed", "Failed")], required=True)
    error_message = fields.Text()
    duration_seconds = fields.Float()
