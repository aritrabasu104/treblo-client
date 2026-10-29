"""Explicit login/renewal for a caller-owned session, never a browser's rotation."""

from dataclasses import dataclass, field
import base64
import hashlib
import json
import math
import re
import secrets
import time
import os
from urllib.parse import parse_qs, urlsplit

from .protocol import (password_login, refresh_session, exchange_oauth_code,
                       oauth_authorize, Request, AUTH_ORIGIN, BACKEND_ORIGIN, WEB_ORIGIN,
                       AUTH_STORAGE_KEY, OAUTH_RESEARCH_CALLBACK)
from .transport import Transport, require_success


@dataclass(frozen=True)
class Session:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float
    user_id: str = field(repr=False)
    email: str = field(repr=False)
    cookie_session: str = field(default="", repr=False, compare=False)

    @classmethod
    def from_response(cls, data, *, expected_email: str):
        if not isinstance(data, dict):
            raise ValueError("Authentication response is not an object")
        user = data.get("user", {})
        if not isinstance(user, dict) or user.get("email") != expected_email:
            raise ValueError("Authenticated account does not match the selected account")
        for key in ["access_token", "refresh_token"]:
            if not isinstance(data.get(key), str) or not data[key]:
                raise ValueError("Authentication response omitted the original session tokens")
        expires_at = data.get("expires_at", time.time() + data.get("expires_in", 0))
        if (type(expires_at) not in {int, float} or not math.isfinite(expires_at)
                or expires_at <= time.time()):
            raise ValueError("Authentication response has no current expiry")
        # Treblo needs Supabase tokens, never the separate Google provider token.
        cookie_session = {key: data[key] for key in (
            "access_token", "refresh_token", "token_type", "expires_in", "user") if key in data}
        cookie_session["expires_at"] = expires_at
        return cls(data["access_token"], data["refresh_token"], expires_at,
                   user["id"], expected_email,
                   json.dumps(cookie_session, ensure_ascii=False, separators=(",", ":")))

    def cookie_header(self, storage_key: str) -> str:
        """Reproduce the observed Supabase SSR cookie, not a security-clearance cookie."""
        if not re.fullmatch(r"sb-[a-z0-9]+-auth-token", storage_key) or not self.cookie_session:
            raise ValueError("Original login response and observed storage key required")
        raw = self.cookie_session.encode()
        encoded = "base64-" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
        chunks = [encoded[i:i + 3180] for i in range(0, len(encoded), 3180)]
        return "; ".join((storage_key if len(chunks) == 1 else f"{storage_key}.{i}") + "=" + chunk
                         for i, chunk in enumerate(chunks))


@dataclass(frozen=True)
class OAuthAttempt:
    """Caller-owned one-time PKCE state; do not print or persist in history."""
    verifier: str = field(repr=False)
    authorize_url: str

    @classmethod
    def prepare(cls, *, redirect_to=OAUTH_RESEARCH_CALLBACK):
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        return cls(verifier, oauth_authorize(challenge, redirect_to=redirect_to))

    def callback_code(self, callback_url: str) -> str:
        """Read the code only from this attempt's exact first-party return path."""
        expected = urlsplit(parse_qs(urlsplit(self.authorize_url).query)["redirect_to"][0])
        actual = urlsplit(callback_url)
        if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
            raise ValueError("OAuth callback changed its selected recipient/path")
        query = parse_qs(actual.query)
        if actual.fragment or "error" in query or len(query.get("code", [])) != 1:
            raise ValueError("Original OAuth callback has no unambiguous successful code")
        return query["code"][0]


