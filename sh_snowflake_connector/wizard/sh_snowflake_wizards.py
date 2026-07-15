# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import _, fields, models


class SnowflakeTestConnectionWizard(models.TransientModel):
    _name = "snowflake.test.connection.wizard"
    _description = "Snowflake Test Connection Wizard"

    connection_id = fields.Many2one("snowflake.connection", required=True)
    result_message = fields.Text(readonly=True)
    result_state = fields.Selection([("success", "Success"), ("failed", "Failed")], readonly=True)

    def action_test(self):
        self.ensure_one()
        try:
            self.connection_id.action_test_connection()
            self.write({"result_state": "success", "result_message": self.connection_id.last_test_message})
        except Exception as exc:
            self.write({"result_state": "failed", "result_message": str(exc)})
            raise
        return self._close()

    def _close(self):
        return {"type": "ir.actions.act_window_close"}


class SnowflakeColumnPreviewWizard(models.TransientModel):
    _name = "snowflake.column.preview"
    _description = "Snowflake Column Preview Wizard"

    table_config_id = fields.Many2one("snowflake.table.config", required=True)
    preview_text = fields.Text(readonly=True)

    def action_refresh(self):
        self.ensure_one()
        lines = []
        for mapping in self.table_config_id.field_mapping_ids.filtered("is_included").sorted("sequence"):
            lines.append("%s | %s | %s" % (mapping.column_name, mapping.column_type, mapping.odoo_field_type))
        self.preview_text = "\n".join(lines) or _("No mapped columns found.")
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "new"}


class SnowflakeDataPreviewWizard(models.TransientModel):
    _name = "snowflake.data.preview"
    _description = "Snowflake Data Preview Wizard"

    table_config_id = fields.Many2one("snowflake.table.config", required=True)
    preview_text = fields.Text(readonly=True)

    def action_refresh(self):
        self.ensure_one()
        records = self.table_config_id.env[self.table_config_id.model_name].search(self.table_config_id._get_domain(), limit=10)
        lines = []
        for record in records:
            lines.append("%s" % record.display_name)
        self.preview_text = "\n".join(lines) or _("No records found.")
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id, "view_mode": "form", "target": "new"}
