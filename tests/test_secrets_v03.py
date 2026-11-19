"""Secret-boundary regressions using only an in-memory HTTP wire transport.

No application import, dotenv load, socket, real credential, or live API is
needed. The cases exercise actual httpcore trace generation, not manually
created log records or MockTransport (which bypasses httpcore entirely).
"""
from __future__ import annotations

import asyncio
import logging

import httpcore
import httpx
import pytest

from app.molit import MolitError, fetch_transactions


DUMMY_KEY = "offline-v03-only-upstream-echo-key"
DUMMY_URL = "https://offline-diagnostic.invalid/private-upstream-path"


@pytest.mark.parametrize(
    "header",
    [
        b"X-Upstream-Diagnostic: " + DUMMY_KEY.encode(),
        b"Location: " + DUMMY_URL.encode(),
    ],
    ids=["credential-in-response-header", "url-in-response-header"],
)
def test_debug_transport_logs_do_not_expose_upstream_headers(header, caplog, monkeypatch):
    """A provider echo without the literal `serviceKey` must still stay private."""
    logger_names = ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                    "httpcore.http2", "httpcore.proxy", "httpcore.socks")
    for name in logger_names:
        logger = logging.getLogger(name)
        # Keep this regression isolated from other tests' transport policy.
        monkeypatch.setattr(logger, "filters", list(logger.filters))
        monkeypatch.setattr(logger, "disabled", False)
        caplog.set_level(logging.DEBUG, logger=name)
    wire = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n" + header + b"\r\n\r\n"

    async def run():
        transport = httpx.AsyncHTTPTransport(trust_env=False)
        transport._pool = httpcore.AsyncConnectionPool(
            network_backend=httpcore.AsyncMockBackend([wire]),
        )
        async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
            with pytest.raises(MolitError) as failure:
                await fetch_transactions(
                    kind="trade", lawd_cd="11680", deal_ymd="202610",
                    page_no=1, num_of_rows=5, api_key=DUMMY_KEY, client=client,
                )
            assert failure.value.code == "MOLIT_AUTH_ERROR"
            assert DUMMY_KEY not in str(failure.value)

    asyncio.run(run())
    assert DUMMY_KEY not in caplog.text
    assert DUMMY_URL not in caplog.text
    assert not [record for record in caplog.records if record.name in logger_names]
    logging.getLogger("app.audit").warning("safe-offline-audit-event")
    assert "safe-offline-audit-event" in caplog.text
