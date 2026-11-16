"""Offline HTTP integration checks for the stage 5 manual tools.

Each test gets an isolated SQLite database. No test calls MOLIT, a provider, or
Zoro. Passing these checks establishes deterministic tool behavior only.
"""

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, Settings
from app.main import create_app


BASE_CONDITIONS = {
    "hard": {
        "max_deposit_krw": 1_000_000_000,
        "min_area_m2": 75,
        "move_in_by": "2026-11-25",
        "step_free_access": True,
        "elevator": None,
    },
    "preferences": {"noise_level": "low", "neighborhoods": []},
}


@pytest.fixture
def settings(tmp_path):
    return Settings.from_env({"STATE_DB_PATH": str(tmp_path / "test.sqlite3")})


@pytest.fixture
def app(settings):
    application = create_app(settings)

    async def forbidden_fetch(**_kwargs):
        raise AssertionError("manual-tool tests must not call a live API")

    application.state.molit_fetcher = forbidden_fetch
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def session_path(state, suffix=""):
    return f"/api/sessions/{state['session_id']}{suffix}"


def new_session(client, conditions=None):
    body = {} if conditions is None else {"conditions": deepcopy(conditions)}
    response = client.post("/api/sessions", json=body)
    assert response.status_code == 201, response.text
    state = response.json()
    assert state["revision"] == 0
    assert isinstance(state["session_id"], str) and state["session_id"]
    return state


def read_state(client, state):
    response = client.get(session_path(state))
    assert response.status_code == 200, response.text
    return response.json()


