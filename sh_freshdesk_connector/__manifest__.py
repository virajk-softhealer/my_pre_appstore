# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

{
    'name': 'Freshdesk Connector',
    'author': 'Softhealer Technologies',
    'license': 'OPL-1',
    'website': 'https://www.softhealer.com',
    'support': 'support@softhealer.com',
    'version': '0.0.1',
    'category': 'Extra Tools',
    "summary": """Freshdesk Odoo Connector for two-way synchronization of Contacts and Tickets between Freshdesk and Odoo odoo freshdesk connector integration sync synchronize helpdesk tickets support customer service two way bi directional real time webhook api automations queue import export contacts users data seamless platform link bridge manage tracking logs dashboard automated updates handling support desk help desk software integration module app plugin add on solution connect freshdesk with odoo The Freshdesk Connector integrates Freshdesk with enabling seamless synchronization of contacts tickets and ticket conversations It helps businesses manage customer support efficiently by automatically importing Freshdesk data into The connector also provides enhanced logging displaying synced ticket and contact counts while identifying unsynced tickets with failure reasons With this module you can easily connect the tickets and customers in the to freshdesk Two-way sync for Freshdesk contacts companies tickets conversations and logs Bi-Directional Integration of Freshdesk Integration App freshdesk Integration Freshdesk connector freshdesk ticket integration""",
    'description': """The Freshdesk Odoo Connector provides a seamless two-way synchronization of contacts and tickets between Freshdesk and Odoo. By integrating your support data flow, this app allows you to manage both platforms efficiently. Enjoy manual and automated sync options, streamline customer service operations, and ensure that your helpdesk agents and Odoo users are always working with the latest information.""",
    'depends': ['contacts', 'helpdesk', 'sh_integration_base'],
    'data': [
        'security/sh_freshdesk_groups.xml',
        'security/ir.model.access.csv',
        'data/sh_ir_cron.xml',
        'data/sh_freshdesk_sync_queue_cron.xml',
        'views/sh_res_config_settings_views.xml',
        'views/sh_freshdesk_dashboard_views.xml',
        'views/sh_res_partner_views.xml',
        'views/sh_helpdesk_ticket_views.xml',
        'views/sh_freshdesk_sync_log_views.xml',
        'views/sh_freshdesk_sync_queue_views.xml',
        'views/sh_freshdesk_user_guide_views.xml',
        # Integration base registration (must load after views so menus exist)
        'data/sh_integration_connector_data.xml',
        'data/sh_integration_entity_data.xml',
        'data/sh_integration_menu_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'sh_freshdesk_connector/static/src/js/sh_freshdesk_configuration_action.js',
            'sh_freshdesk_connector/static/src/xml/sh_freshdesk_configuration_action.xml',
        ],
    },
    'installable': True,
    'application': True,
    'auto_install': False,
    "images": ["static/description/background.png", ],
    "price": 200,
    "currency": "EUR"
}
