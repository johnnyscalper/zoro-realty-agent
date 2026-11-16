"""Offline key entry/report regression tests; all credentials are fake.

No test calls an external API. Fixtures and MockTransport success are not
records of authenticated MOLIT or Zoro execution.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import warnings

from dotenv import dotenv_values
import httpx
import pytest

from app.config import Settings
from app.molit import MolitError, fetch_transactions
from scripts import check_api, configure_keys


TRADE = "offline-only-trade-key+/="
RENT = "offline-only-rent-key+/="
LEAK = "never-print-provider-body-or-key"


@pytest.fixture(autouse=True)
def block_actual_network(monkeypatch):
    async def blocked(*args, **kwargs):
        raise AssertionError("External requests are forbidden in key setup tests")

    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)


def arguments(kind="both", lawd_cd="11680", month="202610"):
    return argparse.Namespace(kind=kind, lawd_cd=lawd_cd, month=month)


def fixture_page(kind="trade", **overrides):
    page = {
        "source": "molit", "synthetic": False, "kind": kind,
        "lawd_cd": "11680", "deal_ymd": "202610", "total_count": 21,
        "returned_count": 3, "skipped_count": 0,
        "retrieved_at": LEAK,
        "warnings": [LEAK],
        "items": [{"apartment_name": LEAK, "legal_dong": LEAK}],
    }
    page.update(overrides)
    return page


def run_check(tmp_path, *, settings=None, args=None):
    path = tmp_path / "reports" / "api_verification_report.json"
    status = asyncio.run(check_api.check(args or arguments(), settings=settings or Settings(), report_path=path))
    return status, json.loads(path.read_text(encoding="utf-8"))


def test_save_distinct_keys_preserves_other_settings_and_multiline_values(tmp_path):
    path = tmp_path / ".env"
    original = (
        "# keep this comment\nAPP_PORT=9000\n"
        "ZORO_API_KEY='offline-zoro'\nCUSTOM='line one\nMOLIT_TRADE_API_KEY=inside-value\nline three'\n"
        "export MOLIT_TRADE_API_KEY='old-one'\nMOLIT_TRADE_API_KEY='duplicate-old'\n"
        "MOLIT_RENT_API_KEY=old-rent\nMOLIT_API_KEY=old-shared\nUNRELATED=${KEEP_LITERAL}\n"
    )
    path.write_text(original, encoding="utf-8")
    previous = dotenv_values(path, interpolate=False)
    configure_keys.save_keys(path, TRADE, RENT)
    values = dotenv_values(path, interpolate=False)
    for key in set(previous) - set(configure_keys.KEY_NAMES):
        assert values[key] == previous[key]
    assert values["MOLIT_TRADE_API_KEY"] == TRADE
    assert values["MOLIT_RENT_API_KEY"] == RENT
    assert values["MOLIT_API_KEY"] == ""
    text = path.read_text(encoding="utf-8")
    assert "# keep this comment" in text
    assert all(old not in text for old in ("old-one", "duplicate-old", "old-rent", "old-shared"))


def test_shared_key_clears_specific_keys_and_settings_fallback_remains_unverified(tmp_path):
    path = tmp_path / ".env"
    path.write_text("MOLIT_TRADE_API_KEY=old\nMOLIT_RENT_API_KEY=old\n", encoding="utf-8")
    configure_keys.save_keys(path, TRADE, TRADE, shared=True)
    values = dotenv_values(path, interpolate=False)
    assert values == {"MOLIT_TRADE_API_KEY": "", "MOLIT_RENT_API_KEY": "", "MOLIT_API_KEY": TRADE}
    cfg = Settings.from_env(values)
    assert cfg.molit_trade_api_key == cfg.molit_rent_api_key == TRADE
    assert cfg.public_status()["molit"]["verified"] is False


@pytest.mark.parametrize("key", ["offline%2B%2F%3D", "offline+key/=", "offline'quote\\slash#${LITERAL}"])
def test_saved_key_round_trips_without_predecoding(tmp_path, key):
    path = tmp_path / ".env"
    configure_keys.save_keys(path, key, key, shared=True)
    assert dotenv_values(path, interpolate=False)["MOLIT_API_KEY"] == key
    assert configure_keys.validate_key(key) == key


@pytest.mark.parametrize("value", ["", "   ", "fake key", "fake\nMOLIT_API_KEY=injected", "fake\x00key", "nonascii키", "double%252Bencoded", "invalid%xx", "bad%FF", "a" * 1025])
def test_invalid_key_does_not_modify_existing_settings(tmp_path, value):
    path = tmp_path / ".env"
    path.write_text("APP_PORT=9001\n", encoding="utf-8")
    with pytest.raises(configure_keys.KeySetupError):
        configure_keys.save_keys(path, value, RENT)
    assert path.read_text(encoding="utf-8") == "APP_PORT=9001\n"


def test_malformed_existing_env_is_not_overwritten_or_echoed(tmp_path, capsys):
    path = tmp_path / ".env"
    original = f"BROKEN='unterminated {LEAK}\n"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(configure_keys.KeySetupError) as exc:
        configure_keys.save_keys(path, TRADE, RENT)
    assert path.read_text(encoding="utf-8") == original
    assert LEAK not in str(exc.value)
    assert LEAK not in capsys.readouterr().out


def test_failed_replace_retains_original_and_removes_temporary_key_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("APP_PORT=9002\n", encoding="utf-8")

    def failed_replace(*args):
        raise OSError(LEAK)

    monkeypatch.setattr(configure_keys.os, "replace", failed_replace)
    with pytest.raises(configure_keys.KeySetupError) as exc:
        configure_keys.save_keys(path, TRADE, RENT)
    assert LEAK not in str(exc.value)
    assert path.read_text(encoding="utf-8") == "APP_PORT=9002\n"
    assert list(tmp_path.iterdir()) == [path]


def test_private_permissions_are_applied_before_any_secret_is_written(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    observed = []
    original = configure_keys._private_permissions

    def inspect_before_write(temporary):
        observed.append(temporary.read_bytes())
        original(temporary)

    monkeypatch.setattr(configure_keys, "_private_permissions", inspect_before_write)
    configure_keys.save_keys(path, TRADE, RENT)
    assert observed == [b""]
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_permission_failure_writes_no_credentials_and_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("APP_PORT=9003\n", encoding="utf-8")

    def denied(*args):
        raise PermissionError(LEAK)

    monkeypatch.setattr(configure_keys, "_private_permissions", denied)
    with pytest.raises(configure_keys.KeySetupError) as exc:
        configure_keys.save_keys(path, TRADE, RENT)
    assert LEAK not in str(exc.value)
    assert path.read_text(encoding="utf-8") == "APP_PORT=9003\n"
    assert list(tmp_path.iterdir()) == [path]


def test_symlink_target_is_never_read_or_replaced(tmp_path):
    target = tmp_path / "target"
    target.write_text(LEAK, encoding="utf-8")
    path = tmp_path / ".env"
    path.symlink_to(target)
    with pytest.raises(configure_keys.KeySetupError):
        configure_keys.save_keys(path, TRADE, RENT)
    assert path.is_symlink()
    assert target.read_text(encoding="utf-8") == LEAK


def test_windows_permissions_use_sid_no_shell_and_capture_output(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout='"TEST\\person","S-1-5-21-123-456-789-1001"\r\n')

    monkeypatch.setattr(configure_keys, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(configure_keys.subprocess, "run", run)
    path = tmp_path / "empty.tmp"
    configure_keys._private_permissions(path)
    assert calls[1][0] == ["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-21-123-456-789-1001:(F)"]
    assert all(kwargs.get("capture_output") and kwargs.get("check") and not kwargs.get("shell") for _, kwargs in calls)


def test_noninteractive_terminal_refuses_hidden_input_without_consuming_stdin(monkeypatch):
    stdin = io.StringIO(TRADE)
    monkeypatch.setattr(configure_keys.sys, "stdin", stdin)
    with pytest.raises(configure_keys.KeySetupError):
        configure_keys._read_hidden("키: ")
    assert stdin.tell() == 0


def test_getpass_echo_fallback_is_rejected(monkeypatch):
    monkeypatch.setattr(configure_keys.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def fallback(prompt):
        warnings.warn("Password input may be echoed.", configure_keys.getpass.GetPassWarning)
        raise AssertionError("Must stop before echo fallback")

    monkeypatch.setattr(configure_keys.getpass, "getpass", fallback)
    with pytest.raises(configure_keys.KeySetupError):
        configure_keys._read_hidden("키: ")


def setup_interactive(monkeypatch, tmp_path, *, keys, answers):
    key_iter, answer_iter = iter(keys), iter(answers)
    monkeypatch.setattr(configure_keys, "ROOT", tmp_path)
    monkeypatch.setattr(configure_keys, "_read_hidden", lambda prompt: next(key_iter))
    monkeypatch.setattr("builtins.input", lambda prompt: next(answer_iter))


def test_save_then_decline_verification_does_not_run_check(tmp_path, monkeypatch, capsys):
    setup_interactive(monkeypatch, tmp_path, keys=[TRADE, RENT], answers=["n", ""])

    async def forbidden(*args, **kwargs):
        pytest.fail("Declined verification must not run")

    monkeypatch.setattr(check_api, "check", forbidden)
    assert configure_keys.main([]) == 0
    assert (tmp_path / ".env").is_file()
    assert not (tmp_path / "reports").exists()
    output = capsys.readouterr().out
    assert "check_api.py --kind both" in output
    assert "실제 API 검증은 실행하지 않았습니다" in output
    assert TRADE not in output and RENT not in output


def test_explicit_confirmation_checks_both_only_after_saving(tmp_path, monkeypatch, capsys):
    setup_interactive(monkeypatch, tmp_path, keys=[TRADE], answers=["y", "y"])
    observed = []

    async def fake_check(args):
        assert dotenv_values(tmp_path / ".env", interpolate=False)["MOLIT_API_KEY"] == TRADE
        observed.append(vars(args))
        return 1

    monkeypatch.setattr(check_api, "check", fake_check)
    assert configure_keys.main([]) == 1
    assert observed == [{"kind": "both", "lawd_cd": "11680", "month": "202610"}]
    assert TRADE not in capsys.readouterr().out


def test_cancelled_second_key_keeps_existing_env(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("APP_PORT=9004\n", encoding="utf-8")
    monkeypatch.setattr(configure_keys, "ROOT", tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    calls = 0

    def entry(prompt):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return TRADE

    monkeypatch.setattr(configure_keys, "_read_hidden", entry)
    assert configure_keys.main([]) == 1
    assert path.read_text(encoding="utf-8") == "APP_PORT=9004\n"


@pytest.mark.parametrize("script", ["configure_keys.py", "check_api.py"])
def test_secret_cli_arguments_are_rejected_without_echoing(script):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / "scripts" / script), "--api-key", LEAK], capture_output=True, text=True)
    assert result.returncode == 2
    assert LEAK not in result.stdout + result.stderr


def test_missing_keys_report_not_run_without_network(tmp_path, monkeypatch):
    async def forbidden(**kwargs):
        pytest.fail("Missing keys must not call client")

    monkeypatch.setattr(check_api, "fetch_transactions", forbidden)
    status, report = run_check(tmp_path)
    assert status == 1
    assert report["status"] == "NOT_RUN"
    assert report["both_apis_verified"] is False
    assert all(item["status"] == "NOT_RUN" and item["total_count"] is None for item in report["services"].values())


def test_success_report_uses_counts_local_timestamps_and_fixed_warnings_only(tmp_path, monkeypatch, capsys):
    calls = []

    async def fake_fetch(**kwargs):
        calls.append(kwargs)
        return fixture_page(kwargs["kind"])

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, report = run_check(tmp_path, settings=Settings(molit_trade_api_key=TRADE, molit_rent_api_key=RENT))
    assert status == 0 and report["status"] == "VERIFIED" and report["both_apis_verified"] is True
    assert [call["api_key"] for call in calls] == [TRADE, RENT]
    assert all(call["page_no"] == 1 and call["num_of_rows"] == 5 for call in calls)
    for stamp in [report["started_at"], report["finished_at"], *(result["checked_at"] for result in report["services"].values())]:
        assert datetime.fromisoformat(stamp).tzinfo is not None
    contents = json.dumps(report, ensure_ascii=False) + capsys.readouterr().out
    for secret in (TRADE, RENT, LEAK, "apartment_name", "items", "retrieved_at"):
        assert secret not in contents


def test_shared_settings_key_is_used_for_both_live_attempts(tmp_path, monkeypatch):
    calls = []

    async def fake_fetch(**kwargs):
        calls.append(kwargs["api_key"])
        return fixture_page(kwargs["kind"])

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, _ = run_check(tmp_path, settings=Settings.from_env({"MOLIT_API_KEY": TRADE}))
    assert status == 0 and calls == [TRADE, TRADE]


def test_empty_success_is_reported_with_no_item_validation_claim(tmp_path, monkeypatch):
    async def fake_fetch(**kwargs):
        return fixture_page(kwargs["kind"], total_count=0, returned_count=0)

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, report = run_check(tmp_path, settings=Settings(molit_trade_api_key=TRADE, molit_rent_api_key=RENT))
    assert status == 0
    assert all(item["total_count"] == 0 and any("0건" in warning for warning in item["warnings"]) for item in report["services"].values())


def test_skipped_rows_are_partial_and_never_mark_both_apis_verified(tmp_path, monkeypatch):
    async def fake_fetch(**kwargs):
        return fixture_page(kwargs["kind"], skipped_count=1 if kwargs["kind"] == "trade" else 0)

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, report = run_check(tmp_path, settings=Settings(molit_trade_api_key=TRADE, molit_rent_api_key=RENT))
    assert status == 1 and report["both_apis_verified"] is False
    assert report["status"] == "PARTIAL"
    assert report["services"]["trade"]["status"] == "PARTIAL"
    assert report["services"]["trade"]["skipped_count"] == 1


@pytest.mark.parametrize("error", [MolitError("MOLIT_AUTH_ERROR", LEAK), MolitError(LEAK, LEAK), RuntimeError(LEAK)])
def test_errors_are_sanitized_and_other_api_is_still_attempted(tmp_path, monkeypatch, capsys, error):
    calls = []

    async def fake_fetch(**kwargs):
        calls.append(kwargs["kind"])
        if kwargs["kind"] == "trade":
            raise error
        return fixture_page("rent")

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, report = run_check(tmp_path, settings=Settings(molit_trade_api_key=TRADE, molit_rent_api_key=RENT))
    assert status == 1 and calls == ["trade", "rent"]
    assert report["services"]["trade"]["status"] == "FAILED"
    assert report["services"]["rent"]["status"] == "OK"
    assert LEAK not in json.dumps(report) + capsys.readouterr().out


@pytest.mark.parametrize("change", [
    {"total_count": LEAK}, {"returned_count": True}, {"skipped_count": -1},
    {"total_count": 2**100}, {"returned_count": 6}, {"returned_count": 3, "skipped_count": 3},
    {"synthetic": True}, {"source": "synthetic_sample"}, {"kind": "rent"},
    {"total_count": 0, "returned_count": 1}, {"total_count": 1, "returned_count": 0},
])
def test_untrusted_or_inconsistent_success_metadata_is_not_written(tmp_path, monkeypatch, change):
    async def fake_fetch(**kwargs):
        return fixture_page(**change)

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, report = run_check(tmp_path, settings=Settings(molit_trade_api_key=TRADE), args=arguments("trade"))
    assert status == 1
    assert report["services"]["trade"]["code"] == "MOLIT_INVALID_RESPONSE"
    assert report["services"]["trade"]["total_count"] is None
    assert LEAK not in json.dumps(report)


def test_single_api_cli_compatibility_does_not_imply_other_api_verified(tmp_path, monkeypatch):
    async def fake_fetch(**kwargs):
        assert kwargs["kind"] == "rent"
        return fixture_page("rent")

    monkeypatch.setattr(check_api, "fetch_transactions", fake_fetch)
    status, report = run_check(tmp_path, settings=Settings(molit_rent_api_key=RENT), args=arguments("rent"))
    assert status == 0
    assert report["both_apis_verified"] is False
    assert report["services"]["trade"]["status"] == "NOT_REQUESTED"


def test_invalid_query_is_sanitized_before_any_request(tmp_path, monkeypatch, capsys):
    async def forbidden(**kwargs):
        pytest.fail("Invalid query must not call client")

    monkeypatch.setattr(check_api, "fetch_transactions", forbidden)
    status, report = run_check(tmp_path, settings=Settings(molit_trade_api_key=TRADE), args=arguments(month=LEAK))
    assert status == 1
    assert all(item["code"] == "INVALID_QUERY" for item in report["services"].values())
    assert LEAK not in json.dumps(report) + capsys.readouterr().out


def test_configuration_error_produces_safe_not_run_report(tmp_path, monkeypatch, capsys):
    def invalid():
        raise ValueError(LEAK)

    monkeypatch.setattr(check_api.Settings, "from_env", invalid)
    path = tmp_path / "report.json"
    status = asyncio.run(check_api.check(arguments(), report_path=path))
    report = json.loads(path.read_text(encoding="utf-8"))
    assert status == 1
    assert all(item["code"] == "CONFIGURATION_ERROR" for item in report["services"].values())
    assert LEAK not in json.dumps(report) + capsys.readouterr().out


def test_report_save_failure_returns_failure_without_raw_exception(tmp_path, monkeypatch, capsys):
    def denied(*args):
        raise configure_keys.KeySetupError(LEAK)

    monkeypatch.setattr(check_api, "atomic_write_private", denied)
    assert asyncio.run(check_api.check(arguments(), settings=Settings(), report_path=tmp_path / "report.json")) == 1
    output = capsys.readouterr().out
    assert "보고서를 저장하지 못했습니다" in output and LEAK not in output


def test_windows_launcher_bootstraps_without_running_server_first():
    text = (Path(__file__).parents[1] / "CONFIGURE_API_KEYS_WINDOWS.cmd").read_text(encoding="utf-8")
    assert text.count("scripts\\bootstrap.py --no-run") == 2
    assert '".venv\\Scripts\\python.exe" scripts\\configure_keys_gui.py' in text
    after_gui = text.split("scripts\\configure_keys_gui.py", 1)[1]
    before_check = after_gui.split("scripts\\check_api.py", 1)[0]
    assert "if errorlevel 1 (" in before_check and "goto finish" in before_check
    assert "scripts\\configure_keys.py" not in text
