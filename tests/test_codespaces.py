from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_codespace_preview_accepts_only_its_assigned_browser_origin(tmp_path):
    env = {
        "CODESPACES": "true",
        "CODESPACE_NAME": "example-workspace",
        "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "app.github.dev",
        "STATE_DB_PATH": str(tmp_path / "preview.sqlite3"),
    }
    settings = Settings.from_env(env)
    host = "example-workspace-8000.app.github.dev"
    assert settings.codespace_host == host
    with TestClient(create_app(settings), base_url=f"http://{host}") as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/sessions", json={}, headers={"Origin": f"https://{host}"}).status_code == 201
        assert client.post("/api/sessions", json={}, headers={"Origin": "https://other-8000.app.github.dev"}).status_code == 403
        assert client.get("/", headers={"Host": "other-8000.app.github.dev"}).status_code == 400
        response = client.post("/api/sessions", json={}, headers={"Host": "localhost:8000", "Origin": f"https://{host}"})
        assert response.status_code == 201

    assert Settings.from_env({**env, "CODESPACES": "false"}).codespace_host == ""
    assert Settings.from_env({**env, "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "*.github.dev"}).codespace_host == ""
