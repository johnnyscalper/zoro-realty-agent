"""Credential-entry regressions. All keys/files are disposable and offline."""
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
import subprocess
import sys

from dotenv import dotenv_values
import pytest

from scripts import configure_keys_gui as gui


KEY = "a" * 64
OTHER = "b" * 64


class Value:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_same_key_saved_without_touching_other_settings(tmp_path):
    path = tmp_path / ".env"
    path.write_text("APP_PORT=9010\nZORO_BASE_URL=http://localhost:9900\n")
    ok, message = gui.save_form(path, KEY, "ignored-stale-rent", True)
    assert ok and KEY not in message
    assert dotenv_values(path, interpolate=False) == {
        "APP_PORT": "9010", "ZORO_BASE_URL": "http://localhost:9900",
        "MOLIT_API_KEY": KEY, "MOLIT_TRADE_API_KEY": "", "MOLIT_RENT_API_KEY": "",
    }


def test_distinct_keys_saved_independently(tmp_path):
    path = tmp_path / ".env"
    assert gui.save_form(path, KEY, OTHER, False)[0]
    values = dotenv_values(path, interpolate=False)
    assert values["MOLIT_TRADE_API_KEY"] == KEY
    assert values["MOLIT_RENT_API_KEY"] == OTHER
    assert values["MOLIT_API_KEY"] == ""


@pytest.mark.parametrize("invalid", ["", "bad key", "bad\nkey", "bad\x16key", "a" * 1025])
def test_invalid_gui_key_preserves_existing_file(tmp_path, invalid):
    path = tmp_path / ".env"
    path.write_text("APP_PORT=9010\n")
    assert not gui.save_form(path, invalid, OTHER, False)[0]
    assert path.read_text() == "APP_PORT=9010\n"


def test_permission_error_is_not_displayed_with_secret(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(KEY)

    monkeypatch.setattr(gui, "save_keys", denied)
    ok, message = gui.save_form(tmp_path / ".env", KEY, "", True)
    assert not ok and KEY not in message


def bare_window():
    window = gui.KeyWindow.__new__(gui.KeyWindow)
    window.busy = False
    window.result = 2
    window.trade, window.rent = Value(), Value()
    window.shared = Value(True)
    window.status = Value()
    window.events = Queue()
    window.root = SimpleNamespace(destroy=lambda: None, clipboard_get=lambda: KEY)
    window.refresh = lambda: None
    return window


def test_repeated_paste_replaces_instead_of_duplicating_key():
    window = bare_window()
    window.paste(window.trade)
    window.paste(window.trade)
    assert window.trade.get() == KEY
    assert KEY not in window.status.get()


def test_clipboard_failure_keeps_typed_value_and_does_not_echo_error():
    window = bare_window()
    window.trade.set(OTHER)

    def unavailable():
        raise RuntimeError(KEY)

    window.root.clipboard_get = unavailable
    window.paste(window.trade)
    assert window.trade.get() == OTHER
    assert KEY not in window.status.get()


def test_blank_form_never_starts_save():
    window = bare_window()
    window.submit()
    assert not window.busy
    assert "먼저 입력" in window.status.get()


def test_cancel_clears_values_and_returns_nonzero():
    window = bare_window()
    window.trade.set(KEY)
    window.rent.set(OTHER)
    closed = []
    window.root.destroy = lambda: closed.append(True)
    window.cancel()
    assert window.result == 2 and closed
    assert window.trade.get() == window.rent.get() == ""


def test_cannot_cancel_or_paste_while_save_is_in_progress():
    window = bare_window()
    window.busy = True
    window.trade.set(OTHER)
    window.root.destroy = lambda: pytest.fail("Must wait for save result")
    window.cancel()
    window.paste(window.trade)
    assert window.trade.get() == OTHER and window.result == 2


def test_save_failure_keeps_window_open_for_retry():
    window = bare_window()
    window.busy = True
    window.root.destroy = lambda: pytest.fail("Failure must not close the form")
    window.events.put((False, "safe error"))
    window.poll()
    assert not window.busy and window.result == 2
    assert window.status.get() == "safe error"


def test_only_successful_save_returns_zero_and_closes_window():
    window = bare_window()
    window.busy = True
    window.trade.set(KEY)
    closed = []
    window.root.destroy = lambda: closed.append(True)
    window.events.put((True, "saved"))
    window.poll()
    assert window.result == 0 and closed
    assert not window.trade.get()


def test_gui_callback_error_does_not_disclose_keys():
    window = bare_window()
    window.callback_error(RuntimeError, RuntimeError(KEY), None)
    assert KEY not in window.status.get()


def test_cli_secret_argument_rejected_before_opening_a_window():
    script = Path(__file__).resolve().parents[1] / "scripts/configure_keys_gui.py"
    result = subprocess.run([sys.executable, str(script), "--api-key", KEY], capture_output=True, text=True)
    assert result.returncode == 2
    assert KEY not in result.stdout + result.stderr
