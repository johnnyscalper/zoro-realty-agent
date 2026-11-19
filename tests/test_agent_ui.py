"""Offline agent UI checks; the small DOM double does not replace browser QA."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_agent_ui_state_regressions():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the offline agent UI state checks")
    script = Path(__file__).with_name("agent_ui.cjs")
    result = subprocess.run(
        [node, "--test", str(script)], text=True, capture_output=True, timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_agent_selects_have_real_option_elements():
    """Inspect actual HTML start tags; a DOM mock must not manufacture modes."""
    from html.parser import HTMLParser

    class SelectParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.current = None
            self.selects = {}

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "select":
                self.current = attributes.get("id")
                self.selects[self.current] = []
            elif tag == "option" and self.current:
                self.selects[self.current].append(attributes)

        def handle_endtag(self, tag):
            if tag == "select":
                self.current = None

    parser = SelectParser()
    html = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")
    parser.feed(html)
    modes = parser.selects["agent-mode"]
    assert [option["value"] for option in modes] == ["development", "zoro"]
    assert "selected" in modes[0]
    assert "disabled" in modes[1]
    data_modes = parser.selects["agent-data-mode"]
    assert [option["value"] for option in data_modes] == ["sample", "live"]
    assert "selected" in data_modes[0]
