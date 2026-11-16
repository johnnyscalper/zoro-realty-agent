"""Independent delivery checks: installed bootstrap and extracted ZIP, offline."""
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import zipfile

from scripts import bootstrap, build_package


def test_stage5_runtime_reports_and_key_files_cannot_enter_distribution(tmp_path, monkeypatch):
    root = tmp_path / "source"
    private = "synthetic-private-runtime-sentinel"
    files = {
        "README.md": "Public readme",
        ".env.example": "MOLIT_API_KEY=\n",
        "CONFIGURE_API_KEYS_WINDOWS.cmd": "@echo off\n",
        "scripts/configure_keys.py": "# public source\n",
        "scripts/check_api.py": "# public source\n",
        "app/tools.py": "# public source\n",
        ".env": private,
        ".runtime/realty.sqlite3": private,
        ".runtime/state.json": private,
        "reports/api_verification_report.json": private,
        "data/reports/api_verification_report.json": private,
        "tests/.runtime/session.json": private,
        "scripts/private.key": private,
        "scripts/.env.backup": private,
    }
    for relative, value in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    monkeypatch.setattr(build_package, "ROOT", root)
    archive_path = tmp_path / "delivery.zip"
    build_package.build(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert "zoro-realty-prep/CONFIGURE_API_KEYS_WINDOWS.cmd" in archive.namelist()
        assert "zoro-realty-prep/scripts/configure_keys.py" in archive.namelist()
        for name in archive.namelist():
            assert private.encode() not in archive.read(name)


def test_extracted_distribution_serves_prior_stage_and_persistent_manual_tools(tmp_path):
    """A clean extracted tree must not depend on unbundled source or fixtures."""
    archive_path = tmp_path / "delivery.zip"
    build_package.build(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(tmp_path / "extracted")
    project = tmp_path / "extracted" / "zoro-realty-prep"
    for required in (
        "START_WINDOWS.cmd", "START_MAC_LINUX.sh", "CONFIGURE_API_KEYS_WINDOWS.cmd",
        "scripts/bootstrap.py", "scripts/configure_keys.py", "scripts/check_api.py",
        "app/domain.py", "app/store.py", "app/scheduling.py", "app/tools_routes.py",
        "data/listings.json", "data/inspections.json", "data/schedules.json", "data/scenarios.json",
        "static/index.html", "static/app.js", "static/styles.css", ".env.example",
    ):
        assert (project / required).is_file(), required
    assert not (project / ".env").exists()
    assert not (project / ".runtime").exists()
    assert not (project / "reports").exists()
    program = textwrap.dedent("""
        from pathlib import Path
        from fastapi.testclient import TestClient
        from app.config import Settings
        from app.main import create_app

        settings = Settings(state_db_path=Path('.runtime/delivery-smoke.sqlite3'))
        app = create_app(settings)
        async def no_live_calls(**kwargs):
            raise AssertionError('This smoke test is offline')
        app.state.molit_fetcher = no_live_calls
        with TestClient(app) as client:
            for route in ('/', '/static/app.js', '/static/styles.css', '/api/preparation'):
                assert client.get(route).status_code == 200, route
            assert len(client.get('/api/listings').json()['listings']) == 20
            sample = client.get('/api/transactions', params={
                'kind': 'rent', 'lawd_cd': '11680', 'deal_ymd': '202610', 'mode': 'sample'
            })
            assert sample.status_code == 200 and sample.json()['synthetic'] is True
            health = client.get('/api/health').json()
            assert health['mode'] == 'preparation'
            assert health['molit']['verified'] is False
            assert health['zoro']['status'] == 'not_connected'
            assert health['zoro']['verified'] is False
            response = client.post('/api/sessions', json={'conditions': {'hard': {'max_deposit_krw': 1000000000}}})
            assert response.status_code == 201
            state = response.json()
            session_id = state['session_id']
            response = client.post('/api/sessions/' + session_id + '/search', json={'expected_revision': 0})
            assert response.status_code == 200
            saved = response.json()
            assert saved['revision'] == 1
            assert saved['search_result']['status'] == 'ready'
            assert saved['synthetic'] is True and saved['zoro_used'] is False
        with TestClient(create_app(settings)) as restarted:
            restored = restarted.get('/api/sessions/' + session_id)
            assert restored.status_code == 200 and restored.json() == saved
        print('extracted-delivery-offline-ok')
    """)
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("MOLIT_", "ZORO_", "APP_"))
        and key not in {"STATE_DB_PATH", "PYTHONPATH"}
    }
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=project, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "extracted-delivery-offline-ok" in result.stdout


def test_bootstrap_no_run_preserves_existing_keys_and_does_not_launch_or_reinstall(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--no-run"])
    environment = tmp_path / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.write_text("fake executable", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("example==1.0\n", encoding="utf-8")
    (environment / ".requirements-sha256").write_text(sha256(requirements.read_bytes()).hexdigest())
    original = "MOLIT_API_KEY=synthetic-user-key\nAPP_PORT=8123\n"
    (tmp_path / ".env").write_text(original, encoding="utf-8")
    (tmp_path / ".env.example").write_text("MOLIT_API_KEY=\n", encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise AssertionError("Cached --no-run setup must not install or launch")

    monkeypatch.setattr(bootstrap.subprocess, "run", forbidden)
    bootstrap.main()
    assert (tmp_path / ".env").read_text(encoding="utf-8") == original


def test_first_bootstrap_no_run_creates_environment_then_installs_before_key_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["bootstrap.py", "--no-run"])
    (tmp_path / "requirements.txt").write_text("example==1.0\n", encoding="utf-8")
    example = "MOLIT_TRADE_API_KEY=\nMOLIT_RENT_API_KEY=\n"
    (tmp_path / ".env.example").write_text(example, encoding="utf-8")
    events = []

    def create_environment(self, environment):
        events.append("environment")
        executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable.parent.mkdir(parents=True)
        executable.write_text("fake executable", encoding="utf-8")

    def install(command, *, check, cwd):
        events.append("install")
        assert command[1:5] == ["-m", "pip", "install", "-r"]
        assert check is True and cwd == tmp_path
        assert not (tmp_path / ".env").exists()

    monkeypatch.setattr(bootstrap.venv.EnvBuilder, "create", create_environment)
    monkeypatch.setattr(bootstrap.subprocess, "run", install)
    bootstrap.main()
    assert events == ["environment", "install"]
    assert (tmp_path / ".env").read_text(encoding="utf-8") == example
    assert (tmp_path / ".venv/.requirements-sha256").is_file()
