# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .sh_snowflake_connection import _quote_sql_identifier


_logger = logging.getLogger(__name__)


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

    def _ensure_connection_is_tested(self):
        self.ensure_one()
        if not self.connection_id or self.connection_id.state != "tested":
            raise UserError(_("Test the Snowflake connection first."))

    def action_create_database(self):
        for database in self:
            try:
                database._ensure_connection_is_tested()
                _logger.info(
                    "Snowflake database create started for database %s (%s) using connection %s.",
                    database.name,
                    database.id,
                    database.connection_id.id,
                )
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
                _logger.info(
                    "Snowflake database create finished for database %s (%s).",
                    database.name,
                    database.id,
                )
            except Exception as exc:  # pragma: no cover - surfaced to user
                _logger.exception(
                    "Snowflake database create failed for database %s (%s): %s",
                    database.name,
                    database.id,
                    exc,
                )
                raise UserError(_("Database creation failed:\n%s") % exc) from exc
        return True

    def action_delete_database(self):
        for database in self:
            try:
                database._ensure_connection_is_tested()
                _logger.info(
                    "Snowflake database delete started for database %s (%s) using connection %s.",
                    database.name,
                    database.id,
                    database.connection_id.id,
                )
                database.table_config_ids.write(
                    {
                        "table_state": "not_created",
                        "migration_state": "up_to_date",
                        "last_sync_date": False,
                        "last_sync_new_count": 0,
                        "last_sync_updated_count": 0,
                        "last_sync_status": False,
                    }
                )
                database.table_config_ids._update_column_sync_states(
                    table_state="not_published",
                    table_column_state="not_published",
                )
                connection = database.connection_id._open_connection()
                try:
                    cursor = connection.cursor()
                    cursor.execute("DROP DATABASE IF EXISTS %s" % _quote_sql_identifier(database.name))
                    connection.commit()
                finally:
                    connection.close()
                database.state = "to_publish"
                _logger.info(
                    "Snowflake database delete finished for database %s (%s).",
                    database.name,
                    database.id,
                )
            except Exception as exc:  # pragma: no cover - surfaced to user
                _logger.exception(
                    "Snowflake database delete failed for database %s (%s): %s",
                    database.name,
                    database.id,
                    exc,
                )
                raise UserError(_("Database deletion failed:\n%s") % exc) from exc
        return True

    def action_fetch_tables(self):
        self.ensure_one()
        _logger.info(
            "Snowflake database fetch tables opened for database %s (%s).",
            self.name,
            self.id,
        )
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

    def unlink(self):
        for database in self:
            if database.connection_id and database.connection_id.state == "tested":
                database.table_config_ids.write(
                    {
                        "table_state": "not_created",
                        "migration_state": "up_to_date",
                        "last_sync_date": False,
                        "last_sync_new_count": 0,
                        "last_sync_updated_count": 0,
                        "last_sync_status": False,
                    }
                )
                database.action_delete_database()
        return super().unlink()
