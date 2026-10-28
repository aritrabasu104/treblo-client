"""Request models for Treblo's web service.

These builders perform no HTTP. Dispatch, accounting and retries belong to the
caller's native owner. In particular, preparing generation is not submitting it.
"""

from dataclasses import dataclass, field
import math
import re
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

WEB_ORIGIN = "https://treblo.com"
AUTH_ORIGIN = "https://db.treblo.com"
BACKEND_ORIGIN = "https://p.treblo.com"
AUTH_STORAGE_KEY = "sb-db-auth-token"
# Public frontend application configuration, not a user or developer API secret.
OAUTH_APP_CALLBACK = WEB_ORIGIN + "/auth/callback?next=%2Fcreate"
OAUTH_RESEARCH_CALLBACK = WEB_ORIGIN + "/robots.txt"


@dataclass(frozen=True)
class Request:
    method: str
    url: str
    body: dict[str, Any] | None = field(default=None, repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)


def _identifier(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("Expected a canonical provider UUID")
    return value


def password_login(email: str, password: str, publishable_key: str, *,
                   captcha_token: str | None = None) -> Request:
    if not email or not password or not publishable_key:
        raise ValueError("Explicit account credentials and publishable key are required")
    return Request("POST", AUTH_ORIGIN + "/auth/v1/token?grant_type=password",
                   {"email": email, "password": password,
                    "gotrue_meta_security": {"captcha_token": captcha_token}},
                   {"apikey": publishable_key, "Content-Type": "application/json"})


def refresh_session(refresh_token: str, publishable_key: str) -> Request:
    if not refresh_token or not publishable_key:
        raise ValueError("The original session's refresh token and publishable key are required")
    return Request("POST", AUTH_ORIGIN + "/auth/v1/token?grant_type=refresh_token",
                   {"refresh_token": refresh_token},
                   {"apikey": publishable_key, "Content-Type": "application/json"})


def oauth_authorize(code_challenge: str, *, redirect_to: str) -> str:
    """Normal Google OAuth; the provider must allow this exact callback.

    This only builds a URL. It grants no Google permissions and follows no
    redirect. The caller owns the original PKCE verifier and callback.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", code_challenge):
        raise ValueError("Expected an S256 PKCE challenge")
    if redirect_to not in {OAUTH_APP_CALLBACK, OAUTH_RESEARCH_CALLBACK}:
        raise ValueError("Unselected callback; the provider's redirect allowlist remains authoritative")
    return AUTH_ORIGIN + "/auth/v1/authorize?" + urlencode({
        "provider": "google", "redirect_to": redirect_to,
        "code_challenge": code_challenge, "code_challenge_method": "s256"})


def exchange_oauth_code(auth_code: str, code_verifier: str, publishable_key: str) -> Request:
    if not auth_code or not publishable_key or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier):
        raise ValueError("The original OAuth code, PKCE verifier and publishable key are required")
    return Request("POST", AUTH_ORIGIN + "/auth/v1/token?grant_type=pkce",
                   {"auth_code": auth_code, "code_verifier": code_verifier},
                   {"apikey": publishable_key, "Content-Type": "application/json"})


def current_user() -> Request:
    """The web BFF uses the current same-origin authenticated cookies."""
    return Request("GET", WEB_ORIGIN + "/api/v1/users/me", headers={"X-Treblo-Platform": "web"})


def project(project_id: str) -> Request:
    return Request("GET", WEB_ORIGIN + "/api/v1/projects/" + _identifier(project_id),
                   headers={"X-Treblo-Platform": "web"})


def track(track_id: str) -> Request:
    return Request("GET", WEB_ORIGIN + "/api/v1/tracks/" + _identifier(track_id),
                   headers={"X-Treblo-Platform": "web"})


@dataclass(frozen=True)
class GenerationV3:
    prompt: str | None = field(default=None, repr=False)
    tags: tuple[str, ...] | None = None
    lyrics: str | None = field(default=None, repr=False)
    instrumental: bool | None = None
    update_project_name: bool | None = None
    prompt_strength: float | None = None
    style_scale: float | None = None
    new_style: Any = None
    creative_boost: Any = None
    repeat_track_id: str | None = None
    stream_format: str | None = None
    compare_mode: bool | None = None
    length_range: tuple[float, float] | None = None
    negative_tags: tuple[str, ...] | None = None
    lyrics_model: str | None = None
    drop_lyrics: bool | None = None

    def payload(self) -> dict[str, Any]:
        if self.instrumental and self.lyrics:
            raise ValueError("Treblo rejects lyrics on an instrumental request")
        if self.length_range is not None:
            if (len(self.length_range) != 2
                    or any(isinstance(x, bool) or not math.isfinite(x) for x in self.length_range)
                    or not 0 <= self.length_range[0] <= self.length_range[1]):
                raise ValueError("Duration must be an ordered finite range in seconds")
        if self.repeat_track_id is not None:
            _identifier(self.repeat_track_id)
        result = {}
        for key in self.__dataclass_fields__:
            value = getattr(self, key)
            if value is not None:
                result[key] = list(value) if isinstance(value, tuple) else value
        return result

    def prepare(self, project_id: str, *, access_token: str,
                attempt_id: str, client_version: str) -> Request:
        """Build the original native attempt; never allocate an ID or retry it.

        Provider-requested verification must return to the normal browser owner.
        This module does not manufacture or replay CAPTCHA tokens.
        """
        if not access_token or not client_version:
            raise ValueError("A current session and observed client version are required")
        _identifier(attempt_id)
        return Request("POST", BACKEND_ORIGIN + "/generate/v3/create/" + _identifier(project_id),
                       self.payload(), {"Authorization": "Bearer " + access_token,
                       "Content-Type": "application/json", "X-Treblo-Client-Platform": "web",
                       "X-Treblo-Client-Version": client_version,
                       "X-Generation-Attempt-ID": attempt_id})


def download_format(track_id: str, format: str, *, access_token: str) -> Request:
    """Prepare the server-conversion path; its result carries audio_url.

    The frontend also offers direct original audio and local browser conversion.
    Those choices must remain explicit; they need not return identical bytes.
    """
    if format not in {"ogg", "mp3", "wav", "flac", "m4a"}:
        raise ValueError("Unsupported observed download format")
    if not access_token:
        raise ValueError("A current authenticated session is required")
    return Request("POST", BACKEND_ORIGIN + "/process/download_audio",
                   {"track_id": _identifier(track_id), "format": format},
                   {"Authorization": "Bearer " + access_token,
                    "Content-Type": "application/json"})
