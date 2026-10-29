"""Genuine provider-proof handoff for one unchanged generation operation."""
from dataclasses import dataclass, field
import hashlib
import json
import math
import threading
import time


@dataclass(frozen=True)
class GenerationChallenge:
    project_id: str
    attempt_id: str
    request_sha256: str
    kind: str
    ordinal: int
    code: str

    @classmethod
    def for_request(cls, request, project_id, attempt_id, kind, ordinal, code):
        encoded = json.dumps(request.body, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode()
        return cls(project_id, attempt_id, hashlib.sha256(encoded).hexdigest(), kind, ordinal, code)


@dataclass(frozen=True)
class VerificationDispatch:
    challenge: GenerationChallenge
    proof_sha256: str
    issued_at: float


@dataclass(frozen=True)
class VerificationProof:
    """One freshly issued normal-widget response, never a saved login token.

    The provider validates authenticity. This object binds the response to its
    original caller operation and prevents accidental local reuse; the caller
    retains durable single-use/dispatch custody across restarts.
    """
    challenge: GenerationChallenge
    token: str = field(repr=False)
    site_key: str = field(repr=False)
    issued_at: float
    _used: bool = field(default=False, init=False, repr=False, compare=False)
    _lock: object = field(default_factory=threading.Lock, init=False, repr=False, compare=False)

    def consume(self, challenge, *, now=None):
        now = time.time() if now is None else now
        if (self.challenge != challenge or not isinstance(self.token, str)
                or not 1 <= len(self.token) <= 2048
                or not isinstance(self.site_key, str) or not 1 <= len(self.site_key) <= 255
                or any(char in self.token + self.site_key for char in "\r\n\x00")
                or type(self.issued_at) not in (int, float) or not math.isfinite(self.issued_at)
                or not -5 <= now - self.issued_at < 300):
            raise ValueError("Verification proof is stale, invalid or belongs to another operation")
        with self._lock:
            if self._used:
                raise ValueError("Verification proof has already been consumed")
            object.__setattr__(self, "_used", True)
        return {"X-Captcha-Token": self.token, "X-Captcha-Sitekey": self.site_key}, VerificationDispatch(
            challenge, hashlib.sha256(self.token.encode()).hexdigest(), self.issued_at)
