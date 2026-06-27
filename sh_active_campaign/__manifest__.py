# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.
{
    'name': 'ActiveCampaign Odoo Connector',
    'version': '0.0.1',
    'author': 'Softhealer Technologies',
    'website': 'https://www.softhealer.com',
    'support': 'support@softhealer.com',
    'category': 'Sales/CRM',
    'summary': 'Sync Contacts, Deals, Tags, Forms, Campaigns between Odoo and ActiveCampaign',
    'description': """
ActiveCampaign Odoo Connector
==============================
Bidirectional synchronization between Odoo 18 and ActiveCampaign via REST API v3.

Features:
- Import/Export Contacts (res.partner)
- Import/Export Tags (sh.active.campaign.tag)
- Import/Export Deals (crm.lead)
- Import Lists, Campaigns, Forms from ActiveCampaign
- Interactive Dashboard with sync matrix and auto scheduler
- Multi-company support with per-company API credentials
- Secure webhook receiver for real-time events
    """,
    'depends': [
        'crm',
        'mass_mailing',
        'sh_integration_base',
    ],
    'data': [
        'security/sh_ac_security.xml',
        'security/ir.model.access.csv',
        
        'data/sh_ac_cron.xml',
        'data/sh_active_campaign_sync_queue_cron.xml',
        'data/sh_active_campaign_user_guide_data.xml',

        'wizard/sh_ac_message_wizard_views.xml',
        'views/sh_res_config_settings_views.xml',
        'views/sh_active_campaign_dashboard_views.xml',
        'views/sh_active_campaign_campaign_views.xml',
        'views/sh_active_campaign_form_views.xml',
        'views/sh_active_campaign_log_views.xml',
        'views/sh_active_campaign_user_guide_views.xml',
        'views/sh_res_partner_views.xml',
        'views/sh_res_partner_category_views.xml',
        'views/sh_crm_lead_views.xml',
        'views/sh_mailing_list_views.xml',
        'views/sh_active_campaign_sync_queue_views.xml',
        'views/sh_menus.xml',
        # Integration base registration (must load after views so menus exist)
        'data/sh_integration_connector_data.xml',
        'data/sh_integration_entity_data.xml',

    ],
    'images': ['static/description/banner.png'],
    'license': 'OPL-1',
    'installable': True,
    'auto_install': False,
    'application': True,
}
