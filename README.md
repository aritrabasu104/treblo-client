# treblo-client

A small, importable Python client for Treblo authentication, account/project/track
metadata and audio downloads. Python 3.11+, no required third-party dependencies.

This is an independent, unofficial client for the website's service endpoints.
It is not affiliated with Treblo. Endpoint behavior can change. Use an account
you are authorized to access and respect provider refusals and verification.

## Install

Install directly from this repository; no package-index account is required:

```sh
python -m pip install "treblo-client @ git+https://github.com/aritrabasu104/treblo-client.git@v0.2.0"
```

For the optional browser adapter:

```sh
python -m pip install "treblo-client[browser] @ git+https://github.com/aritrabasu104/treblo-client.git@v0.2.0"
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

## Reuse a session and download in parallel

```python
from treblo_client import SessionManager, MemorySessionStore, DownloadRequest

# Memory storage lasts for this process. Use your secure-store adapter below
# for reuse across scheduled runs. Share this manager across calling threads.
manager = SessionManager(auth, MemorySessionStore())
manager.login_with_google(PlaywrightBrowser())  # Opens one tab only if needed.

results = manager.download_many([
    DownloadRequest(first_track_id, Path("first.wav")),
    DownloadRequest(second_track_id, Path("second.wav")),
])  # Default: at most ten concurrent downloads; max_workers is configurable.
for result in results:
    if result.download:
        print(result.download.path)
    elif result.skipped:
        print("Not started because an earlier operation failed")
    else:
        # Reconcile this operation before deciding whether it may be repeated.
        handle_failure(result.request, result.error)
```

The account lock covers session load, login, refresh and secure commit. It is
released **before metadata, conversion or media transfers**. Each download uses
an immutable session snapshot and independent HTTP transport. The lower-level
HTTP transport also keeps its default opener local to each thread.

An already verified session is reused without another login or verification GET
on each download. A session within 90 seconds of expiry is refreshed once under
the account lock. The replacement token pair is committed before verification
or use. Waiting callers load that replacement; they do not rotate the old token
again. Sessions already in use are not mutated underneath running requests.

`get_session()`, `client()` and `download_file()` never open a browser.
`login_with_google()` is explicit, serialized with refresh, and reuses an existing
valid session. Only its owned authentication tab closes, before the lock releases.
A lost login/refresh response retains its pending state and requires reconciliation
without automatic replay. An explicit verification refusal remains blocked.

For reuse across processes/scheduled runs, implement `SessionStore.load()` and
atomic `save(SessionRecord)` using your secure credential store, then supply:

```python
from treblo_client import FileSessionLock

manager = SessionManager(auth, your_secure_store,
    lock=FileSessionLock(your_private_state_directory / "session.lock"))
# Same secure store and canonical lock path in every process for this account.
client = manager.client()
```

`FileSessionLock` supports POSIX systems and stores no credentials. The default
in-memory lock coordinates one manager's threads only. On other platforms supply
an equivalent account lock. Do not keep separate unsynchronized refresh-token
copies. Store failures propagate; an earlier session is not a fallback around a
current secure-store refusal. No plaintext token file, daemon or scheduler is added.

`download_many()` validates distinct track/format and destination pairs before
dispatch, runs at most `max_workers` tasks (default 10; range 1–32), stops admitting
work when a failure is observed, joins already dispatched siblings and returns
every result in input order. It never retries. Concurrent calls/batches are
independent; your scheduler must prevent duplicate requests across them. The
limit is a client setting, not a claim about provider capacity or rate limits.

Applications with durable accounting can keep their own bounded executor and
call `manager.client()` for each task. Use the existing conversion-intent and
issued-URL APIs described above to preserve outcomes across process interruption.
Direct `TrebloClient(session)` still uses a fixed snapshot and does not auto-refresh.

## API reference

| Import | Purpose |
| --- | --- |
| `AuthClient` | Password login, Google bootstrap, PKCE exchange, refresh, exact-account verification, local-only logout |
| `Session`, `OAuthAttempt`, `SessionTransport` | Private token pair, PKCE state and origin-bound authentication |
| `TrebloClient` | `current_user()`, `project(id)`, paginated `project_tracks(id)`, `track(id)`, conversion and `download_file()` |
| `SessionManager`, `SessionRecord`, `SessionStore` | Short account coordination, secure-store contract, session reuse and serialized refresh |
| `MemorySessionStore`, `FileSessionLock`, `SessionUnavailable` | Process-lifetime storage, optional POSIX coordination and a required login/reconciliation |
| `DownloadRequest`, `DownloadResult`, `download_many` | Bounded parallel downloads with retained partial success and no retries |
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
