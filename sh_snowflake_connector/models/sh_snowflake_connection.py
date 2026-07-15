# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError


_logger = logging.getLogger(__name__)


def _quote_sql_identifier(value):
    return '"%s"' % (value or "").replace('"', '""')


def _normalize_snowflake_value(value):
    """Strip accidental whitespace, wrapping quotes, and URL prefixes."""
    if not value:
        return value
    value = value.strip()
    if value.startswith(("https://", "http://")):
        value = value.split("://", 1)[1].split("/", 1)[0]
    return value.strip('"').strip("'")


class SnowflakeConnection(models.Model):
    _name = "snowflake.connection"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Snowflake Connection"
    _order = "id desc"

    name = fields.Char(string="Instance Name",required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    account_identifier = fields.Char(string="Snowflake Account", required=True, tracking=True)
    auth_type = fields.Selection(
        [
            ("password", "Username / Password"),
            ("pat", "Programmatic Access Token"),
        ],
        required=True,
        default="password",
        tracking=True,
    )
    login = fields.Char(string="User", required=True, tracking=True)
    password = fields.Char(string="Password")
    pat_token = fields.Char(string="Access Token")
    warehouse = fields.Char(required=True, tracking=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("tested", "Connected"),
            ("failed", "Failed"),
        ],
        default="draft",
        readonly=True,
        tracking=True,
    )
    last_test_date = fields.Datetime(readonly=True, tracking=True)
    last_test_message = fields.Text(readonly=True)
    database_ids = fields.One2many("snowflake.database", "connection_id", string="Databases")
    table_config_ids = fields.One2many("snowflake.table.config", "connection_id", string="Table Configs")

    def _get_connection_kwargs(self):
        self.ensure_one()
        kwargs = {
            "account": _normalize_snowflake_value(self.account_identifier),
            "warehouse": _normalize_snowflake_value(self.warehouse),
        }
        if self.auth_type == "password":
            kwargs.update(
                {
                    "user": _normalize_snowflake_value(self.login),
                    "password": self.password,
                }
            )
        else:
            kwargs.update(
                {
                    "user": _normalize_snowflake_value(self.login),
                    "password": self.pat_token,
                }
            )
        return {key: value for key, value in kwargs.items() if value}

    def _open_connection(self):
        self.ensure_one()
        try:
            import snowflake.connector
        except ImportError as exc:
            raise UserError(_("Snowflake connector library is not installed: %s") % exc) from exc
        connection = snowflake.connector.connect(**self._get_connection_kwargs())
        self._activate_warehouse(connection)
        return connection

    def _activate_warehouse(self, connection):
        self.ensure_one()
        warehouse = _normalize_snowflake_value(self.warehouse)
        if not warehouse:
            raise UserError(_("Please configure a Snowflake warehouse."))
        cursor = connection.cursor()
        try:
            candidate_names = []
            for candidate in (warehouse, warehouse.upper()):
                if candidate and candidate not in candidate_names:
                    candidate_names.append(candidate)
            last_error = None
            for candidate in candidate_names:
                try:
                    cursor.execute("USE WAREHOUSE %s" % _quote_sql_identifier(candidate))
                    return
                except Exception as exc:
                    last_error = exc
                    _logger.warning(
                        "Unable to activate Snowflake warehouse %s for connection %s: %s",
                        candidate,
                        self.id,
                        exc,
                    )
            _logger.warning(
                "Continuing without explicit warehouse activation for connection %s after %s attempt(s). Last error: %s",
                self.id,
                len(candidate_names),
                last_error,
            )
        finally:
            cursor.close()

    def _build_notification_action(self, title, message, notif_type="success", next_action=None):
        params = {
            "title": title,
            "message": message,
            "type": notif_type,
            "sticky": False,
        }
        if next_action:
            params["next"] = next_action
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": params,
        }

    def _execute_scalar(self, sql):
        self.ensure_one()
        connection = self._open_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            return cursor.fetchone()
        finally:
            connection.close()

    def action_test_connection(self):
        for connection in self:
            try:
                result = connection._execute_scalar("SELECT CURRENT_VERSION()")
                message = _("Connected! Warehouse: %(warehouse)s. Version: %(version)s") % {
                    "warehouse": connection.warehouse,
                    "version": result[0] if result else "",
                }
                connection.write(
                    {
                        "state": "tested",
                        "last_test_date": fields.Datetime.now(),
                        "last_test_message": message,
                    }
                )
                return connection._build_notification_action(
                    _("Connection Test"),
                    message,
                    "success",
                    {"type": "ir.actions.client", "tag": "soft_reload"},
                )
            except Exception as exc:
                message = str(exc)
                connection.write(
                    {
                        "state": "failed",
                        "last_test_date": fields.Datetime.now(),
                        "last_test_message": message,
                    }
                )
                return connection._build_notification_action(
                    _("Connection Test Failed"),
                    message,
                    "danger",
                    {"type": "ir.actions.client", "tag": "soft_reload"},
                )
        return True

    def action_reset_configuration(self):
        self.ensure_one()
        self.write(
            {
                "state": "draft",
                "last_test_date": False,
                "last_test_message": False,
            }
        )
        return {"type": "ir.actions.client", "tag": "soft_reload"}
