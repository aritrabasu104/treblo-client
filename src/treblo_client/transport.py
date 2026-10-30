"""HTTP execution with explicit failures and no mutation replay."""

from dataclasses import dataclass, field
import json
from http.client import HTTPException
import re
from typing import Any, Protocol
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request as UrlRequest, build_opener

from .protocol import Request


@dataclass(frozen=True)
class Response:
    status: int
    data: Any = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)


class Transport(Protocol):
    def execute(self, request: Request) -> Response: ...


class ApiError(RuntimeError):
    def __init__(self, status: int, code: str):
        super().__init__(f"Treblo HTTP {status}: {code}")
        self.status, self.code = status, code


class VerificationRequired(ApiError):
    """A definite provider challenge; waiting or login is not clearance.

    The caller retains its original operation and obtains verification through
    the provider's supported flow. This exception never replays that request.
    """
    def __init__(self, code: str):
        super().__init__(403, code)
        self.verification_kind = {
            "turnstile_required": "turnstile", "captcha_required": "passive",
            "captcha_challenge_required": "challenge",
        }[code]
        self.retryable = False


class TransportUnavailable(OSError):
    """A read failed without exposing credential-bearing transport diagnostics."""


class OutcomeUnknown(RuntimeError):
    """A dispatched mutation has no conclusive response; reconcile, never replay."""


class AccessRefused(ApiError):
    """An explicit provider access refusal, not an expired-login retry signal."""
    def __init__(self, status: int, code: str, *, provider_error_code: int,
                 owner_action_required: bool):
        super().__init__(status, code)
        self.provider_error_code = provider_error_code
        self.retryable = False
        self.owner_action_required = owner_action_required


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpTransport:
    def __init__(self, *, timeout: float = 60):
        self.timeout = timeout
        self._local = threading.local()

    @property
    def opener(self):
        if not hasattr(self._local, "opener"):
            self._local.opener = build_opener(_NoRedirect())
        return self._local.opener

    @opener.setter
    def opener(self, value):
        # Explicit adapters are scoped to the calling thread too.
        self._local.opener = value

    def execute(self, request: Request) -> Response:
        origin = urlsplit(request.url)
        if (origin.scheme != "https" or origin.hostname not in
                {"treblo.com", "p.treblo.com", "db.treblo.com"}
                or origin.username or origin.password or origin.port not in {None, 443}):
            raise ValueError("Request is outside the selected Treblo API origins")
        body = None if request.body is None else json.dumps(
            request.body, allow_nan=False, separators=(",", ":")).encode()
        # Identify this application truthfully; do not impersonate a browser.
        headers = {"User-Agent": "treblo-client/0.3.3", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        headers.update(request.headers)
        native = UrlRequest(request.url, data=body, headers=headers,
                            method=request.method)
        try:
            try:
                response = self.opener.open(native, timeout=self.timeout)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise ApiError(response.code, "response_limit")
                headers = {k.lower(): v for k, v in response.headers.items()
                           if k.lower() in {"content-type", "retry-after", "server", "cf-mitigated",
                                            "treblo-deployment", "cf-error-code", "cf-error-type"}}
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError):
                    data = None
                return Response(response.code, data, headers)
        except (URLError, TimeoutError, ConnectionError, OSError, HTTPException):
            if request.method not in {"GET", "HEAD"}:
                raise OutcomeUnknown("Mutation response unavailable; original attempt requires reconciliation") from None
            raise TransportUnavailable("Treblo read transport is temporarily unavailable") from None


def require_success(response: Response) -> Any:
    if not 200 <= response.status < 300:
        data = response.data if isinstance(response.data, dict) else {}
        if (response.status == 403 and data.get("cloudflare_error") is True
                and data.get("error_code") == 1010
                and data.get("error_name") == "browser_signature_banned"):
            raise AccessRefused(response.status, "cloudflare_browser_signature_banned",
                                provider_error_code=1010,
                                owner_action_required=data.get("owner_action_required") is True)
        detail = data.get("detail")
        error = data.get("error")
        code = (detail if isinstance(detail, str) else
                error.get("code") if isinstance(error, dict) else data.get("code"))
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,96}", code):
            code = "edge_or_access_refused" if response.status == 403 and response.data is None else "provider_error"
        if response.status == 403 and code in {
                "turnstile_required", "captcha_required", "captcha_challenge_required"}:
            raise VerificationRequired(code)
        raise ApiError(response.status, code)
    return response.data
