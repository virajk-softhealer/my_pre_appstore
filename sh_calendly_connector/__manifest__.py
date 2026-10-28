# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

{
    'name': 'Calendly Connector',
    'version': '19.0.1.2.75',
    'category': 'Productivity/Calendar',
    'summary': 'Synchronize Calendly bookings with Odoo Calendar, Contacts, and CRM.',
    'description': (
        'OAuth, signed Calendly webhooks, polling recovery, booking cancellation, logs, '
        'and optional Odoo-to-Calendly Calendar synchronization.'
    ),
    'author': 'Softhealer Technologies',
    'website': 'https://www.softhealer.com',
    'license': 'OPL-1',
    'depends': ['crm', 'sh_integration_base'],
    'data': [
        'security/ir.model.access.csv',
        'security/sh_calendly_connector_security.xml',
        'views/calendly_event_type_views.xml',
        'views/calendly_guide_views.xml',
        'views/res_config_settings_views.xml',
        'views/calendar_event_views.xml',
        'views/calendly_sync_log_views.xml',
        'views/calendly_sync_queue_views.xml',
        'data/sh_calendly_guide_data.xml',
        'views/menus.xml',
        'data/calendly_cron.xml',
        'data/calendly_sync_queue_cron.xml',
        'data/sh_integration_connector_data.xml',
        'data/sh_integration_entity_data.xml',
        'data/sh_integration_menu_data.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    # 'post_init_hook': 'post_init_hook',
}
