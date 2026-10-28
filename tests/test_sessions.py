from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import wave

from treblo_client import (AuthClient, Session, SessionManager, SessionRecord,
    MemorySessionStore, FileSessionLock, SessionUnavailable, OutcomeUnknown,
    DownloadRequest, download_many, TrebloClient, SessionTransport, HttpTransport, ApiError)
from treblo_client.transport import Response


EMAIL = 'owner@example.test'


def session():
    return Session.from_response({'access_token': 'synthetic-access',
        'refresh_token': 'synthetic-refresh', 'expires_at': time.time() + 3600,
        'user': {'id': 'owned-user', 'email': EMAIL}}, expected_email=EMAIL)


class SessionTests(unittest.TestCase):
    def auth(self):
        return Mock(expected_email=EMAIL)

    def test_concurrent_cold_login_opens_and_closes_one_tab_then_reuses_session(self):
        auth = AuthClient(expected_email=EMAIL, publishable_key='synthetic-key')
        store = MemorySessionStore()
        tab, browser = Mock(), Mock()
        browser.open.return_value = tab
        tab.wait_for_callback.return_value = 'https://treblo.com/robots.txt?code=synthetic'
        barrier = threading.Barrier(10)
        with tempfile.TemporaryDirectory() as root, \
                patch.object(auth, 'exchange_callback', return_value=session()) as exchange, \
                patch.object(auth, 'verify') as verify:
            def login(_):
                # Separate manager instances coordinate through the same path.
                manager = SessionManager(auth, store, lock=FileSessionLock(Path(root) / 'auth.lock'))
                barrier.wait(timeout=5)
                return manager.login_with_google(browser)
            with ThreadPoolExecutor(max_workers=10) as pool:
                sessions = list(pool.map(login, range(10)))
            self.assertTrue(all(value is sessions[0] for value in sessions))
            for _ in range(3):
                self.assertIs(SessionManager(auth, store).get_session(), sessions[0])
        browser.open.assert_called_once()
        tab.close.assert_called_once()
        exchange.assert_called_once()
        verify.assert_called_once()

    def test_concurrent_expiry_rotates_once_and_commits_before_verification(self):
        old = replace(session(), expires_at=time.time() - 10)
        store = MemorySessionStore(SessionRecord(old))
        auth = self.auth()
        renewed = replace(session(), refresh_token='new-synthetic-refresh')
        def renew(value):
            self.assertIs(value, old)
            self.assertEqual(store.record.state, 'refresh_intent')
            return renewed
        def verify(value):
            self.assertIs(store.record.session, renewed)
            self.assertEqual(store.record.state, 'verification_required')
        auth.renew.side_effect = renew
        auth.verify.side_effect = verify
        manager = SessionManager(auth, store)
        with ThreadPoolExecutor(max_workers=10) as pool:
            values = list(pool.map(lambda _: manager.get_session(), range(20)))
        self.assertTrue(all(value is renewed for value in values))
        auth.renew.assert_called_once()
        auth.verify.assert_called_once()

    def test_unknown_refresh_is_not_replayed_by_waiting_callers(self):
        store = MemorySessionStore(SessionRecord(replace(session(), expires_at=0)))
        auth = self.auth()
        auth.renew.side_effect = OutcomeUnknown('synthetic lost response')
        manager = SessionManager(auth, store)
        def get(_):
            try:
                manager.get_session()
            except (OutcomeUnknown, SessionUnavailable) as error:
                return type(error)
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(get, range(10)))
        self.assertEqual(results.count(OutcomeUnknown), 1)
        self.assertEqual(results.count(SessionUnavailable), 9)
        self.assertEqual(store.record.state, 'refresh_intent')
        auth.renew.assert_called_once()

    def test_store_denial_cannot_be_bypassed_using_an_earlier_snapshot(self):
        store = Mock()
        store.load.side_effect = [SessionRecord(session()), PermissionError('synthetic denied')]
        auth = self.auth(); manager = SessionManager(auth, store)
        manager.get_session()
        with self.assertRaises(PermissionError): manager.get_session()
        auth.verify.assert_not_called(); auth.renew.assert_not_called()

    def test_refused_verification_retains_new_pair_and_never_rotates_again(self):
        store = MemorySessionStore(SessionRecord(replace(session(), expires_at=0)))
        auth = self.auth(); auth.renew.return_value = session()
        auth.verify.side_effect = ApiError(403, 'synthetic_refusal')
        manager = SessionManager(auth, store)
        with self.assertRaises(ApiError): manager.get_session()
        with self.assertRaises(SessionUnavailable): manager.get_session()
        self.assertEqual(store.record.state, 'verification_refused')
        self.assertIs(store.record.session, auth.renew.return_value)
        auth.renew.assert_called_once()

    def test_failed_login_retains_intent_and_closes_owned_tab(self):
        auth = AuthClient(expected_email=EMAIL, publishable_key='synthetic-key')
        browser = Mock(); browser.open.return_value.wait_for_callback.side_effect = TimeoutError()
        manager = SessionManager(auth, MemorySessionStore())
        with self.assertRaises(TimeoutError): manager.login_with_google(browser)
        with self.assertRaises(SessionUnavailable): manager.login_with_google(browser)
        browser.open.assert_called_once(); browser.open.return_value.close.assert_called_once()

    def test_mismatched_account_and_missing_session_do_not_launch_browser(self):
        auth = self.auth(); auth.expected_email = 'other@example.test'
        with self.assertRaises(SessionUnavailable):
            SessionManager(auth, MemorySessionStore(SessionRecord(session()))).get_session()
        with self.assertRaises(SessionUnavailable):
            SessionManager(auth, MemorySessionStore()).get_session()
        auth.login_with_google.assert_not_called()

    @unittest.skipUnless(os.name == 'posix', 'POSIX file lock')
    def test_file_lock_contains_no_credentials_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'auth.lock'
            with FileSessionLock(path)():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.read_bytes(), b'')
            alias = Path(root) / 'alias'; alias.symlink_to(path)
            with self.assertRaises(OSError):
                with FileSessionLock(alias)(): pass

    @unittest.skipUnless(os.name == 'posix', 'POSIX file lock')
    def test_another_process_cannot_enter_until_the_account_lock_releases(self):
        script = """import fcntl,sys
with open(sys.argv[1], 'a+') as handle:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(7)
"""
        with tempfile.TemporaryDirectory() as root:
            path = Path(root).resolve() / 'auth.lock'
            with FileSessionLock(path)():
                blocked = subprocess.run([sys.executable, '-c', script, str(path)], timeout=5)
            admitted = subprocess.run([sys.executable, '-c', script, str(path)], timeout=5)
        self.assertEqual(blocked.returncode, 7)
        self.assertEqual(admitted.returncode, 0)


