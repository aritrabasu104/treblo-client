import time
import unittest
from unittest.mock import Mock

from treblo_client import (TrebloClient, SessionManager, SessionRecord,
                          MemorySessionStore, Session, ApiError, OutcomeUnknown, SessionUnavailable)

PROJECT = '00000000-0000-4000-8000-000000000001'
TOP = '00000000-0000-4000-8000-000000000002'
BOTTOM = '00000000-0000-4000-8000-000000000003'


class GenerationLifecycleTests(unittest.TestCase):
    def client(self):
        client = TrebloClient(Mock())
        client.project = Mock(return_value={'entities': {'projects': {PROJECT: {'id': PROJECT, 'artistId': 'owner'}}}})
        def page(ids, cursor=None):
            return {'data': {'trackIds': ids, 'nextCursor': cursor}, 'entities': {'tracks': {
                identity: {'id': identity, 'projectId': PROJECT, 'artistId': 'owner'} for identity in ids}}}
        client.project_tracks = Mock(side_effect=[page([TOP], 'next'), page([BOTTOM])])
        return client, page

    def test_complete_owned_census_preserves_display_order(self):
        client, _ = self.client()
        self.assertEqual([t['id'] for t in client.owned_project_tracks(PROJECT, artist_id='owner')], [TOP, BOTTOM])

    def test_duplicate_partial_census_is_not_absence_or_selection(self):
        client, page = self.client()
        client.project_tracks.side_effect = [page([TOP], 'next'), page([TOP])]
        with self.assertRaises(ValueError):
            client.owned_project_tracks(PROJECT, artist_id='owner')

    def test_other_account_prevents_track_read(self):
        client, _ = self.client()
        with self.assertRaises(ValueError):
            client.owned_project_tracks(PROJECT, artist_id='different')
        client.project_tracks.assert_not_called()

    def test_valid_session_and_successful_refresh_do_not_open_browser(self):
        for expiry in [time.time() + 3600, time.time() - 1]:
            session = Session('access', 'refresh', expiry, 'user', 'owner@example.test')
            store = MemorySessionStore(SessionRecord(session))
            auth = Mock(expected_email=session.email)
            auth.renew.return_value = Session('new', 'new-refresh', time.time()+3600, 'user', session.email)
            manager = SessionManager(auth, store)
            browser = Mock()
            manager.get_session(browser=browser)
            browser.open.assert_not_called()
            auth.login_with_google.assert_not_called()

    def test_only_definite_expired_refresh_can_enter_login_and_cleanup_fences_api(self):
        session = Session('access', 'refresh', time.time()-1, 'user', 'owner@example.test')
        store = MemorySessionStore(SessionRecord(session))
        auth = Mock(expected_email=session.email)
        auth.renew.side_effect = ApiError(400, 'refresh_token_expired')
        def login(browser, *, timeout, on_authenticated):
            self.assertEqual(store.record.state, 'login_intent')
            on_authenticated(Session('new', 'new-refresh', time.time()+3600, 'user', session.email))
            raise RuntimeError('Owned tab did not close')
        auth.login_with_google.side_effect = login
        manager = SessionManager(auth, store)
        with self.assertRaisesRegex(RuntimeError, 'did not close'):
            manager.get_session(browser=Mock())
        self.assertEqual(store.record.state, 'login_cleanup_pending')
        self.assertEqual(store.record.session.access_token, 'new')
        with self.assertRaises(SessionUnavailable):
            manager.get_session(browser=Mock())
        self.assertEqual(auth.login_with_google.call_count, 1)

    def test_unknown_refresh_never_opens_login_or_repeats_rotation(self):
        session = Session('access', 'refresh', time.time()-1, 'user', 'owner@example.test')
        store = MemorySessionStore(SessionRecord(session))
        auth = Mock(expected_email=session.email)
        auth.renew.side_effect = OutcomeUnknown('Original refresh is unknown')
        manager = SessionManager(auth, store)
        with self.assertRaises(OutcomeUnknown):
            manager.get_session(browser=Mock())
        with self.assertRaises(SessionUnavailable):
            manager.get_session(browser=Mock())
        auth.login_with_google.assert_not_called()
        self.assertEqual(auth.renew.call_count, 1)
