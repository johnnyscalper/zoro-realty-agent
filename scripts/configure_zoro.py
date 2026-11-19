"""Discover a model ID and update only Zoro settings in the local .env."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from dotenv.parser import parse_stream
from app.zoro_provider import ZoroProvider
from app.molit import _protect_transport_logging
from scripts.configure_keys import atomic_write_private, _quote

NAMES = {"ZORO_BASE_URL", "ZORO_MODEL", "ZORO_API_KEY", "ZORO_TIMEOUT_SECONDS"}


def discover_models(base_url, api_key="", *, transport=None):
    # Reuse the production adapter's URL/key validation before any request.
    ZoroProvider(base_url=base_url, model="connection-check", api_key=api_key)
    base = base_url.rstrip("/")
    endpoint = base + ("/models" if base.endswith("/v1") else "/v1/models")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    _protect_transport_logging()
    with httpx.Client(transport=transport, timeout=5, trust_env=False, follow_redirects=False) as client:
        with client.stream("GET", endpoint, headers=headers) as response:
            if response.status_code != 200:
                raise ValueError("Model list unavailable")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 65_536:
                    raise ValueError("Model list too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("Invalid model list")
    models = []
    for item in data["data"]:
        model = item.get("id") if isinstance(item, dict) else None
        ZoroProvider(base_url=base_url, model=model, api_key=api_key)
        if model not in models:
            models.append(model)
    if not models:
        raise ValueError("Empty model list")
    return models


def save_connection(path, base_url, model, api_key=""):
    ZoroProvider(base_url=base_url, model=model, api_key=api_key)
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Settings symlink not supported")
    original = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    bindings = list(parse_stream(io.StringIO(original)))
    if any(binding.error for binding in bindings):
        raise ValueError("Invalid existing settings")
    preserved = "".join(binding.original.string for binding in bindings if binding.key not in NAMES)
    if preserved and not preserved.endswith("\n"):
        preserved += "\n"
    values = {"ZORO_BASE_URL": base_url.rstrip("/"), "ZORO_MODEL": model,
              "ZORO_API_KEY": api_key, "ZORO_TIMEOUT_SECONDS": "30"}
    atomic_write_private(path, preserved + "".join(f"{key}={_quote(value)}\n" for key, value in values.items()))


def main():
    import tkinter as tk
    from tkinter import ttk
    root = tk.Tk()
    root.title("집의 다음 · Zoro 연결 설정")
    root.geometry("680x340")
    frame = ttk.Frame(root, padding=20)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    ttk.Label(frame, text="먼저 GPU 실행과 SSH 터널을 열어주세요.").grid(row=0, sticky="w", pady=5)
    ttk.Label(frame, text="Zoro 서버 주소 (SSH 터널이면 기본값 유지)").grid(row=1, sticky="w")
    url = tk.StringVar(value="http://127.0.0.1:30001/v1")
    ttk.Entry(frame, textvariable=url, width=70).grid(row=2, sticky="ew", pady=6)
    ttk.Label(frame, text="Zoro 서버 인증키 (이번 SSH 연결에서는 비워 둡니다. 국토부/HF 키 아님)").grid(row=3, sticky="w")
    key = tk.StringVar()
    ttk.Entry(frame, textvariable=key, show="●", width=70).grid(row=4, sticky="ew", pady=6)
    message = tk.StringVar(value="모델 ID는 서버에서 자동으로 읽습니다. 기존 국토부 키는 보존합니다.")
    ttk.Label(frame, textvariable=message, wraplength=620).grid(row=5, sticky="w", pady=15)
    result = [1]

    def save():
        button.configure(state="disabled")
        message.set("서버 연결 확인 중입니다. 잠시 기다려주세요.")
        root.update_idletasks()
        try:
            models = discover_models(url.get().strip(), key.get().strip())
            if len(models) != 1:
                message.set("서버에 모델이 여러 개 있습니다. 정확한 모델 ID를 확인한 뒤 설정해야 합니다.")
                return
            save_connection(ROOT / ".env", url.get().strip(), models[0], key.get().strip())
            result[0] = 0
            key.set("")
            root.destroy()
        except Exception:
            message.set("연결 또는 저장 실패. GPU 서버·SSH 터널·주소·기존 설정 파일을 확인하세요. 모델 검증 성공으로 처리하지 않습니다.")
        finally:
            if result[0] != 0:
                button.configure(state="normal")

    button = ttk.Button(frame, text="연결 확인 후 설정 저장", command=save)
    button.grid(row=6, sticky="e")
    root.mainloop()
    if result[0] == 0:
        print("Zoro settings saved. Restart the app, then run CHECK_ZORO_WINDOWS.cmd.")
        if any(os.environ.get(name) for name in NAMES):
            print("Existing ZORO environment variables override the saved .env values.")
    return result[0]


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Could not open the settings window. Check Python tkinter support. No secret is printed.")
        raise SystemExit(1)