class ParallelTests(unittest.TestCase):
    def requests(self, root, count):
        root = root.resolve()
        return [DownloadRequest(f'{i:08x}-1111-4111-8111-111111111111', root / f'{i}.wav')
                for i in range(count)]

    def test_default_ten_real_streams_overlap_and_more_work_stays_bounded(self):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(8000)
            out.writeframes(b'\0\0' * 80)
        body = audio.getvalue()
        barrier, guard = threading.Barrier(10), threading.Lock()
        active = peak = conversions = 0
        class Media(io.BytesIO):
            code = 200
            headers = {'Content-Length': str(len(body))}
            def __enter__(self):
                nonlocal active, peak
                with guard:
                    active += 1; peak = max(peak, active)
                barrier.wait(timeout=10)
                return self
            def __exit__(self, *args):
                nonlocal active
                with guard: active -= 1
                return super().__exit__(*args)
        class Transport:
            def execute(self, request):
                nonlocal conversions
                with guard: conversions += 1
                return Response(200, {'audio_url': 'https://cdn.treblo.com/synthetic.wav'})
        auth = Mock(expected_email=EMAIL)
        manager = SessionManager(auth, MemorySessionStore(SessionRecord(session())))
        def client():
            return TrebloClient(SessionTransport(Transport(), manager.get_session()))
        with tempfile.TemporaryDirectory() as root, \
                patch.object(manager, 'client', side_effect=client), \
                patch('treblo_client.media.build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: Media(body)
            results = manager.download_many(self.requests(Path(root).resolve(), 20))
            self.assertTrue(all(item.download and item.error is None for item in results))
            self.assertEqual(peak, 10)
            self.assertEqual(conversions, 20)
            self.assertEqual(len(list(Path(root).glob('*.wav'))), 20)
            self.assertEqual(list(Path(root).glob('*.part')), [])
        auth.verify.assert_not_called(); auth.renew.assert_not_called()

    def test_failure_settles_dispatched_siblings_and_skips_unstarted_tail(self):
        from concurrent.futures import wait as real_wait
        manager, released = Mock(), threading.Event()
        def download(track_id, *a, **k):
            if track_id.startswith('00000000'): raise OutcomeUnknown('synthetic unknown')
            self.assertTrue(released.wait(timeout=5))
            return 'retained successful receipt'
        def wait(*a, **k):
            result = real_wait(*a, **k)
            released.set()
            return result
        manager.download_file.side_effect = download
        with tempfile.TemporaryDirectory() as root, patch('treblo_client.parallel.wait', side_effect=wait):
            results = download_many(manager, self.requests(Path(root), 3), max_workers=2)
        self.assertIsInstance(results[0].error, OutcomeUnknown)
        self.assertEqual(results[1].download, 'retained successful receipt')
        self.assertTrue(results[2].skipped)
        self.assertEqual(manager.download_file.call_count, 2)

    def test_duplicate_destinations_tracks_and_existing_files_rejected_before_dispatch(self):
        manager = Mock()
        with tempfile.TemporaryDirectory() as root:
            requests = self.requests(Path(root), 2)
            for batch in ([requests[0], requests[0]],
                    [requests[0], replace(requests[1], destination=requests[0].destination)]):
                with self.assertRaises(ValueError): download_many(manager, batch)
            requests[1].destination.write_bytes(b'existing')
            with self.assertRaises(FileExistsError): download_many(manager, requests)
        manager.download_file.assert_not_called()

    def test_shared_http_transport_uses_distinct_openers_per_thread(self):
        transport, barrier = HttpTransport(), threading.Barrier(2)
        def opener(_):
            value = transport.opener
            barrier.wait(timeout=5)
            self.assertIs(value, transport.opener)
            return value
        with ThreadPoolExecutor(max_workers=2) as pool:
            values = list(pool.map(opener, range(2)))
        self.assertIsNot(values[0], values[1])
