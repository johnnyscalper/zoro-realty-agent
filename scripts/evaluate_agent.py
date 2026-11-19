"""Twenty offline HTTP scenarios, never an actual Zoro/model evaluation.

Run with the normal application dependencies: python scripts/evaluate_agent.py
All settings and SQLite databases are isolated. Even app.main's legacy global
app construction is intercepted before import. Real network transports and
socket connection functions are forbidden; simulated MOLIT uses MockTransport.
Reports deliberately omit responses, messages, keys, URLs and exception text.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from fastapi.testclient import TestClient

from app.config import Settings
from app.molit import ENDPOINTS, fetch_transactions

CATALOG = ROOT / "data" / "agent_scenarios.json"
BASE_CONDITIONS = {
    "hard": {"max_deposit_krw": 1_000_000_000, "min_area_m2": 75,
             "move_in_by": "2026-11-25", "step_free_access": True, "elevator": None},
    "preferences": {"noise_level": "low", "neighborhoods": []},
}
FAKE_KEYS = {"trade": "offline-evaluator-trade-sentinel", "rent": "offline-evaluator-rent-sentinel"}
BUSINESS_FIELDS = ("conditions", "listings", "inspections", "schedule", "schedule_proposals")
ALLOWED_TOOLS = {
    "update_conditions", "search_candidates", "request_verification", "record_inspection",
    "set_listing_status", "set_entry_time", "propose_schedule", "apply_schedule",
    "lookup_transactions", "ask_user", "finish",
}


class ScenarioFailure(AssertionError):
    """Only fixed, author-written check identifiers may be used as messages."""


def check(value, code):
    if not value:
        raise ScenarioFailure(code)


def load_scenarios():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    check(catalog.get("synthetic") is True, "catalog_not_synthetic")
    check(catalog.get("real_zoro_evaluated") is False, "catalog_claims_zoro")
    cases = catalog["scenarios"]
    check(len(cases) == 20 and len({case["id"] for case in cases}) == 20, "catalog_case_count")
    check({case["id"] for case in cases} == {f"A{i:02d}" for i in range(1, 21)}, "catalog_ids")
    return cases


@contextmanager
def forbid_network():
    """TestClient/ASGI and explicit MockTransport work; real sockets cannot."""
    attempts = []

    def denied(*_args, **_kwargs):
        attempts.append(True)
        raise ScenarioFailure("offline_network_blocked")

    async def async_denied(*_args, **_kwargs):
        return denied()

    with ExitStack() as stack:
        for target in ("socket.create_connection", "socket.getaddrinfo",
                       "socket.socket.connect", "socket.socket.connect_ex",
                       "httpx.HTTPTransport.handle_request"):
            stack.enter_context(patch(target, denied))
        stack.enter_context(patch("httpx.AsyncHTTPTransport.handle_async_request", async_denied))
        yield attempts


class Harness:
    def __init__(self, application, client):
        self.app = application
        self.client = client
        self.state = None
        self.responses = []
        self.mock_calls = []
        self.requests = 0

    def request(self, method, path, body=None):
        self.requests += 1
        response = self.client.request(method, path, **({"json": body} if body is not None else {}))
        self.responses.append(response.text)
        check(all(key not in response.text for key in FAKE_KEYS.values()), "secret_in_http_response")
        return response

    def create(self, initial="base"):
        conditions = {} if initial == "empty" else deepcopy(BASE_CONDITIONS)
        if initial == "no_match":
            conditions["hard"]["max_deposit_krw"] = 600_000_000
        response = self.request("POST", "/api/sessions", {"conditions": conditions})
        check(response.status_code == 201, "session_create_http")
        self.state = response.json()
        check(self.state["revision"] == 0, "fresh_case_revision")
        return self.state

    def path(self, suffix=""):
        return f"/api/sessions/{self.state['session_id']}{suffix}"

    def accept(self, response):
        check(response.status_code == 200, "agent_http_status")
        saved = response.json()
        check(saved["session_id"] == self.state["session_id"], "session_identity_changed")
        check(saved["revision"] == self.state["revision"] + 1, "non_atomic_revision")
        self.state = saved
        self.validate_agent()
        persisted = self.request("GET", self.path())
        check(persisted.status_code == 200 and persisted.json() == saved, "state_not_persisted")
        return saved

    def chat_body(self, message, *, data_mode="sample", mode="development", request_id=None):
        return {"expected_revision": self.state["revision"], "message": message,
                "mode": mode, "data_mode": data_mode, "request_id": request_id or str(uuid4())}

    def chat(self, message, **kwargs):
        return self.accept(self.request("POST", self.path("/chat"), self.chat_body(message, **kwargs)))

    def confirm_body(self, approve=True, request_id=None):
        pending = self.state["agent"]["pending"]
        check(pending is not None, "confirmation_missing")
        return {"expected_revision": self.state["revision"], "pending_id": pending["id"],
                "approve": approve, "request_id": request_id or str(uuid4())}

    def confirm(self, approve=True):
        return self.accept(self.request("POST", self.path("/chat/confirm"), self.confirm_body(approve)))

    def pending(self, tool):
        check(self.state["agent"]["pending"] is not None, "review_card_missing")
        check(self.state["agent"]["pending"]["tool"] == tool, "unexpected_review_tool")
        check(self.turn()["status"] == "awaiting_confirmation", "review_status_missing")

    def validate_agent(self):
        agent = self.state["agent"]
        check(len(agent["messages"]) <= 60 and len(agent["turns"]) <= 20, "history_bound")
        for turn in agent["turns"]:
            check(len(turn["trace"]) <= 6, "trace_bound")
            check(turn["mode"] == "development" and turn["zoro_used"] is False, "development_mode_claim")
            check(turn["reply"] and turn["status"] in {
                "completed", "awaiting_confirmation", "needs_input", "error", "limit_reached"
            }, "invalid_turn_outcome")

    def turn(self):
        return self.state["agent"]["turns"][-1]

    def evidence(self):
        found = [entry["observation"] for entry in self.turn()["trace"]
                 if entry["tool"] == "lookup_transactions" and entry["status"] == "executed"]
        check(len(found) == 1, "historical_observation_missing")
        return found[0]

    def install_molit(self, failure=False):
        """Only replace HTTP transport; retain credential selection and parser."""
        def transport(request):
            kind = "trade" if str(request.url).split("?")[0] == ENDPOINTS["trade"] else "rent"
            check(str(request.url).split("?")[0] == ENDPOINTS[kind], "mock_upstream_endpoint")
            check(request.url.params["serviceKey"] == FAKE_KEYS[kind], "mock_upstream_key_selection")
            check(request.url.params["LAWD_CD"] == "11680", "mock_upstream_district")
            check(request.url.params["DEAL_YMD"] == "202610", "mock_upstream_month")
            self.mock_calls.append(kind)
            if failure:
                return httpx.Response(503, text="private simulated upstream error " + FAKE_KEYS[kind])
            fixture = ROOT / "tests" / "fixtures" / f"molit_{kind}.xml"
            check(request.url.params["numOfRows"] == "100", "mock_upstream_page_size")
            xml = fixture.read_bytes().replace(b"<numOfRows>20</numOfRows>", b"<numOfRows>100</numOfRows>")
            return httpx.Response(200, content=xml, headers={"Content-Type": "application/xml"})

        async def fetch(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport),
                                         trust_env=False, follow_redirects=False) as upstream:
                return await fetch_transactions(**kwargs, client=upstream)

        self.app.state.molit_fetcher = fetch


@contextmanager
def isolated_harness():
    with TemporaryDirectory(prefix="agent-evaluation-") as temporary, forbid_network() as attempts:
        settings = Settings(state_db_path=Path(temporary) / "state.sqlite3",
                            molit_trade_api_key=FAKE_KEYS["trade"], molit_rent_api_key=FAKE_KEYS["rent"])
        # app.main still creates a global app at import; never let it read .env.
        with patch.object(Settings, "from_env", return_value=settings):
            module = import_module("app.main")
            application = module.create_app(settings)

        async def unexpected_fetch(**_kwargs):
            attempts.append(True)
            raise ScenarioFailure("unexpected_molit_call")

        application.state.molit_fetcher = unexpected_fetch
        with TestClient(application) as client:
            harness = Harness(application, client)
            yield harness
            check(attempts == [], "real_network_attempted")
            for filename in Path(temporary).iterdir():
                if filename.is_file():
                    check(not any(key.encode() in filename.read_bytes() for key in FAKE_KEYS.values()),
                          "secret_in_persisted_state")


def business(state):
    return {key: deepcopy(state[key]) for key in BUSINESS_FIELDS}


def ids(state, bucket):
    check(state["search_result"] is not None, "search_not_executed")
    return {item["listing"]["id"] for item in state["search_result"][bucket]}


def row(state, listing_id):
    return next(item for item in state["listings"] if item["id"] == listing_id)


def events(state):
    return {item["id"]: item for item in state["schedule"]["events"]}


def missing_budget(h, case):
    h.chat(case["messages"][0])
    check(h.turn()["status"] == "needs_input", "missing_budget_not_asked")
    check(h.state["conditions"]["hard"]["max_deposit_krw"] is None, "budget_invented")
    check(h.state["search_result"] is None or not ids(h.state, "eligible"), "missing_budget_recommended")
    check(h.state["agent"]["pending"] is None, "unrequested_budget_mutation")
    check(any(entry["tool"] == "ask_user" for entry in h.turn()["trace"]), "question_trace_missing")


def hard_and_preference(h, case):
    before = deepcopy(h.state["conditions"])
    h.chat(case["messages"][0]); h.pending("update_conditions")
    check(h.state["conditions"] == before, "conditions_changed_before_approval")
    h.confirm()
    check(h.state["conditions"]["hard"]["max_deposit_krw"] == 1_000_000_000, "korean_budget_parse")
    check(h.state["conditions"]["hard"]["min_area_m2"] == 75, "korean_area_parse")
    check(h.state["conditions"]["hard"]["step_free_access"] is True, "hard_access_parse")
    check(h.state["conditions"]["preferences"]["noise_level"] == "low", "noise_preference_parse")
    check({"L003", "L014"} <= ids(h.state, "eligible"), "preference_or_inclusive_boundary")
    check({"L005", "L017"} <= ids(h.state, "excluded"), "hard_numeric_boundary")


def unknown_access(h, case):
    h.chat(case["messages"][0])
    check("L001" in ids(h.state, "needs_verification"), "unknown_access_promoted")
    check(row(h.state, "L001")["step_free_access"] is None, "unknown_evidence_changed")
    pending = [task for task in h.state["verification_tasks"] if task["status"] == "pending"]
    check(1 <= len(pending) <= 2, "verification_request_bound")
    check(all(task["field"] == "step_free_access" for task in pending), "optional_evidence_prioritized")


def elevator_distinction(h, case):
    h.chat(case["messages"][0])
    check("L008" in ids(h.state, "excluded"), "elevator_assumed_access")
    check("L011" in ids(h.state, "eligible"), "ground_floor_access_excluded")
    check("L001" in ids(h.state, "needs_verification"), "elevator_resolved_unknown_access")


def contracted_listing(h, case):
    h.chat(case["messages"][0])
    check("L002" in ids(h.state, "eligible"), "fixture_missing_initial_candidate")
    h.chat(case["messages"][1]); h.pending("set_listing_status")
    check(row(h.state, "L002")["status"] == "available", "status_changed_before_approval")
    h.confirm()
    check(row(h.state, "L002")["status"] == "contracted", "status_not_applied")
    check({"L002", "L007", "L016"} <= ids(h.state, "excluded"), "contracted_candidate_retained")


def inspection_recheck(h, case):
    h.chat(case["messages"][0]); h.chat(case["messages"][1]); h.pending("record_inspection")
    check(row(h.state, "L001")["step_free_access"] is None, "inspection_changed_before_approval")
    h.confirm()
    check(row(h.state, "L001")["step_free_access"] is False, "inspection_value_not_applied")
    check("L001" in ids(h.state, "excluded"), "inspection_did_not_recheck")
    check(any(task["listing_id"] == "L001" and task["field"] == "step_free_access"
              and task["status"] == "confirmed" for task in h.state["verification_tasks"]), "task_not_confirmed")
    check(any(item["listing_id"] == "L001" and item["field"] == "step_free_access"
              and item["observed_value"] is False for item in h.state["inspections"]), "inspection_not_persisted")


def cancel_mutation(h, case):
    before = business(h.state)
    h.chat(case["messages"][0]); h.pending("update_conditions"); h.confirm(False)
    check(business(h.state) == before, "cancel_changed_business_state")
    check(h.state["agent"]["pending"] is None, "cancel_did_not_clear_pending")


def partial_conditions(h, case):
    expected = deepcopy(h.state["conditions"])
    expected["hard"]["max_deposit_krw"] = 900_000_000
    h.chat(case["messages"][0]); h.pending("update_conditions"); h.confirm()
    check(h.state["conditions"] == expected, "partial_change_cleared_conditions")
    check("L002" in ids(h.state, "excluded"), "budget_change_did_not_recheck")


def no_relaxation(h, case):
    before = deepcopy(h.state["conditions"])
    h.chat(case["messages"][0])
    check(not ids(h.state, "eligible"), "no_match_invented_candidate")
    check(h.state["conditions"] == before, "hard_requirement_relaxed")
    check(h.state["agent"]["pending"] is None, "unsolicited_condition_change")
    check(not any(t["tool"] == "update_conditions" for t in h.turn()["trace"]), "relaxation_proposed")


def delayed(h, message):
    before = deepcopy(events(h.state))
    h.chat(message); h.pending("set_entry_time")
    check(events(h.state) == before, "entry_changed_before_approval")
    h.confirm()
    return before


def schedule_delay(h, case):
    before = delayed(h, case["messages"][0])
    after = events(h.state)
    check(after["ENTRY-01"]["end"] == "2026-11-25T15:00:00+09:00", "entry_delay_not_applied")
    check(all(after[key] == before[key] for key in ("CLEAN-01", "MOVE-01")), "dependent_events_auto_moved")
    check(h.state["schedule"]["conflicts"], "schedule_conflicts_missing")
    check(h.state["schedule_proposals"], "replanning_missing")
    slots = h.state["schedule"]["provider_slots"]
    for proposal in h.state["schedule_proposals"]:
        check(proposal["based_on_revision"] == h.state["revision"], "proposal_revision_not_normalized")
        for event in proposal["events"]:
            if event["type"] in {"cleaning", "moving"}:
                check(any(slot["type"] == event["type"] and slot["start"] <= event["start"]
                          and event["end"] <= slot["end"] for slot in slots), "proposal_invented_slot")


def schedule_apply(h, case):
    delayed(h, case["messages"][0])
    proposed = deepcopy(h.state["schedule_proposals"][0])
    before = deepcopy(h.state["schedule"])
    h.chat(case["messages"][1]); h.pending("apply_schedule")
    check(h.state["schedule"] == before, "proposal_selection_auto_applied")
    h.confirm()
    check(h.state["schedule"]["events"] == proposed["events"], "wrong_schedule_applied")
    check(h.state["schedule"]["conflicts"] == [], "applied_schedule_conflicts")
    check(h.state["schedule"]["provider_slots"] == before["provider_slots"], "provider_slots_changed")


def schedule_no_slots(h, case):
    original = deepcopy(h.state["schedule"])
    delayed(h, case["messages"][0])
    check(h.state["schedule_proposals"] == [], "unavailable_schedule_invented")
    check(h.state["schedule"]["provider_slots"] == original["provider_slots"], "provider_slots_invented")
    check(h.state["schedule"]["conflicts"], "no_slot_conflicts_hidden")


def stale_confirmation(h, case):
    delayed(h, case["messages"][0]); h.chat(case["messages"][1]); h.pending("apply_schedule")
    stale = h.confirm_body()
    before = deepcopy(h.state["schedule"])
    response = h.request("PATCH", h.path("/listings/L002/status"),
                         {"expected_revision": h.state["revision"], "status": "contracted"})
    check(response.status_code == 200, "manual_mutation_failed")
    h.state = response.json()
    latest = deepcopy(h.state)
    response = h.request("POST", h.path("/chat/confirm"), stale)
    check(response.status_code == 409 and "error" in response.json(), "stale_confirmation_accepted")
    saved = h.request("GET", h.path()).json()
    check(saved == latest and saved["schedule"] == before, "stale_confirmation_changed_state")
    check(saved["agent"]["pending"] is None, "manual_change_left_pending")


def sample_history(h, case):
    before = business(h.state)
    h.chat(case["messages"][0])
    evidence = h.evidence()
    check(evidence["source"] == "sample" and evidence["synthetic"] is True, "sample_source_mislabelled")
    check(evidence["kind"] == "rent" and evidence["deal_ymd"] == "202610", "sample_query_wrong")
    check(len(evidence["items"]) <= 10 and evidence["matched_count"] >= len(evidence["items"]), "evidence_bound")
    check(business(h.state) == before and h.mock_calls == [], "sample_changed_business_state")


def mock_live_history(h, case):
    before = business(h.state)
    h.install_molit(); h.chat(case["messages"][0], data_mode="live")
    evidence = h.evidence()
    check(evidence["source"] == "molit" and evidence["synthetic"] is False, "live_path_source_mislabelled")
    check(evidence["kind"] == "trade" and evidence["deal_ymd"] == "202610", "live_query_wrong")
    check(h.mock_calls == ["trade"], "live_mock_not_called")
    check(evidence["scanned_count"] == 2 and evidence["matched_count"] == 1, "cancelled_trade_not_excluded")
    check(len(evidence["items"]) == 1, "cancelled_trade_retained")
    check(business(h.state) == before, "history_changed_fictional_listings")


def live_failure(h, case):
    before = business(h.state)
    h.install_molit(failure=True); h.chat(case["messages"][0], data_mode="live")
    check(h.turn()["status"] == "error", "live_failure_not_reported")
    check(h.mock_calls == ["rent"], "live_failure_transport_missing")
    check(not any(t["tool"] == "lookup_transactions" and t["status"] == "executed"
                  for t in h.turn()["trace"]), "live_failure_fell_back")
    check(business(h.state) == before, "live_error_changed_business_state")
    check("private simulated upstream error" not in "".join(h.responses), "upstream_error_leaked")


def arbitrary_tool(h, case):
    before = business(h.state)
    h.app.state.agent_service.planner = lambda *_args: {
        "tool": "execute_shell", "arguments": {"command": "do-not-execute"}, "reason": "unsupported"}
    h.chat(case["messages"][0])
    check(h.turn()["status"] == "error", "arbitrary_tool_not_rejected")
    check(business(h.state) == before, "arbitrary_tool_changed_state")
    check(not any(t["tool"] not in ALLOWED_TOOLS and t["status"] == "executed"
                  for t in h.turn()["trace"]), "arbitrary_tool_executed")


def idempotency(h, case):
    body = h.chat_body(case["messages"][0])
    h.accept(h.request("POST", h.path("/chat"), body)); h.pending("update_conditions")
    first = deepcopy(h.state)
    duplicate = h.request("POST", h.path("/chat"), body)
    check(duplicate.status_code == 200 and duplicate.json() == first, "duplicate_chat_not_idempotent")
    body = h.confirm_body()
    h.accept(h.request("POST", h.path("/chat/confirm"), body))
    confirmed = deepcopy(h.state)
    duplicate = h.request("POST", h.path("/chat/confirm"), body)
    check(duplicate.status_code == 200 and duplicate.json() == confirmed, "duplicate_confirm_not_idempotent")
    check(h.request("GET", h.path()).json() == confirmed, "duplicate_request_mutated_state")
    check(h.state["conditions"]["hard"]["max_deposit_krw"] == 900_000_000, "idempotent_update_missing")


def mode_honesty(h, case):
    response = h.request("GET", "/api/agent/status")
    check(response.status_code == 200, "agent_status_unavailable")
    status = response.json()
    check(status["default_mode"] == "development" and status["modes"]["development"]["available"], "default_mode_not_development")
    check(status["modes"]["zoro"]["available"] is False and status["modes"]["zoro"]["verified"] is False,
          "unconfigured_zoro_claimed_available")
    h.chat(case["messages"][0])
    before = deepcopy(h.state)
    response = h.request("POST", h.path("/chat"), h.chat_body(case["messages"][0], mode="zoro"))
    check(response.status_code == 503 and "error" in response.json(), "unconfigured_zoro_not_rejected")
    check(h.request("GET", h.path()).json() == before, "zoro_error_fell_back_to_rules")


def budget_followup(h, case):
    h.chat(case["messages"][0])
    check(h.turn()["status"] == "needs_input", "budget_followup_question_missing")
    h.chat(case["messages"][1]); h.pending("update_conditions")
    check(h.state["conditions"]["hard"]["max_deposit_krw"] is None, "followup_budget_auto_applied")
    h.confirm()
    check(h.state["conditions"]["hard"]["max_deposit_krw"] == 900_000_000, "short_followup_not_understood")
    check(h.state["conditions"]["hard"]["min_area_m2"] is None, "followup_invented_area")
    check(h.state["conditions"]["hard"]["step_free_access"] is None, "followup_invented_access")
    check(h.state["search_result"] is not None, "followup_not_searched")


RUNNERS = {function.__name__: function for function in (
    missing_budget, hard_and_preference, unknown_access, elevator_distinction,
    contracted_listing, inspection_recheck, cancel_mutation, partial_conditions,
    no_relaxation, schedule_delay, schedule_apply, schedule_no_slots,
    stale_confirmation, sample_history, mock_live_history, live_failure,
    arbitrary_tool, idempotency, mode_honesty, budget_followup,
)}


def run_case(case):
    """Raise fixed check failures for pytest; return only safe summary values."""
    check(case["runner"] in RUNNERS, "unknown_scenario_runner")
    with isolated_harness() as harness:
        harness.create(case["initial"])
        RUNNERS[case["runner"]](harness, case)
        result = {"id": case["id"], "title": case["title"], "passed": True,
                  "mode": "development", "real_model_called": False,
                  "http_requests": harness.requests, "mock_molit_requests": len(harness.mock_calls)}
    return result


def evaluate():
    results = []
    for case in load_scenarios():
        try:
            results.append(run_case(case))
        except Exception as exc:
            # Do not serialize arbitrary exception messages, tracebacks, URLs or state.
            reason = str(exc) if isinstance(exc, ScenarioFailure) and re.fullmatch(r"[a-z_]{1,80}", str(exc)) else "unexpected_evaluation_error"
            results.append({"id": case["id"], "title": case["title"], "passed": False,
                            "mode": "development", "real_model_called": False, "failure_check": reason})
    passed = sum(result["passed"] for result in results)
    return {"schema_version": "1.0", "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "evaluation_kind": "offline_development_http", "synthetic_fixtures": True,
            "real_network_calls": 0, "real_zoro_evaluated": False,
            "live_molit_authenticated": False,
            "unresolved": ["Actual Zoro model/interface evaluation requires separately supplied configuration and explicit opt-in.",
                           "MockTransport live-path checks do not establish real MOLIT authentication or availability."],
            "total": len(results), "passed": passed, "failed": len(results) - passed, "cases": results}


def write_report(report, filename="agent-development-evaluation.json"):
    check(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.json", filename) is not None,
          "invalid_report_filename")
    directory = ROOT / "reports"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = directory / filename
    temporary = directory / (".agent-evaluation-" + str(uuid4()) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline development-only HTTP evaluation; no real model/API calls.")
    parser.add_argument("--mode", choices=["development"], default="development")
    parser.add_argument("--report-name", default="agent-development-evaluation.json", help="JSON basename inside excluded reports/")
    args = parser.parse_args(argv)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.json", args.report_name) is None:
        parser.error("--report-name must be a simple .json filename")
    report = evaluate()
    write_report(report, args.report_name)
    print(f"Offline development HTTP evaluation: {report['passed']}/{report['total']} passed; {report['failed']} failed.")
    for result in report["cases"]:
        if not result["passed"]:
            print(f"  {result['id']}: {result['failure_check']}")
    print("Actual Zoro evaluation: NOT RUN. Real MOLIT authentication: NOT CHECKED.")
    print("Real model/interface verification remains required; no model is called by this command.")
    print("Safe JSON report written under reports/ (excluded from delivery ZIP).")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