class SessionTransport:
    """Use a login-issued JWT and SSR session on the original service origins.

    No automatic refresh: the application's account owner serializes
    rotation, commits the replacement securely, and supplies the current pair.
    """
    def __init__(self, transport: Transport, session: Session, *, storage_key: str = AUTH_STORAGE_KEY):
        self.transport, self.session, self.storage_key = transport, session, storage_key

    def execute(self, request: Request):
        if self.session.expires_at <= time.time():
            raise ValueError("Original account owner must renew this expired session first")
        origin = urlsplit(request.url)
        headers = dict(request.headers)
        if origin.scheme != "https" or origin.username or origin.password or origin.port not in {None, 443}:
            raise ValueError("Unselected session destination")
        selected_origin = "https://" + str(origin.hostname)
        if selected_origin in {AUTH_ORIGIN, BACKEND_ORIGIN}:
            supplied = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
            if supplied is not None and supplied != "Bearer " + self.session.access_token:
                raise ValueError("Request used another session's token")
            headers["Authorization"] = "Bearer " + self.session.access_token
        elif selected_origin == WEB_ORIGIN:
            if any(k.lower() in {"authorization", "cookie"} for k in headers):
                raise ValueError("Web authentication must come from this original session")
            headers["Cookie"] = self.session.cookie_header(self.storage_key)
        else:
            raise ValueError("Session credentials cannot leave the selected Treblo API origins")
        return self.transport.execute(Request(request.method, request.url, request.body, headers))


class AuthClient:
    def __init__(self, transport: Transport | None = None, *, expected_email: str,
                 publishable_key: str | None = None):
        from .transport import HttpTransport
        key = publishable_key or os.environ.get("TREBLO_PUBLIC_AUTH_KEY")
        if not key or not expected_email:
            raise ValueError("Provide expected_email and the website's public auth key")
        self.transport, self.publishable_key = transport or HttpTransport(), key
        self.expected_email = expected_email

    def login_with_google(self, browser, *, timeout: float = 300, on_authenticated=None) -> Session:
        """Authorize in one owned tab and close it on every settled exit path.

        Browser adapters implement ``open(url)`` and return an object with
        ``wait_for_callback(expected_url, timeout=...)`` and ``close()``.
        Google handles sign-in normally; no browser credentials are extracted.
        """
        if type(timeout) not in (int, float) or not 0 < timeout <= 1800:
            raise ValueError("Authentication timeout must be between 0 and 1800 seconds")
        attempt = OAuthAttempt.prepare()
        tab = browser.open(attempt.authorize_url)
        try:
            callback = tab.wait_for_callback(OAUTH_RESEARCH_CALLBACK, timeout=timeout)
            session = self.exchange_callback(attempt, callback)
            if on_authenticated is not None:
                on_authenticated(session)
            self.verify(session)
            return session
        finally:
            tab.close()

    def login(self, password: str, *, captcha_token: str | None = None) -> Session:
        request = password_login(self.expected_email, password, self.publishable_key,
                                 captcha_token=captcha_token)
        return Session.from_response(require_success(self.transport.execute(request)),
                                     expected_email=self.expected_email)

    def renew(self, session: Session) -> Session:
        if session.email != self.expected_email:
            raise ValueError("Cannot renew another account's session")
        request = refresh_session(session.refresh_token, self.publishable_key)
        renewed = Session.from_response(require_success(self.transport.execute(request)),
                                        expected_email=self.expected_email)
        if renewed.user_id != session.user_id:
            raise ValueError("Session renewal changed the original user ID")
        return renewed

    def exchange_code(self, attempt: OAuthAttempt, auth_code: str) -> Session:
        request = exchange_oauth_code(auth_code, attempt.verifier, self.publishable_key)
        return Session.from_response(require_success(self.transport.execute(request)),
                                     expected_email=self.expected_email)

    def exchange_callback(self, attempt: OAuthAttempt, callback_url: str) -> Session:
        return self.exchange_code(attempt, attempt.callback_code(callback_url))

    def sign_out(self, session: Session, *, before_dispatch) -> None:
        """End this session only; the caller's owner must admit the local logout."""
        if session.email != self.expected_email:
            raise ValueError("Cannot sign out another account's session")
        request = Request("POST", AUTH_ORIGIN + "/auth/v1/logout?scope=local", headers={
            "apikey": self.publishable_key, "Authorization": "Bearer " + session.access_token})
        before_dispatch(request)
        require_success(self.transport.execute(request))

    def verify(self, session: Session) -> dict:
        request = Request("GET", AUTH_ORIGIN + "/auth/v1/user", headers={
            "apikey": self.publishable_key, "Authorization": "Bearer " + session.access_token})
        user = require_success(self.transport.execute(request))
        if user.get("email") != self.expected_email or user.get("id") != session.user_id:
            raise ValueError("Provider did not verify the selected account")
        return {"user_id": session.user_id, "email_matches": True}
