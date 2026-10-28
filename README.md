# treblo-client

A small, importable Python client for Treblo authentication, account/project/track
metadata and audio downloads. Python 3.11+, no required third-party dependencies.

This is an independent, unofficial client for the website's service endpoints.
It is not affiliated with Treblo. Endpoint behavior can change. Use an account
you are authorized to access and respect provider refusals and verification.

## Install

Install directly from this repository; no package-index account is required:

```sh
python -m pip install "treblo-client @ git+https://github.com/aritrabasu104/treblo-client.git@v0.1.0"
```

For the optional browser adapter:

```sh
python -m pip install "treblo-client[browser] @ git+https://github.com/aritrabasu104/treblo-client.git@v0.1.0"
```

The core client does not require Chrome, Playwright or an MCP server for API
requests or refresh. Chrome is needed when using the optional browser bootstrap.

## Configure authentication

Supply the website's current **public Supabase publishable key** through
`TREBLO_PUBLIC_AUTH_KEY` or the `publishable_key` constructor argument. This is
public application configuration, distinct from your password and session
tokens. No account credentials or configured key values are bundled here.
Obtain the current value from the website's public frontend configuration or
the `apikey` header of its normal auth requests. Do not copy `Authorization`
or `Cookie` headers as configuration.

```python
import os
from treblo_client import AuthClient, TrebloClient

auth = AuthClient(
    expected_email="you@example.com",
    publishable_key=os.environ["TREBLO_PUBLIC_AUTH_KEY"],
)

# For an account with an actual Treblo password:
session = auth.login(os.environ["TREBLO_PASSWORD"])
auth.verify(session)
client = TrebloClient(session)
account = client.current_user()
```

A Google password is not a Treblo password. Do not put passwords, refresh tokens,
callback URLs or signed media URLs in source control, command arguments or logs.

## Google authorization and automatic tab cleanup

Google's initial authorization uses its normal interactive sign-in. The client
exchanges its own PKCE code over HTTP, verifies the exact expected account, and
closes **only the tab it created**, in `finally`, after success, cancellation,
timeout, rejected callback, token-exchange failure or account mismatch.

```python
from treblo_client.browser import PlaywrightBrowser

# Explicitly launch a temporary Chrome window for normal interactive sign-in.
session = auth.login_with_google(PlaywrightBrowser(), timeout=300)
client = TrebloClient(session)
```

To use an already configured Chrome instance instead, supply its existing CDP
endpoint explicitly: `PlaywrightBrowser(cdp_url=your_selected_endpoint)`. The
adapter creates and closes one new page in that context. It never closes the
existing browser, context or other tabs, and does not silently select another
context or fall back to a new browser after a connection failure. The package
does not enable remote debugging or alter browser security settings.

Other integrations can supply an object with `open(authorize_url)`. Its returned
tab must implement `wait_for_callback(expected_url, timeout=seconds)` and
`close()`. The adapter must clean up an owned tab if `open()` itself fails.

The callback uses the first-party plain resource `https://treblo.com/robots.txt`.
It avoids running the website's normal session writer. The original callback
recipient and code are checked before exchange; existing browser sessions are
neither extracted nor rotated by this package.

## Download audio

Use the actual track UUID from a verified metadata response:

```python
from pathlib import Path

track = client.track(track_id)
download = client.download_file(track_id, Path("track.wav"), format="wav")
print(download.path, download.size_bytes, download.sha256)
```

Formats: `wav`, `mp3`, `flac`, `m4a`, `ogg`. The downloader validates HTTPS CDN
origin, complete Content-Length when supplied, byte limits and audio container
signature. It hashes while streaming, writes an exclusive partial, and publishes
without replacing existing files. Credentials are never forwarded to the CDN.
Only its own partial is removed on failure. WAV duration is read from bounded
RIFF metadata when available. Container checks do not assert that every sample
decodes, or assess musical quality.

For applications with durable request accounting, supply
`before_dispatch=request_intent_callback`; it runs before a conversion POST.
Use the lower-level `client.download(...)` to securely retain an issued URL
before transfer, then `stream_download(...)` to resume that exact URL. Do not
automatically repeat a conversion after `OutcomeUnknown`.

## Refresh and background use

```python
# Under your application's account lock:
replacement = auth.renew(session)
auth.verify(replacement)
# Securely commit replacement before using it or releasing that lock.
session = replacement
client = TrebloClient(session)
```

Keep the separately owned session through your own secure credential store.
Refresh tokens rotate. The package adds no token file, background refresh
thread, scheduler, retry loop or implicit browser launch. After revocation or a
required reauthentication, use the normal login flow again.

## API reference

| Import | Purpose |
| --- | --- |
| `AuthClient` | Password login, Google bootstrap, PKCE exchange, refresh, exact-account verification, local-only logout |
| `Session`, `OAuthAttempt`, `SessionTransport` | Private token pair, PKCE state and origin-bound authentication |
| `TrebloClient` | `current_user()`, `project(id)`, paginated `project_tracks(id)`, `track(id)`, conversion and `download_file()` |
| `stream_download`, `Download` | Bounded transfer of an already issued media URL and byte receipt |
| `HttpTransport` | Bounded JSON HTTP with an honest application User-Agent and no automatic retries |
| `GenerationV3` | Request preparation for observed generation fields |
| `ApiError`, `AccessRefused`, `OutcomeUnknown` | Explicit HTTP failure, access refusal and an uncertain mutation result |
| `probe_audio` | Optional bounded local FFprobe metadata; no full-stream decode |
| `MediaAccessRefused`, `MediaTransferError` | Non-retryable CDN refusal or sanitized interrupted transfer |

Session issuance/refresh, authenticated reads, conversion and complete transfers
have been exercised. Project creation and generation request builders are
available but their submission behavior has not been live verified by this
project. CAPTCHA/MFA/consent challenges stay with the provider's normal flow.

## Development

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
python -m pip wheel --no-deps . -w dist
```

Tests use synthetic accounts and fake HTTP/browser responses. They never submit
credentials or create provider content. MIT licensed.
