# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging
import time
from ast import literal_eval
from datetime import timedelta
import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sh_snowflake_connection import _quote_sql_identifier


_logger = logging.getLogger(__name__)


class SnowflakeTableConfig(models.Model):
    _name = "snowflake.table.config"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Snowflake Table Configuration"
    _order = "id desc"

    name = fields.Char(string="Table Name", required=True, tracking=True)
    database_id = fields.Many2one("snowflake.database", required=True, ondelete="cascade", index=True)
    connection_id = fields.Many2one(related="database_id.connection_id", store=True, readonly=True, index=True)
    company_id = fields.Many2one(related="connection_id.company_id", store=True, readonly=True, index=True)
    schema_name = fields.Char(required=True, tracking=True)
    use_table_relationship = fields.Boolean(default=True, tracking=True)
    sync_existing_data = fields.Boolean(default=True, tracking=True)
    cron_export_data = fields.Boolean(default=True, tracking=True)
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", tracking=True)
    model_name = fields.Char(related="model_id.model", store=True, readonly=True)
    field_mapping_ids = fields.One2many("snowflake.field.mapping", "table_config_id", string="Field Mapping")
    select_all_columns = fields.Boolean(compute="_compute_select_all_columns", inverse="_inverse_select_all_columns")
    domain_filter = fields.Char(default="[]", tracking=True)
    table_state = fields.Selection(
        [("not_created", "Not Created"), ("created", "Created")],
        default="not_created",
        readonly=True,
        tracking=True,
    )
    migration_state = fields.Selection(
        [("up_to_date", "Up To Date"), ("pending_migration", "Pending Migration")],
        default="up_to_date",
        readonly=True,
        tracking=True,
    )
    created_by_id = fields.Many2one("res.users", related="create_uid", readonly=True, string="Created By")
    created_on = fields.Datetime(related="create_date", readonly=True, string="Created On")
    last_updated_on = fields.Datetime(related="write_date", readonly=True, string="Last Updated On")
    table_schema_version = fields.Integer(default=1, readonly=True, tracking=True)
    last_sync_date = fields.Datetime(readonly=True, tracking=True)
    last_sync_new_count = fields.Integer(readonly=True, tracking=True)
    last_sync_updated_count = fields.Integer(readonly=True, tracking=True)
    last_sync_status = fields.Selection([("success", "Success"), ("failed", "Failed")], readonly=True, tracking=True)
    sync_log_ids = fields.One2many("snowflake.sync.log", "table_config_id", string="Sync Logs")
    migration_log_ids = fields.One2many("snowflake.migration.log", "table_config_id", string="Migration Logs")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        database_id = self.env.context.get("database_id")
        schema_name = self.env.context.get("schema_name")
        if database_id and "database_id" in fields_list and not values.get("database_id"):
            values["database_id"] = database_id
        if schema_name and "schema_name" in fields_list and not values.get("schema_name"):
            values["schema_name"] = schema_name
        return values

    def _get_default_column_type(self, field_type):
        mapping = {
            "char": "VARCHAR",
            "text": "VARCHAR",
            "html": "VARCHAR",
            "selection": "VARCHAR",
            "integer": "NUMBER",
            "float": "FLOAT",
            "monetary": "FLOAT",
            "boolean": "BOOLEAN",
            "date": "DATE",
            "datetime": "TIMESTAMP_NTZ",
            "many2one": "NUMBER",
            "one2many": "VARIANT",
            "many2many": "VARIANT",
            "binary": "VARIANT",
        }
        return mapping.get(field_type, "VARCHAR")

    @api.depends("field_mapping_ids.is_included", "field_mapping_ids.active")
    def _compute_select_all_columns(self):
        for record in self:
            active_lines = record.field_mapping_ids.filtered("active")
            record.select_all_columns = bool(active_lines) and all(active_lines.mapped("is_included"))

    def _inverse_select_all_columns(self):
        for record in self:
            active_lines = record.field_mapping_ids.filtered("active")
            if active_lines:
                active_lines.write({"is_included": record.select_all_columns})

    def _get_odoo_fields(self):
        self.ensure_one()
        return self.env["ir.model.fields"].search([("model_id", "=", self.model_id.id), ("store", "=", True)], order="id")

    def _get_snowflake_connection(self):
        self.ensure_one()
        return self.connection_id._open_connection()

    def _get_table_sql_name(self):
        self.ensure_one()
        return "%s.%s.%s" % (
            _quote_sql_identifier(self.database_id.name),
            _quote_sql_identifier(self.schema_name),
            _quote_sql_identifier(self.name),
        )

    def _get_existing_columns(self):
        self.ensure_one()
        connection = self._get_snowflake_connection()
        try:
            cursor = connection.cursor()
            sql = (
                "SELECT COLUMN_NAME, DATA_TYPE "
                "FROM %s.INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = %%s AND TABLE_NAME = %%s"
            ) % _quote_sql_identifier(self.database_id.name)
            cursor.execute(sql, (self.schema_name, self.name))
            return {row[0].upper(): (row[1] or "").upper() for row in cursor.fetchall()}
        finally:
            connection.close()

    def _ensure_snowflake_namespace(self, cursor):
        self.ensure_one()
        cursor.execute("CREATE DATABASE IF NOT EXISTS %s" % _quote_sql_identifier(self.database_id.name))
        cursor.execute(
            "CREATE SCHEMA IF NOT EXISTS %s.%s"
            % (
                _quote_sql_identifier(self.database_id.name),
                _quote_sql_identifier(self.schema_name),
            )
        )

    def _ensure_export_table(self, cursor):
        self.ensure_one()
        self._ensure_snowflake_namespace(cursor)
        cursor.execute(self._build_create_table_sql())

    def action_generate_field_mapping(self):
        for record in self:
            existing = record.field_mapping_ids.mapped("field_name")
            field_values = []
            for field in record._get_odoo_fields():
                if field.name in existing:
                    continue
                if field.ttype == "binary":
                    continue
                field_values.append(
                    (
                        0,
                        0,
                        {
                            "odoo_field_id": field.id,
                            "column_name": field.name,
                            "column_type": record._get_default_column_type(field.ttype),
                            "is_primary_key": field.name == "id",
                            "is_included": True,
                        },
                    )
                )
            if field_values:
                record.write({"field_mapping_ids": field_values})
        return True

    def action_open_export_wizard(self):
        return self.action_sync_data()

    def action_sync_data(self):
        self.ensure_one()
        summary = self.action_export_data(incremental=bool(self.last_sync_date))
        if summary["status"] == "success":
            message = _(
                "Export completed successfully. %(new)s new record(s) and %(updated)s updated record(s) were synced."
            ) % {
                "new": summary["new_record_count"],
                "updated": summary["updated_record_count"],
            }
            notif_type = "success"
        elif summary["new_record_count"] or summary["updated_record_count"]:
            message = _(
                "Export completed with %(failed)s failed record(s). %(new)s new record(s) and %(updated)s updated record(s) were synced."
            ) % {
                "failed": summary["failed_record_count"],
                "new": summary["new_record_count"],
                "updated": summary["updated_record_count"],
            }
            notif_type = "warning"
        else:
            message = summary["error_message"] or _("Export sync failed.")
            notif_type = "danger"
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Snowflake Sync"),
                "message": message,
                "type": notif_type,
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_open_column_preview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Column Preview"),
            "res_model": "snowflake.column.preview",
            "view_mode": "form",
            "target": "new",
            "context": {"default_table_config_id": self.id},
        }

    def action_open_data_preview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Data Preview"),
            "res_model": "snowflake.data.preview",
            "view_mode": "form",
            "target": "new",
            "context": {"default_table_config_id": self.id},
        }

    def _build_create_table_sql(self):
        self.ensure_one()
        columns = []
        for mapping in self.field_mapping_ids.filtered("is_included").sorted("sequence"):
            if mapping.column_type == "VARCHAR":
                column_sql = "%s VARCHAR" % _quote_sql_identifier(mapping.column_name)
            elif mapping.column_type == "NUMBER":
                column_sql = "%s NUMBER" % _quote_sql_identifier(mapping.column_name)
            elif mapping.column_type == "FLOAT":
                column_sql = "%s FLOAT" % _quote_sql_identifier(mapping.column_name)
            elif mapping.column_type == "BOOLEAN":
                column_sql = "%s BOOLEAN" % _quote_sql_identifier(mapping.column_name)
            elif mapping.column_type == "DATE":
                column_sql = "%s DATE" % _quote_sql_identifier(mapping.column_name)
            elif mapping.column_type == "TIMESTAMP_NTZ":
                column_sql = "%s TIMESTAMP_NTZ" % _quote_sql_identifier(mapping.column_name)
            else:
                column_sql = "%s VARIANT" % _quote_sql_identifier(mapping.column_name)
            columns.append(column_sql)
        if not columns:
            raise UserError(_("Please generate field mapping before creating the table."))
        return "CREATE TABLE IF NOT EXISTS %s (%s)" % (self._get_table_sql_name(), ", ".join(columns))

    def action_create_table_structure(self):
        for record in self:
            try:
                connection = record._get_snowflake_connection()
                try:
                    cursor = connection.cursor()
                    record._ensure_snowflake_namespace(cursor)
                    cursor.execute(record._build_create_table_sql())
                    connection.commit()
                finally:
                    connection.close()
                record.table_state = "created"
                record.migration_state = "up_to_date"
                record.table_schema_version = 1
            except Exception as exc:  # pragma: no cover - surfaced to user
                raise UserError(_("Create table failed:\n%s") % exc) from exc
        return True

    def action_publish_table(self):
        return self.action_create_table_structure()

    def action_unpublish_table(self):
        return self.action_delete_table()

    def action_check_migration(self):
        for record in self:
            if not record.field_mapping_ids:
                record.migration_state = "pending_migration"
                continue
            existing_columns = record._get_existing_columns()
            mapped_columns = {
                mapping.column_name.upper(): mapping.column_type.upper()
                for mapping in record.field_mapping_ids.filtered("is_included")
            }
            normalized_existing_columns = {
                name: record._normalize_snowflake_column_type(data_type) for name, data_type in existing_columns.items()
            }
            if set(normalized_existing_columns.keys()) != set(mapped_columns.keys()):
                record.migration_state = "pending_migration"
                continue
            type_mismatch = any(normalized_existing_columns[name] != mapped_columns[name] for name in mapped_columns)
            record.migration_state = "pending_migration" if type_mismatch else "up_to_date"
        return True

    def action_migrate_schema(self):
        for record in self:
            try:
                connection = record._get_snowflake_connection()
                try:
                    cursor = connection.cursor()
                    table_sql = record._get_table_sql_name()
                    existing_columns = record._get_existing_columns()
                    mapped_columns = {
                        mapping.column_name.upper(): mapping
                        for mapping in record.field_mapping_ids.filtered("is_included")
                    }
                    added_columns = []
                    removed_columns = []
                    altered_columns = []
                    for mapping_name, mapping in mapped_columns.items():
                        if mapping_name not in existing_columns:
                            cursor.execute(
                                "ALTER TABLE %s ADD COLUMN IF NOT EXISTS %s %s"
                                % (table_sql, _quote_sql_identifier(mapping.column_name), mapping.column_type)
                            )
                            added_columns.append(mapping.column_name)
                        elif existing_columns[mapping_name] != mapping.column_type.upper():
                            cursor.execute(
                                "ALTER TABLE %s ALTER COLUMN %s SET DATA TYPE %s"
                                % (table_sql, _quote_sql_identifier(mapping.column_name), mapping.column_type)
                            )
                            altered_columns.append(mapping.column_name)
                    for existing_name in existing_columns:
                        if existing_name not in mapped_columns:
                            cursor.execute("ALTER TABLE %s DROP COLUMN IF EXISTS %s" % (table_sql, _quote_sql_identifier(existing_name)))
                            removed_columns.append(existing_name)
                    connection.commit()
                finally:
                    connection.close()
                self.env["snowflake.migration.log"].create(
                    {
                        "table_config_id": record.id,
                        "added_columns": ", ".join(added_columns),
                        "removed_columns": ", ".join(removed_columns),
                        "altered_columns": ", ".join(altered_columns),
                        "status": "success",
                    }
                )
                record.migration_state = "up_to_date"
                record.table_schema_version = (record.table_schema_version or 0) + 1
            except Exception as exc:  # pragma: no cover - surfaced to user
                self.env["snowflake.migration.log"].create(
                    {
                        "table_config_id": record.id,
                        "status": "failed",
                        "error_message": str(exc),
                    }
                )
                raise UserError(_("Schema migration failed:\n%s") % exc) from exc
        return True

    def action_update_table(self):
        return self.action_migrate_schema()

    def action_delete_table_data(self):
        for record in self:
            try:
                connection = record._get_snowflake_connection()
                try:
                    cursor = connection.cursor()
                    table_sql = record._get_table_sql_name()
                    try:
                        cursor.execute("TRUNCATE TABLE IF EXISTS %s" % table_sql)
                    except Exception as exc:
                        if "Object does not exist" not in str(exc):
                            raise
                    connection.commit()
                finally:
                    connection.close()
            except Exception as exc:  # pragma: no cover - surfaced to user
                raise UserError(_("Delete table data failed:\n%s") % exc) from exc
        return True

    def _get_domain(self):
        self.ensure_one()
        domain = []
        if self.domain_filter:
            try:
                domain = literal_eval(self.domain_filter)
            except Exception as exc:
                raise UserError(_("Invalid domain filter:\n%s") % exc) from exc
        return domain
    
    def _prepare_record_value(self, record, mapping):
        value = record[mapping.field_name]
        if mapping.odoo_field_type == "many2one":
            return value.id if value else None
        if mapping.odoo_field_type in ("one2many", "many2many"):
            return json.dumps(value.ids)
        if mapping.odoo_field_type in ("date",):
            return fields.Date.to_string(value) if value else None
        if mapping.odoo_field_type in ("datetime",):
            return fields.Datetime.to_string(value) if value else None
        if value is None:
            return False
        if isinstance(value, str):
            return str(value)
        if isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, (list, dict, tuple)):
            return json.dumps(value, default=str)
        if hasattr(value, "__html__"):
            return str(value)
        if hasattr(value, "ids"):
            return json.dumps(value.ids)
        if hasattr(value, "id") and hasattr(value, "display_name"):
            return value.id or value.display_name or False
        return str(value)

    def _prepare_variant_value(self, record, mapping):
        value = self._prepare_record_value(record, mapping)
        if value is None:
            return None
        if isinstance(value, str):
            try:
                json.loads(value)
                return value
            except Exception:
                return json.dumps(value)
        return json.dumps(value, default=str)

    def _normalize_snowflake_column_type(self, data_type):
        """Map Snowflake type aliases to the connector's canonical column types."""
        normalized = (data_type or "").upper().strip()
        aliases = {
            "TEXT": "VARCHAR",
            "CHARACTER VARYING": "VARCHAR",
            "INT": "NUMBER",
            "INTEGER": "NUMBER",
            "BIGINT": "NUMBER",
            "SMALLINT": "NUMBER",
            "DECIMAL": "NUMBER",
            "NUMERIC": "NUMBER",
            "DOUBLE": "FLOAT",
            "DOUBLE PRECISION": "FLOAT",
            "REAL": "FLOAT",
            "TIMESTAMP": "TIMESTAMP_NTZ",
            "TIMESTAMP_NTZ(9)": "TIMESTAMP_NTZ",
            "TIMESTAMP_NTZ(0)": "TIMESTAMP_NTZ",
        }
        return aliases.get(normalized, normalized.split("(", 1)[0])

    def action_export_data(self, incremental=True):
        summaries = []
        for record in self:
            summary = {
                "new_record_count": 0,
                "updated_record_count": 0,
                "failed_record_count": 0,
                "error_messages": [],
                "status": "success",
                "error_message": False,
            }
            odoo_model = self.env[record.model_name]
            domain = record._get_domain()
            if incremental and record.last_sync_date:
                domain.append(("write_date", ">=", record.last_sync_date))
            included_mappings = record.field_mapping_ids.filtered("is_included").sorted("sequence")
            if not included_mappings:
                raise UserError(_("Please generate field mapping before exporting data."))
            batch_size = int(self.env["ir.config_parameter"].sudo().get_param("sh_snowflake_connector.default_batch_size", 5000) or 5000)
            batch_size = max(batch_size, 1)
            start = time.time()
            try:
                connection = record._get_snowflake_connection()
                try:
                    cursor = connection.cursor()
                    record._ensure_export_table(cursor)
                    table_sql = "%s.%s.%s" % (
                        _quote_sql_identifier(record.database_id.name),
                        _quote_sql_identifier(record.schema_name),
                        _quote_sql_identifier(record.name),
                    )
                    primary_key = record.field_mapping_ids.filtered("is_primary_key")[:1]
                    key_field = primary_key.column_name if primary_key else "id"
                    offset = 0
                    while True:
                        batch = odoo_model.search(domain, limit=batch_size, offset=offset, order="id")
                        if not batch:
                            break
                        for rec in batch:
                            try:
                                values = []
                                columns = []
                                expressions = []
                                for mapping in included_mappings:
                                    columns.append(_quote_sql_identifier(mapping.column_name))
                                    if mapping.column_type == "VARIANT":
                                        expressions.append("PARSE_JSON(%s)")
                                        values.append(record._prepare_variant_value(rec, mapping))
                                    else:
                                        expressions.append("%s")
                                        values.append(record._prepare_record_value(rec, mapping))
                                pk_value = rec.id if key_field == "id" else record._prepare_record_value(rec, primary_key[0]) if primary_key else rec.id
                                delete_sql = "DELETE FROM %s WHERE %s = %%s" % (table_sql, _quote_sql_identifier(key_field))
                                cursor.execute(delete_sql, (pk_value,))
                                if cursor.rowcount:
                                    summary["updated_record_count"] += 1
                                else:
                                    summary["new_record_count"] += 1
                                # Use SELECT so Snowflake can accept PARSE_JSON() for VARIANT columns.
                                insert_sql = "INSERT INTO %s (%s) SELECT %s" % (
                                    table_sql,
                                    ", ".join(columns),
                                    ", ".join(expressions),
                                )
                                cursor.execute(insert_sql, tuple(values))
                            except Exception as exc:
                                summary["failed_record_count"] += 1
                                error_message = _("%s: %s") % (rec.display_name, exc)
                                summary["error_messages"].append(error_message)
                                _logger.exception(
                                    "Snowflake export failed for record %s on table config %s",
                                    rec.id,
                                    record.id,
                                )
                                continue
                        connection.commit()
                        offset += batch_size
                finally:
                    connection.close()
                summary["status"] = "failed" if summary["failed_record_count"] else "success"
                summary["error_message"] = "\n".join(summary["error_messages"][:5]) if summary["error_messages"] else False
                write_values = {
                    "last_sync_new_count": summary["new_record_count"],
                    "last_sync_updated_count": summary["updated_record_count"],
                    "last_sync_status": "failed" if summary["failed_record_count"] else "success",
                }
                if summary["failed_record_count"]:
                    write_values["last_sync_date"] = False
                else:
                    write_values["last_sync_date"] = fields.Datetime.now()
                record.write(write_values)
                self.env["snowflake.sync.log"].create(
                    {
                        "table_config_id": record.id,
                        "sync_type": "manual",
                        "new_record_count": summary["new_record_count"],
                        "updated_record_count": summary["updated_record_count"],
                        "status": summary["status"],
                        "error_message": summary["error_message"],
                        "duration_seconds": time.time() - start,
                    }
                )
            except Exception as exc:
                summary["status"] = "failed"
                summary["error_message"] = str(exc)
                record.write(
                    {
                        "last_sync_new_count": summary["new_record_count"],
                        "last_sync_updated_count": summary["updated_record_count"],
                        "last_sync_status": "failed",
                    }
                )
                self.env["snowflake.sync.log"].create(
                    {
                        "table_config_id": record.id,
                        "sync_type": "manual",
                        "status": "failed",
                        "error_message": str(exc),
                        "duration_seconds": time.time() - start,
                    }
                )
                raise UserError(_("Export sync failed:\n%s") % exc) from exc
            summaries.append(summary)
        if len(summaries) == 1:
            return summaries[0]
        return summaries

    def action_delete_table(self):
        for record in self:
            try:
                connection = record._get_snowflake_connection()
                try:
                    cursor = connection.cursor()
                    table_sql = "%s.%s.%s" % (
                        _quote_sql_identifier(record.database_id.name),
                        _quote_sql_identifier(record.schema_name),
                        _quote_sql_identifier(record.name),
                    )
                    cursor.execute("DROP TABLE IF EXISTS %s" % table_sql)
                    connection.commit()
                finally:
                    connection.close()
                record.table_state = "not_created"
                record.last_sync_date = False
                record.last_sync_new_count = 0
                record.last_sync_updated_count = 0
                record.last_sync_status = False
            except Exception as exc:  # pragma: no cover - surfaced to user
                raise UserError(_("Delete table failed:\n%s") % exc) from exc
        return True

    def action_remove_table(self):
        return self.action_delete_table()

    def action_reset_sync_status(self):
        self.ensure_one()
        self.write(
                {
                    "last_sync_date": False,
                    "last_sync_new_count": 0,
                    "last_sync_updated_count": 0,
                    "last_sync_status": False,
                }
            )

    def action_open_field_mappings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Field Mappings"),
            "res_model": "snowflake.field.mapping",
            "view_mode": "list,form",
            "domain": [("table_config_id", "=", self.id)],
            "context": {"default_table_config_id": self.id, "default_model_id": self.model_id.id},
        }

    def action_open_sync_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Sync Logs"),
            "res_model": "snowflake.sync.log",
            "view_mode": "list,form",
            "domain": [("table_config_id", "=", self.id)],
        }

    def action_open_migration_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Migration Logs"),
            "res_model": "snowflake.migration.log",
            "view_mode": "list,form",
            "domain": [("table_config_id", "=", self.id)],
        }

    @api.model
    def _cron_cleanup_old_logs(self):
        retention_days = int(self.env["ir.config_parameter"].sudo().get_param("sh_snowflake_connector.log_retention_days", 90) or 90)
        limit_date = fields.Datetime.now() - timedelta(days=retention_days)
        self.env["snowflake.sync.log"].search([("date", "<", limit_date)]).unlink()
        self.env["snowflake.migration.log"].search([("date", "<", limit_date)]).unlink()
