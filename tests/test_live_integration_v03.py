"""Simulated live-route integration; never authenticated production evidence.

Only the upstream transport is replaced. HTTP routing, server credential
selection, MOLIT request construction/parsing and SQLite persistence are real.
Every app uses explicit settings and a temporary database; even an isolated
import of app.main is prevented from loading the developer's .env or database.
"""

from copy import deepcopy
from importlib import import_module
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings
from app.molit import ENDPOINTS, fetch_transactions


TRADE_KEY = "integration-trade+value/="
RENT_KEY = "integration-rent%2Bvalue%2F%3D"
SHARED_KEY = "integration-shared-must-not-be-selected"
NORMALIZED_KEYS = {"trade": TRADE_KEY, "rent": "integration-rent+value/="}
CONDITIONS = {
    "hard": {
        "max_deposit_krw": 1_000_000_000,
        "min_area_m2": 75,
        "move_in_by": "2026-11-25",
        "step_free_access": True,
        "elevator": None,
    },
    "preferences": {"noise_level": "low", "neighborhoods": []},
}
QUERY = {"lawd_cd": "11110", "deal_ymd": "202609", "page_no": 2, "num_of_rows": 3}


def _create(client, conditions):
    response = client.post("/api/sessions", json={"conditions": conditions})
    assert response.status_code == 201, response.text
    return response.json()


