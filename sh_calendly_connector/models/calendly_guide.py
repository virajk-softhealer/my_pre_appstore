# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

from odoo import fields, models


class CalendlyGuide(models.Model):
    """Shared, read-only setup instructions for the Calendly connector."""

    _name = 'sh.calendly.guide'
    _description = 'Calendly Setup Guide'
    _rec_name = 'name'

    name = fields.Char(required=True)
    content = fields.Html(sanitize=True, readonly=True)
