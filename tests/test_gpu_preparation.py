"""Offline checks for the GPU handoff; no GPU/model success is inferred here."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import ModuleType

import httpx
import pytest
from dotenv import dotenv_values

from app.config import Settings
from app.zoro_provider import ProviderError, ZoroProvider
from scripts.configure_zoro import discover_models, save_connection
from scripts.check_zoro import run_checks
from scripts import gpu_zoro

BASE = "http://127.0.0.1:30001/v1"
KEY = "test-only-not-a-real-key"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("No real HTTP allowed in unit tests")
    async def async_denied(*args, **kwargs):
        denied()
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", denied)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_denied)


def test_discovery_reads_model_id_and_auth_without_redirects():
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "test-model-sha"}]})
    assert discover_models(BASE, KEY, transport=httpx.MockTransport(handler)) == ["test-model-sha"]
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["authorization"] == "Bearer " + KEY


@pytest.mark.parametrize("status,body", [(302, {}), (401, {}), (200, {"data": []}),
    (200, {"data": [{"id": "bad\nmodel"}]}), (200, []), (200, {"data": "wrong"})])
def test_discovery_rejects_unusable_model_responses(status, body):
    with pytest.raises((ValueError, ProviderError)):
        discover_models(BASE, transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body)))


def test_discovery_bounds_large_body():
    with pytest.raises(ValueError):
        discover_models(BASE, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * 70_000)))


def test_zoro_settings_preserve_molit_and_multiline_values(tmp_path):
    target = tmp_path / "settings.env"
    original = "# user comment\nMOLIT_API_KEY='fake-molit-key'\nCUSTOM='first\nZORO_MODEL=inside-other-value'\nZORO_MODEL=old\n"
    target.write_text(original)
    save_connection(target, BASE, "test-model-sha", KEY)
    result = dotenv_values(target, interpolate=False)
    assert result["MOLIT_API_KEY"] == "fake-molit-key"
    assert result["CUSTOM"] == "first\nZORO_MODEL=inside-other-value"
    assert result["ZORO_MODEL"] == "test-model-sha"
    assert result["ZORO_TIMEOUT_SECONDS"] == "30"
    assert target.read_text().startswith("# user comment")


def test_zoro_settings_invalid_input_does_not_overwrite(tmp_path):
    target = tmp_path / "settings.env"
    target.write_text("MOLIT_API_KEY='fake-preserve'\n")
    before = target.read_bytes()
    with pytest.raises(ProviderError):
        save_connection(target, "http://user:password@localhost:3", "model")
    assert target.read_bytes() == before


def action(tool, arguments=None):
    return {"tool": tool, "arguments": arguments or {}, "reason": "offline test response"}


class ScriptedProvider:
    def __init__(self, wrong_inspection=False):
        self.steps = [action("search_candidates"), action("finish"),
                      action("record_inspection", {"listing_id": "L001", "field": "step_free_access", "value": wrong_inspection}),
                      action("set_entry_time", {"new_end": "2026-11-25T15:00:00+09:00"}),
                      action("propose_schedule"), action("finish")]
        self.calls = []

    async def decide(self, message, state, trace):
        self.calls.append(deepcopy((message, state, trace)))
        assert "test-molit-secret" not in json.dumps((message, state, trace))
        return self.steps.pop(0)


def test_live_check_harness_runs_real_business_tools_with_fake_inference(tmp_path):
    original_db = tmp_path / "user.sqlite3"
    original_db.write_bytes(b"do-not-touch-user-state")
    settings = Settings(zoro_base_url=BASE, zoro_model="test-model",
                        molit_trade_api_key="test-molit-secret", state_db_path=original_db)
    fake = ScriptedProvider()
    results = asyncio.run(run_checks(settings, provider=fake))
    assert len(results) == 3 and all(row["passed"] for row in results)
    assert results[1]["approval_checked"] and results[2]["approval_checked"]
    assert [turn["zoro_used"] for turn in results[1]["turns"]] == [True, False]
    assert all(results[1]["evidence"].values())
    assert original_db.read_bytes() == b"do-not-touch-user-state"
    assert not fake.steps


def test_live_check_will_not_autoapprove_a_wrong_model_proposal():
    fake = ScriptedProvider(wrong_inspection=True)
    results = asyncio.run(run_checks(Settings(zoro_base_url=BASE, zoro_model="test-model"), provider=fake))
    assert results[0]["passed"]
    assert not results[1]["passed"] and not results[1]["approval_checked"]
    assert results[2]["passed"]


def test_gpu_launch_binds_only_loopback_and_uses_fixed_official_source(tmp_path, monkeypatch):
    python = tmp_path / "venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    (tmp_path / "model-snapshot.json").write_text(json.dumps({"path": "/cache/official-model-snapshot"}))
    calls = []
    monkeypatch.setattr(gpu_zoro, "preflight", lambda *a, **kw: None)
    monkeypatch.setattr(gpu_zoro, "run", lambda command, **kw: calls.append(command))
    monkeypatch.setattr(gpu_zoro.os, "execve", lambda executable, command, env: calls.append(command))
    gpu_zoro.start(tmp_path)
    command = calls[-1]
    assert command[command.index("--host") + 1] == "127.0.0.1"
    assert command[command.index("--context-length") + 1] == "16384"
    assert gpu_zoro.REPOSITORY == "https://github.com/trillion-labs/trida-stack.git"
    assert len(gpu_zoro.REVISION) == 40


def test_gpu_preflight_rejects_windows_before_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_zoro.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="Linux"):
        gpu_zoro.preflight(tmp_path)


@pytest.mark.parametrize("filename", ["decision_head.pt", "jev_head.pt", None])
def test_gpu_download_records_a_snapshot_only_with_a_supported_head(tmp_path, monkeypatch, filename):
    checkpoint = tmp_path / "snapshots" / ("a" * 40)
    checkpoint.mkdir(parents=True)
    if filename:
        (checkpoint / filename).touch()

    hub = ModuleType("huggingface_hub")
    requested_revisions = []

    def snapshot_download(model, revision):
        assert model == gpu_zoro.MODEL
        requested_revisions.append(revision)
        return str(checkpoint)

    hub.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setattr(sys, "argv", ["-c", str(tmp_path)])
    marker = tmp_path / "model-snapshot.json"
    if filename is None:
        with pytest.raises(SystemExit) as error:
            exec(gpu_zoro.DOWNLOAD, {})
        assert error.value.code == 1 and not marker.exists()
        return

    exec(gpu_zoro.DOWNLOAD, {})
    assert json.loads(marker.read_text())["path"] == str(checkpoint)
    exec(gpu_zoro.DOWNLOAD, {})
    assert requested_revisions == ["main", checkpoint.name]


def test_action_request_disables_thinking_for_bounded_tool_output():
    request = json.loads(ZoroProvider(base_url=BASE, model="model")._request_bytes("조건 확인", {}, []))
    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    assert request["temperature"] == 0
    assert request["max_tokens"] == 1024
