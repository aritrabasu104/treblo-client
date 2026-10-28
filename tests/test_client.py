import unittest

from treblo_client.auth import AuthClient, Session
from treblo_client.client import TrebloClient
from treblo_client.protocol import GenerationV3
from treblo_client.transport import Response, ApiError


class ScriptedTransport:
    def __init__(self, responses):
        self.responses, self.requests = iter(responses), []

    def execute(self, request):
        self.requests.append(request)
        return next(self.responses)


class ClientTests(unittest.TestCase):
    def test_login_and_rotation_validate_account_and_keep_tokens_out_of_repr(self):
        def result(token, refresh):
            return Response(200, {'access_token': token, 'refresh_token': refresh,
                                 'expires_in': 3600, 'user': {'id': 'owned-user', 'email': 'owner@example.test'}})
        transport = ScriptedTransport([result('secret-access-1', 'secret-refresh-1'),
                                       result('secret-access-2', 'secret-refresh-2')])
        auth = AuthClient(transport, publishable_key='public-key', expected_email='owner@example.test')
        session = auth.login('password-secret', captcha_token='current-token')
        renewed = auth.renew(session)
        self.assertEqual(transport.requests[1].body['refresh_token'], 'secret-refresh-1')
        self.assertEqual(renewed.access_token, 'secret-access-2')
        self.assertNotIn('secret-', repr(renewed))

    def test_login_account_mismatch_is_not_adopted(self):
        transport = ScriptedTransport([Response(200, {'user': {'email': 'another@example.test'}})])
        auth = AuthClient(transport, publishable_key='public', expected_email='owner@example.test')
        with self.assertRaises(ValueError):
            auth.login('secret')

    def test_generation_refusal_keeps_attempt_and_is_not_retried(self):
        transport = ScriptedTransport([Response(403, {'detail': 'turnstile_required'})])
        client = TrebloClient(transport)
        intents = []
        with self.assertRaises(ApiError) as error:
            client.generate(GenerationV3(prompt='A short original instrumental', instrumental=True),
                            '00000000-0000-4000-8000-000000000001', access_token='secret',
                            attempt_id='00000000-0000-4000-8000-000000000002',
                            client_version='observed', before_dispatch=intents.append)
        self.assertEqual(error.exception.code, 'turnstile_required')
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(len(intents), 1)
        self.assertEqual(transport.requests[0].headers['X-Generation-Attempt-ID'],
                         '00000000-0000-4000-8000-000000000002')

    def test_failed_native_intent_prevents_remote_mutation(self):
        transport = ScriptedTransport([])
        def refused(_):
            raise RuntimeError('Original owner did not admit dispatch')
        with self.assertRaises(RuntimeError):
            TrebloClient(transport).create_project('Test', before_dispatch=refused)
        self.assertEqual(transport.requests, [])
