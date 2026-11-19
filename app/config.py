"""Server-only configuration. Never serialize credentials into API responses."""
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit
import os
import re

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(ValueError):
    """Safe configuration error; intentionally excludes supplied values."""


@dataclass(frozen=True)
class Settings:
    molit_trade_api_key: str = field(default="", repr=False)
    molit_rent_api_key: str = field(default="", repr=False)
    zoro_base_url: str = field(default="", repr=False)
    zoro_api_key: str = field(default="", repr=False)
    zoro_model: str = field(default="", repr=False)
    zoro_timeout_seconds: float = 15.0
    molit_timeout_seconds: float = 10.0
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    state_db_path: Path = field(default_factory=lambda: PROJECT_ROOT / ".runtime" / "realty.sqlite3", repr=False)
    codespace_host: str = field(default="", repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        if env is None:
            values = dict(dotenv_values(PROJECT_ROOT / ".env", interpolate=False))
            values.update(os.environ)
        else:
            values = dict(env)

        def text(key: str, default: str = "") -> str:
            return str(values.get(key) or default).strip()

        try:
            timeout = float(text("MOLIT_TIMEOUT_SECONDS", "10"))
            if not isfinite(timeout) or not 1 <= timeout <= 30:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigurationError("MOLIT_TIMEOUT_SECONDS는 1~30초여야 합니다.") from None
        try:
            port = int(text("APP_PORT", "8000"))
            if not 1024 <= port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigurationError("APP_PORT는 1024~65535의 정수여야 합니다.") from None
        host = text("APP_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
            raise ConfigurationError("APP_HOST는 localhost, 127.0.0.1 또는 0.0.0.0이어야 합니다.")
        zoro_url = text("ZORO_BASE_URL")
        if zoro_url:
            try:
                parsed = urlsplit(zoro_url)
                valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                         and not parsed.username and not parsed.password
                         and not parsed.query and not parsed.fragment)
                _ = parsed.port
                if not valid:
                    raise ValueError
            except ValueError:
                raise ConfigurationError("ZORO_BASE_URL에 인증정보·쿼리가 없는 HTTP(S) 주소를 넣어주세요.") from None
        codespace_host = ""
        if text("CODESPACES") == "true":
            name = text("CODESPACE_NAME")
            domain = text("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
            dns_label = r"[a-z0-9]+(?:-[a-z0-9]+)*"
            if re.fullmatch(dns_label, name) and re.fullmatch(rf"(?:{dns_label}\.)+{dns_label}", domain):
                codespace_host = f"{name}-{port}.{domain}"
        shared_key = text("MOLIT_API_KEY")
        model = text("ZORO_MODEL")
        if len(model) > 200 or any(ord(c) < 32 for c in model):
            raise ConfigurationError("ZORO_MODEL에 주최 측에서 안내한 짧은 모델 식별자를 넣어주세요.")
        try:
            zoro_timeout = float(text("ZORO_TIMEOUT_SECONDS", "15"))
            if not isfinite(zoro_timeout) or not 1 <= zoro_timeout <= 30:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigurationError("ZORO_TIMEOUT_SECONDS는 1~30초여야 합니다.") from None
        return cls(
            molit_trade_api_key=text("MOLIT_TRADE_API_KEY") or shared_key,
            molit_rent_api_key=text("MOLIT_RENT_API_KEY") or shared_key,
            zoro_base_url=zoro_url.rstrip("/"), zoro_api_key=text("ZORO_API_KEY"),
            zoro_model=model, zoro_timeout_seconds=zoro_timeout,
            molit_timeout_seconds=timeout, app_host=host, app_port=port,
            codespace_host=codespace_host,
            state_db_path=Path(text("STATE_DB_PATH", str(PROJECT_ROOT / ".runtime" / "realty.sqlite3"))).expanduser().resolve(),
        )

    def public_status(self) -> dict:
        return {
            "molit": {
                "trade_key_configured": bool(self.molit_trade_api_key),
                "rent_key_configured": bool(self.molit_rent_api_key),
                "verified": False,
            },
            "zoro": {
                "configured": bool(self.zoro_base_url),
                "verified": False,
                "status": "not_connected",
            },
        }
