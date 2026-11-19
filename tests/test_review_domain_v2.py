"""Independent regressions found during the second-wave domain review."""

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_explicit_null_conditions_are_not_silently_replaced_with_defaults(tmp_path):
    """Omission is valid, but a supplied Conditions value must be an object."""
    settings = Settings.from_env({"STATE_DB_PATH": str(tmp_path / "review.sqlite3")})
    with TestClient(create_app(settings)) as client:
        assert client.post("/api/sessions", json={}).status_code == 201
        assert client.post("/api/sessions", json={"conditions": {}}).status_code == 201

        response = client.post("/api/sessions", json={"conditions": None})

    assert response.status_code == 422, response.text
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message", "retryable"}
