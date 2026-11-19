"""Owned-client regressions; no external requests, local keys or dotenv reads.

Only HTTPX's final request transport is replaced. This covers the default
client constructor that the other fixture tests bypass with an injected client.
It does not establish a real TLS handshake or authenticated provider access.
"""
from __future__ import annotations

import argparse
import asyncio
import json

import httpx
import pytest

from app import molit
from app.config import Settings
from scripts import check_api


QUERY = dict(kind="trade", lawd_cd="11680", deal_ymd="202610", page_no=1, num_of_rows=5)
FAKE_KEY = "offline-transport-v03-key"
PRIVATE_DETAIL = "offline-private-proxy-user:offline-private-password@offline.invalid"
EMPTY = (
    b"<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg>"
    b"</header><body><items/><totalCount>0</totalCount><pageNo>1</pageNo>"
    b"<numOfRows>5</numOfRows></body></response>"
)


@pytest.fixture(autouse=True)
def deny_external_requests_and_configuration(monkeypatch):
    async def forbidden_request(*args, **kwargs):
        pytest.fail("Transport regressions must remain offline")

    def forbidden_configuration(*args, **kwargs):
        pytest.fail("Transport regressions must not read local configuration")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_request)
    monkeypatch.setattr(Settings, "from_env", forbidden_configuration)


def capture_owned_clients(monkeypatch):
    real_client = httpx.AsyncClient
    clients, options = [], []

    def create_client(**kwargs):
        client = real_client(**kwargs)
        clients.append(client)
        options.append(kwargs)
        return client

    monkeypatch.setattr(molit.httpx, "AsyncClient", create_client)
    return clients, options


def test_owned_client_ignores_environment_transport_overrides_and_closes(monkeypatch, tmp_path):
    # Replace these variables with invented values; never inspect the host's
    # proxy credentials or CA paths. An accidental trust_env=True would fail.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "unsupported-proxy-scheme://offline.invalid:8888")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "")
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "nonexistent-ca"))
    clients, options = capture_owned_clients(monkeypatch)
    requests = []

    async def offline_response(_transport, request):
        requests.append(request)
        return httpx.Response(200, content=EMPTY)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", offline_response)
    result = asyncio.run(molit.fetch_transactions(**QUERY, api_key=FAKE_KEY))

    assert result["total_count"] == 0
    assert len(requests) == len(clients) == 1
    assert requests[0].url.scheme == "https"
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert options[0].get("verify", True) is True
    assert clients[0].is_closed


@pytest.mark.parametrize("error_type, expected_code, expected_status", [
    (httpx.ConnectError, "MOLIT_CONNECTION_ERROR", 502),
    (httpx.ProxyError, "MOLIT_CONNECTION_ERROR", 502),
    (httpx.ConnectTimeout, "MOLIT_TIMEOUT", 504),
    (httpx.ReadTimeout, "MOLIT_TIMEOUT", 504),
])
def test_owned_client_transport_failure_is_safe_and_closes(monkeypatch, error_type, expected_code, expected_status):
    clients, _options = capture_owned_clients(monkeypatch)
    requests = []

    async def offline_failure(_transport, request):
        requests.append(request)
        raise error_type(f"{PRIVATE_DETAIL} {FAKE_KEY}", request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", offline_failure)
    with pytest.raises(molit.MolitError) as caught:
        asyncio.run(molit.fetch_transactions(**QUERY, api_key=FAKE_KEY))

    assert caught.value.code == expected_code
    assert caught.value.http_status == expected_status
    assert caught.value.retryable is True
    assert caught.value.__suppress_context__ is True
    assert PRIVATE_DETAIL not in str(caught.value)
    assert FAKE_KEY not in str(caught.value)
    assert len(requests) == len(clients) == 1
    assert clients[0].is_closed


def test_body_read_failure_after_http_200_cannot_become_verified(monkeypatch):
    clients, _options = capture_owned_clients(monkeypatch)
    observations = []

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            observations.append("body_started")
            yield b"<response>"
            raise httpx.ReadError(f"{PRIVATE_DETAIL} {FAKE_KEY}")

    async def offline_response(_transport, request):
        observations.append("http_200_received")
        return httpx.Response(200, stream=BrokenBody())

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", offline_response)
    with pytest.raises(molit.MolitError) as caught:
        asyncio.run(molit.fetch_transactions(**QUERY, api_key=FAKE_KEY))

    # The existing connection code covers failures after headers too. It is
    # not, by itself, proof that an HTTP response was never received.
    assert observations == ["http_200_received", "body_started"]
    assert caught.value.code == "MOLIT_CONNECTION_ERROR"
    assert PRIVATE_DETAIL not in str(caught.value)
    assert FAKE_KEY not in str(caught.value)
    assert clients[0].is_closed


def test_injected_client_remains_owned_by_the_caller():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=EMPTY)),
            trust_env=False,
        ) as client:
            result = await molit.fetch_transactions(**QUERY, api_key=FAKE_KEY, client=client)
            assert result["total_count"] == 0
            assert not client.is_closed
        assert client.is_closed

    asyncio.run(run())


@pytest.mark.parametrize("error_type", [FileNotFoundError, ValueError, ImportError])
def test_client_setup_failure_report_stays_safe_and_unverified(monkeypatch, tmp_path, capsys, error_type):
    constructions = []

    def failed_constructor(**kwargs):
        constructions.append(kwargs)
        raise error_type(f"{PRIVATE_DETAIL} {FAKE_KEY}")

    monkeypatch.setattr(molit.httpx, "AsyncClient", failed_constructor)
    report_path = tmp_path / "report.json"
    args = argparse.Namespace(kind="both", lawd_cd=QUERY["lawd_cd"], month=QUERY["deal_ymd"])
    status = asyncio.run(check_api.check(
        args,
        settings=Settings(molit_trade_api_key=FAKE_KEY, molit_rent_api_key=FAKE_KEY),
        report_path=report_path,
    ))
    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)

    assert status == 1
    assert len(constructions) == 2
    assert report["status"] == "FAILED"
    assert report["both_apis_verified"] is False
    for service in report["services"].values():
        assert service["status"] == "FAILED"
        assert service["total_count"] is None
        assert service["returned_count"] is None
    public_output = report_text + capsys.readouterr().out
    assert PRIVATE_DETAIL not in public_output
    assert FAKE_KEY not in public_output
