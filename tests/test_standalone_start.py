"""Offline verification of standalone setup and private-setting preservation."""
from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

from dotenv import dotenv_values
import pytest

from app.config import Settings
from scripts import bootstrap, build_package, first_run
from scripts.configure_keys import KeySetupError
from run import open_when_ready

KEY = "a" * 64
RENT_KEY = "b" * 64


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("shared", [False, True])
def test_import_copies_only_selected_molit_keys_and_preserves_original(tmp_path, nested, shared):
    old = tmp_path / "old"
    source = old / "zoro-realty-prep" if nested else old
    source.mkdir(parents=True)
    text = (f"MOLIT_API_KEY={KEY}\n" if shared else f"MOLIT_TRADE_API_KEY={KEY}\nMOLIT_RENT_API_KEY={RENT_KEY}\n")
    text += "STATE_DB_PATH=/old/db.sqlite3\nZORO_API_KEY=old-private-server-key\nAPP_PORT=9999\n"
    (source / ".env").write_text(text)
    (source / ".runtime").mkdir()
    (source / ".runtime/user.sqlite3").write_bytes(b"old-conversation")
    target = tmp_path / "new" / ".env"
    target.parent.mkdir()
    target.write_text("APP_PORT=8000\nZORO_MODEL=new-setting\n")
    before = (source / ".env").read_bytes()
    first_run.import_keys(old, target)
    settings = Settings.from_env(dict(dotenv_values(target)))
    assert settings.molit_trade_api_key == KEY
    assert settings.molit_rent_api_key == (KEY if shared else RENT_KEY)
    assert settings.app_port == 8000 and settings.zoro_model == "new-setting"
    assert "old-private-server-key" not in target.read_text()
    assert (source / ".env").read_bytes() == before
    assert (source / ".runtime/user.sqlite3").read_bytes() == b"old-conversation"
    assert not (target.parent / ".runtime").exists()


@pytest.mark.parametrize("body", [None, "MOLIT_API_KEY=\n", "MOLIT_API_KEY='unfinished", "MOLIT_API_KEY=bad key\n"])
def test_missing_or_invalid_old_keys_never_replace_destination(tmp_path, body):
    old = tmp_path / "old"
    old.mkdir()
    if body is not None:
        (old / ".env").write_text(body)
    target = tmp_path / "new.env"
    target.write_text("APP_PORT=8000\n")
    with pytest.raises(KeySetupError):
        first_run.import_keys(old, target)
    assert target.read_text() == "APP_PORT=8000\n"


def test_import_rejects_symlinked_source(tmp_path):
    source = tmp_path / "outside.env"
    source.write_text(f"MOLIT_API_KEY={KEY}\n")
    folder = tmp_path / "old"
    folder.mkdir()
    (folder / ".env").symlink_to(source)
    with pytest.raises(KeySetupError):
        first_run.imported_keys(folder)


def test_first_setup_skips_when_keys_are_present_or_sample_choice_saved(tmp_path):
    assert first_run.needs_setup(Settings(), tmp_path)
    assert not first_run.needs_setup(Settings(molit_trade_api_key=KEY, molit_rent_api_key=KEY), tmp_path)
    first_run.remember_sample(tmp_path)
    assert not first_run.needs_setup(Settings(), tmp_path)
    assert KEY not in (tmp_path / ".runtime/first_run.json").read_text()


def test_cancel_and_sample_never_call_network_or_key_gui(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected subprocess")
    assert first_run.complete_setup("cancel", root=tmp_path, runner=forbidden) == 2
    assert first_run.complete_setup("sample", root=tmp_path, runner=forbidden) == 0


@pytest.mark.parametrize("choice,check_exit", [("enter", 0), ("enter", 1), ("imported", 0)])
def test_key_setup_runs_check_after_success_and_does_not_hide_failure(tmp_path, choice, check_exit, capsys):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        code = check_exit if command[1].endswith("check_api.py") else 0
        return subprocess.CompletedProcess(command, code)
    assert first_run.complete_setup(choice, root=tmp_path, runner=runner) == 0
    if choice == "enter":
        assert calls[0][1].endswith("configure_keys_gui.py")
    assert calls[-1][1].endswith("check_api.py")
    assert ("did not finish successfully" in capsys.readouterr().out) == (check_exit != 0)


def test_cancelled_key_entry_does_not_query_api(tmp_path):
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 2)
    assert first_run.complete_setup("enter", root=tmp_path, runner=runner) == 2
    assert len(calls) == 1 and calls[0][1].endswith("configure_keys_gui.py")


def test_browser_waits_for_this_server_and_skips_failed_start():
    server = SimpleNamespace(started=False, should_exit=False)
    opened = []
    def wait(_):
        server.started = True
    open_when_ready(server, "http://127.0.0.1:8000", wait=wait, opener=opened.append)
    assert opened == ["http://127.0.0.1:8000"]
    opened.clear()
    server = SimpleNamespace(started=False, should_exit=True)
    open_when_ready(server, "http://127.0.0.1:8000", wait=wait, opener=opened.append)
    assert not opened


def test_flat_bundle_is_complete_and_does_not_include_local_keys(tmp_path):
    target = tmp_path / "standalone.zip"
    build_package.build(target, flat=True)
    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        for path in ("START_WINDOWS.cmd", "scripts/first_run.py", "scripts/gpu_zoro.py",
                     "scripts/check_zoro.py", "app/main.py", "static/index.html", "data/listings.json"):
            assert path in names
        assert not any(name.startswith("zoro-realty-prep/") for name in names)
        assert ".env" not in names
        assert not any(name.startswith((".runtime/", ".venv/", "reports/")) for name in names)
        assert "--guided" in archive.read("START_WINDOWS.cmd").decode()


def test_guided_start_only_opens_app_after_first_setup_succeeds(tmp_path, monkeypatch):
    from hashlib import sha256
    import os
    import sys
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--guided"])
    environment = tmp_path / ".venv"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(b"test==1\n")
    (environment / ".requirements-sha256").write_text(sha256(requirements.read_bytes()).hexdigest())
    (tmp_path / ".env").write_text("MOLIT_API_KEY=\n")
    calls = []
    def runner(command, **kwargs):
        calls.append(command)
    monkeypatch.setattr(bootstrap.subprocess, "run", runner)
    bootstrap.main()
    assert calls == [[str(python), str(tmp_path / "scripts/first_run.py")],
                     [str(python), str(tmp_path / "run.py"), "--open-browser"]]
