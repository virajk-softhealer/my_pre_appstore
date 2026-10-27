# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import logging


_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Import event types for companies already connected during installation."""
    from .services.calendly_sync import CalendlySyncService

    for company in env['res.company'].search([
        ('sh_calendly_connection_state', '=', 'connected'),
    ]):
        try:
            CalendlySyncService(env, company).sync_event_types()
        except Exception:
            _logger.exception('Calendly event type import failed during installation: company_id=%s', company.id)
