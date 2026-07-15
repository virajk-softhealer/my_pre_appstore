# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import _, fields, models
from odoo.exceptions import UserError

from .sh_snowflake_connection import _quote_sql_identifier


class SnowflakeDatabase(models.Model):
    _name = "snowflake.database"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Snowflake Database"
    _order = "id desc"

    name = fields.Char(required=True, tracking=True)
    connection_id = fields.Many2one("snowflake.connection", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="connection_id.company_id", store=True, readonly=True, index=True)
    schema_name = fields.Char(required=True, tracking=True)
    state = fields.Selection(
        [
            ("to_publish", "To Publish"),
            ("published", "Published"),
        ],
        default="to_publish",
        readonly=True,
        tracking=True,
    )
    table_count = fields.Integer(compute="_compute_table_count")
    table_config_ids = fields.One2many("snowflake.table.config", "database_id", string="Table Configurations")
    table_ids = fields.Many2many(
        "snowflake.table.config",
        relation="snowflake_database_table_config_rel",
        column1="database_id",
        column2="table_config_id",
        compute="_compute_table_ids",
        inverse="_inverse_table_ids",
        string="Tables",
    )
    created_by_id = fields.Many2one("res.users", related="create_uid", readonly=True, string="Created By")
    created_on = fields.Datetime(related="create_date", readonly=True, string="Created On")
    last_updated_on = fields.Datetime(related="write_date", readonly=True, string="Last Updated On")

    def _compute_table_ids(self):
        for database in self:
            database.table_ids = database.table_config_ids

    def _inverse_table_ids(self):
        for database in self:
            current_tables = database.table_config_ids
            selected_tables = database.table_ids
            added_tables = selected_tables - current_tables
            removed_tables = current_tables - selected_tables
            if added_tables:
                added_tables.write({"database_id": database.id})
            if removed_tables:
                removed_tables.write({"database_id": False})

    def _compute_table_count(self):
        for database in self:
            database.table_count = len(database.table_ids)

    def action_create_database(self):
        for database in self:
            try:
                connection = database.connection_id._open_connection()
                try:
                    cursor = connection.cursor()
                    sql = "CREATE DATABASE IF NOT EXISTS %s" % _quote_sql_identifier(database.name)
                    cursor.execute(sql)
                    if database.schema_name:
                        cursor.execute(
                            "CREATE SCHEMA IF NOT EXISTS %s.%s"
                            % (
                                _quote_sql_identifier(database.name),
                                _quote_sql_identifier(database.schema_name),
                            )
                        )
                    connection.commit()
                finally:
                    connection.close()
                database.state = "published"
            except Exception as exc:  # pragma: no cover - surfaced to user
                raise UserError(_("Database creation failed:\n%s") % exc) from exc
        return True

    def action_fetch_tables(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Table Configurations"),
            "res_model": "snowflake.table.config",
            "view_mode": "list,form",
            "domain": [("database_id", "=", self.id)],
            "context": {
                "default_database_id": self.id,
                "default_schema_name": self.schema_name,
            },
        }
