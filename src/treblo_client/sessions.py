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
        if record.state not in {"ready", "verification_required"} or record.session is None:
            raise SessionUnavailable("Original authentication requires reconciliation without replay")
        session = record.session
        if (not session.access_token or not session.refresh_token or not session.user_id
                or not session.email or not math.isfinite(session.expires_at)):
            raise SessionUnavailable("Stored session is incomplete")
        auth = self._auth()
        if session.email != auth.expected_email:
            raise SessionUnavailable("Stored session belongs to another selected account")
        if record.state == "verification_required":
            self._verify(auth, session)
        if session.expires_at <= time.time() + self.refresh_margin:
            # Persist before dispatch. A lost refresh reply must not spend the
            # old rotating refresh token again, including after a restart.
            self.store.save(SessionRecord(session, "refresh_intent"))
            session = auth.renew(session)
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

    def get_session(self) -> Session:
        with self.lock():
            return self._current(self.store.load())

    def client(self):
        """A fresh HTTP transport using the current immutable session snapshot."""
        from .client import TrebloClient
        return TrebloClient(self.get_session())

    def login_with_google(self, browser, *, timeout=300) -> Session:
        with self.lock():
            record = self.store.load()
            if record is not None:
                return self._current(record)
            self.store.save(SessionRecord(None, "login_intent"))
            session = self._auth().login_with_google(browser, timeout=timeout)
            self.store.save(SessionRecord(session))
            return session

    def download_file(self, track_id, destination, **options):
        return self.client().download_file(track_id, destination, **options)

    def download_many(self, requests, *, max_workers=10):
        from .parallel import download_many
        return download_many(self, requests, max_workers=max_workers)
