# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import fields, models


class SnowflakeDashboard(models.TransientModel):
    _name = "snowflake.dashboard"
    _description = "Snowflake Dashboard"

    connection_count = fields.Integer(compute="_compute_stats")
    tested_connection_count = fields.Integer(compute="_compute_stats")
    database_count = fields.Integer(compute="_compute_stats")
    table_config_count = fields.Integer(compute="_compute_stats")
    pending_migration_count = fields.Integer(compute="_compute_stats")
    sync_log_count = fields.Integer(compute="_compute_stats")
    failed_sync_count = fields.Integer(compute="_compute_stats")
    migration_log_count = fields.Integer(compute="_compute_stats")

    def _compute_stats(self):
        connection_model = self.env["snowflake.connection"]
        database_model = self.env["snowflake.database"]
        table_model = self.env["snowflake.table.config"]
        sync_log_model = self.env["snowflake.sync.log"]
        migration_log_model = self.env["snowflake.migration.log"]
        for dashboard in self:
            dashboard.connection_count = connection_model.search_count([])
            dashboard.tested_connection_count = connection_model.search_count([("state", "=", "tested")])
            dashboard.database_count = database_model.search_count([])
            dashboard.table_config_count = table_model.search_count([])
            dashboard.pending_migration_count = table_model.search_count([("migration_state", "=", "pending_migration")])
            dashboard.sync_log_count = sync_log_model.search_count([])
            dashboard.failed_sync_count = sync_log_model.search_count([("status", "=", "failed")])
            dashboard.migration_log_count = migration_log_model.search_count([])

    def action_open_connections(self):
        return self.env.ref("sh_snowflake_connector.action_snowflake_connection").read()[0]

    def action_open_databases(self):
        return self.env.ref("sh_snowflake_connector.action_snowflake_database").read()[0]

    def action_open_table_configs(self):
        return self.env.ref("sh_snowflake_connector.action_snowflake_table_config").read()[0]

    def action_open_sync_logs(self):
        return self.env.ref("sh_snowflake_connector.action_snowflake_sync_log").read()[0]

    def action_open_migration_logs(self):
        return self.env.ref("sh_snowflake_connector.action_snowflake_migration_log").read()[0]
