import base64
import hashlib
import json
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from treblo_client.auth import AuthClient, OAuthAttempt, Session, SessionTransport
from treblo_client.protocol import Request, exchange_oauth_code, oauth_authorize
from treblo_client.transport import Response


class CaptureTransport:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return Response(200, {})


class AuthTests(unittest.TestCase):
    def session(self, **extra):
        data = {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
                'expires_at': time.time() + 3600, 'token_type': 'bearer',
                'user': {'id': 'owned-user', 'email': 'owner@example.test'}, **extra}
        return Session.from_response(data, expected_email='owner@example.test')

    def test_pkce_preserves_the_original_verifier_through_the_code_exchange(self):
        attempt = OAuthAttempt.prepare()
        query = parse_qs(urlsplit(attempt.authorize_url).query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual(query['code_challenge'], [expected])
        self.assertEqual(query['provider'], ['google'])
        self.assertEqual(query['redirect_to'], ['https://treblo.com/robots.txt'])
        request = exchange_oauth_code('original-code', attempt.verifier, 'public-key')
        self.assertEqual(request.body['code_verifier'], attempt.verifier)
        self.assertNotIn(attempt.verifier, repr(attempt))
        with self.assertRaises(ValueError):
            oauth_authorize(expected, redirect_to='https://other.example/callback')

    def test_ssr_cookie_round_trips_chunked_unicode_without_google_tokens(self):
        session = self.session(provider_token='unneeded-google-secret', user={
            'id': 'owned-user', 'email': 'owner@example.test', 'metadata': '音' * 2000})
        header = session.cookie_header('sb-project-auth-token')
        parts = header.split('; ')
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p.split('=', 1)[1]) <= 3180 for p in parts))
        encoded = ''.join(p.split('=', 1)[1] for p in parts)[7:]
        decoded = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
        self.assertEqual(decoded['access_token'], session.access_token)
        self.assertEqual(decoded['user']['metadata'], '音' * 2000)
        self.assertNotIn('provider_token', decoded)

    def test_callback_cannot_switch_recipient_or_choose_between_multiple_codes(self):
        attempt = OAuthAttempt.prepare()
        self.assertEqual(attempt.callback_code('https://treblo.com/robots.txt?code=original-code'), 'original-code')
        for url in ['https://other.example/robots.txt?code=original-code',
                    'https://treblo.com/another-path?code=original-code',
                    'https://treblo.com/robots.txt?code=one&code=two',
                    'https://treblo.com/robots.txt?error=access_denied']:
            with self.assertRaises(ValueError):
                attempt.callback_code(url)

    def test_login_session_uses_cookie_for_web_and_bearer_for_backend_only(self):
        capture = CaptureTransport()
        transport = SessionTransport(capture, self.session(), storage_key='sb-project-auth-token')
        transport.execute(Request('GET', 'https://treblo.com/api/v1/users/me'))
        transport.execute(Request('POST', 'https://p.treblo.com/tags/v3/search', {'search': 'piano'}))
        self.assertIn('Cookie', capture.requests[0].headers)
        self.assertNotIn('Authorization', capture.requests[0].headers)
        self.assertEqual(capture.requests[1].headers['Authorization'], 'Bearer synthetic-access')
        self.assertNotIn('Cookie', capture.requests[1].headers)
        for url in ['https://other.example/api', 'https://treblo.com@other.example/api']:
            with self.assertRaises(ValueError):
                transport.execute(Request('GET', url))
        self.assertEqual(len(capture.requests), 2)

    def test_expired_or_different_session_is_not_dispatched(self):
        capture = CaptureTransport()
        session = self.session()
        transport = SessionTransport(capture, session, storage_key='sb-project-auth-token')
        with self.assertRaises(ValueError):
            transport.execute(Request('GET', 'https://p.treblo.com/anything', headers={'Authorization': 'Bearer another-session'}))
        expired = Session(session.access_token, session.refresh_token, 0, session.user_id, session.email)
        with self.assertRaises(ValueError):
            SessionTransport(capture, expired, storage_key='sb-project-auth-token').execute(Request('GET', 'https://p.treblo.com/anything'))
        self.assertEqual(capture.requests, [])

    def test_storage_key_comes_from_the_observed_auth_domain_not_the_jwt_issuer(self):
        capture = CaptureTransport()
        SessionTransport(capture, self.session()).execute(Request('GET', 'https://treblo.com/api/v1/users/me'))
        self.assertTrue(capture.requests[0].headers['Cookie'].startswith('sb-db-auth-token='))

    def test_logout_is_local_to_the_admitted_session_and_cannot_default_to_global(self):
        capture = CaptureTransport()
        auth = AuthClient(capture, publishable_key='public-key', expected_email='owner@example.test')
        intents = []
        auth.sign_out(self.session(), before_dispatch=intents.append)
        self.assertEqual(capture.requests[0].url, 'https://db.treblo.com/auth/v1/logout?scope=local')
        self.assertEqual(capture.requests, intents)
