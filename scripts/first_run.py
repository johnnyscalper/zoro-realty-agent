"""First-launch setup for a fresh, standalone Windows bundle.

Only the selected old folder's MOLIT key settings can be imported. No recursive
disk scan, old code copy, database move, or remote model request is performed.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from dotenv.parser import parse_stream

from app.config import Settings
from scripts.configure_keys import KeySetupError, atomic_write_private, save_keys, validate_key


def imported_keys(folder: Path) -> tuple[str, str]:
    """Read only an explicitly chosen project folder or its standard child."""
    folder = Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise KeySetupError("기존 프로젝트 폴더를 선택하세요.")
    for parent in (folder, folder / "zoro-realty-prep"):
        path = parent / ".env"
        if parent.is_symlink() or path.is_symlink():
            raise KeySetupError("연결된 경로 대신 실제 프로젝트 폴더를 선택하세요.")
        if not path.is_file():
            continue
        if path.stat().st_size > 65_536:
            raise KeySetupError("설정 파일이 너무 커서 가져오지 않았습니다.")
        text = path.read_text(encoding="utf-8-sig")
        if any(binding.error for binding in parse_stream(io.StringIO(text))):
            raise KeySetupError("기존 설정 파일 형식을 확인할 수 없습니다.")
        values = dotenv_values(stream=io.StringIO(text), interpolate=False)
        shared = str(values.get("MOLIT_API_KEY") or "").strip()
        trade = str(values.get("MOLIT_TRADE_API_KEY") or shared).strip()
        rent = str(values.get("MOLIT_RENT_API_KEY") or shared).strip()
        return validate_key(trade), validate_key(rent)
    raise KeySetupError("이 폴더에는 저장된 API 키가 없습니다. ‘API 키 입력’을 선택해 주세요.")


def import_keys(folder: Path, target: Path):
    trade, rent = imported_keys(folder)
    save_keys(target, trade, rent, shared=(trade == rent))


def remember_sample(root: Path):
    atomic_write_private(root / ".runtime" / "first_run.json", '{"choice":"sample"}\n')


def needs_setup(settings: Settings, root: Path) -> bool:
    if settings.molit_trade_api_key and settings.molit_rent_api_key:
        return False
    marker = root / ".runtime" / "first_run.json"
    try:
        if marker.is_symlink() or marker.stat().st_size > 1024:
            return True
        return json.loads(marker.read_text(encoding="utf-8")) != {"choice": "sample"}
    except (OSError, ValueError):
        return True


def choose_setup():
    import tkinter as tk
    from tkinter import filedialog, ttk

    root = tk.Tk()
    root.title("집의 다음 · 처음 시작하기")
    root.geometry("680x410")
    root.minsize(620, 390)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    result = ["cancel"]
    ttk.Label(frame, text="새 폴더에서 바로 시작하세요", font=("Segoe UI", 17, "bold")).grid(row=0, sticky="w", pady=(0, 10))
    ttk.Label(frame, text="모든 최신 코드가 들어 있습니다. 기존 폴더에 덮어쓰지 않아도 됩니다.", wraplength=610).grid(row=1, sticky="w", pady=(0, 18))
    status = tk.StringVar(value="기존 폴더를 찾기 어렵다면 ‘API 키 입력’을 선택하세요.")

    def finish(choice):
        result[0] = choice
        root.destroy()

    def use_old_keys():
        selected = filedialog.askdirectory(parent=root, title="예전에 API 키를 저장한 프로젝트 폴더 선택")
        if not selected:
            return
        try:
            import_keys(Path(selected), ROOT / ".env")
        except Exception:
            status.set("선택한 폴더에서 사용할 키를 찾지 못했습니다. 다른 폴더를 선택하거나 ‘API 키 입력’을 누르세요.")
            return
        finish("imported")

    ttk.Button(frame, text="API 키 입력하고 시작", command=lambda: finish("enter")).grid(row=2, sticky="ew", pady=5, ipady=7)
    ttk.Button(frame, text="기존 폴더에서 API 키 가져오기", command=use_old_keys).grid(row=3, sticky="ew", pady=5, ipady=7)
    ttk.Button(frame, text="키 없이 예시 자료로 먼저 시작", command=lambda: finish("sample")).grid(row=4, sticky="ew", pady=5, ipady=7)
    ttk.Label(frame, textvariable=status, wraplength=610).grid(row=5, sticky="w", pady=(16, 8))
    ttk.Label(frame, text="키를 입력하거나 가져오면 국토부 API를 각 1회 검사합니다. Zoro GPU 연결은 이후에 진행합니다.", wraplength=610).grid(row=6, sticky="w")
    root.mainloop()
    return result[0]


def complete_setup(choice: str, *, root: Path = ROOT, runner=None) -> int:
    runner = runner or subprocess.run
    if choice == "cancel":
        return 2
    if choice == "sample":
        remember_sample(root)
        return 0
    if choice not in {"enter", "imported"}:
        return 2
    if choice == "enter":
        entered = runner([sys.executable, str(root / "scripts" / "configure_keys_gui.py")], cwd=root, check=False)
        if entered.returncode:
            return entered.returncode
    checked = runner([sys.executable, str(root / "scripts" / "check_api.py"),
                      "--kind", "both", "--lawd-cd", "11680", "--month", "202610"], cwd=root, check=False)
    if checked.returncode:
        print("API verification did not finish successfully. The app will still open; read the check results above.", flush=True)
    else:
        print("API checks completed. Starting the app.", flush=True)
    return 0


def main():
    try:
        if not needs_setup(Settings.from_env(), ROOT):
            return 0
        return complete_setup(choose_setup(), root=ROOT)
    except KeyboardInterrupt:
        print("First-run setup cancelled.")
        return 130
    except Exception:
        print("First-run setup could not finish. Check Python tkinter support and folder permissions. No secret is printed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
