"""Desktop credential entry; never reads keys from a console or command line.

The Windows launcher runs the existing live checker only after this window
successfully saves. Cancel/error returns a nonzero exit status. The console
helper remains available separately for macOS/Linux.
"""
from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import os
import sys
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.configure_keys import KEY_NAMES, KeySetupError, SafeArgumentParser, save_keys


def save_form(path: Path, trade: str, rent: str, shared: bool) -> tuple[bool, str]:
    """Return only fixed status text; never interpolate form values/errors."""
    try:
        save_keys(path, trade, trade if shared else rent, shared=shared)
    except KeySetupError:
        return False, "저장하지 못했습니다. 키의 앞뒤 공백·줄바꿈과 폴더 권한을 확인하세요. 기존 설정은 유지됩니다."
    except Exception:
        return False, "저장하지 못했습니다. 폴더 권한과 실행 환경을 확인한 뒤 다시 시도하세요."
    return True, "키를 저장했습니다. 매매·전월세 연결 검사를 시작합니다."


class KeyWindow:
    def __init__(self, root, *, env_path: Path | None = None):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.env_path = env_path if env_path is not None else ROOT / ".env"
        self.result = 2  # Closing/cancelling must never trigger a live API call.
        self.busy = False
        self.events = Queue()
        self.trade = tk.StringVar(root)
        self.rent = tk.StringVar(root)
        self.shared = tk.BooleanVar(root, value=True)
        self.trade_count = tk.StringVar(root, value="입력 0자")
        self.rent_count = tk.StringVar(root, value="매매와 같은 키를 사용합니다.")
        self.status = tk.StringVar(root, value="복사한 인증키를 ‘붙여넣기’ 버튼으로 넣으세요.")

        root.title("집의 다음 · 국토부 API 키 설정")
        root.geometry("700x510")
        root.minsize(660, 490)
        root.protocol("WM_DELETE_WINDOW", self.cancel)
        root.report_callback_exception = self.callback_error
        frame = ttk.Frame(root, padding=24)
        frame.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="API 키를 여기에 붙여넣으세요", font=("Segoe UI", 17, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(frame, text="검은 CMD 창에는 입력하지 않아도 됩니다. 입력하면 ●와 글자 수가 표시됩니다.", wraplength=580).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 18))
        ttk.Label(frame, text="매매 API 일반 인증키").grid(row=2, column=0, sticky="w")
        self.trade_entry = ttk.Entry(frame, textvariable=self.trade, show="●", width=55)
        self.trade_entry.grid(row=3, column=0, sticky="ew", pady=6)
        self.trade_paste = ttk.Button(frame, text="붙여넣기", command=lambda: self.paste(self.trade))
        self.trade_paste.grid(row=3, column=1, padx=(10, 0))
        ttk.Label(frame, textvariable=self.trade_count).grid(row=4, column=0, sticky="w")
        self.shared_check = ttk.Checkbutton(frame, text="전월세에도 같은 인증키 사용", variable=self.shared, command=self.refresh)
        self.shared_check.grid(row=5, column=0, columnspan=2, sticky="w", pady=(16, 8))
        ttk.Label(frame, text="전월세 API 일반 인증키 (서로 다를 때만 입력)").grid(row=6, column=0, columnspan=2, sticky="w")
        self.rent_entry = ttk.Entry(frame, textvariable=self.rent, show="●", width=55)
        self.rent_entry.grid(row=7, column=0, sticky="ew", pady=6)
        self.rent_paste = ttk.Button(frame, text="붙여넣기", command=lambda: self.paste(self.rent))
        self.rent_paste.grid(row=7, column=1, padx=(10, 0))
        ttk.Label(frame, textvariable=self.rent_count).grid(row=8, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, textvariable=self.status, wraplength=580).grid(row=9, column=0, columnspan=2, sticky="w", pady=(18, 10))
        ttk.Label(frame, text="저장 후 두 API를 각 1회 조회합니다. 검사 결과는 CMD 창에 표시됩니다.", wraplength=580).grid(row=10, column=0, columnspan=2, sticky="w", pady=(0, 12))
        self.save_button = ttk.Button(frame, text="저장하고 연결 검사", command=self.submit)
        self.save_button.grid(row=11, column=0, sticky="e")
        self.cancel_button = ttk.Button(frame, text="취소", command=self.cancel)
        self.cancel_button.grid(row=11, column=1, padx=(10, 0))
        self.trade.trace_add("write", self.refresh)
        self.rent.trace_add("write", self.refresh)
        root.bind("<Return>", lambda event: self.submit())
        root.bind("<Escape>", lambda event: self.cancel())
        self.refresh()
        root.after(150, self.focus)

    def focus(self):
        self.root.lift()
        self.trade_entry.focus_force()

    def refresh(self, *args):
        self.trade_count.set(f"입력 {len(self.trade.get().strip())}자")
        shared = self.shared.get()
        self.rent_count.set("매매와 같은 키를 사용합니다." if shared else f"입력 {len(self.rent.get().strip())}자")
        for widget in (self.trade_entry, self.trade_paste, self.shared_check, self.save_button, self.cancel_button):
            widget.configure(state="disabled" if self.busy else "normal")
        for widget in (self.rent_entry, self.rent_paste):
            widget.configure(state="disabled" if self.busy or shared else "normal")

    def paste(self, variable):
        if self.busy:
            return
        try:
            value = self.root.clipboard_get()
            if not isinstance(value, str) or not value.strip() or len(value) > 4096:
                self.status.set("클립보드에 인증키가 없습니다. 포털에서 키만 다시 복사하세요.")
                return
            variable.set(value.strip())  # Replace, so repeated clicks never double a key.
            self.status.set("입력되었습니다. 글자 수를 확인하고 ‘저장하고 연결 검사’를 누르세요.")
        except Exception:
            self.status.set("클립보드를 읽지 못했습니다. 입력칸을 클릭하고 Ctrl+V로 붙여넣으세요.")

    def submit(self):
        if self.busy:
            return
        trade, rent, shared = self.trade.get(), self.rent.get(), self.shared.get()
        if not trade.strip() or (not shared and not rent.strip()):
            self.status.set("인증키를 먼저 입력하세요. 전월세 키가 같으면 체크를 유지하세요.")
            return
        self.busy = True
        self.status.set("이 컴퓨터에 저장하고 있습니다. 잠시 기다려주세요.")
        self.refresh()

        def work():
            self.events.put(save_form(self.env_path, trade, rent, shared))

        Thread(target=work, daemon=True).start()
        self.root.after(80, self.poll)

    def poll(self):
        try:
            ok, message = self.events.get_nowait()
        except Empty:
            self.root.after(80, self.poll)
            return
        self.busy = False
        self.status.set(message)
        if ok:
            self.result = 0
            self.trade.set("")
            self.rent.set("")
            self.root.destroy()
        else:
            self.refresh()

    def cancel(self):
        if self.busy:
            return
        self.trade.set("")
        self.rent.set("")
        self.result = 2
        self.root.destroy()

    def callback_error(self, exception, value, traceback):
        # Tk's default handler prints raw exception values, which may contain keys.
        self.status.set("화면 동작을 완료하지 못했습니다. 입력을 확인하거나 창을 닫고 다시 실행하세요.")


def main(argv: list[str] | None = None) -> int:
    SafeArgumentParser(description="별도 창에서 인증키를 입력합니다. 키를 명령행 인수로 받지 않습니다.").parse_args(argv)
    try:
        import tkinter as tk
        root = tk.Tk()
    except Exception:
        print("키 입력창을 열 수 없습니다. Python 설치의 Modify에서 'tcl/tk and IDLE'을 설치하고 다시 실행하세요.")
        print("기존 키 설정은 변경하지 않았습니다. 숨김 콘솔 입력으로 자동 전환하지 않습니다.")
        return 1
    try:
        window = KeyWindow(root)
        root.mainloop()
        if window.result == 0 and any(os.environ.get(name) for name in KEY_NAMES):
            print("주의: 현재 터미널의 MOLIT 환경 변수가 저장한 키보다 우선합니다. 연결 검사도 해당 설정을 사용합니다.")
        return window.result
    except Exception:
        print("키 입력창을 완료하지 못했습니다. 창을 닫고 다시 실행하세요.")
        return 1
    finally:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
