# Credential, log, and export boundary review — v0.3

Reviewed 2026-11-12. This review used source inspection and synthetic, in-memory
HTTP responses. It did not read the project's actual `.env`, uploaded images,
or verification reports, and did not call a live API. The authorized follow-up
fix changed only the HTTP transport-filter block in `app/molit.py`.

## Resolved finding: upstream HTTP trace metadata could expose a credential

**Severity before fix: medium. Status: fixed and regression-tested.**

`app/molit.py` installed `_DropCredentialLogs` on HTTPX/httpcore loggers. The
old filter rejected a record only when its formatted message contained the literal
`servicekey`. This protects the normal HTTPX request line, but does not protect
httpcore's response-header traces when a provider or gateway echoes a key or a
private URL under another header name.

The offline reproduction uses the real HTTPX transport and HTTP/1.1 parser,
with `httpcore.AsyncMockBackend` supplying a 403 response entirely in memory.
Two cases include a dummy key in `X-Upstream-Diagnostic` or a private `Location` URL.
At DEBUG, `httpcore.http11` emits `receive_response_headers.complete` with the
raw header, and the existing filter accepts it because neither value contains
`servicekey`. The caller receives the fixed `MOLIT_AUTH_ERROR`; the leak is in
the log, not the public response. `run.py` uses a warning log level, which
reduces exposure in the normal launcher. The verification script or a host
that enables HTTP debug logging still reaches the vulnerable behavior.

The fix suppresses complete records from the HTTPX and httpcore transport
loggers without formatting them. It also covers `httpcore.socks`, the remaining
transport logger in the installed dependency source. Application/audit loggers
and fixed public error handling are untouched. Logger-level filters do not
apply to propagated child records, so each transport namespace is explicitly
covered. The tradeoff is intentionally losing low-level transport diagnostics;
the fixed public errors and verification report remain available.

Regression: `tests/test_secrets_v03.py` contains two cases for the same defect.
Both reproduced the log leak before the fix and pass after it. They assert that
there are no captured transport records, that the public auth error remains
fixed, and that an application audit event still reaches logging. They use
actual httpcore traces; `httpx.MockTransport` alone bypasses that layer.

## Other reviewed boundaries

- Key setup validates synthetic values, uses hidden input, refuses echo
  fallback, applies private permissions before writing, and replaces the
  destination atomically. Existing tests cover preservation, cleanup, and
  explicit consent before a live check; no additional concrete defect was
  established here.
- Public status omits key values and configured upstream URLs. The browser
  receives local API responses and has no key-entry/storage path. The default
  application import loads settings, so isolated review code must patch that
  initialization before importing `app.main` or avoid it.
- The MOLIT parser and transport convert the exercised upstream auth/body/
  connection failures into fixed messages. The verification reporter copies
  bounded counts and local diagnostics, not raw records, provider warnings,
  or error text.
- Packaging allows selected source file names/extensions, excludes `.env`,
  `.runtime`, `reports`, and symlink files, and has no permitted raster image
  extension. An additional temporary-package exercise excluded a synthetic
  marker placed in credentials, SQLite/WAL state, verification reports, and
  PNG/JPG/JPEG/WebP filenames. As already documented, it is not a content-based secret scanner
  for secrets deliberately inserted into otherwise allowed source files.

## Verification

The clean `zoro-clean-check/bin/python` environment passed:

- **132 tests** across `test_config.py`, `test_molit.py`, `test_key_setup.py`,
  `test_package.py`, and `test_secrets_v03.py`, with dotenv loading forbidden.
- **44 tests** across `test_app.py` and `test_review_security_v2.py`, with default
  settings and every explicit settings mapping missing `STATE_DB_PATH` redirected
  to a temporary database before application import. Dotenv loading was forbidden.
  One existing Starlette TestClient deprecation warning remains.

The HTTP group was rerun after tightening the settings wrapper: the first run
redirected no-argument settings only, while legacy HTTP fixtures also construct
explicit mappings without a DB path. That first run could initialize the local
default database; it did not inspect runtime records or request session writes.
The final reported run used the stricter isolation above.

These are offline implementation findings. They establish neither live key
approval/validity nor actual Windows ACL execution.
