# Stage 5 security review

Reviewed 2026-11-12. Scope: session HTTP mutations, origin/host checks, secret setup and verification reporting, SQLite isolation and rollback, JSON/resource bounds, and distributable-file selection. This was an offline code and regression review. Only synthetic credentials and temporary databases/files were used; no live API, model, deployment, or external network request was performed.

## Result

One concrete request-size defect was found, reported to the integration owner, fixed, and independently retested. No unresolved security blocker was found in the reviewed paths after that fix.

### SEC-V2-01 — Content-Length alone did not enforce the 64 KiB mutation limit

**Before the fix:** `app/main.py` checked the declared `Content-Length` but never counted the received body. A valid session-create JSON containing 1,000 distinct short neighborhood names was 96,942 bytes. Sending it with these headers returned `201` and persisted all names:

```text
POST /api/sessions
Host: localhost:8000
Origin: http://localhost:8000
Content-Type: application/json
Content-Length: 2
Transfer-Encoding: chunked
```

The payload can be reproduced with:

```python
json.dumps({"conditions": {"preferences": {
    "neighborhoods": ["x" * 90 + str(i) for i in range(1000)]
}}}).encode()
```

This was also reproduced through the installed Uvicorn `H11Protocol` using a memory transport and the actual application, rather than relying only on TestClient's permissive framing. The h11 parser accepts these headers, uses chunked framing, and passes both headers and the full body to the application. Thus larger requests could bypass the advertised limit and consume JSON parsing and persistence resources.

**Fix and retest:** the integration owner added `app/http_safety.py` and registered `BodySizeLimitMiddleware` in `app/main.py`. It counts actual ASGI body bytes before routing or JSON parsing. The same in-memory Uvicorn request now returns `HTTP/1.1 413 Request Entity Too Large`; the database contains zero sessions. A UTF-8 regression also verifies rejection when the character count is below 65,536 but the encoded byte count exceeds the cap.

Regression: `tests/test_review_security_v2.py::test_body_limit_counts_actual_bytes_before_creating_session`.

## Other verified boundaries

| Boundary | Evidence and result |
|---|---|
| Browser-origin protection | Foreign Origin, `Origin: null`, a different port with `Sec-Fetch-Site: same-site`, and `Sec-Fetch-Site: cross-site` were denied with 403. Same-origin JSON succeeds. A foreign Host is denied with 400. Non-JSON mutations are rejected. |
| Atomic session mutation | `BEGIN IMMEDIATE` covers the revision read, callback, snapshot encoding, and write. The store tests demonstrate one winner for concurrent writes at the same revision, no lost updates during revision retries, and rollback for invalid or failed callbacks. |
| Failure after a partial in-memory change | An injected exception after changing a listing status returns the fixed `INTERNAL_ERROR`, leaves every persisted field/revision unchanged, and does not echo or log the synthetic exception detail. |
| JSON and persisted-state bounds | Existing tests cover non-finite values, non-JSON values, malformed IDs, invalid revisions, cyclic/deep documents, excessive node counts, oversized Unicode snapshots, and corrupted stored JSON. Rejected mutations preserve the prior state. |
| Server credentials | Settings repr/public status, session HTTP responses, SQLite files, validation responses, and unexpected-error logs do not expose the synthetic configured credentials in the exercised tests. Session state contains fixture/tool data, not Settings. |
| Key setup | Separate and shared MOLIT key saves preserve unrelated dotenv bindings, including multiline and literal `${...}` values. Shared mode clears old per-service overrides. Atomic replacement/private-permission tests pass, including failure cleanup and mocked Windows ACL handling. A temporary POSIX file was independently confirmed as mode 0600. |
| Verification report | The completed `scripts/check_api.py` copies bounded counts and locally generated statuses/timestamps/warnings into its report, not upstream records or provider warning/error text. Tests cover hostile metadata, unsafe exception strings, missing keys, partial responses, report-write failure, and no-network NOT_RUN behavior. |
| Packaging | Temporary-package regressions confirm `.env`, `.runtime` session/SQLite/WAL/SHM files, and `reports/api_verification_report.json` are absent. A marker placed in every private fixture is absent from every ZIP member. Existing symlink exclusion tests also pass. |

## Executed checks

The clean virtual environment has `include-system-site-packages = false`. Command:

```bash
../zoro-clean-check/bin/python -m pytest -q \
  tests/test_store.py tests/test_tools_api.py tests/test_app.py \
  tests/test_config.py tests/test_package.py tests/test_key_setup.py \
  tests/test_review_security_v2.py
```

Result after the body-limit fix: **214 passed**, with one Starlette TestClient dependency deprecation warning. The four focused cases in `tests/test_review_security_v2.py` all pass. The raw Uvicorn parser reproduction and the dotenv preservation/0600 check were also rerun independently as described above.

These results establish the exercised offline implementation properties. They do not establish live MOLIT credentials/approvals, Windows host execution, or Zoro behavior.
