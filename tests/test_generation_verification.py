import time
from dataclasses import replace
import unittest
from unittest.mock import Mock

from treblo_client import (TrebloClient, GenerationV3, VerificationProof,
                          VerificationRequired, OutcomeUnknown)
from treblo_client.transport import Response

PROJECT = '00000000-0000-4000-8000-000000000001'
ATTEMPT = '00000000-0000-4000-8000-000000000002'


class GenerationVerificationTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.requests = []
        self.responses = [Response(403, {'detail': 'turnstile_required'}), Response(200, {'track_ids': ['a', 'b']})]
        transport = Mock()
        def execute(request):
            self.requests.append(request)
            self.events.append('http')
            value = self.responses.pop(0)
            if isinstance(value, Exception):
                raise value
            return value
        transport.execute.side_effect = execute
        self.client = TrebloClient(transport)
        self.proofs = []

    def proof(self, challenge):
        self.events.append('widget')
        proof = VerificationProof(challenge, 'synthetic-widget-response', 'synthetic-site-key', time.time())
        self.proofs.append(proof)
        return proof

    def journal(self, request, dispatch):
        self.events.append('verification-intent')
        self.assertEqual(request.headers['X-Generation-Attempt-ID'], ATTEMPT)
        self.assertEqual(dispatch.challenge.attempt_id, ATTEMPT)
        self.assertEqual(len(dispatch.proof_sha256), 64)

    def generate(self, **kwargs):
        return self.client.generate(GenerationV3(prompt='Original', lyrics='Exact lyrics'), PROJECT,
            access_token='synthetic-access', attempt_id=ATTEMPT, client_version='observed-version', **kwargs)

    def test_real_proof_callback_preserves_body_attempt_and_pre_http_custody(self):
        self.assertEqual(self.generate(verification_handler=self.proof,
            before_verification_dispatch=self.journal), {'track_ids': ['a', 'b']})
        first, second = self.requests
        self.assertEqual(first.body, second.body)
        self.assertEqual(first.url, second.url)
        self.assertEqual(first.headers['X-Generation-Attempt-ID'], second.headers['X-Generation-Attempt-ID'])
        self.assertNotIn('X-Captcha-Token', first.headers)
        self.assertEqual(second.headers['X-Captcha-Token'], 'synthetic-widget-response')
        self.assertEqual(self.events, ['http', 'widget', 'verification-intent', 'http'])
        self.assertNotIn('synthetic-widget-response', repr(self.proofs[0]))

    def test_default_challenge_still_has_no_automatic_continuation(self):
        with self.assertRaises(VerificationRequired):
            self.generate()
        self.assertEqual(len(self.requests), 1)

    def test_missing_dispatch_owner_prevents_even_initial_http(self):
        with self.assertRaises(ValueError):
            self.generate(verification_handler=self.proof)
        self.assertEqual(self.requests, [])

    def test_same_kind_rejection_stops_without_another_widget(self):
        self.responses[1] = Response(403, {'detail': 'turnstile_required'})
        with self.assertRaises(VerificationRequired):
            self.generate(verification_handler=self.proof, before_verification_dispatch=self.journal)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(self.proofs), 1)

    def test_unknown_continuation_is_never_replayed(self):
        self.responses[1] = OutcomeUnknown('Original reply lost')
        with self.assertRaises(OutcomeUnknown):
            self.generate(verification_handler=self.proof, before_verification_dispatch=self.journal)
        self.assertEqual(len(self.requests), 2)

    def test_no_widget_proof_keeps_original_requirement(self):
        with self.assertRaises(VerificationRequired):
            self.generate(verification_handler=lambda challenge:None, before_verification_dispatch=self.journal)
        self.assertEqual(len(self.requests), 1)

    def test_expired_proof_cannot_reach_http(self):
        def expired(challenge):
            return VerificationProof(challenge, 'synthetic', 'site', time.time()-301)
        with self.assertRaises(ValueError):
            self.generate(verification_handler=expired, before_verification_dispatch=self.journal)
        self.assertEqual(len(self.requests), 1)

    def test_proof_reuse_or_changed_operation_is_rejected(self):
        self.generate(verification_handler=self.proof, before_verification_dispatch=self.journal)
        proof = self.proofs[0]
        with self.assertRaises(ValueError):
            proof.consume(proof.challenge)

    def test_changed_attempt_cannot_supply_a_proof(self):
        def wrong(challenge):
            return VerificationProof(replace(challenge, attempt_id=PROJECT), 'synthetic', 'site', time.time())
        with self.assertRaises(ValueError):
            self.generate(verification_handler=wrong, before_verification_dispatch=self.journal)
        self.assertEqual(len(self.requests), 1)

    def test_journal_failure_sends_no_continuation(self):
        def failed(*args):
            raise RuntimeError('Native intent did not commit')
        with self.assertRaises(RuntimeError):
            self.generate(verification_handler=self.proof, before_verification_dispatch=failed)
        self.assertEqual(len(self.requests), 1)