def _mutate(client, saved, suffix, body=None, method="POST"):
    response = client.request(method, f"/api/sessions/{saved['session_id']}{suffix}", json={
        "expected_revision": saved["revision"], **(body or {}),
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["revision"] == saved["revision"] + 1
    return result


def _seed_workbench(client):
    """Keep edited conditions/evidence and both proposed/applied schedules."""
    first = _create(client, CONDITIONS)
    changed = deepcopy(CONDITIONS)
    changed["hard"]["max_deposit_krw"] = 900_000_000
    first = _mutate(client, first, "/conditions", {"conditions": changed}, "PATCH")
    first = _mutate(client, first, "/search")
    first = _mutate(client, first, "/verification-tasks", {
        "listing_id": "L001", "field": "step_free_access",
    })
    task = next(item for item in first["verification_tasks"] if item["status"] == "pending")
    first = _mutate(client, first, "/inspections", {
        "task_id": task["id"], "value": False, "notes": "simulated integration observation",
    })
    first = _mutate(client, first, "/schedule/entry", {
        "new_end": "2026-11-25T15:00:00+09:00",
    }, "PATCH")
    first = _mutate(client, first, "/schedule/proposals")
    assert first["conditions"] == changed
    assert first["search_result"] and first["inspections"] and first["audit"]
    assert first["schedule"]["conflicts"] and first["schedule_proposals"]

    second = _create(client, CONDITIONS)
    second = _mutate(client, second, "/schedule/entry", {
        "new_end": "2026-11-25T15:00:00+09:00",
    }, "PATCH")
    second = _mutate(client, second, "/schedule/proposals")
    chosen = second["schedule_proposals"][0]
    second = _mutate(client, second, "/schedule/apply", {"proposal_id": chosen["id"]})
    assert second["schedule"]["events"] == chosen["events"]
    assert second["schedule"]["conflicts"] == []
    return first, second


def _database_rows(database: Path):
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        return connection.execute(
            "SELECT session_id, revision, snapshot FROM sessions ORDER BY session_id"
        ).fetchall()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    settings = Settings.from_env({
        "STATE_DB_PATH": str(tmp_path / "live-integration.sqlite3"),
        "MOLIT_TRADE_API_KEY": TRADE_KEY,
        "MOLIT_RENT_API_KEY": RENT_KEY,
        "MOLIT_API_KEY": SHARED_KEY,
        "MOLIT_TIMEOUT_SECONDS": "6",
    })
    # app.main has an import-time default app; do not let that read real config.
    with monkeypatch.context() as importing:
        importing.setattr(Settings, "from_env", classmethod(lambda cls, env=None: settings))
        main = import_module("app.main")
    application = main.create_app(settings)
    sample_calls = []

    def forbidden_sample(**query):
        sample_calls.append(query)
        raise AssertionError("live lookup must never invoke sample data")

    monkeypatch.setattr(main, "sample_transactions", forbidden_sample)
    paths = [PROJECT_ROOT / "data" / f"{name}.json"
             for name in ("listings", "inspections", "schedules")]
    fixtures_before = {path: path.read_bytes() for path in paths}
    with TestClient(application, raise_server_exceptions=False) as client:
        states = _seed_workbench(client)
        before = _database_rows(settings.state_db_path)
        yield SimpleNamespace(
            main=main, app=application, client=client, settings=settings,
            states=states, rows=before,
        )
        for saved in states:
            response = client.get(f"/api/sessions/{saved['session_id']}")
            assert response.status_code == 200
            assert response.json() == saved
        assert _database_rows(settings.state_db_path) == before
    # Recreate the application and read durable rows, not an in-memory cache.
    with TestClient(main.create_app(settings)) as restarted:
        for saved in states:
            response = restarted.get(f"/api/sessions/{saved['session_id']}")
            assert response.status_code == 200
            assert response.json() == saved
    assert _database_rows(settings.state_db_path) == before
    assert all(path.read_bytes() == original for path, original in fixtures_before.items())
    assert sample_calls == []


def _transport_for(application, handler):
    calls = []

    def capture(request):
        calls.append(request)
        return handler(request)

    async def actual_fetch_with_simulated_transport(**kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(capture), trust_env=False) as upstream:
            return await fetch_transactions(**kwargs, client=upstream)

    application.state.molit_fetcher = actual_fetch_with_simulated_transport
    return calls


def _assert_request(calls, kind):
    assert len(calls) == 1, "a lookup must not retry or query the other service"
    request = calls[0]
    assert str(request.url.copy_with(query=None)) == ENDPOINTS[kind]
    assert dict(request.url.params) == {
        "serviceKey": NORMALIZED_KEYS[kind], "LAWD_CD": QUERY["lawd_cd"],
        "DEAL_YMD": QUERY["deal_ymd"], "pageNo": str(QUERY["page_no"]),
        "numOfRows": str(QUERY["num_of_rows"]),
    }
    assert request.extensions["timeout"] == dict.fromkeys(("connect", "read", "write", "pool"), 6.0)


def _assert_no_test_credentials(text):
    for key in (TRADE_KEY, RENT_KEY, SHARED_KEY, NORMALIZED_KEYS["rent"]):
        assert key not in text


def _success_xml(kind, result):
    if result == "provider-no-data":
        return b"<response><header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header></response>"
    values = "<dealAmount>12,345</dealAmount><cdealType>N</cdealType>" if kind == "trade" else "<deposit>7,000</deposit><monthlyRent>80</monthlyRent>"
    item = f"""<item><aptNm>Simulated integration apartment</aptNm><umdNm>Simulated dong</umdNm>
      <sggCd>11110</sggCd><dealYear>2026</dealYear><dealMonth>9</dealMonth><dealDay>12</dealDay>
      <excluUseAr>84.5</excluUseAr><floor>4</floor><buildYear>2012</buildYear>{values}</item>"""
    total = 4 if result == "record" else 0
    return f"""<response><header><resultCode>000</resultCode><resultMsg>NORMAL_SERVICE</resultMsg></header>
      <body><items>{item if result == 'record' else ''}</items><totalCount>{total}</totalCount>
      <pageNo>2</pageNo><numOfRows>3</numOfRows></body></response>""".encode()


@pytest.mark.parametrize("kind", ["trade", "rent"])
@pytest.mark.parametrize("result", ["record", "empty", "provider-no-data"])
def test_real_route_and_parser_use_separate_credentials_without_changing_stage5(isolated, kind, result, caplog):
    calls = _transport_for(isolated.app, lambda request: httpx.Response(200, content=_success_xml(kind, result)))
    response = isolated.client.get("/api/transactions", params={"mode": "live", "kind": kind, **QUERY})
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_request(calls, kind)
    assert body["source"] == "molit" and body["synthetic"] is False
    assert body["kind"] == kind
    assert all(body[key] == value for key, value in QUERY.items())
    assert body["skipped_count"] == 0
    assert body["warnings"] and body["retrieved_at"]
    if result == "record":
        assert body["returned_count"] == len(body["items"]) == 1
        assert body["total_count"] == 4 and body["has_next"] is False
        item = body["items"][0]
        assert item["apartment_name"] == "Simulated integration apartment"
        assert item["contract_date"] == "2026-09-12"
        assert item["price_krw"] == (123_450_000 if kind == "trade" else None)
        assert item["deposit_krw"] == (70_000_000 if kind == "rent" else None)
        assert item["monthly_rent_krw"] == (800_000 if kind == "rent" else None)
    else:
        assert body["returned_count"] == body["total_count"] == 0
        assert body["items"] == [] and body["has_next"] is False
    health = isolated.client.get("/api/health").json()
    assert health["molit"]["verified"] is False
    assert health["zoro"]["verified"] is False
    _assert_no_test_credentials(response.text + caplog.text)


FAILURES = [
    ("authorization", "MOLIT_AUTH_ERROR", 502, False),
    ("quota", "MOLIT_RATE_LIMIT", 429, False),
    ("provider-timeout", "MOLIT_TIMEOUT", 504, True),
    ("provider-outage", "MOLIT_UPSTREAM_ERROR", 502, True),
    ("malformed", "MOLIT_INVALID_RESPONSE", 502, False),
    ("network-timeout", "MOLIT_TIMEOUT", 504, True),
    ("connection", "MOLIT_CONNECTION_ERROR", 502, True),
    ("unexpected", "UPSTREAM_FAILURE", 502, True),
]


@pytest.mark.parametrize("kind", ["trade", "rent"])
@pytest.mark.parametrize("failure,code,status,retryable", FAILURES)
def test_live_failures_preserve_saved_work_and_never_fall_back(isolated, kind, failure, code, status, retryable, caplog):
    def failed_upstream(request):
        if failure == "authorization":
            return httpx.Response(200, content=b"<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>")
        if failure in {"quota", "provider-timeout", "provider-outage"}:
            return httpx.Response({"quota": 429, "provider-timeout": 504, "provider-outage": 503}[failure])
        if failure == "malformed":
            return httpx.Response(200, content=b"<response>malformed")
        private_detail = f"simulated failure at {request.url}"
        if failure == "network-timeout":
            raise httpx.ReadTimeout(private_detail, request=request)
        if failure == "connection":
            raise httpx.ConnectError(private_detail, request=request)
        raise RuntimeError(private_detail)

    calls = _transport_for(isolated.app, failed_upstream)
    response = isolated.client.get("/api/transactions", params={"mode": "live", "kind": kind, **QUERY})
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "retryable"}
    assert body["error"]["code"] == code
    assert body["error"]["retryable"] is retryable
    assert body["error"]["message"]
    assert "synthetic_sample" not in response.text
    assert "simulated failure" not in response.text + caplog.text
    _assert_request(calls, kind)
    _assert_no_test_credentials(response.text + caplog.text)


@pytest.mark.parametrize("missing", ["trade", "rent"])
def test_one_service_cannot_borrow_the_other_services_key(isolated, missing):
    other = "rent" if missing == "trade" else "trade"
    isolated.app.state.settings = Settings.from_env({
        "STATE_DB_PATH": str(isolated.settings.state_db_path),
        f"MOLIT_{other.upper()}_API_KEY": NORMALIZED_KEYS[other],
    })
    calls = []

    async def forbidden_fetch(**kwargs):
        calls.append(kwargs)
        raise AssertionError("missing service credentials must stop before transport")

    isolated.app.state.molit_fetcher = forbidden_fetch
    response = isolated.client.get("/api/transactions", params={"mode": "live", "kind": missing, **QUERY})
    assert response.status_code == 503
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == "MISSING_API_KEY"
    assert response.json()["error"]["retryable"] is False
    assert calls == []
    _assert_no_test_credentials(response.text)
