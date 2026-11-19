"""Adversarial stage-6 HTTP checks using only temporary state and fake planners.

No test loads the user's .env or contacts a model/MOLIT server. Injected decisions
exercise the same validation, review, transaction and replay boundaries as model
decisions. Passing this suite is not a real-Zoro compatibility or quality result.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import importlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.molit import MolitError
from app.sample import sample_transactions


# app.main has a convenience module-level app. Guard that import as well as the
# explicit test application so collecting this module never reads real settings.
with TemporaryDirectory(prefix="zoro-security-import-") as _import_directory:
    with patch.object(Settings, "from_env", return_value=Settings(
        state_db_path=Path(_import_directory) / "import.sqlite3",
    )):
        create_app = importlib.import_module("app.main").create_app


CONDITIONS = {
    "hard": {"max_deposit_krw": 1_000_000_000, "min_area_m2": 75,
             "move_in_by": "2026-11-25", "step_free_access": True},
    "preferences": {"noise_level": "low", "neighborhoods": ["역삼동"]},
}
BUSINESS_FIELDS = (
    "conditions", "listings", "verification_tasks", "inspections",
    "search_result", "search_stale", "schedule", "schedule_proposals",
)
FAKE_SECRETS = (
    "security-test-trade-key-never-real", "security-test-rent-key-never-real",
    "security-test-zoro-key-never-real", "https://offline-zoro.invalid/private",
)


def decision(tool="finish", arguments=None, reason="오프라인 경계 검사"):
    return {"tool": tool, "arguments": arguments or {}, "reason": reason}


class Decisions:
    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, message, state, trace):
        self.calls.append({"message": message, "state": deepcopy(state), "trace": deepcopy(trace)})
        item = self.items.pop(0) if self.items else decision()
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)

    async def decide(self, message, state, trace):
        return self(message, state, trace)


@pytest.fixture
def app(tmp_path, monkeypatch):
    async def no_network(*_args, **_kwargs):
        raise AssertionError("Security review tests must remain offline")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    # An explicit mapping bypasses dotenv; all apparent credentials are sentinels.
    settings = Settings.from_env({
        "STATE_DB_PATH": str(tmp_path / "security.sqlite3"),
        "MOLIT_TRADE_API_KEY": FAKE_SECRETS[0], "MOLIT_RENT_API_KEY": FAKE_SECRETS[1],
        "ZORO_API_KEY": FAKE_SECRETS[2], "ZORO_BASE_URL": FAKE_SECRETS[3],
        "ZORO_MODEL": "offline-security-fixture", 
    })
    application = create_app(settings)
    application.state.molit_fetcher = no_network
    application.state.agent_service.planner = Decisions()
    application.state.agent_service.provider = Decisions()
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as connected:
        yield connected


def new_session(client):
    response = client.post("/api/sessions", json={"conditions": deepcopy(CONDITIONS)})
    assert response.status_code == 201, response.text
    return response.json()


def path(state, suffix=""):
    return f"/api/sessions/{state['session_id']}{suffix}"


def read(client, state):
    response = client.get(path(state))
    assert response.status_code == 200, response.text
    return response.json()


def business(state):
    return {key: deepcopy(state[key]) for key in BUSINESS_FIELDS}


def chat_body(state, **overrides):
    return {"expected_revision": state["revision"], "message": "보안 경계 검사",
            "mode": "development", "data_mode": "sample", "request_id": str(uuid4()),
            **overrides}


def chat(client, state, **overrides):
    response = client.post(path(state, "/chat"), json=chat_body(state, **overrides))
    assert response.status_code == 200, response.text
    saved = response.json()
    assert read(client, saved) == saved
    return saved


def confirm_body(state, **overrides):
    return {"expected_revision": state["revision"],
            "pending_id": state["agent"]["pending"]["id"], "approve": True,
            "request_id": str(uuid4()), **overrides}


def confirm(client, state, **overrides):
    response = client.post(path(state, "/chat/confirm"), json=confirm_body(state, **overrides))
    assert response.status_code == 200, response.text
    saved = response.json()
    assert read(client, saved) == saved
    return saved


def error(response, expected=(409, 422)):
    assert response.status_code in expected, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "retryable"}
    assert "Traceback" not in response.text
    return body["error"]


def listing(state, listing_id="L001"):
    return next(item for item in state["listings"] if item["id"] == listing_id)


@pytest.mark.parametrize("tool,arguments", [
    ("update_conditions", {"conditions": {"hard": {"max_deposit_krw": 900_000_000}}}),
    ("record_inspection", {"listing_id": "L001", "field": "step_free_access", "value": True}),
    ("set_listing_status", {"listing_id": "L001", "status": "contracted"}),
    ("set_entry_time", {"new_end": "2026-11-25T15:00:00+09:00"}),
])
def test_mutations_are_reviewed_without_business_changes(app, client, tool, arguments):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(decision(tool, arguments))
    pending = chat(client, before)
    assert business(pending) == business(before)
    assert pending["agent"]["pending"]["tool"] == tool
    assert pending["agent"]["pending"]["created_revision"] == pending["revision"]
    assert pending["agent"]["turns"][-1]["status"] == "awaiting_confirmation"
    after = confirm(client, pending, approve=False)
    assert business(after) == business(before)
    assert after["agent"]["pending"] is None


def test_confirmation_uses_stored_arguments_and_merges_partial_conditions(app, client):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(decision("update_conditions", {
        "conditions": {"hard": {"max_deposit_krw": 900_000_000}},
    }))
    pending = chat(client, before)
    injected = confirm_body(pending, arguments={"conditions": {"hard": {}}})
    error(client.post(path(pending, "/chat/confirm"), json=injected), (422,))
    assert read(client, pending) == pending
    saved = confirm(client, pending)
    expected = deepcopy(before["conditions"])
    expected["hard"]["max_deposit_krw"] = 900_000_000
    assert saved["conditions"] == expected
    assert saved["agent"]["pending"] is None


def test_forged_and_cross_session_pending_ids_cannot_confirm(app, client):
    first, second = new_session(client), new_session(client)
    app.state.agent_service.planner = Decisions(decision("set_listing_status", {
        "listing_id": "L001", "status": "contracted",
    }))
    first = chat(client, first)
    for target, pending_id in ((first, str(uuid4())), (second, first["agent"]["pending"]["id"])):
        payload = {"expected_revision": target["revision"], "pending_id": pending_id,
                   "approve": True, "request_id": str(uuid4())}
        error(client.post(path(target, "/chat/confirm"), json=payload), (404, 409, 422))
        assert read(client, target) == target
    assert listing(first)["status"] != "contracted"


@pytest.mark.parametrize("suffix,method,extra", [
    ("/search", "post", {}),
    ("/listings/L002/status", "patch", {"status": "contracted"}),
    ("/schedule/proposals", "post", {}),
])
def test_manual_revision_change_invalidates_pending_confirmation(app, client, suffix, method, extra):
    app.state.agent_service.planner = Decisions(decision("set_listing_status", {
        "listing_id": "L001", "status": "contracted",
    }))
    pending = chat(client, new_session(client))
    original_pending_id = pending["agent"]["pending"]["id"]
    changed = client.request(method, path(pending, suffix), json={
        "expected_revision": pending["revision"], **extra,
    })
    assert changed.status_code == 200, changed.text
    current = changed.json()
    assert current["agent"]["pending"] is None
    for revision in (pending["revision"], current["revision"]):
        error(client.post(path(current, "/chat/confirm"), json={
            "expected_revision": revision, "pending_id": original_pending_id,
            "approve": True, "request_id": str(uuid4()),
        }), (409,))
        assert read(client, current) == current
    assert listing(current)["status"] != "contracted"


@pytest.mark.parametrize("bad", [
    decision("exec_command", {"cmd": "private-input-sentinel"}),
    decision("https://private-input-sentinel.invalid", {}),
    decision("search_candidates", {"api_key": "private-input-sentinel"}),
    decision("update_conditions", {"conditions": {"hard": {"max_deposit_krw": True}}}),
    decision("record_inspection", {"listing_id": "L001", "field": "step_free_access", "value": 1}),
    decision("record_inspection", {"listing_id": "L001", "field": "deposit_krw", "value": 1}),
    decision("lookup_transactions", {"kind": "trade", "deal_ymd": "202610", "lawd_cd": "11110"}),
    decision("lookup_transactions", {"kind": "trade", "deal_ymd": "202610", "min_area_m2": True}),
    {"tool": "finish", "arguments": {}, "reason": "ok", "system": "private-input-sentinel"},
    [decision()],
])
def test_untrusted_decisions_fail_closed_without_persisting_raw_input(app, client, bad):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(bad)
    saved = chat(client, before)
    assert saved["agent"]["turns"][-1]["status"] == "error"
    assert saved["agent"]["pending"] is None
    assert business(saved) == business(before)
    assert "private-input-sentinel" not in json.dumps(saved)


def test_later_invalid_decision_rolls_back_all_earlier_tool_changes(app, client):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(
        decision("search_candidates"),
        decision("request_verification", {"listing_id": "L001", "field": "step_free_access"}),
        decision("unapproved-arbitrary-tool"),
    )
    saved = chat(client, before)
    assert saved["agent"]["turns"][-1]["status"] == "error"
    assert business(saved) == business(before)
    assert saved["revision"] == before["revision"] + 1
    assert saved["audit"][:len(before["audit"])] == before["audit"]
    assert all(entry["action"] == "agent_turn" for entry in saved["audit"][len(before["audit"]):])
    assert all(entry["status"] in {"executed", "pending_confirmation", "rolled_back", "error"}
               for entry in saved["agent"]["turns"][-1]["trace"])
    assert all(entry["status"] == "rolled_back" for entry in saved["agent"]["turns"][-1]["trace"]
               if entry["tool"] in {"search_candidates", "request_verification"})


def test_failure_after_approved_mutation_does_not_partially_commit(app, client):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(
        decision("set_listing_status", {"listing_id": "L001", "status": "contracted"}),
        RuntimeError("raw-provider-error-private-input-sentinel"),
    )
    pending = chat(client, before)
    saved = confirm(client, pending)
    assert saved["agent"]["turns"][-1]["status"] == "error"
    assert business(saved) == business(before)
    assert "private-input-sentinel" not in json.dumps(saved)


def test_request_replay_after_later_turn_is_idempotent_and_bound_to_payload(app, client):
    before = new_session(client)
    planner = Decisions(decision("search_candidates"), decision())
    app.state.agent_service.planner = planner
    original = chat_body(before)
    first = client.post(path(before, "/chat"), json=original)
    assert first.status_code == 200, first.text
    first = first.json()
    later = chat(client, first, message="후속 대화")
    count = len(planner.calls)
    replay = client.post(path(before, "/chat"), json=original)
    assert replay.status_code == 200, replay.text
    assert replay.json() == later
    assert len(planner.calls) == count
    for altered in ({"message": "다른 명령"}, {"mode": "zoro"}, {"data_mode": "live"}):
        error(client.post(path(before, "/chat"), json={**original, **altered}), (409,))
        assert read(client, later) == later
        assert len(planner.calls) == count


def test_confirm_replay_cannot_reapply_or_change_cancel_to_approve(app, client):
    app.state.agent_service.planner = Decisions(decision("record_inspection", {
        "listing_id": "L001", "field": "step_free_access", "value": True,
    }))
    before = new_session(client)
    pending = chat(client, before)
    payload = confirm_body(pending, approve=False)
    cancelled_response = client.post(path(pending, "/chat/confirm"), json=payload)
    assert cancelled_response.status_code == 200, cancelled_response.text
    cancelled = cancelled_response.json()
    replay = client.post(path(pending, "/chat/confirm"), json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == cancelled
    error(client.post(path(pending, "/chat/confirm"), json={**payload, "approve": True}), (409,))
    assert read(client, cancelled) == cancelled
    assert business(cancelled) == business(before)


def test_request_id_cannot_cross_from_chat_into_confirmation(app, client):
    app.state.agent_service.planner = Decisions(decision("set_listing_status", {
        "listing_id": "L001", "status": "contracted",
    }))
    request_id = str(uuid4())
    pending = chat(client, new_session(client), request_id=request_id)
    error(client.post(path(pending, "/chat/confirm"), json=confirm_body(
        pending, request_id=request_id,
    )), (409,))
    assert read(client, pending) == pending


def test_final_request_slot_resolves_review_without_leaving_an_unconfirmable_change(app, client):
    before = new_session(client)

    def fill_old_ledger(state):
        state["agent"] = {"requests": [
            {"request_id": str(uuid4()), "fingerprint": f"old-request-{index}", "revision": 0}
            for index in range(198)
        ]}
        return state

    seeded = app.state.session_store.mutate(before["session_id"], before["revision"], fill_old_ledger)
    app.state.agent_service.planner = Decisions(
        decision("set_listing_status", {"listing_id": "L001", "status": "contracted"}),
        decision("set_listing_status", {"listing_id": "L002", "status": "contracted"}),
    )
    pending = chat(client, seeded)
    assert pending["agent"]["pending"]
    final = confirm(client, pending)
    assert len(final["agent"]["requests"]) == 200
    assert final["agent"]["pending"] is None
    assert listing(final, "L001")["status"] == "contracted"
    assert listing(final, "L002")["status"] != "contracted"
    error(client.post(path(final, "/chat"), json=chat_body(final)), (409,))
    assert read(client, final) == final


@pytest.mark.parametrize("overrides", [
    {"expected_revision": True}, {"expected_revision": "0"},
    {"request_id": "not-a-uuid"}, {"message": "x" * 2001},
    {"mode": "automatic"}, {"data_mode": "fallback"},
    {"api_key": "private-input-sentinel"},
    {"base_url": "https://private-input-sentinel.invalid"},
])
def test_chat_boundary_rejects_unsafe_shapes_without_echo(app, client, overrides):
    before = new_session(client)
    response = client.post(path(before, "/chat"), json=chat_body(before, **overrides))
    error(response, (422,))
    assert "private-input-sentinel" not in response.text
    assert read(client, before) == before


@pytest.mark.parametrize("secret", [*FAKE_SECRETS, "a1" * 32, "sk-" + "X" * 30])
def test_accidentally_pasted_credentials_are_rejected_before_persistence(app, client, secret):
    before = new_session(client)
    response = client.post(path(before, "/chat"), json=chat_body(before, message=f"키를 확인해줘 {secret}"))
    error(response, (422,))
    assert secret not in response.text
    assert read(client, before) == before
    assert app.state.agent_service.planner.calls == []
    assert app.state.agent_service.provider.calls == []


@pytest.mark.parametrize("approve", [1, "true", None])
def test_confirmation_requires_literal_boolean(app, client, approve):
    app.state.agent_service.planner = Decisions(decision("set_listing_status", {
        "listing_id": "L001", "status": "contracted",
    }))
    pending = chat(client, new_session(client))
    error(client.post(path(pending, "/chat/confirm"), json=confirm_body(pending, approve=approve)), (422,))
    assert read(client, pending) == pending


def test_zoro_error_never_uses_development_fallback_or_echoes_secrets(app, client, tmp_path, caplog):
    before = new_session(client)
    planner = Decisions(decision("search_candidates"))
    provider = Decisions(RuntimeError(" ".join(FAKE_SECRETS)))
    app.state.agent_service.planner = planner
    app.state.agent_service.provider = provider
    saved = chat(client, before, mode="zoro")
    assert len(provider.calls) == 1
    assert planner.calls == []
    assert saved["agent"]["turns"][-1]["status"] == "error"
    assert saved["agent"]["turns"][-1]["zoro_used"] is False
    assert business(saved) == business(before)
    public = json.dumps(saved) + client.get("/api/agent/status").text + caplog.text
    for sentinel in FAKE_SECRETS:
        assert sentinel not in public
        assert all(sentinel.encode() not in item.read_bytes()
                   for item in tmp_path.iterdir() if item.is_file())


def test_live_market_failure_is_not_replaced_by_sample_data(app, client):
    calls = []

    async def failed_fetch(**kwargs):
        calls.append(kwargs)
        raise MolitError("MOLIT_AUTH_ERROR", "오프라인 인증 오류 검사")

    app.state.molit_fetcher = failed_fetch
    app.state.agent_service.planner = Decisions(decision("lookup_transactions", {
        "kind": "rent", "deal_ymd": "202610",
    }))
    before = new_session(client)
    saved = chat(client, before, data_mode="live")
    assert len(calls) == 1
    assert saved["agent"]["turns"][-1]["status"] == "error"
    assert business(saved) == business(before)
    turn = json.dumps(saved["agent"]["turns"][-1])
    assert '"source": "sample"' not in turn
    assert "synthetic_sample" not in turn


def test_confirmation_resumes_original_provider_message_and_data_mode(app, client):
    fetches = []

    async def offline_fetch(**kwargs):
        fetches.append(kwargs)
        query = {key: kwargs[key] for key in ("kind", "lawd_cd", "deal_ymd", "page_no", "num_of_rows")}
        # Deliberately synthetic mock of a normalized live response, never a
        # claim that live access succeeded in this test environment.
        result = sample_transactions(**query)
        result.update(source="molit", synthetic=False)
        return result

    app.state.molit_fetcher = offline_fetch
    provider = Decisions(
        decision("set_listing_status", {"listing_id": "L001", "status": "contracted"}),
        decision("lookup_transactions", {"kind": "rent", "deal_ymd": "202610"}),
        decision(),
    )
    development = Decisions()
    app.state.agent_service.provider = provider
    app.state.agent_service.planner = development
    pending = chat(client, new_session(client), message="원래 확인할 요청", mode="zoro", data_mode="live")
    saved = confirm(client, pending)
    turn = saved["agent"]["turns"][-1]
    assert turn["mode"] == "zoro" and turn["data_mode"] == "live"
    assert turn["zoro_used"] is True and turn["status"] == "completed"
    assert len(provider.calls) == 3 and development.calls == []
    assert all(call["message"] == "원래 확인할 요청" for call in provider.calls)
    assert len(fetches) == 1
    assert listing(saved)["status"] == "contracted"
    observations = [entry["observation"] for entry in turn["trace"] if entry["tool"] == "lookup_transactions"]
    assert len(observations) == 1 and observations[0]["source"] == "molit"


def test_model_finish_reason_cannot_claim_unperformed_booking(app, client):
    app.state.agent_service.provider = Decisions(decision(
        reason="FAKE-BOOKING-COMPLETED-AND-MONEY-PAID",
    ))
    saved = chat(client, new_session(client), mode="zoro")
    assert "FAKE-BOOKING-COMPLETED-AND-MONEY-PAID" not in saved["agent"]["turns"][-1]["reply"]
    assert "FAKE-BOOKING-COMPLETED-AND-MONEY-PAID" not in saved["agent"]["messages"][-1]["text"]


def test_planner_context_mutation_cannot_bypass_validated_tools(app, client):
    before = new_session(client)

    def mutating_planner(message, state, trace):
        state["conditions"]["hard"]["max_deposit_krw"] = 999_000_000_000
        state["listings"][0]["step_free_access"] = True
        state["schedule"]["events"].clear()
        state["agent"]["pending"] = {"id": "forged-context-pending"}
        trace.append({"tool": "forged-context-action"})
        return decision()

    app.state.agent_service.planner = mutating_planner
    saved = chat(client, before)
    assert business(saved) == business(before)
    assert saved["agent"]["pending"] is None
    assert "forged-context" not in json.dumps(saved)


def test_chat_proposals_survive_review_revision_and_apply_only_after_confirm(app, client):
    before = new_session(client)
    changed_response = client.patch(path(before, "/schedule/entry"), json={
        "expected_revision": before["revision"], "new_end": "2026-11-25T15:00:00+09:00",
    })
    assert changed_response.status_code == 200, changed_response.text
    changed = changed_response.json()
    app.state.agent_service.planner = Decisions(decision("propose_schedule"), decision())
    proposed = chat(client, changed)
    assert proposed["schedule_proposals"]
    assert all(p["based_on_revision"] == proposed["revision"] for p in proposed["schedule_proposals"])
    selected = deepcopy(proposed["schedule_proposals"][0])
    app.state.agent_service.planner = Decisions(decision("apply_schedule", {"proposal_id": selected["id"]}))
    pending = chat(client, proposed)
    assert pending["schedule"] == changed["schedule"]
    assert pending["agent"]["pending"]["tool"] == "apply_schedule"
    saved = confirm(client, pending)
    assert saved["agent"]["turns"][-1]["status"] == "completed"
    assert saved["schedule"]["events"] == selected["events"]
    assert saved["schedule"]["conflicts"] == []
    assert saved["schedule_proposals"] == []


@pytest.mark.parametrize("extra", [
    {"listing_id": "L001", "field": "step_free_access", "value": False, "notes": "표현이 달라진 메모"},
    {"listing_id": "L001", "field": "step_free_access", "value": True},
    {"listing_id": "L001", "field": "elevator", "value": False},
    {"listing_id": "L009", "field": "step_free_access", "value": False},
])
def test_one_report_records_one_fact_and_returns_actual_candidate_bucket(app, client, extra):
    before = new_session(client)
    search = client.post(path(before, "/search"), json={"expected_revision": before["revision"]})
    assert search.status_code == 200
    before = search.json()
    observation = {"listing_id": "L001", "field": "step_free_access", "value": False}
    app.state.agent_service.provider = Decisions(decision("record_inspection", observation),
                                                decision("record_inspection", extra))
    pending = chat(client, before, mode="zoro", message="L001의 무단차 접근 불가를 기록하고 후보도 다시 검토해줘.")
    assert business(pending) == business(before)
    saved = confirm(client, pending)
    assert saved["agent"]["pending"] is None
    assert len(saved["inspections"]) == len(before["inspections"]) + 1
    assert saved["inspections"][-1]["observed_value"] is False
    assert listing(saved)["step_free_access"] is False
    assert any(row["listing"]["id"] == "L001" for row in saved["search_result"]["excluded"])
    turn = saved["agent"]["turns"][-1]
    assert turn["status"] == "completed" and turn["zoro_used"]
    assert [(row["tool"], row["status"]) for row in turn["trace"]] == [("record_inspection", "executed")]
    assert turn["trace"][0]["observation"]["candidate_bucket"] == "excluded"
    assert "L001" in turn["reply"] and "제외" in turn["reply"]


@pytest.mark.parametrize("listing_id,field,value,message", [
    ("L001", "step_free_access", False, "현장에서 L001의 무계단 접근이 불가능한 것으로 확인했어. 이 확인 결과를 반영해줘."),
    ("L009", "elevator", False, "L009에 엘리베이터 없음. 기록해줘."),
    ("L004", "noise_level", "low", "L004 현장 소음은 낮음으로 확인했어요. 저장해 주세요."),
])
def test_single_approved_report_returns_persisted_classification_without_more_inference(app, client, listing_id, field, value, message):
    before = new_session(client)
    result = client.post(path(before, "/search"), json={"expected_revision": before["revision"]})
    assert result.status_code == 200, result.text
    before = result.json()
    fact = {"listing_id": listing_id, "field": field, "value": value}
    provider = Decisions(decision("record_inspection", fact), RuntimeError("unexpected post-confirmation inference"))
    app.state.agent_service.provider = provider
    pending = chat(client, before, mode="zoro", message=message)
    assert pending["agent"]["pending"]["arguments"] == fact and business(pending) == business(before)
    assert pending["agent"]["turns"][-1]["zoro_used"]
    saved = confirm(client, pending)
    turn = saved["agent"]["turns"][-1]
    assert turn["status"] == "completed" and not turn["zoro_used"] and len(provider.calls) == 1
    assert saved["agent"]["pending"] is None and not saved["search_stale"]
    bucket = turn["trace"][0]["observation"]["candidate_bucket"]
    assert [entry["tool"] for entry in turn["trace"]] == ["record_inspection"]
    assert listing(saved, listing_id)[field] == value
    assert any(row["listing"]["id"] == listing_id for row in saved["search_result"][bucket])
    assert len(saved["inspections"]) == len(before["inspections"]) + 1
    assert listing_id in turn["reply"]


@pytest.mark.parametrize("has_previous_search", [True, False])
def test_repeated_recheck_reuses_one_successful_search_after_inspection(app, client, has_previous_search):
    before = new_session(client)
    if has_previous_search:
        result = client.post(path(before, "/search"), json={"expected_revision": before["revision"]})
        assert result.status_code == 200
        before = result.json()
    fact = {"listing_id": "L001", "field": "step_free_access", "value": False}
    app.state.agent_service.provider = Decisions(decision("record_inspection", fact),
                                                decision("search_candidates"), decision("search_candidates"))
    pending = chat(client, before, mode="zoro", message="L001 무단차 접근 불가를 기록하고 후보 명단도 다시 검색해줘.")
    saved = confirm(client, pending)
    turn = saved["agent"]["turns"][-1]
    assert turn["status"] == "completed" and turn["zoro_used"]
    assert [(item["tool"], item["status"]) for item in turn["trace"]] == [
        ("record_inspection", "executed"), ("search_candidates", "executed")]
    assert len(saved["inspections"]) == len(before["inspections"]) + 1
    actions = [item["action"] for item in saved["audit"][len(before["audit"]):]]
    assert actions.count("inspection_confirmed") == actions.count("candidates_searched") == 1
    assert any(row["listing"]["id"] == "L001" for row in saved["search_result"]["excluded"])
    assert saved["agent"]["pending"] is None and "후보" in turn["reply"]


def test_compound_recheck_can_continue_to_requested_transaction_lookup(app, client):
    before = new_session(client)
    result = client.post(path(before, "/search"), json={"expected_revision": before["revision"]})
    assert result.status_code == 200
    before = result.json()
    fact = {"listing_id": "L001", "field": "step_free_access", "value": False}
    app.state.agent_service.provider = Decisions(
        decision("record_inspection", fact), decision("search_candidates"),
        decision("lookup_transactions", {"kind": "rent", "deal_ymd": "202610"}), decision())
    review = chat(client, before, mode="zoro", message="L001의 무단차 접근 불가를 반영해줘. 후보 검색 후 2026년 10월 전월세 실거래도 알려줘.")
    saved = confirm(client, review)
    turn = saved["agent"]["turns"][-1]
    assert turn["status"] == "completed" and turn["zoro_used"]
    assert [item["tool"] for item in turn["trace"]] == ["record_inspection", "search_candidates", "lookup_transactions", "finish"]
    assert turn["trace"][2]["observation"]["source"] == "sample"


def test_incomplete_search_after_inspection_cannot_satisfy_duplicate(app, client):
    result = client.post("/api/sessions", json={"conditions": {"hard": {}, "preferences": {}}})
    assert result.status_code == 201, result.text
    before = result.json()
    fact = {"listing_id": "L001", "field": "elevator", "value": False}
    app.state.agent_service.provider = Decisions(decision("record_inspection", fact),
                                                decision("search_candidates"), decision("search_candidates"))
    saved = confirm(client, chat(client, before, mode="zoro", message="L001의 엘리베이터 없음. 기록하고 후보를 확인해줘."))
    turn = saved["agent"]["turns"][-1]
    assert turn["status"] == "error" and turn["trace"][-1]["observation"]["code"] == "REPEATED_ACTION"
    assert business(saved) == business(before)


def test_distinct_reported_facts_have_separate_reviews_and_fresh_corrections_are_allowed(app, client):
    before = new_session(client)
    access = {"listing_id": "L001", "field": "step_free_access", "value": False}
    lift = {"listing_id": "L001", "field": "elevator", "value": True}
    app.state.agent_service.provider = Decisions(*(decision("record_inspection", args) for args in (access, lift, access)))
    first = chat(client, before, mode="zoro", message="L001 현장은 무단차 접근이 불가능하고 엘리베이터는 있었어. 둘 다 반영해줘.")
    second = confirm(client, first)
    assert second["agent"]["pending"]["arguments"] == lift
    assert len(second["inspections"]) == len(before["inspections"]) + 1
    saved = confirm(client, second)
    assert saved["agent"]["pending"] is None
    assert [row["field"] for row in saved["inspections"][-2:]] == ["step_free_access", "elevator"]
    assert saved["search_result"] is None and "다시 확인" not in saved["agent"]["turns"][-1]["reply"]
    app.state.agent_service.provider = Decisions(decision("record_inspection", {**access, "value": True}))
    correction = chat(client, saved, mode="zoro", message="L001을 다시 방문했어. 무단차 접근이 가능해. 정정해줘.")
    assert correction["agent"]["pending"]["arguments"]["value"] is True
    corrected = confirm(client, correction)
    assert listing(corrected)["step_free_access"] is True


def test_show_new_options_then_let_user_choose_a_different_one(app, client):
    before = new_session(client)
    entry_end = "2026-11-25T15:00:00+09:00"

    class EagerProvider:
        async def decide(self, message, state, trace):
            if not trace:
                return decision("set_entry_time", {"new_end": entry_end})
            if trace[-1]["tool"] == "set_entry_time":
                return decision("propose_schedule")
            return decision("apply_schedule", {"proposal_id": state["schedule_proposals"][0]["id"]})

    app.state.agent_service.provider = EagerProvider()
    review = chat(client, before, mode="zoro", message="입주가 오후 3시로 지연됐어. 입주 시각을 바꾸고 이사 일정 대안을 보여줘.")
    assert business(review) == business(before)
    offered = confirm(client, review)
    assert offered["agent"]["pending"] is None and offered["schedule"]["conflicts"]
    turn = offered["agent"]["turns"][-1]
    assert turn["status"] == "completed"
    assert [e["tool"] for e in turn["trace"]] == ["set_entry_time", "propose_schedule"]
    assert "선택" in turn["reply"] and str(len(offered["schedule_proposals"])) in turn["reply"]
    assert [e for e in offered["schedule"]["events"] if e["type"] != "entry"] == [
        e for e in before["schedule"]["events"] if e["type"] != "entry"]
    selection = offered["schedule_proposals"][1]
    apply = decision("apply_schedule", {"proposal_id": selection["id"]})
    app.state.agent_service.provider = Decisions(apply, apply)
    request = chat(client, offered, mode="zoro", message="두 번째 일정안을 적용해줘.")
    assert request["agent"]["pending"]["arguments"]["proposal_id"] == selection["id"]
    assert request["schedule"] == offered["schedule"]
    result = confirm(client, request)
    assert result["agent"]["pending"] is None
    assert result["schedule"]["events"] == selection["events"] and result["schedule"]["conflicts"] == []


def test_multi_step_turn_has_one_commit_and_consistent_audit_revisions(app, client):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(
        decision("search_candidates"),
        decision("request_verification", {"listing_id": "L001", "field": "step_free_access"}),
        decision("request_verification", {"listing_id": "L009", "field": "step_free_access"}),
        decision(),
    )
    saved = chat(client, before)
    assert saved["revision"] == before["revision"] + 1
    assert len(saved["verification_tasks"]) == 2
    new_audit = saved["audit"][len(before["audit"]):]
    assert [entry["action"] for entry in new_audit if entry["action"] != "agent_turn"] == [
        "candidates_searched", "verification_requested", "verification_requested",
    ]
    assert all(entry["revision"] == saved["revision"] for entry in new_audit)


def test_public_proposal_observations_use_the_committed_revision(app, client):
    before = new_session(client)
    app.state.agent_service.planner = Decisions(
        decision("search_candidates"), decision("propose_schedule"), decision(),
    )
    saved = chat(client, before)
    proposals = saved["schedule_proposals"]
    assert proposals
    trace = saved["agent"]["turns"][-1]["trace"]
    observed = next(entry["observation"]["proposals"] for entry in trace
                    if entry["tool"] == "propose_schedule")
    assert observed == proposals
    assert all(proposal["based_on_revision"] == saved["revision"] for proposal in observed)


def test_concurrent_manual_change_wins_without_locking_or_partial_chat_commit(app, client):
    before = new_session(client)
    entered, release = Event(), Event()

    async def waiting_planner(message, state, trace):
        if not trace:
            entered.set()
            await asyncio.to_thread(release.wait, 5)
            return decision("search_candidates")
        return decision()

    app.state.agent_service.planner = waiting_planner
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(client.post, path(before, "/chat"), json=chat_body(before))
        try:
            assert entered.wait(3), "planner was not reached"
            manual = client.patch(path(before, "/listings/L002/status"), json={
                "expected_revision": before["revision"], "status": "contracted",
            })
            assert manual.status_code == 200, manual.text
            current = manual.json()
        finally:
            release.set()
        error(task.result(timeout=5), (409,))
    assert read(client, current) == current
    assert current["search_result"] is None
    assert listing(current, "L002")["status"] == "contracted"


def test_concurrent_duplicate_request_commits_at_most_once(app, client):
    before = new_session(client)
    both_started, release = Event(), Event()
    invocation_count = 0

    async def racing_planner(message, state, trace):
        nonlocal invocation_count
        if not trace:
            invocation_count += 1
            if invocation_count == 2:
                both_started.set()
            await asyncio.to_thread(release.wait, 5)
            return decision("search_candidates")
        return decision()

    app.state.agent_service.planner = racing_planner
    payload = chat_body(before)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, path(before, "/chat"), json=payload) for _ in range(2)]
        try:
            assert both_started.wait(3), "concurrent requests did not reach their isolated planners"
        finally:
            release.set()
        responses = [future.result(timeout=5) for future in futures]
    assert all(response.status_code in (200, 409) for response in responses)
    assert any(response.status_code == 200 for response in responses)
    saved = read(client, before)
    assert saved["revision"] == before["revision"] + 1
    assert len(saved["agent"]["turns"]) == 1
    assert len(saved["agent"]["messages"]) == 2
    assert len([entry for entry in saved["audit"] if entry["action"] == "candidates_searched"]) == 1


def test_cross_site_agent_requests_cannot_mutate_state(app, client):
    before = new_session(client)
    for headers in ({"Origin": "https://foreign.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
        response = client.post(path(before, "/chat"), json=chat_body(before), headers=headers)
        error(response, (403,))
        assert read(client, before) == before
