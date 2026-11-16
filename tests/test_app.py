"""HTTP boundary checks; all network dependencies are synthetic or injected.

These tests verify the preparation application, not Zoro decision quality and
not authenticated access to the Ministry's production API.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.molit import MolitError


TRADE_SECRET = "offline-trade-key+with/slash=do-not-expose"
RENT_SECRET = "offline-rent-key+with/slash=do-not-expose"
ZORO_SECRET = "offline-zoro-private-token-do-not-expose"
PRIVATE_ZORO_URL = "https://private-zoro.invalid:9443/v1"


def configured_app():
    return create_app(Settings.from_env({
        "MOLIT_TRADE_API_KEY": TRADE_SECRET,
        "MOLIT_RENT_API_KEY": RENT_SECRET,
        "ZORO_BASE_URL": PRIVATE_ZORO_URL,
        "ZORO_API_KEY": ZORO_SECRET,
        "MOLIT_TIMEOUT_SECONDS": "7",
    }))


@pytest.fixture
def app():
    return create_app(Settings.from_env({}))


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def assert_safe_error(response):
    assert 400 <= response.status_code <= 599
    payload = response.json()
    assert set(payload) == {"error"}
    error = payload["error"]
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["retryable"], bool)
    assert "items" not in payload
    assert "source" not in payload
    return error


def assert_no_secrets(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    for secret in (TRADE_SECRET, RENT_SECRET, ZORO_SECRET, PRIVATE_ZORO_URL):
        assert secret not in text


def test_health_has_explicit_unverified_preparation_status(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "preparation"
    assert body["zoro"] == {
        "configured": False,
        "verified": False,
        "status": "not_connected",
    }
    assert body["molit"]["verified"] is False
    assert body["molit"]["trade_key_configured"] is False
    assert body["molit"]["rent_key_configured"] is False
    assert body["notice"]


def test_configured_credentials_do_not_claim_verified_or_expose_values():
    with TestClient(configured_app()) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["molit"]["trade_key_configured"] is True
    assert body["molit"]["rent_key_configured"] is True
    assert body["molit"]["verified"] is False
    assert body["zoro"]["configured"] is True
    assert body["zoro"]["verified"] is False
    assert body["zoro"]["status"] == "not_connected"
    assert_no_secrets(body)


def test_listing_and_preparation_counts_match_health(client):
    health = client.get("/api/health").json()
    listings = client.get("/api/listings").json()
    preparation = client.get("/api/preparation").json()
    assert listings["synthetic"] is True
    assert health["counts"]["listings"] == len(listings["listings"])
    assert health["counts"]["scenarios"] == len(preparation["scenarios"]["scenarios"])
    for key in ("inspections", "schedules", "scenarios"):
        assert preparation[key]["synthetic"] is True
    stages = {s["verification_stage"] for s in preparation["scenarios"]["scenarios"]}
    assert "zoro_required" in stages
    assert "live_api_required" in stages


def test_listing_filters_really_enforce_price_area_and_status(client):
    all_rows = client.get("/api/listings").json()["listings"]
    available = [item for item in all_rows if item["status"] == "available"]
    anchor = sorted(available, key=lambda item: item["deposit_krw"])[len(available) // 2]
    params = {
        "max_deposit_krw": anchor["deposit_krw"],
        "min_area_m2": anchor["area_m2"],
        "status": "available",
    }
    response = client.get("/api/listings", params=params)
    assert response.status_code == 200
    result = response.json()
    assert result["synthetic"] is True
    expected_ids = {
        row["id"] for row in all_rows
        if row["deposit_krw"] <= anchor["deposit_krw"]
        and row["area_m2"] >= anchor["area_m2"]
        and row["status"] == "available"
    }
    assert {row["id"] for row in result["listings"]} == expected_ids
    assert len(result["listings"]) < len(all_rows)


def test_empty_listing_filter_is_not_replaced_with_unrelated_candidates(client):
    response = client.get("/api/listings", params={"max_deposit_krw": 1})
    assert response.status_code == 200
    assert response.json()["listings"] == []


@pytest.mark.parametrize("kind", ["trade", "rent"])
def test_sample_transactions_work_without_keys_and_never_call_upstream(app, kind):
    async def forbidden_fetch(**kwargs):
        raise AssertionError("sample route must not call the upstream service")

    app.state.molit_fetcher = forbidden_fetch
    with TestClient(app) as client:
        response = client.get("/api/transactions", params={"kind": kind, "mode": "sample"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "synthetic_sample"
    assert body["synthetic"] is True
    assert body["kind"] == kind
    assert body["returned_count"] == len(body["items"])
    assert body["total_count"] >= body["returned_count"] > 0
    assert body["warnings"]
    for item in body["items"]:
        if kind == "trade":
            assert item["price_krw"] is not None
            assert item["deposit_krw"] is None
        else:
            assert item["deposit_krw"] is not None
            assert item["price_krw"] is None


def test_default_transaction_mode_is_explicitly_labeled_sample(client):
    response = client.get("/api/transactions")
    assert response.status_code == 200
    assert response.json()["source"] == "synthetic_sample"
    assert response.json()["synthetic"] is True


def test_sample_pagination_uses_requested_window(client):
    first = client.get("/api/transactions", params={"page_no": 1, "num_of_rows": 1}).json()
    second = client.get("/api/transactions", params={"page_no": 2, "num_of_rows": 1}).json()
    assert first["returned_count"] == second["returned_count"] == 1
    assert first["total_count"] == second["total_count"]
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["page_no"] == 1 and second["page_no"] == 2
    assert first["has_next"] is True


@pytest.mark.parametrize("kind", ["trade", "rent"])
def test_live_missing_key_never_falls_back_to_sample(app, kind):
    calls = []

    async def forbidden_fetch(**kwargs):
        calls.append(kwargs)
        raise AssertionError("missing credentials must be rejected before network access")

    app.state.molit_fetcher = forbidden_fetch
    with TestClient(app) as client:
        response = client.get("/api/transactions", params={"mode": "live", "kind": kind})
    error = assert_safe_error(response)
    assert error["retryable"] is False
    assert calls == []
    assert "synthetic_sample" not in response.text


@pytest.mark.parametrize("kind,expected_key", [("trade", TRADE_SECRET), ("rent", RENT_SECRET)])
def test_live_routes_forward_only_the_correct_server_side_key(kind, expected_key):
    app = configured_app()
    calls = []

    async def fake_fetch(**kwargs):
        calls.append(kwargs)
        return {
            "source": "molit", "synthetic": False,
            "kind": kwargs["kind"], "lawd_cd": kwargs["lawd_cd"], "deal_ymd": kwargs["deal_ymd"],
            "page_no": kwargs["page_no"], "num_of_rows": kwargs["num_of_rows"],
            "total_count": 0, "returned_count": 0, "has_next": False,
            "retrieved_at": "2026-11-12T17:00:00+09:00", "items": [],
            "warnings": ["과거 신고된 계약 내역이며 현재 매물의 입주 가능 여부를 나타내지 않습니다."],
        }

    app.state.molit_fetcher = fake_fetch
    with TestClient(app) as client:
        response = client.get("/api/transactions", params={
            "mode": "live", "kind": kind, "lawd_cd": "11110", "deal_ymd": "202609",
            "page_no": 2, "num_of_rows": 5,
        })
    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["api_key"] == expected_key
    assert calls[0]["kind"] == kind
    assert calls[0]["lawd_cd"] == "11110"
    assert calls[0]["deal_ymd"] == "202609"
    assert calls[0]["page_no"] == 2
    assert calls[0]["num_of_rows"] == 5
    assert calls[0]["timeout_seconds"] == 7
    assert response.json()["source"] == "molit"
    assert response.json()["synthetic"] is False
    assert response.json()["items"] == []
    assert_no_secrets(response.text)


def test_known_upstream_failure_keeps_safe_error_and_does_not_fallback():
    app = configured_app()

    async def fake_fetch(**kwargs):
        raise MolitError("UPSTREAM_AUTH", "인증키와 활용 승인을 확인해 주세요.", False, 502)

    app.state.molit_fetcher = fake_fetch
    with TestClient(app) as client:
        response = client.get("/api/transactions", params={"mode": "live"})
    error = assert_safe_error(response)
    assert error["code"] == "UPSTREAM_AUTH"
    assert error["retryable"] is False
    assert_no_secrets(response.text)


def test_unexpected_upstream_failure_does_not_leak_exception_or_secret(caplog):
    app = configured_app()

    async def fake_fetch(**kwargs):
        raise RuntimeError("private request https://upstream.invalid/?serviceKey=" + kwargs["api_key"])

    app.state.molit_fetcher = fake_fetch
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/transactions", params={"mode": "live"})
    assert_safe_error(response)
    assert_no_secrets(response.text)
    assert_no_secrets(caplog.text)
    assert "upstream.invalid" not in response.text


@pytest.mark.parametrize("params", [
    {"kind": "not-a-kind"}, {"mode": "auto"}, {"lawd_cd": "1168"},
    {"lawd_cd": "116800"}, {"lawd_cd": "abcde"}, {"deal_ymd": "202613"},
    {"deal_ymd": "202600"}, {"deal_ymd": "20261"}, {"deal_ymd": "2026-10"},
    {"page_no": 0}, {"page_no": -1}, {"num_of_rows": 0}, {"num_of_rows": 1000000},
])
def test_invalid_transaction_requests_are_rejected_safely(client, params):
    response = client.get("/api/transactions", params=params)
    assert response.status_code == 422
    assert_safe_error(response)


def test_validation_error_does_not_echo_sensitive_input(client):
    response = client.get("/api/transactions", params={"lawd_cd": TRADE_SECRET})
    assert response.status_code == 422
    assert_safe_error(response)
    assert_no_secrets(response.text)


@pytest.mark.parametrize("params", [
    {"max_deposit_krw": -1}, {"min_area_m2": -1},
    {"min_area_m2": "NaN"}, {"min_area_m2": "Infinity"}, {"status": "anything"},
])
def test_invalid_listing_filters_are_rejected(client, params):
    response = client.get("/api/listings", params=params)
    assert response.status_code == 422
    assert_safe_error(response)


@pytest.mark.parametrize("path", ["/.env", "/.env.example", "/app/config.py", "/static/../.env", "/static/%2e%2e/.env"])
def test_private_files_are_not_served(client, path):
    response = client.get(path)
    assert response.status_code in (403, 404)
    assert_no_secrets(response.text)


def test_home_is_a_local_app_shell_not_a_secret_settings_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "집의 다음" in response.text
    assert_no_secrets(response.text)

