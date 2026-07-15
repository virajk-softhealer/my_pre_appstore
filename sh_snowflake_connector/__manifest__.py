# -- coding: utf-8 --
# Copyright (C) Softhealer Technologies Pvt. Ltd.

{
    "name": "Snowflake Connector",
    "version": "19.0.1.0.0",
    "category": "Extra Tools",
    "summary": "Connect Odoo with Snowflake for secure sync, table management, and logs",
    "description": """
Snowflake Connector for Odoo 19
================================

This module provides Snowflake connection management, database creation,
table configuration, field mapping, export synchronization, migration logs,
and preview wizards.
""",
    "author": "Softhealer Technologies Pvt. Ltd.",
    "website": "https://www.softhealer.com",
    "license": "LGPL-3",
    "depends": ["base", "mail"],
    "external_dependencies": {
        "python": [
            "snowflake-connector-python",
        ]
    },
    "data": [
        "security/sh_snowflake_security.xml",
        "security/ir.model.access.csv",
        "views/sh_snowflake_connection_views.xml",
        "views/sh_snowflake_database_views.xml",
        "views/sh_snowflake_table_config_views.xml",
        "views/sh_snowflake_field_mapping_views.xml",
        "views/sh_snowflake_log_views.xml",
        "views/sh_snowflake_dashboard_views.xml",
        "views/sh_snowflake_menu_views.xml",
        "data/sh_snowflake_cron_data.xml",
    ],
    "installable": True,
    "application": True,
}
