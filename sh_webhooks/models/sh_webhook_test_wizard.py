# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import models, fields, _
from odoo.exceptions import ValidationError, UserError

class ShWebhookTestWizard(models.TransientModel):
    _name = 'sh.webhook.test.wizard'
    _description = 'Webhook Test Wizard'

    webhook_id = fields.Many2one('sh.webhook.action', string="Webhook")
    model_id = fields.Many2one('ir.model', string="Model")
    res_id = fields.Integer("Record ID", required=True)

    def action_test(self):
        records = self.env[self.webhook_id.model_name].browse(self.res_id)
        if not records.exists():
            raise ValidationError(_("Record with ID %s not found in model %s") % (self.res_id, self.webhook_id.model_name))
        try:
            self.webhook_id.trigger_webhook(records)
        except UserError:
            raise
        except Exception as e:
            raise UserError(_("Webhook test failed:\n%s") % str(e))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Webhook Triggered'),
                'message': _('The webhook has been triggered for record %s. Check logs for details.') % self.res_id,
                'sticky': False,
            }
        }
