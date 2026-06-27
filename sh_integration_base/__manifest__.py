# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies.
{
    "name": "Base Integration",
    "author": "Softhealer Technologies",
    "website": "https://www.softhealer.com",
    "support": "support@softhealer.com",
    "version": "0.0.2",
    "license": "OPL-1",
    "category": "Extra Tools",
    "summary": "Base module for Integration",
    "description": """Base Integration module is a base module for below modules.""",
    "depends": ["base","mail"],
    "data": [
        # Security
        'security/ir.model.access.csv',

        # DATA
        'data/sh_api_group_config.xml',
        'data/sh_mass_action_queue.xml',
        'data/sh_ir_log_sequence.xml',
        
        # VIEW'S
        'views/sh_api_scope_view.xml',
        'views/sh_base_config.xml',
        # 'views/sh_lead_log.xml',
        'views/sh_queue_view.xml',
        'views/sh_integration_log_view.xml',
        
        # MENU
        'views/sh_integration_menu.xml',
    ],
    "auto_install": False,
    "installable": True,
    "application": True,
}