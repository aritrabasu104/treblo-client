# Google login: verified flow and limits

The usable Google-login design is one browser authorization followed by direct
HTTP session issuance, refresh and application requests. A fresh end-to-end
check on 29 October 2026 verified this with `treblo-client` 0.2.0. A dependable
HTTP-only cold Google sign-in has not been established for the observed client.

## Fresh live verification

One independent research authorization used the original PKCE verifier and a
new owned browser tab. Selecting the intended existing Google account produced
a code at the selected first-party callback. The library then performed:

| Step | Transport | Observed result |
| --- | --- | --- |
| Existing Google account selection | Normal Chrome authorization | Successful selection RPC and a first-party authorization code |
| `POST /auth/v1/token?grant_type=pkce` | Python HTTPS | 200; independent Treblo session issued |
| `GET /auth/v1/user` | Python HTTPS | 200; exact expected account verified |
| `POST /auth/v1/token?grant_type=refresh_token` | Python HTTPS | 200; replacement token pair issued |
| `GET /auth/v1/user` using the replacement | Python HTTPS | 200; same account verified |
| `POST /auth/v1/logout?scope=local` | Python HTTPS | 204; only the temporary research session ended |

The replacement was retained in private process memory before verification.
Login and refresh used the same account lock. The authentication tab closed
through the browser adapter's ordinary close acknowledgement. No other tab or
stored session was replaced. No credentials, authorization codes, cookie values,
raw HAR or TLS keys are included in this report.

A separate, empty-cookie-jar HTTP observation verified the entry sequence:

1. Treblo's original Supabase authorization URL returned 302.
2. Google's returned authorization URL returned 302.
3. Google's returned identifier URL returned 200 with its real form and context.

This was a separate read-only observation, with no identifier/password POST. A
bare identifier URL lacks that context and is not a meaningful cold-login test.
The earlier credential-form HTTP attempt reached Google's explicit rejection;
that refusal was retained and was not replayed during the new verification.

## What the browser trace establishes

The account-selection request was a POST to Google's
`/v3/signin/_/AccountsSignInUi/data/batchexecute`, using RPC `umJEY` and a
38-position argument array. Its form contained `f.req` and `at`; its URL also
carried current request context. This successful request had existing Google
authentication cookies, including `SID`, `__Secure-1PSID`, `__Secure-3PSID` and
`__Host-GAPS`. Only names, flags and request structure were recorded.

These observations establish that this success used an already authenticated
Google browser session. They do not identify the internal check responsible for
the earlier HTTP rejection. Copying a CSRF field or RPC envelope does not prove
that a fresh client can authenticate an account. This project does not export
browser credentials or replay those private Google requests.

The browser event buffer evicted some early events. The report therefore does
not claim a complete raw browser trace. The later successful RPC, callback and
independently journaled HTTP outcomes were retained; the separate read-only
entry trace supplies distinct evidence for the initial redirects.

## Libraries and alternative flows

| Option | Assessment |
| --- | --- |
| Google's maintained [`google-auth-oauthlib`](https://github.com/googleapis/google-cloud-python/tree/main/packages/google-auth-oauthlib) | Implements authorization and token handling. Its [`open_browser=False`](https://googleapis.dev/python/google-auth-oauthlib/latest/reference/google_auth_oauthlib.flow.html) setting suppresses automatic launch; the user still authorizes through a browser. |
| Google device authorization | Uses a different supported client type and user approval elsewhere. The observed Treblo client previously received `invalid_client`; no device code was issued. |
| Google password grant | The earlier credential-free capability check returned `unsupported_grant_type`; it is not an available replacement for this OAuth flow. |
| [`gpsoauth`](https://github.com/simon-weber/gpsoauth) | Targets Android Google Play Services master tokens and app-specific authorization. Its documented alternative also uses browser sign-in and cookie extraction. It supplies no verified compatible initial login for this Treblo client; it was not installed or credential-tested. |
| Google ID token into Supabase | [Supported by Supabase](https://supabase.com/docs/guides/auth/social-sign-in/auth-google) when an appropriate Google credential already exists. It does not mint the initial Google identity proof. |
| Actual Treblo email/password credentials | `AuthClient.login()` implements this direct HTTP grant. This is conditional on the account actually having a Treblo password; a linked Google account does not establish that. No account password or login method was changed. |

Google documents the browser authorization and subsequent HTTP token exchange
in its [web-server procedure](https://developers.google.com/identity/protocols/oauth2/web-server)
and [desktop procedure](https://developers.google.com/identity/protocols/oauth2/native-app).
The Google authorization code and the later Supabase PKCE code are different;
the library exchanges the latter with its original verifier. Supabase owns the
server-side Google exchange, so this client needs no Google client secret.

## Operational consequence

Keep the normal browser bootstrap for initial Google authorization or required
reauthentication. Scheduled work loads its own securely retained Treblo session
and refreshes it through HTTP. It does not need to authenticate with Google on
each download. Use `SessionManager`, one shared account lock and the existing
parallel-download APIs described in the [README](../README.md).

No authentication implementation change was needed after this verification.
No paid service, interception certificate or packet-capture privilege is needed
for that design. Chrome's per-tab network tooling was sufficient for diagnosis.