def mutate(client, state, suffix, body=None, method="post"):
    payload = {"expected_revision": state["revision"], **(body or {})}
    response = client.request(method, session_path(state, suffix), json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["session_id"] == state["session_id"]
    assert saved["revision"] == state["revision"] + 1
    assert read_state(client, saved) == saved
    return saved


def safe_error(response, statuses=(400, 404, 409, 415, 422)):
    assert response.status_code in statuses, response.text
    payload = response.json()
    assert set(payload) == {"error"}
    error = payload["error"]
    assert set(error) == {"code", "message", "retryable"}
    assert isinstance(error["code"], str) and error["code"]
    assert isinstance(error["message"], str) and error["message"]
    assert isinstance(error["retryable"], bool)
    assert "Traceback" not in response.text
    return error


def row(state, listing_id):
    return next(item for item in state["listings"] if item["id"] == listing_id)


def bucket_ids(state, bucket):
    return {item["listing"]["id"] for item in state["search_result"][bucket]}


def create_task(client, state, listing_id="L001", field="step_free_access"):
    saved = mutate(client, state, "/verification-tasks", {
        "listing_id": listing_id, "field": field,
    })
    task = next(task for task in saved["verification_tasks"]
                if task["listing_id"] == listing_id
                and task["field"] == field and task["status"] == "pending")
    return saved, task


def inspect(client, state, task, value, notes="가상 현장 확인 결과"):
    return mutate(client, state, "/inspections", {
        "task_id": task["id"], "value": value, "notes": notes,
    })


def test_sessions_persist_after_app_recreation_without_mutating_other_sessions(client, settings):
    fixture_paths = [PROJECT_ROOT / "data" / f"{name}.json"
                     for name in ("listings", "inspections", "schedules")]
    original_files = {path: path.read_bytes() for path in fixture_paths}
    public_listings = client.get("/api/listings").json()
    first = new_session(client, BASE_CONDITIONS)
    second = new_session(client, BASE_CONDITIONS)
    assert first["session_id"] != second["session_id"]

    first = mutate(client, first, "/listings/L002/status", {"status": "contracted"}, "patch")
    first = mutate(client, first, "/search")
    assert "L002" in bucket_ids(first, "excluded")
    assert read_state(client, second) == second
    assert row(second, "L002")["status"] == "available"
    assert client.get("/api/listings").json() == public_listings
    assert all(path.read_bytes() == original_files[path] for path in fixture_paths)

    with TestClient(create_app(settings)) as reloaded:
        assert read_state(reloaded, first) == first
        assert read_state(reloaded, second) == second


def test_missing_budget_is_incomplete_and_never_produces_eligible_recommendations(client):
    state = new_session(client)
    assert state["conditions"]["hard"]["max_deposit_krw"] is None
    state = mutate(client, state, "/search")
    result = state["search_result"]
    assert result["status"] == "incomplete"
    assert any(field.endswith("max_deposit_krw") for field in result["missing_fields"])
    assert result["eligible"] == []
    assert state["search_stale"] is False


def test_search_enforces_hard_boundaries_and_preserves_unknown_evidence(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    result = state["search_result"]
    assert result["status"] == "ready" and result["missing_fields"] == []
    assert bucket_ids(state, "eligible") == {"L002", "L003", "L004", "L011", "L014", "L019"}
    assert bucket_ids(state, "needs_verification") == {"L001", "L009", "L013"}
    all_ids = [candidate["listing"]["id"] for bucket in (
        "eligible", "needs_verification", "excluded") for candidate in result[bucket]]
    assert len(all_ids) == len(set(all_ids)) == len(state["listings"]) == 20
    assert {"L005", "L007", "L012", "L016", "L017", "L018"} <= bucket_ids(state, "excluded")
    for candidate in result["needs_verification"]:
        assert candidate["listing"]["step_free_access"] is None
        assert any(reason["field"] == "step_free_access" for reason in candidate["reasons"])
    for bucket in ("eligible", "needs_verification", "excluded"):
        candidates = result[bucket]
        assert candidates == sorted(candidates, key=lambda item: (-item["preference_score"], item["listing"]["id"]))
        for candidate in candidates:
            assert type(candidate["preference_score"]) is int
            assert all(reason["code"] and reason["message"] and reason["field"]
                       for reason in candidate["reasons"])


def test_noise_preference_does_not_become_an_unrequested_hard_constraint(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    candidates = {item["listing"]["id"]: item for item in state["search_result"]["eligible"]}
    assert candidates["L003"]["listing"]["noise_level"] == "high"
    assert candidates["L002"]["preference_score"] > candidates["L003"]["preference_score"]
    assert state["conditions"] == BASE_CONDITIONS


def test_explicit_unknown_accessibility_requirement_is_not_false(client):
    conditions = deepcopy(BASE_CONDITIONS)
    conditions["hard"]["step_free_access"] = None
    state = mutate(client, new_session(client, conditions), "/search")
    assert state["conditions"]["hard"]["step_free_access"] is None
    assert {"L001", "L008", "L009", "L013", "L020"} <= bucket_ids(state, "eligible")
    assert row(state, "L001")["step_free_access"] is None


def test_no_matching_candidates_does_not_relax_conditions(client):
    conditions = deepcopy(BASE_CONDITIONS)
    conditions["hard"].update(max_deposit_krw=600_000_000, min_area_m2=90)
    state = mutate(client, new_session(client, conditions), "/search")
    assert state["search_result"]["status"] == "ready"
    assert state["search_result"]["eligible"] == []
    assert state["search_result"]["needs_verification"] == []
    assert state["conditions"] == conditions


def test_conditions_change_invalidates_search_and_proposals_before_explicit_research(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    state = mutate(client, state, "/schedule/proposals")
    assert state["schedule_proposals"]
    lowered = deepcopy(BASE_CONDITIONS)
    lowered["hard"]["max_deposit_krw"] = 800_000_000
    state = mutate(client, state, "/conditions", {"conditions": lowered}, "patch")
    assert state["search_stale"] is True
    assert state["schedule_proposals"] == []
    assert state["conditions"] == lowered
    state = mutate(client, state, "/search")
    assert state["search_stale"] is False
    assert {"L002", "L004", "L019"} <= bucket_ids(state, "excluded")
    assert all(candidate["listing"]["deposit_krw"] <= 800_000_000
               for bucket in ("eligible", "needs_verification")
               for candidate in state["search_result"][bucket])


def test_creating_and_deduplicating_pending_tasks_is_not_confirmed_evidence(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    listing_before = deepcopy(row(state, "L001"))
    evidence_before = deepcopy(state["inspections"])
    state, task = create_task(client, state)
    state, same_task = create_task(client, state)
    assert task["id"] == same_task["id"]
    assert sum(item["listing_id"] == "L001" and item["field"] == "step_free_access"
               and item["status"] == "pending" for item in state["verification_tasks"]) == 1
    assert task["question"] and task["created_at"]
    assert row(state, "L001") == listing_before
    assert state["inspections"] == evidence_before
    assert "L001" in bucket_ids(state, "needs_verification")


def test_confirmed_false_accessibility_recalculates_search_and_retains_evidence(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    state, task = create_task(client, state)
    evidence_count = len(state["inspections"])
    state = inspect(client, state, task, False, "8계단과 사용 가능한 우회 경로 없음 확인")
    assert row(state, "L001")["step_free_access"] is False
    assert row(state, "L001")["entry_steps"] is None
    assert row(state, "L001")["verified_at"]
    assert "L001" in bucket_ids(state, "excluded")
    assert "L001" not in bucket_ids(state, "needs_verification")
    assert state["search_stale"] is False
    assert next(item for item in state["verification_tasks"] if item["id"] == task["id"])["status"] == "confirmed"
    assert len(state["inspections"]) == evidence_count + 1
    assert state["audit"]


def test_eight_entry_steps_alone_does_not_disprove_an_unknown_alternate_route(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    state, task = create_task(client, state, field="entry_steps")
    state = inspect(client, state, task, 8)
    assert row(state, "L001")["entry_steps"] == 8
    assert row(state, "L001")["step_free_access"] is None
    assert "L001" in bucket_ids(state, "needs_verification")
    assert "L001" not in bucket_ids(state, "excluded")


def test_confirming_one_field_does_not_invent_other_facts(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    state, task = create_task(client, state, listing_id="L009")
    state = inspect(client, state, task, True)
    assert "L009" in bucket_ids(state, "eligible")
    assert row(state, "L009")["step_free_access"] is True
    assert row(state, "L009")["entry_steps"] is None
    assert row(state, "L009")["noise_level"] is None
    state, task = create_task(client, state, listing_id="L013", field="elevator")
    state = inspect(client, state, task, True)
    assert row(state, "L013")["elevator"] is True
    assert row(state, "L013")["step_free_access"] is None
    assert "L013" in bucket_ids(state, "needs_verification")


@pytest.mark.parametrize("value", [None, "unknown", 1, "false"])
def test_unconfirmed_or_malformed_observation_is_atomic_and_remains_pending(client, value):
    state, task = create_task(client, new_session(client, BASE_CONDITIONS))
    response = client.post(session_path(state, "/inspections"), json={
        "expected_revision": state["revision"], "task_id": task["id"], "value": value,
    })
    safe_error(response, (400, 422))
    assert read_state(client, state) == state


def test_completed_task_cannot_be_silently_rewritten_but_new_confirmation_can_be_requested(client):
    state, task = create_task(client, new_session(client, BASE_CONDITIONS))
    state = inspect(client, state, task, True)
    response = client.post(session_path(state, "/inspections"), json={
        "expected_revision": state["revision"], "task_id": task["id"], "value": False,
    })
    safe_error(response)
    assert read_state(client, state) == state
    state, new_task = create_task(client, state)
    assert new_task["id"] != task["id"]
    assert row(state, "L001")["step_free_access"] is True


def test_contracting_a_listing_cancels_pending_tasks_and_recalculates_search(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    state, task = create_task(client, state)
    state = mutate(client, state, "/listings/L001/status", {"status": "contracted"}, "patch")
    assert "L001" in bucket_ids(state, "excluded")
    cancelled = next(item for item in state["verification_tasks"] if item["id"] == task["id"])
    assert cancelled["status"] in {"cancelled", "canceled"}
    for suffix, payload in (
        ("/verification-tasks", {"listing_id": "L001", "field": "noise_level"}),
        ("/inspections", {"task_id": task["id"], "value": True}),
    ):
        response = client.post(session_path(state, suffix), json={
            "expected_revision": state["revision"], **payload,
        })
        safe_error(response)
        assert read_state(client, state) == state


def test_task_ids_are_scoped_to_their_session(client):
    first, task = create_task(client, new_session(client, BASE_CONDITIONS))
    second = new_session(client, BASE_CONDITIONS)
    response = client.post(session_path(second, "/inspections"), json={
        "expected_revision": second["revision"], "task_id": task["id"], "value": True,
    })
    safe_error(response)
    assert read_state(client, first) == first
    assert read_state(client, second) == second


def test_stale_client_gets_409_and_cannot_overwrite_the_newer_session(client):
    stale = new_session(client, BASE_CONDITIONS)
    current = mutate(client, stale, "/search")
    response = client.patch(session_path(stale, "/conditions"), json={
        "expected_revision": stale["revision"], "conditions": {},
    })
    assert safe_error(response, (409,))["code"] == "REVISION_CONFLICT"
    assert read_state(client, current) == current


@pytest.mark.parametrize("revision", [True, False, 0.5, -1, "0", None])
def test_revision_must_be_an_explicit_nonnegative_integer(client, revision):
    state = new_session(client, BASE_CONDITIONS)
    response = client.post(session_path(state, "/search"), json={"expected_revision": revision})
    safe_error(response, (400, 422))
    assert read_state(client, state) == state


def test_missing_revision_cannot_mutate_session(client):
    state = new_session(client, BASE_CONDITIONS)
    safe_error(client.post(session_path(state, "/search"), json={}), (400, 422))
    assert read_state(client, state) == state


@pytest.mark.parametrize("condition_patch", [
    {"max_deposit_krw": True}, {"max_deposit_krw": 900_000_000.5},
    {"max_deposit_krw": "900000000"}, {"max_deposit_krw": -1},
    {"min_area_m2": True}, {"move_in_by": "2026-02-30"},
    {"step_free_access": "false"}, {"elevator": 1},
    {"unknown_secret_field_do_not_echo": "private-input-sentinel"},
])
def test_invalid_conditions_leave_every_persisted_field_unchanged(client, condition_patch):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    conditions = deepcopy(BASE_CONDITIONS)
    conditions["hard"].update(condition_patch)
    response = client.patch(session_path(state, "/conditions"), json={
        "expected_revision": state["revision"], "conditions": conditions,
    })
    safe_error(response, (400, 422))
    assert "private-input-sentinel" not in response.text
    assert "unknown_secret_field_do_not_echo" not in response.text
    assert read_state(client, state) == state


@pytest.mark.parametrize("listing_id,field", [
    ("L999", "step_free_access"), ("L007", "step_free_access"),
    ("L001", "deposit_krw"), ("L001", "private-input-sentinel"),
])
def test_invalid_verification_request_does_not_create_a_task(client, listing_id, field):
    state = new_session(client, BASE_CONDITIONS)
    response = client.post(session_path(state, "/verification-tasks"), json={
        "expected_revision": state["revision"], "listing_id": listing_id, "field": field,
    })
    safe_error(response)
    assert "private-input-sentinel" not in response.text
    assert read_state(client, state) == state


def event_map(events):
    return {event["id"]: event for event in events}


def stamp(value):
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() is not None
    return parsed


def assert_proposal_is_feasible(schedule, proposal):
    """Check returned plans independently of the production scheduling helpers."""
    original = event_map(schedule["events"])
    proposed = event_map(proposal["events"])
    assert proposed.keys() == original.keys()
    for event_id, event in proposed.items():
        before = original[event_id]
        assert event["type"] == before["type"]
        assert event["depends_on"] == before["depends_on"]
        assert stamp(event["end"]) - stamp(event["start"]) == stamp(before["end"]) - stamp(before["start"])
        for parent_id in event["depends_on"]:
            assert stamp(proposed[parent_id]["end"]) <= stamp(event["start"])
        if event["type"] == "entry":
            assert event["start"] == before["start"] and event["end"] == before["end"]
        else:
            assert any(slot["type"] == event["type"]
                       and stamp(slot["start"]) <= stamp(event["start"])
                       and stamp(event["end"]) <= stamp(slot["end"])
                       for slot in schedule["provider_slots"])


def test_delayed_entry_exposes_dependency_conflicts_without_moving_later_events(client):
    state = new_session(client, BASE_CONDITIONS)
    original = event_map(state["schedule"]["events"])
    assert not state["schedule"].get("conflicts", [])
    state = mutate(client, state, "/schedule/entry", {"new_end": "2026-11-25T15:00:00+09:00"}, "patch")
    changed = event_map(state["schedule"]["events"])
    assert changed["ENTRY-01"]["end"] == "2026-11-25T15:00:00+09:00"
    assert stamp(changed["ENTRY-01"]["end"]) - stamp(changed["ENTRY-01"]["start"]) == stamp(original["ENTRY-01"]["end"]) - stamp(original["ENTRY-01"]["start"])
    assert changed["CLEAN-01"] == original["CLEAN-01"]
    assert changed["MOVE-01"] == original["MOVE-01"]
    assert state["schedule"]["conflicts"]
    assert any("ENTRY-01" in conflict["event_ids"] for conflict in state["schedule"]["conflicts"])


def test_proposals_use_listed_fixture_slots_and_apply_only_after_explicit_selection(client):
    state = new_session(client, BASE_CONDITIONS)
    state = mutate(client, state, "/schedule/entry", {"new_end": "2026-11-25T15:00:00+09:00"}, "patch")
    delayed_schedule = deepcopy(state["schedule"])
    state = mutate(client, state, "/schedule/proposals")
    proposals = state["schedule_proposals"]
    assert 1 <= len(proposals) <= 5
    assert len({proposal["id"] for proposal in proposals}) == len(proposals)
    assert state["schedule"] == delayed_schedule
    for proposal in proposals:
        assert proposal["based_on_revision"] == state["revision"]
        assert proposal["summary"]
        assert_proposal_is_feasible(delayed_schedule, proposal)
    chosen = proposals[0]
    state = mutate(client, state, "/schedule/apply", {"proposal_id": chosen["id"]})
    assert state["schedule"]["events"] == chosen["events"]
    assert state["schedule"]["conflicts"] == []
    assert state["schedule_proposals"] == []
    assert state["schedule"]["provider_slots"] == delayed_schedule["provider_slots"]
    assert state["audit"]


def test_stale_or_unknown_schedule_proposal_cannot_be_applied(client):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/schedule/proposals")
    proposal_id = state["schedule_proposals"][0]["id"]
    state = mutate(client, state, "/search")
    for selected_id in (proposal_id, "unknown-proposal"):
        response = client.post(session_path(state, "/schedule/apply"), json={
            "expected_revision": state["revision"], "proposal_id": selected_id,
        })
        safe_error(response)
        assert read_state(client, state) == state


def test_next_day_options_require_explicit_selection_and_do_not_invent_same_day_slots(client):
    state = new_session(client, BASE_CONDITIONS)
    state = mutate(client, state, "/schedule/entry", {"new_end": "2026-11-25T19:00:00+09:00"}, "patch")
    before = deepcopy(state["schedule"])
    state = mutate(client, state, "/schedule/proposals")
    assert state["schedule_proposals"]
    assert state["schedule"] == before
    for proposal in state["schedule_proposals"]:
        assert_proposal_is_feasible(before, proposal)
        assert all(event["start"].startswith("2026-11-26") for event in proposal["events"]
                   if event["type"] in {"cleaning", "moving"})


def test_no_available_provider_slots_returns_no_alternatives(client):
    state = new_session(client, BASE_CONDITIONS)
    state = mutate(client, state, "/schedule/entry", {"new_end": "2026-11-27T09:00:00+09:00"}, "patch")
    before = deepcopy(state["schedule"])
    state = mutate(client, state, "/schedule/proposals")
    assert state["schedule_proposals"] == []
    assert state["schedule"] == before


@pytest.mark.parametrize("timestamp", ["2026-11-25T15:00:00", "2026-02-30T15:00:00+09:00", True])
def test_invalid_entry_time_does_not_partially_change_schedule(client, timestamp):
    state = new_session(client, BASE_CONDITIONS)
    response = client.patch(session_path(state, "/schedule/entry"), json={
        "expected_revision": state["revision"], "new_end": timestamp,
    })
    safe_error(response, (400, 422))
    assert read_state(client, state) == state


def test_json_and_same_origin_are_enforced_for_session_writes(client):
    response = client.post("/api/sessions", content="{}", headers={"Content-Type": "text/plain"})
    safe_error(response, (400, 415, 422))
    state = new_session(client, BASE_CONDITIONS)
    for headers in ({"Origin": "https://foreign.example.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
        response = client.post(session_path(state, "/search"),
                               json={"expected_revision": state["revision"]}, headers=headers)
        safe_error(response, (403,))
        assert read_state(client, state) == state
    response = client.post(session_path(state, "/search"),
                           json={"expected_revision": state["revision"]}, headers={"Origin": "http://testserver"})
    assert response.status_code == 200, response.text


def test_unknown_session_uses_the_standard_safe_404(client):
    response = client.get(f"/api/sessions/{uuid4()}")
    assert safe_error(response, (404,))["code"] == "SESSION_NOT_FOUND"


@pytest.mark.parametrize("kind", ["trade", "rent"])
def test_stage5_preserves_sample_and_live_separation_without_claiming_connections(client, kind):
    state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
    sample = client.get("/api/transactions", params={"kind": kind, "mode": "sample"})
    assert sample.status_code == 200
    assert sample.json()["source"] == "synthetic_sample"
    assert sample.json()["synthetic"] is True
    live = client.get("/api/transactions", params={"kind": kind, "mode": "live"})
    assert safe_error(live, (503,))["code"] == "MISSING_API_KEY"
    assert "synthetic_sample" not in live.text
    health = client.get("/api/health").json()
    assert health["mode"] == "preparation"
    assert health["molit"]["verified"] is False
    assert health["zoro"]["status"] == "not_connected"
    assert health["zoro"]["verified"] is False
    assert read_state(client, state) == state


def test_session_state_and_sqlite_do_not_persist_server_credentials(tmp_path, caplog):
    secrets = ("offline-trade-secret-sentinel", "offline-rent-secret-sentinel", "offline-zoro-secret-sentinel")
    database = tmp_path / "private.sqlite3"
    settings = Settings.from_env({
        "STATE_DB_PATH": str(database), "MOLIT_TRADE_API_KEY": secrets[0],
        "MOLIT_RENT_API_KEY": secrets[1], "ZORO_API_KEY": secrets[2],
        "ZORO_BASE_URL": "https://offline-zoro.invalid/private",
    })
    with TestClient(create_app(settings)) as client:
        state = mutate(client, new_session(client, BASE_CONDITIONS), "/search")
        responses = (client.get(session_path(state)).text, client.get("/api/health").text)
    assert database.is_file()
    files = [path for path in Path(tmp_path).iterdir() if path.is_file()]
    for secret in (*secrets, "https://offline-zoro.invalid/private"):
        assert all(secret not in body for body in responses)
        assert secret not in caplog.text
        assert all(secret.encode() not in path.read_bytes() for path in files)
