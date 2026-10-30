import io
import unittest
from email.message import Message
from urllib.error import HTTPError

from treblo_client.protocol import Request
from treblo_client.transport import AccessRefused, ApiError, HttpTransport, OutcomeUnknown, TransportUnavailable, Response, require_success, VerificationRequired


class Opener:
    def __init__(self, error):
        self.error, self.calls = error, 0

    def open(self, request, *, timeout):
        self.calls += 1
        self.last_request = request
        raise self.error


class TransportTests(unittest.TestCase):
    def test_html_access_refusal_stays_definite_without_retry(self):
        headers = Message(); headers['Content-Type'] = 'text/html'; headers['Server'] = 'cloudflare'
        transport = HttpTransport()
        transport.opener = Opener(HTTPError('https://p.treblo.com/test', 403, 'Forbidden', headers, io.BytesIO(b'<html>Blocked</html>')))
        response = transport.execute(Request('POST', 'https://p.treblo.com/test', {}))
        with self.assertRaises(ApiError) as error:
            require_success(response)
        self.assertEqual(error.exception.code, 'edge_or_access_refused')
        self.assertEqual(transport.opener.calls, 1)

    def test_missing_mutation_response_is_unknown_and_not_replayed(self):
        transport = HttpTransport(); transport.opener = Opener(TimeoutError())
        with self.assertRaises(OutcomeUnknown):
            transport.execute(Request('POST', 'https://p.treblo.com/test', {}))
        self.assertEqual(transport.opener.calls, 1)

    def test_arbitrary_provider_detail_is_not_logged_as_an_error_code(self):
        with self.assertRaises(ApiError) as error:
            require_success(Response(403, {'detail': 'private data with spaces and credentials'}))
        self.assertEqual(error.exception.code, 'provider_error')

    def test_default_client_identifies_the_application_without_browser_impersonation(self):
        transport = HttpTransport(); transport.opener = Opener(TimeoutError())
        with self.assertRaises(TransportUnavailable):
            transport.execute(Request('GET', 'https://treblo.com/api/v1/users/me'))
        headers = dict(transport.opener.last_request.header_items())
        self.assertEqual(headers['User-agent'], 'treblo-client/0.3.3')
        self.assertEqual(headers['Accept'], 'application/json')

    def test_observed_cloudflare_refusal_is_not_treated_as_a_login_retry(self):
        with self.assertRaises(AccessRefused) as error:
            require_success(Response(403, {'cloudflare_error': True, 'error_code': 1010,
                                          'error_name': 'browser_signature_banned',
                                          'retryable': False, 'owner_action_required': True}))
        self.assertFalse(error.exception.retryable)
        self.assertTrue(error.exception.owner_action_required)
        self.assertEqual(error.exception.provider_error_code, 1010)

    def test_exact_403_challenges_are_typed_without_replaying_or_renewing(self):
        for code, kind in [('turnstile_required', 'turnstile'), ('captcha_required', 'passive'),
                           ('captcha_challenge_required', 'challenge')]:
            with self.subTest(code=code), self.assertRaises(VerificationRequired) as error:
                require_success(Response(403, {'detail': code}))
            self.assertEqual(error.exception.verification_kind, kind)
            self.assertFalse(error.exception.retryable)
        for status, code in [(500, 'turnstile_required'), (403, 'access_denied')]:
            with self.subTest(status=status, code=code), self.assertRaises(ApiError) as error:
                require_success(Response(status, {'detail': code}))
            self.assertNotIsInstance(error.exception, VerificationRequired)

    def test_json_body_is_not_mislabeled_as_form_data_by_urllib(self):
        transport = HttpTransport(); transport.opener = Opener(TimeoutError())
        with self.assertRaises(OutcomeUnknown):
            transport.execute(Request('POST', 'https://treblo.com/api/v1/projects', {'title': 'Synthetic project'}))
        headers = dict(transport.opener.last_request.header_items())
        self.assertEqual(headers['Content-type'], 'application/json')
        self.assertEqual(transport.opener.last_request.data, b'{"title":"Synthetic project"}')
