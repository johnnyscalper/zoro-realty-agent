"""Run the 20 synthetic scenarios against actual chat and confirm HTTP routes."""
from copy import deepcopy
import json

import pytest

from scripts import evaluate_agent as evaluator


CASES = evaluator.load_scenarios()


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"] + "-" + case["runner"])
def test_offline_development_http_scenario(case):
    result = evaluator.run_case(deepcopy(case))
    assert result["passed"] is True
    assert result["real_model_called"] is False
    assert result["mode"] == "development"
    assert result["http_requests"] >= 3


def test_catalog_covers_twenty_distinct_executable_cases():
    assert len(CASES) == len(evaluator.RUNNERS) == 20
    assert {case["runner"] for case in CASES} == set(evaluator.RUNNERS)
    assert all(case["messages"] and case["expected"] for case in CASES)
    assert {case["category"] for case in CASES} >= {"consultation", "evidence", "mutation", "schedule", "market", "security", "mode"}


def test_report_is_safe_and_identifies_no_real_model(monkeypatch):
    def simulated_failure(_case):
        raise RuntimeError("private URL https://private.invalid/?secret=must-not-appear")

    monkeypatch.setattr(evaluator, "run_case", simulated_failure)
    report = evaluator.evaluate()
    encoded = json.dumps(report)
    assert report["total"] == report["failed"] == 20
    assert report["passed"] == 0
    assert report["real_zoro_evaluated"] is False
    assert report["live_molit_authenticated"] is False
    assert "must-not-appear" not in encoded and "private.invalid" not in encoded
    assert all(item["failure_check"] == "unexpected_evaluation_error" for item in report["cases"])


def test_report_location_is_excluded_from_delivery(monkeypatch, tmp_path):
    monkeypatch.setattr(evaluator, "ROOT", tmp_path)
    report = {"real_zoro_evaluated": False, "total": 20, "passed": 20}
    path = evaluator.write_report(report)
    assert path == tmp_path / "reports" / "agent-development-evaluation.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    with pytest.raises(evaluator.ScenarioFailure, match="invalid_report_filename"):
        evaluator.write_report(report, "../outside.json")
    from scripts.build_package import source_files
    assert all("reports" not in path.parts for path in source_files())


def test_evaluator_forbids_real_transport():
    with evaluator.forbid_network() as attempts:
        with pytest.raises(evaluator.ScenarioFailure, match="offline_network_blocked"):
            import socket
            socket.create_connection(("127.0.0.1", 9))
    assert len(attempts) == 1


def test_evaluator_does_not_offer_silent_zoro_mode():
    with pytest.raises(SystemExit) as result:
        evaluator.main(["--mode", "zoro"])
    assert result.value.code == 2


def test_evaluator_never_reads_private_env(monkeypatch):
    def forbidden_env(*_args, **_kwargs):
        pytest.fail("offline evaluator must never read .env")

    monkeypatch.setattr("app.config.dotenv_values", forbidden_env)
    with evaluator.isolated_harness() as harness:
        state = harness.create()
        assert state["revision"] == 0
        assert harness.app.state.settings.molit_trade_api_key == evaluator.FAKE_KEYS["trade"]
