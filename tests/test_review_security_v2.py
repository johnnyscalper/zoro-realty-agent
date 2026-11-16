"""Focused Stage 5 security regressions; synthetic inputs and no network calls."""

import json
from pathlib import Path
import sqlite3
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from scripts import build_package


@pytest.fixture
def local_application(tmp_path):
    database = tmp_path / "sessions.sqlite3"
    application = create_app(Settings.from_env({"STATE_DB_PATH": str(database)}))

    async def forbidden_fetch(**_kwargs):
        raise AssertionError("Security review tests must not call an upstream API")

    application.state.molit_fetcher = forbidden_fetch
    return application, database


def assert_safe_error(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "retryable"}
    assert body["error"]["code"] == code
    assert type(body["error"]["retryable"]) is bool


@pytest.mark.parametrize("prefix", ["x" * 90, "가" * 40])
def test_body_limit_counts_actual_bytes_before_creating_session(local_application, prefix):
    application, database = local_application
    # Each name is valid. The complete JSON exceeds 64 KiB, including a case
    # whose character count is below the byte cap.
    body = json.dumps({
        "conditions": {"preferences": {"neighborhoods": [prefix + str(i) for i in range(1_000)]}},
    }, ensure_ascii=False).encode("utf-8")
    assert len(body) > 65_536
    if prefix.startswith("가"):
        assert len(body.decode("utf-8")) < 65_536
    with TestClient(application) as client:
        response = client.post("/api/sessions", content=body, headers={
            "Content-Type": "application/json", "Origin": "http://testserver",
            "Content-Length": "2", "Transfer-Encoding": "chunked",
        })
    # Uvicorn's h11 parser accepts CL+TE, keeps both headers, and emits the full
    # chunked payload. The application cannot rely on the declared length.
    assert_safe_error(response, 413, "REQUEST_TOO_LARGE")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_tool_exception_rolls_back_and_never_echoes_or_logs_details(local_application, monkeypatch, caplog):
    application, _database = local_application
    sentinel = "offline-private-diagnostic-do-not-echo"

    def fail_after_listing_mutation(_state):
        raise RuntimeError(sentinel)

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post("/api/sessions", json={})
        assert response.status_code == 201
        before = response.json()
        listing = next(item for item in before["listings"] if item["status"] != "contracted")
        monkeypatch.setattr(application.state.tool_service, "_refresh_search", fail_after_listing_mutation)
        response = client.patch(
            f"/api/sessions/{before['session_id']}/listings/{listing['id']}/status",
            json={"expected_revision": before["revision"], "status": "contracted"},
        )
        assert_safe_error(response, 500, "INTERNAL_ERROR")
        assert sentinel not in response.text
        assert client.get(f"/api/sessions/{before['session_id']}").json() == before
    assert sentinel not in caplog.text


def test_package_omits_runtime_sessions_and_verification_reports(tmp_path, monkeypatch):
    root = tmp_path / "source"
    private_marker = "offline-private-runtime-record"
    files = {
        "README.md": "public documentation",
        "data/listings.json": "{}",
        ".env.example": "MOLIT_API_KEY=\n",
        ".env": private_marker,
        ".runtime/sessions.json": private_marker,
        ".runtime/realty.sqlite3": private_marker,
        ".runtime/realty.sqlite3-wal": private_marker,
        ".runtime/realty.sqlite3-shm": private_marker,
        "reports/api_verification_report.json": private_marker,
        "reports/private.md": private_marker,
        "data/reports/private.json": private_marker,
    }
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(build_package, "ROOT", root)
    output = tmp_path / "review.zip"
    build_package.build(output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert "zoro-realty-prep/data/listings.json" in names
        for name in names:
            assert not {".env", ".runtime", "reports"}.intersection(Path(name).parts)
            assert private_marker.encode() not in archive.read(name)
