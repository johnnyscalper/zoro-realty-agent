"""Offline upgrade checks using only synthetic settings in temporary folders.

These checks do not read the workspace .env, build an archive, install packages,
launch a server, or make a provider request.
"""
from hashlib import sha256
import os
import subprocess
import sys

import pytest

from scripts import bootstrap


@pytest.mark.parametrize("installation_succeeds", [True, False])
def test_normal_start_after_dependency_update_keeps_existing_local_settings(
    tmp_path, monkeypatch, capsys, installation_succeeds
):
    """An app update must retain saved credentials even if installation fails."""
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap.py"])
    environment = tmp_path / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.write_text("not an executable; subprocess is replaced", encoding="utf-8")

    old_digest = sha256(b"example==1.0\n").hexdigest()
    marker = environment / ".requirements-sha256"
    marker.write_text(old_digest, encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_bytes(b"example==2.0\n")
    local_settings = (
        b"# Local settings survive replacing the public code.\r\n"
        b"MOLIT_TRADE_API_KEY=synthetic-upgrade-trade-key\r\n"
        b"MOLIT_RENT_API_KEY=synthetic-upgrade-rent-key\r\n"
        b"APP_PORT=8123\r\nSTATE_DB_PATH=.runtime/saved.sqlite3\r\n"
    )
    env_path = tmp_path / ".env"
    env_path.write_bytes(local_settings)
    (tmp_path / ".env.example").write_text(
        "MOLIT_TRADE_API_KEY=\nMOLIT_RENT_API_KEY=\nAPP_PORT=8000\n",
        encoding="utf-8",
    )
    calls = []

    def run(command, *, check, cwd):
        assert check is True and cwd == tmp_path
        assert env_path.read_bytes() == local_settings
        calls.append(command)
        if len(calls) == 1:
            assert command == [str(executable), "-m", "pip", "install", "-r", str(requirements)]
            if not installation_succeeds:
                raise subprocess.CalledProcessError(1, command)
        else:
            assert command == [str(executable), str(tmp_path / "run.py")]

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    if installation_succeeds:
        bootstrap.main()
        assert len(calls) == 2
        assert marker.read_text() == sha256(requirements.read_bytes()).hexdigest()
    else:
        with pytest.raises(subprocess.CalledProcessError):
            bootstrap.main()
        assert len(calls) == 1
        assert marker.read_text() == old_digest

    assert env_path.read_bytes() == local_settings
    output = capsys.readouterr()
    assert "synthetic-upgrade-trade-key" not in output.out + output.err
    assert "synthetic-upgrade-rent-key" not in output.out + output.err
