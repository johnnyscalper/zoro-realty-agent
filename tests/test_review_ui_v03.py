"""Offline JS transaction-state checks; no server, browser, network, or credentials."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_transaction_ui_state_regressions():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the offline transaction UI state checks")
    script = Path(__file__).with_name("ui_transactions_v03.cjs")
    result = subprocess.run(
        [node, "--test", str(script)], text=True, capture_output=True, timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
