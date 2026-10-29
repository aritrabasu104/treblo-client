# Security and privacy

Account credentials, provider tokens, callback URLs and signed media URLs belong
in an application-owned vault. This package ships no account configuration.
Examples and tests use synthetic identities and mocked provider responses.

Read transport failures use `TransportUnavailable`, an `OSError`. Dispatched
mutations remain `OutcomeUnknown`. Their default tracebacks suppress raw
credential-bearing transport diagnostics. Explicit provider/security refusals
retain their typed outcomes and must never become automatic login or replay.

Session and request representations omit credentials, account identity and URLs.
Do not log raw response bodies, private object attributes, callback URLs or
unrestricted dataclass serialization. Source privacy checks reject machine paths,
literal account secrets and undeclared/private imports.

Use synthetic data when reporting bugs. Never attach credential files,
environment dumps, authenticated network captures or private account details.
