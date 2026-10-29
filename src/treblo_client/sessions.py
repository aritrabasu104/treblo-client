"""Short account coordination, immutable session snapshots and parallel HTTP work."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import stat
import threading
import time
from typing import Protocol

from .auth import Session


class SessionUnavailable(RuntimeError):
    """Login or reconciliation is required; do not silently repeat authentication."""


class LoginRequired(SessionUnavailable):
    """The provider definitively rejected an actually expired refresh grant."""


@dataclass(frozen=True)
class SessionRecord:
    session: Session | None = field(default=None, repr=False)
    state: str = "ready"


class SessionStore(Protocol):
    """Implement atomic, secure load/save; never log record contents."""
    def load(self) -> SessionRecord | None: ...
    def save(self, record: SessionRecord) -> None: ...


class MemorySessionStore:
    """Process-lifetime storage; share one manager/lock for this store."""
    def __init__(self, record: SessionRecord | None = None):
        self.record = record

    def load(self):
        return self.record

    def save(self, record):
        self.record = record


class FileSessionLock:
    """POSIX thread/process coordination. The lock file contains no credentials.

    Every process using one secure session store must use the same canonical
    path. This is a short authentication lock, never a download lock.
    """
    _registry_guard = threading.Lock()
    _registry: dict[str, threading.RLock] = {}

    def __init__(self, path):
        path = Path(path).expanduser().absolute()
        self.path = path.parent.resolve() / path.name
        with self._registry_guard:
            self._thread_lock = self._registry.setdefault(str(self.path), threading.RLock())

    @contextmanager
    def __call__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise SessionUnavailable("Session lock is not an owned regular file")
                os.fchmod(fd, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


class SessionManager:
    """Serialize login/refresh, then release the lock before authenticated work.

    ``auth`` is an AuthClient, or a factory called after loading the store.
    ``lock`` is a context-manager factory (FileSessionLock for cross-process
    use). The default RLock coordinates callers of this manager in one process.
    Login is explicit; get_session never opens a browser. No background threads,
    auth retries, or caching around a secure-store denial are introduced.
    """
    def __init__(self, auth, store: SessionStore, *, lock=None, refresh_margin=90):
        if (type(refresh_margin) not in (int, float) or not math.isfinite(refresh_margin)
                or not 0 <= refresh_margin <= 600):
            raise ValueError("refresh_margin must be between 0 and 600 seconds")
        self.auth, self.store, self.refresh_margin = auth, store, refresh_margin
        self._thread_lock = threading.RLock()
        self.lock = lock or (lambda: self._thread_lock)

    def _auth(self):
        return self.auth if hasattr(self.auth, 'expected_email') else self.auth()

    def _current(self, record):
        if record is None:
            raise SessionUnavailable("Explicit login is required")
        if record.state not in {"ready", "verification_required", "expired"} or record.session is None:
            raise SessionUnavailable("Original authentication requires reconciliation without replay")
        session = record.session
        if (not session.access_token or not session.refresh_token or not session.user_id
                or not session.email or not math.isfinite(session.expires_at)):
            raise SessionUnavailable("Stored session is incomplete")
        auth = self._auth()
        if session.email != auth.expected_email:
            raise SessionUnavailable("Stored session belongs to another selected account")
        if record.state == "expired":
            if session.expires_at <= time.time():
                raise LoginRequired("The expired session requires normal sign-in")
            raise SessionUnavailable("Stored expiry state contradicts the original session")
        if record.state == "verification_required":
            self._verify(auth, session)
        if session.expires_at <= time.time() + self.refresh_margin:
            # Persist before dispatch. A lost refresh reply must not spend the
            # old rotating refresh token again, including after a restart.
            self.store.save(SessionRecord(session, "refresh_intent"))
            from .transport import ApiError
            try:
                session = auth.renew(session)
            except ApiError as error:
                if (error.status in {400, 401} and session.expires_at <= time.time()
                        and error.code in {"invalid_grant", "refresh_token_not_found",
                                           "refresh_token_expired", "session_expired"}):
                    self.store.save(SessionRecord(session, "expired"))
                    raise LoginRequired("The expired session requires normal sign-in") from None
                # Unknown rotation and access refusals cannot commission login.
                raise
            # Save the new pair before verification: a failed GET must never
            # send a caller back to an already consumed refresh token.
            self.store.save(SessionRecord(session, "verification_required"))
            self._verify(auth, session)
        return session

    def _verify(self, auth, session):
        from .transport import ApiError
        try:
            auth.verify(session)
        except (ApiError, ValueError):
            self.store.save(SessionRecord(session, "verification_refused"))
            raise
        self.store.save(SessionRecord(session))

    def get_session(self, *, browser=None, timeout=300) -> Session:
        with self.lock():
            record = self.store.load()
            try:
                return self._current(record)
            except LoginRequired:
                if browser is None:
                    raise
                return self._login(browser, self.store.load(), timeout)

    def client(self):
        """A fresh HTTP transport using the current immutable session snapshot."""
        from .client import TrebloClient
        return TrebloClient(self.get_session())

    def login_with_google(self, browser, *, timeout=300) -> Session:
        with self.lock():
            record = self.store.load()
            if record is not None:
                try:
                    return self._current(record)
                except LoginRequired:
                    record = self.store.load()
            return self._login(browser, record, timeout)

    def _login(self, browser, previous, timeout):
        # Retain the old pair and fence interruption before opening a tab.
        self.store.save(SessionRecord(previous.session if previous else None, "login_intent"))
        def authenticated(session):
            self.store.save(SessionRecord(session, "login_cleanup_pending"))
        session = self._auth().login_with_google(browser, timeout=timeout,
                                                on_authenticated=authenticated)
        # Returning from the auth client proves that its owned tab closed.
        self.store.save(SessionRecord(session))
        return session

    def download_file(self, track_id, destination, **options):
        return self.client().download_file(track_id, destination, **options)

    def download_many(self, requests, *, max_workers=10):
        from .parallel import download_many
        return download_many(self, requests, max_workers=max_workers)
