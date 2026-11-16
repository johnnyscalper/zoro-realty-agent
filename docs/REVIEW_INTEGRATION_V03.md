# V0.3 live-route integration review

Date: 2026-11-12

Outcome: **24 new simulated integration cases passed; 244 relevant tests passed overall.** No production files were changed by this review. This is offline integration evidence, not evidence of successful authenticated MOLIT access or a Zoro connection.

## Scope and isolation

Reviewed the transaction route, separate trade/rent credential selection, MOLIT transport/parser path, error handling, and separation from the Stage 5 manual workbench. Tests use FastAPI `TestClient`, real application routing, real `fetch_transactions`, real XML parsing, and temporary SQLite databases. Only the external HTTP transport is replaced with `httpx.MockTransport`.

All settings were constructed from explicit mappings containing invented test keys. The launcher redirected default database settings to a temporary directory, replaced `dotenv_values` with a failing guard, and blocked socket `connect`/`connect_ex`. This also isolates the default application constructed when `app.main` is imported. No real `.env`, uploads, existing session database, authenticated API, or Zoro service was read or contacted.

The new test fixture also protects its own first import of `app.main`, so running the new file by itself cannot load the developer's default configuration through that import. The launcher remains necessary for safely running older test modules that import the default application at module scope.

## New coverage

File: `tests/test_live_integration_v03.py`.

| Cases | Count | Assertions |
| --- | ---: | --- |
| Trade/rent record, empty response, provider no-data response | 6 | Route reaches the appropriate real client endpoint; corresponding dedicated key wins over the configured shared key; decoding keys and encoded keys reach the request correctly; district, month, pagination and timeout are forwarded; actual parser output has correct amounts and provenance; empty responses stay empty. |
| Trade/rent authorization failure, quota, provider timeout, provider outage, malformed XML, transport timeout, connection failure, unexpected exception | 16 | Expected safe HTTP status/error code/retryability; one upstream attempt; no credential or exception-detail disclosure; no sample generator invocation. |
| Only the other service has a credential | 2 | Missing service returns `MISSING_API_KEY` before calling transport; trade never borrows the rent key and rent never borrows the trade key. |

Every case first creates two independent workbench sessions using the public HTTP API. One has saved condition changes, search results, confirmed inspection evidence, a delayed entry event, conflicts, and pending schedule proposals. The other has explicitly applied a schedule proposal. After the transaction request, fixture teardown checks:

- Both complete session responses remain exactly equal to their saved versions, including revisions, conditions, search results, evidence, schedules, proposal identifiers, audit records, and timestamps.
- Raw SQLite `(session_id, revision, snapshot)` rows remain exactly equal; no session is created or removed.
- A newly constructed application reloads both unchanged sessions from the same temporary database.
- The source listing, inspection and schedule fixture files remain byte-for-byte unchanged.
- The sample transaction function has never been invoked.

Successful simulated MOLIT responses also leave health claims unverified. The tests' `source=molit` response value exercises the production parser contract; it does **not** turn the transport fixture into real provider evidence.

## Source review

- `app/main.py`: `/api/transactions` validates the query before dispatch, uses the sample generator only for `mode=sample`, selects a credential by `kind`, and returns a missing-key error for that service. The live path does not call the session store or tool service. Known `MolitError` and unexpected failure paths return errors instead of sample data.
- `app/molit.py`: trade/rent use separate fixed endpoints. Live fetches pass through the bounded HTTP client and parser; redirect, authorization, quota, transport and response errors do not select the sample module.
- `static/app.js`: transaction controls inspect the selected service's key flag. Live response rendering requires `source=molit` and `synthetic=false`; transaction failure clears the transaction display and reports the error. The Stage 5 workbench uses a separate state object and separate session routes. This frontend statement is source inspection only; this review did not execute browser interaction tests.

No integration defect was found within this scope. Import-time application construction is a test-isolation concern, handled by the guards above; this review makes no production change to it.

## Reproduction and results

Executed with the existing clean runner, `../zoro-clean-check/bin/python` (Python 3.12), from the project directory. This launcher makes the configuration and network restrictions explicit:

```bash
../zoro-clean-check/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import socket
import pytest
from app.config import Settings

original = Settings.from_env.__func__
with TemporaryDirectory(prefix='integration-v03-regression-') as temporary:
    def isolated_settings(cls, env=None):
        values = dict(env or {})
        values.setdefault('STATE_DB_PATH', str(Path(temporary) / 'bootstrap.sqlite3'))
        return original(cls, values)

    def forbidden(*args, **kwargs):
        raise AssertionError('real environment reads and outbound network are forbidden in simulated integration')

    with patch.object(Settings, 'from_env', classmethod(isolated_settings)), patch('app.config.dotenv_values', forbidden), patch.object(socket.socket, 'connect', forbidden), patch.object(socket.socket, 'connect_ex', forbidden):
        raise SystemExit(pytest.main([
            '-q', 'tests/test_app.py', 'tests/test_config.py',
            'tests/test_molit.py', 'tests/test_tools_api.py',
            'tests/test_store.py', 'tests/test_live_integration_v03.py',
        ]))
PY
```

| Run | Result |
| --- | --- |
| New file alone, same isolation guards | **24 passed**, 1 warning, 3.93 seconds |
| Existing HTTP/config/MOLIT/manual-tools/store suites plus new file | **244 passed**, 1 warning, 7.45 seconds |

The warning is the existing Starlette `TestClient` deprecation notice concerning its `httpx` integration. No assertion failed.

## Limits of this evidence

These checks do not establish current provider availability, account approval, production response schema, real transaction values, or authenticated trade/rent success. They do not evaluate Zoro reasoning quality, current property availability, or real provider bookings. Actual credential validation and browser behavior must be reported separately from these simulated integration results.
