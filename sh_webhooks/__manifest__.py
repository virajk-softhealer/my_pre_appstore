# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

{
    'name': 'All in One Webhooks | Webhook Integration',
    'author': 'Softhealer Technologies',
    'website': 'https://www.softhealer.com',
    'support': 'support@softhealer.com',
    'category': 'Extra Tools',
    'summary': 'All in One Webhooks, Custom Webhooks, Incoming Webhooks, Outgoing Webhooks, Webhook Integration, Webhook Automation, Webhook Logging, Webhook Analytics Odoo',
    'description': """This module allows you to create custom webhooks for any model in Odoo. You can define HTTP callbacks that fire when records are created, updated, or on a schedule. It supports Python scripted adaptations for URL, headers, and payload.""",
    'version': '0.0.1',
    'depends': ['base', 'base_automation', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'views/sh_webhook_action_views.xml',
        'views/sh_webhook_log_views.xml',
        'views/sh_webhook_auth_views.xml',
        'views/sh_webhook_incoming_views.xml',
        'views/ir_actions_views.xml',
        'data/sh_webhook_log_cron.xml',
        'views/sh_webhook_menus.xml',
    ],
    'images': ['static/description/banner.png'],
    'license': 'OPL-1',
    'installable': True,
    'application': True,
    'auto_install': False,
}
