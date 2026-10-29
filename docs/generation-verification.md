# Website generation and verification

This package uses Treblo's website service endpoints with a website session.
It does not implement Treblo's separately keyed developer API. Website generation
may require verification even while authenticated account/project reads succeed.

The captured frontend handles a generation verification response by executing
Treblo's normal widget. Its genuine success callback resolves a pending promise;
the wrapper then supplies `X-Captcha-Token` and `X-Captcha-Sitekey` on the same
generation attempt and original body. It retains `X-Generation-Attempt-ID`.
Login verification uses a separate site key.

The observed widget execution has an 80-second timeout. That frontend detail is
not a retry allowance. The proof has its own
[single-use, five-minute lifetime](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).
A notification deadline or repeated local/account reads cannot complete the
widget callback. A canceled, expired or failed widget supplies no clearance.

An application owner can explicitly commission a normal browser-generation
backup for a proved rejected API operation. Its native controller must retain
the original rejection, semantic candidate and cumulative budget before admitting
that browser work. The website owns its actual verification flow. A timeout or
uncertain acceptance does not qualify merely because browser transport exists.
This package does not activate that backup automatically.

Version 0.3.2 supports an explicit genuine-proof continuation on `generate()`.
Supply `verification_handler(challenge)` and
`before_verification_dispatch(prepared_request, dispatch)`. The handler returns
`VerificationProof(challenge, token, site_key, issued_at)` from the provider's
normal widget, or `None` when verification remains incomplete. The dispatch
callback commits the new physical intent before HTTP; `dispatch` exposes the
original challenge and proof hash without exposing the proof value.

The SDK preserves the original URL, body and attempt ID. It accepts a proof once,
requires its exact original challenge and a lifetime shorter than five minutes,
and stops if the same challenge kind is returned. Unknown mutation outcomes are
never repeated. At most three distinct challenge kinds are supported within the
same operation. Native controllers must retain these counters across restarts.
The default remains a typed, non-retrying `VerificationRequired` when no handler
is configured. Browser account validation, widget interaction, private IPC and
tab cleanup belong to the caller's original native owner, not this HTTP package.
Completing an unrelated login or repeating Generate to test clearance is not
the missing bridge. Do not synthesize a proof or transfer session cookies.

For a workflow requiring browser-free generation, Treblo documents
[API-key authentication and generation task polling](https://treblo.com/developers/docs)
at `https://api.treblo.com/v1`. That route needs its own developer key and credits.
It has a different task/result contract and must be integrated explicitly rather
than substituted after a website refusal or uncertain mutation.

These are static protocol observations from backend source SHA-256
`cdffa50581283dfad3edbb4ef34ba58dc6f336dfcaea753f62702b7ab6dfbbbf` and provider
source SHA-256 `247e185e7704d9424087a51b1633f212ee5f9f37360af76abbdb5b69717a02e4`.
They do not establish live verification success or a guarantee that every future
frontend uses the same timeouts.
