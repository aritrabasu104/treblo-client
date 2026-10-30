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

    def resume(self, challenge, **kwargs):
        return self.client.resume_generation_verification(
            GenerationV3(prompt='Original', lyrics='Exact lyrics'), PROJECT,
            access_token='synthetic-access', attempt_id=ATTEMPT,
            client_version='observed-version', challenge=challenge,
            verification_handler=kwargs.get('verification_handler', self.proof),
            before_verification_dispatch=kwargs.get('before_verification_dispatch', self.journal))

    def retained_challenge(self):
        from treblo_client import GenerationChallenge
        request=GenerationV3(prompt='Original',lyrics='Exact lyrics').prepare(PROJECT,
            access_token='synthetic-access',attempt_id=ATTEMPT,client_version='observed-version')
        return GenerationChallenge.for_request(request,PROJECT,ATTEMPT,'turnstile',1,'turnstile_required')

    def test_retained_first_challenge_sends_only_its_proof_bearing_continuation(self):
        self.responses=[Response(200,{'track_ids':['a','b']})]
        self.assertEqual(self.resume(self.retained_challenge()),{'track_ids':['a','b']})
        self.assertEqual(self.events,['widget','verification-intent','http'])
        self.assertEqual(len(self.requests),1)
        self.assertIn('X-Captcha-Token',self.requests[0].headers)

    def test_incomplete_retained_verification_sends_no_http(self):
        with self.assertRaises(VerificationRequired):
            self.resume(self.retained_challenge(),verification_handler=lambda c:None)
        self.assertEqual(self.requests,[])

    def test_changed_retained_operation_or_ordinal_never_calls_widget_or_http(self):
        for values in ({'request_sha256':'0'*64},{'attempt_id':PROJECT},{'ordinal':2},
                {'kind':'passive'},{'code':'unknown-code'}):
            with self.assertRaises(ValueError):self.resume(replace(self.retained_challenge(),**values))
        self.assertEqual((self.events,self.requests),([],[]))

    def test_resumed_same_kind_refusal_has_one_widget_and_one_http_only(self):
        with self.assertRaises(VerificationRequired):self.resume(self.retained_challenge())
        self.assertEqual(self.events,['widget','verification-intent','http'])

    def test_resumed_unknown_or_failed_intent_never_adds_another_post(self):
        self.responses=[OutcomeUnknown('Synthetic proof response lost')]
        with self.assertRaises(OutcomeUnknown):self.resume(self.retained_challenge())
        self.assertEqual(len(self.requests),1)
        self.requests.clear()
        def refused(*args):raise OSError('Synthetic native journal failure')
        with self.assertRaises(OSError):self.resume(self.retained_challenge(),before_verification_dispatch=refused)
        self.assertEqual(self.requests,[])
