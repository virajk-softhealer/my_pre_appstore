# -*- coding: utf-8 -*-
# Copyright (C) Softhealer Technologies Pvt. Ltd.

import base64
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

from odoo import fields


_logger = logging.getLogger(__name__)


class CalendlyApi:
    """Small standard-library Calendly API client with company-scoped credentials."""

    api_url = 'https://api.calendly.com'
    oauth_url = 'https://auth.calendly.com'
    webhook_events = [
        'invitee.created', 'invitee.canceled',
        'event_type.created', 'event_type.updated', 'event_type.deleted',
    ]

    def __init__(self, env, company):
        self.env = env
        self.company = company

    def authorization_action(self):
        if self.company.sh_calendly_auth_method != 'oauth':
            _logger.warning(
                'Calendly OAuth authorization skipped for non-OAuth company_id=%s', self.company.id,
            )
            return False
        state = secrets.token_urlsafe(32)
        self.env['ir.config_parameter'].sudo().set_param(
            f'sh_calendly.oauth_state.{self.company.id}', state,
        )
        params = urllib.parse.urlencode({
            'client_id': self.company.sh_calendly_client_id,
            'response_type': 'code',
            'redirect_uri': self._redirect_uri(),
            'scope': (
                'users:read event_types:read scheduled_events:read scheduled_events:write '
                'webhooks:read webhooks:write'
            ),
            'state': state,
        })
        return {
            'type': 'ir.actions.act_url',
            'url': f'{self.oauth_url}/oauth/authorize?{params}',
            'target': 'self',
        }

    def _redirect_uri(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        return f'{base_url}/calendly/oauth/callback'

    def exchange_code(self, code):
        payload = self._request(
            '/oauth/token', method='POST', base_url=self.oauth_url,
            data={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': self._redirect_uri(),
            }, authenticated=False, form_encoded=True,
            basic_auth=(self.company.sh_calendly_client_id, self.company.sh_calendly_client_secret),
        )
        access_token = (payload or {}).get('access_token')
        if not access_token:
            _logger.error('Calendly OAuth failed: access token missing, company_id=%s', self.company.id)
            return False
        resource = self.get_current_user(access_token=access_token, persist=False)
        if not resource.get('uri'):
            _logger.error(
                'Calendly OAuth validation failed: /users/me returned no user URI, company_id=%s',
                self.company.id,
            )
            return False
        expires_in = int(payload.get('expires_in') or 7200)
        self.company.write({
            'sh_calendly_access_token': access_token,
            'sh_calendly_refresh_token': payload.get('refresh_token') or False,
            'sh_calendly_token_expires_at': fields.Datetime.now() + timedelta(seconds=expires_in),
            'sh_calendly_authenticated_user_uri': resource.get('uri'),
            'sh_calendly_organization_uri': resource.get('current_organization') or payload.get('organization'),
            'sh_calendly_auth_method': 'oauth',
            'sh_calendly_connection_state': 'connected',
        })
        try:
            from .calendly_sync import CalendlySyncService
            CalendlySyncService(self.env, self.company).sync_event_types()
        except Exception:
            _logger.exception('Calendly event type import failed after OAuth: company_id=%s', self.company.id)
        return True

    def _refresh_oauth_token(self):
        """Refresh the OAuth access token and persist the rotated credentials."""
        refresh_token = self.company.sh_calendly_refresh_token
        if not refresh_token:
            _logger.warning('Calendly OAuth refresh token missing: company_id=%s', self.company.id)
            return False
        payload = self._request(
            '/oauth/token', method='POST', base_url=self.oauth_url,
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            }, authenticated=False, form_encoded=True,
            basic_auth=(self.company.sh_calendly_client_id, self.company.sh_calendly_client_secret),
        ) or {}
        access_token = payload.get('access_token')
        if not access_token:
            _logger.error('Calendly OAuth refresh failed: access token missing, company_id=%s', self.company.id)
            return False
        expires_in = int(payload.get('expires_in') or 7200)
        self.company.write({
            'sh_calendly_access_token': access_token,
            'sh_calendly_refresh_token': payload.get('refresh_token') or refresh_token,
            'sh_calendly_token_expires_at': fields.Datetime.now() + timedelta(seconds=expires_in),
            'sh_calendly_connection_state': 'connected',
        })
        _logger.info('Calendly OAuth token refreshed: company_id=%s', self.company.id)
        return access_token

    def _ensure_token(self):
        """Return the active token without exposing it in logs or the UI."""
        if self.company.sh_calendly_auth_method == 'pat':
            if not self.company.sh_calendly_pat_token:
                _logger.warning('Calendly PAT missing: company_id=%s', self.company.id)
                return False
            return self.company.sh_calendly_pat_token
        if self.company.sh_calendly_auth_method != 'oauth':
            _logger.warning(
                'Calendly authentication method is invalid: company_id=%s method=%s',
                self.company.id, self.company.sh_calendly_auth_method,
            )
            return False
        if not self.company.sh_calendly_client_id or not self.company.sh_calendly_client_secret:
            _logger.warning('Calendly OAuth client credentials missing: company_id=%s', self.company.id)
            return False
        if self.company.sh_calendly_access_token and self.company.sh_calendly_token_expires_at:
            refresh_at = self.company.sh_calendly_token_expires_at - timedelta(seconds=60)
            if fields.Datetime.now() < refresh_at:
                return self.company.sh_calendly_access_token
        if self._refresh_oauth_token():
            return self.company.sh_calendly_access_token
        self.company.write({'sh_calendly_connection_state': 'revoked'})
        _logger.warning('Calendly OAuth authorization unavailable: company_id=%s', self.company.id)
        return False

    def _request(
        self, path, method='GET', data=None, authenticated=True, base_url=None, form_encoded=False,
        basic_auth=None, access_token=None, retry_auth=True, error_detail=None, error_response=None,
    ):
        token = access_token
        if authenticated:
            token = token or self._ensure_token()
            if not token:
                return None
        url = f'{base_url or self.api_url}{path}'
        body = None
        content_type = 'application/json'
        if data is not None:
            if form_encoded:
                body = urllib.parse.urlencode(data).encode()
                content_type = 'application/x-www-form-urlencoded'
            else:
                body = json.dumps(data).encode()
        headers = {
            'Accept': 'application/json',
            'Content-Type': content_type,
            'User-Agent': 'Odoo/19.0 sh_calendly_connector',
        }
        if basic_auth:
            credentials = f'{basic_auth[0]}:{basic_auth[1]}'.encode()
            headers['Authorization'] = f'Basic {base64.b64encode(credentials).decode()}'
        if authenticated:
            headers['Authorization'] = f'Bearer {token}'
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response_body = response.read().decode()
                payload = json.loads(response_body) if response_body else {}
                return payload
        except urllib.error.HTTPError as error:
            error_body = error.read().decode(errors='replace')
            try:
                error_payload = json.loads(error_body) if error_body else {}
            except ValueError:
                error_payload = {}
            error_reason = (
                error_payload.get('message')
                or error_payload.get('detail')
                or error_payload.get('title')
                or error.reason
            )
            if error_detail is not None:
                error_detail.append(str(error_reason))
            if error_response is not None:
                error_response.append(error_payload or {
                    'title': 'HTTP Error',
                    'message': str(error_reason),
                    'status': error.code,
                })
            _logger.error(
                'Calendly HTTP request failed: company_id=%s method=%s url=%s status=%s reason=%s',
                self.company.id, method, url, error.code, error_reason,
            )
            if error.code == 401 and authenticated and retry_auth and not access_token:
                if self.company.sh_calendly_auth_method == 'oauth' and self._refresh_oauth_token():
                    return self._request(
                        path, method=method, data=data, authenticated=authenticated,
                        base_url=base_url, form_encoded=form_encoded, basic_auth=basic_auth,
                        access_token=self.company.sh_calendly_access_token, retry_auth=False,
                    )
            return None
        except (urllib.error.URLError, TimeoutError) as error:
            _logger.error(
                'Calendly network request failed: company_id=%s method=%s url=%s error_type=%s',
                self.company.id, method, url, type(error).__name__,
            )
            return None
        except (ValueError, UnicodeDecodeError) as error:
            _logger.error(
                'Calendly response parsing failed: company_id=%s method=%s url=%s error_type=%s',
                self.company.id, method, url, type(error).__name__,
            )
            return None

    def get_current_user(self, access_token=None, persist=True):
        payload = self._request('/users/me', access_token=access_token, retry_auth=not access_token) or {}
        resource = payload.get('resource') or {}
        if persist and resource.get('uri'):
            self.company.write({'sh_calendly_authenticated_user_uri': resource['uri']})
        organization = resource.get('current_organization')
        if persist and organization:
            self.company.write({'sh_calendly_organization_uri': organization})
        if persist and resource.get('uri'):
            self.company.write({'sh_calendly_connection_state': 'connected'})
        _logger.info('Calendly authenticated user validated: company_id=%s', self.company.id)
        return resource

    def test_connection(self):
        """Validate the currently configured PAT or OAuth client credentials."""
        if self.company.sh_calendly_auth_method == 'oauth':
            if not self.company.sh_calendly_client_id or not self.company.sh_calendly_client_secret:
                return False
        return self.validate_connection()

    def validate_connection(self):
        """Validate the current credentials against Calendly, not only the saved state."""
        token = self._ensure_token()
        if not token:
            self.company.write({'sh_calendly_connection_state': 'revoked'})
            return False
        resource = self.get_current_user(access_token=token, persist=False)
        if not resource.get('uri'):
            self.company.write({'sh_calendly_connection_state': 'error'})
            _logger.warning('Calendly credential validation failed: company_id=%s', self.company.id)
            return False
        self.company.write({
            'sh_calendly_connection_state': 'connected',
            'sh_calendly_authenticated_user_uri': resource.get('uri'),
            'sh_calendly_organization_uri': resource.get('current_organization') or False,
        })
        return True

    def register_webhook(self):
        if not self.validate_connection():
            return False
        signing_key = self.company.sh_calendly_webhook_signing_key
        if not signing_key:
            _logger.error('Calendly webhook signing key missing: company_id=%s', self.company.id)
            return False
        organization = self.company.sh_calendly_organization_uri or (self.get_current_user() or {}).get('current_organization')
        if not organization:
            _logger.error('Calendly organization missing: company_id=%s', self.company.id)
            return False

        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '').rstrip('/')
        webhook_url = f'{base_url}/calendly/webhook/{self.company.id}'
        list_path = '/webhook_subscriptions?' + urllib.parse.urlencode({
            'organization': organization,
            'scope': 'organization',
            'count': 100,
        })
        existing = self._request(list_path) or {}
        for subscription in existing.get('collection', []):
            if subscription.get('callback_url') == webhook_url:
                uri = subscription.get('uri')
                if uri and set(self.webhook_events).issubset(set(subscription.get('events') or [])):
                    self.company.write({'sh_calendly_webhook_subscription_uri': uri})
                    return uri
                if uri:
                    self._request(uri.replace(self.api_url, ''), method='DELETE')

        webhook_data = {
            'url': webhook_url,
            'events': self.webhook_events,
            'organization': organization,
            'scope': 'organization',
            'signing_key': signing_key,
        }
        payload = self._request('/webhook_subscriptions', method='POST', data=webhook_data)
        if not payload:
            # A concurrent request may have created the subscription after the list call.
            existing = self._request(list_path) or {}
            for subscription in existing.get('collection', []):
                if subscription.get('callback_url') == webhook_url:
                    uri = subscription.get('uri')
                    if uri and set(self.webhook_events).issubset(set(subscription.get('events') or [])):
                        self.company.write({'sh_calendly_webhook_subscription_uri': uri})
                        return uri
        uri = ((payload or {}).get('resource') or {}).get('uri')
        if not uri:
            _logger.error('Calendly webhook URI missing in response: company_id=%s', self.company.id)
            return False
        self.company.write({'sh_calendly_webhook_subscription_uri': uri})
        return uri

    def disconnect(self):
        if self.company.sh_calendly_webhook_subscription_uri and self._ensure_token():
            try:
                self._request(
                    self.company.sh_calendly_webhook_subscription_uri.replace(self.api_url, ''),
                    method='DELETE',
                )
            except Exception:
                _logger.exception('Calendly webhook deletion failed: company_id=%s', self.company.id)
        self.company.write({
            'sh_calendly_access_token': False,
            'sh_calendly_refresh_token': False,
            'sh_calendly_token_expires_at': False,
            'sh_calendly_authenticated_user_uri': False,
            'sh_calendly_organization_uri': False,
            'sh_calendly_webhook_subscription_uri': False,
            'sh_calendly_connection_state': 'disconnected',
        })

    def list_events(self, min_start_time=None, max_start_time=None):
        current_user = self.get_current_user() or {}
        user_uri = current_user.get('uri')
        params = {
            'count': 100,
            'sort': 'start_time:asc',
        }
        if user_uri:
            params['user'] = user_uri
        else:
            _logger.error('Calendly authenticated user missing: company_id=%s', self.company.id)
            return []
        if min_start_time:
            params['min_start_time'] = min_start_time
        if max_start_time:
            params['max_start_time'] = max_start_time
        events = []
        page_token = False
        while True:
            if page_token:
                params['page_token'] = page_token
            query = urllib.parse.urlencode({key: value for key, value in params.items() if value})
            payload = self._request(f'/scheduled_events?{query}')
            if not isinstance(payload, dict):
                _logger.error(
                    'Calendly scheduled-events response invalid: company_id=%s', self.company.id,
                )
                return events
            _logger.info(
                'Calendly scheduled-events response: company_id=%s collection_count=%s pagination=%s',
                self.company.id, len(payload.get('collection') or []), bool(payload.get('pagination')),
            )
            events.extend((payload or {}).get('collection', []))
            next_page_token = ((payload or {}).get('pagination') or {}).get('next_page_token')
            if not next_page_token or next_page_token == page_token:
                return events
            page_token = next_page_token

    def list_event_types(self):
        current_user = self.get_current_user() or {}
        user_uri = current_user.get('uri') or self.company.sh_calendly_authenticated_user_uri
        if not user_uri:
            _logger.error('Calendly authenticated user missing for event types: company_id=%s', self.company.id)
            return []
        event_types = []
        page_token = False
        while True:
            params = {'user': user_uri, 'active': 'true', 'count': 100}
            if page_token:
                params['page_token'] = page_token
            query = urllib.parse.urlencode(params)
            payload = self._request(f'/event_types?{query}')
            if not isinstance(payload, dict):
                return event_types
            event_types.extend(payload.get('collection') or [])
            next_page_token = (payload.get('pagination') or {}).get('next_page_token')
            if not next_page_token or next_page_token == page_token:
                return event_types
            page_token = next_page_token

    def get_invitee(self, uri):
        payload = self._request(uri.replace(self.api_url, '')) or {}
        return payload.get('resource', {})

    def get_scheduled_event(self, uri):
        payload = self._request(uri.replace(self.api_url, '')) or {}
        return payload.get('resource', {})

    def get_event_type(self, uri):
        payload = self._request(uri.replace(self.api_url, '')) or {}
        return payload.get('resource', {})

    def create_invitee(
        self, event_type, email, start_time, timezone='UTC', event_guests=None, invitee_name=None,
        location=None,
    ):
        if hasattr(start_time, 'strftime'):
            start_time = start_time.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        data = {
            'event_type': event_type,
            'start_time': start_time,
            'invitee': {
                'name': invitee_name or email,
                'email': email,
                'timezone': timezone or 'UTC',
            },
        }
        if event_guests:
            data['event_guests'] = event_guests[:10]
        if location:
            data['location'] = location
        error_detail = []
        error_response = []
        payload = self._request(
            '/invitees', method='POST', data=data, error_detail=error_detail,
            error_response=error_response,
        )
        if payload is None:
            return (error_response[0] if error_response else None), (error_detail[0] if error_detail else False)
        return payload, False

    def cancel_event(self, event_uri):
        path = event_uri.replace(self.api_url, '')
        return self._request(f'{path}/cancellation', method='POST', data={'reason': 'Canceled in Odoo'})
