"""Executable offline domain cases, independent of any model or real API."""

from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
from uuid import UUID

import pytest

from app.domain import (
    DomainError, apply_inspection, make_verification_task,
    normalize_conditions, search_candidates,
)


@pytest.fixture
def listings():
    path = Path(__file__).resolve().parents[1] / "data" / "listings.json"
    return json.loads(path.read_text(encoding="utf-8"))["listings"]


@pytest.fixture
def conditions():
    return {
        "hard": {
            "max_deposit_krw": 1_000_000_000,
            "min_area_m2": 75,
            "move_in_by": "2026-11-25",
            "step_free_access": True,
        },
        "preferences": {"noise_level": "low", "neighborhoods": ["삼성동"]},
    }


def ids(result, bucket):
    return [candidate["listing"]["id"] for candidate in result[bucket]]


def reasons(result, listing_id):
    return [
        reason
        for bucket in ("eligible", "needs_verification", "excluded")
        for candidate in result[bucket] if candidate["listing"]["id"] == listing_id
        for reason in candidate["reasons"]
    ]


def test_normalization_fills_unknowns_and_keeps_explicit_false():
    result = normalize_conditions({"hard": {"elevator": False}})
    assert result == {
        "hard": {
            "max_deposit_krw": None, "min_area_m2": None, "move_in_by": None,
            "step_free_access": None, "elevator": False,
        },
        "preferences": {"noise_level": None, "neighborhoods": []},
    }
    assert normalize_conditions(result) == result


def test_normalization_copies_nested_preferences_and_deduplicates_names():
    original = {"preferences": {"neighborhoods": [" 삼성동 ", "삼성동", "청담동"]}}
    before = deepcopy(original)
    result = normalize_conditions(original)
    assert result["preferences"]["neighborhoods"] == ["삼성동", "청담동"]
    result["preferences"]["neighborhoods"].append("대치동")
    assert original == before


@pytest.mark.parametrize("payload", [
    None, [], "conditions", {"unexpected": "SECRET_SENTINEL"},
    {"hard": None}, {"hard": []}, {"preferences": None},
    {"hard": {"deposit_krw": 100}},
    {"hard": {"noise_level": "low"}},
    {"preferences": {"max_deposit_krw": 100}},
    {"preferences": {"unknown_policy": "SECRET_SENTINEL"}},
])
def test_normalization_rejects_wrong_shape_and_unknown_fields_without_echo(payload):
    with pytest.raises(DomainError) as caught:
        normalize_conditions(payload)
    assert caught.value.code == "INVALID_CONDITIONS"
    assert caught.value.message == str(caught.value)
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("value", [True, False, 1_000_000_000.0, 10.5, "1000000000", -1, 0, [], {}])
def test_money_must_be_positive_integer_krw(value):
    with pytest.raises(DomainError):
        normalize_conditions({"hard": {"max_deposit_krw": value}})


@pytest.mark.parametrize("value", [True, False, "75", -1, 0, float("nan"), float("inf"), float("-inf")])
def test_area_must_be_finite_positive_number(value):
    with pytest.raises(DomainError):
        normalize_conditions({"hard": {"min_area_m2": value}})


@pytest.mark.parametrize("value", ["2026-02-29", "2026-11-31", "2026-1-02", "20261125", "2026-W48-3", "2026-11-25T00:00:00", "2026-11-25 ", date(2026, 11, 25), True])
def test_dates_must_exist_and_use_exact_yyyy_mm_dd(value):
    with pytest.raises(DomainError):
        normalize_conditions({"hard": {"move_in_by": value}})


def test_valid_leap_day_and_integer_precision_are_preserved():
    large_budget = 9_007_199_254_740_993
    result = normalize_conditions({"hard": {"move_in_by": "2028-02-29", "max_deposit_krw": large_budget}})
    assert result["hard"]["move_in_by"] == "2028-02-29"
    assert result["hard"]["max_deposit_krw"] == large_budget
    assert type(result["hard"]["max_deposit_krw"]) is int


@pytest.mark.parametrize("field,value", [
    ("step_free_access", 1), ("step_free_access", "true"),
    ("elevator", 0), ("elevator", []),
])
def test_boolean_hard_conditions_are_never_coerced(field, value):
    with pytest.raises(DomainError):
        normalize_conditions({"hard": {field: value}})


@pytest.mark.parametrize("preferences", [
    {"noise_level": True}, {"noise_level": "quiet"}, {"noise_level": []},
    {"neighborhoods": None}, {"neighborhoods": "삼성동"},
    {"neighborhoods": [None]}, {"neighborhoods": [""]},
    {"neighborhoods": ["   "]}, {"neighborhoods": ["a" * 101]},
])
def test_preferences_reject_malformed_values(preferences):
    with pytest.raises(DomainError):
        normalize_conditions({"preferences": preferences})


def test_real_fixture_hard_boundaries_unknowns_and_soft_preferences(listings, conditions):
    result = search_candidates(listings, conditions)
    assert result["status"] == "ready" and result["missing_fields"] == []
    assert ids(result, "eligible") == ["L002", "L019", "L003", "L004", "L011", "L014"]
    assert set(ids(result, "needs_verification")) == {"L001", "L009", "L013"}
    assert set(ids(result, "excluded")) == {"L005", "L006", "L007", "L008", "L010", "L012", "L015", "L016", "L017", "L018", "L020"}
    assert any(r["code"] == "PREFERENCE_MISMATCH" for r in reasons(result, "L003"))
    assert any(r["code"] == "BUDGET_EXCEEDED" for r in reasons(result, "L005"))
    assert any(r["code"] == "AREA_TOO_SMALL" for r in reasons(result, "L017"))
    assert any(r["code"] == "MOVE_IN_TOO_LATE" for r in reasons(result, "L018"))
    assert any(r["code"] == "CONTRACTED" for r in reasons(result, "L007"))
    assert all(candidate["reasons"] for bucket in ("eligible", "needs_verification", "excluded") for candidate in result[bucket])
    assert len(set(sum((ids(result, b) for b in ("eligible", "needs_verification", "excluded")), []))) == 20


def test_missing_budget_never_claims_eligible_recommendations(listings):
    result = search_candidates(listings, {"hard": {"step_free_access": True}})
    assert result["status"] == "incomplete"
    assert result["missing_fields"] == ["hard.max_deposit_krw"]
    assert result["eligible"] == []
    assert "L002" in ids(result, "needs_verification")
    assert "L007" in ids(result, "excluded")
    assert all(any(r["code"] == "BUDGET_REQUIRED" for r in row["reasons"]) for row in result["needs_verification"])


def test_no_match_does_not_relax_budget_or_access(listings, conditions):
    conditions["hard"]["max_deposit_krw"] = 600_000_000
    before = deepcopy(conditions)
    result = search_candidates(listings, conditions)
    assert result["status"] == "ready"
    assert result["eligible"] == result["needs_verification"] == []
    assert len(result["excluded"]) == 20
    assert conditions == before


def test_unknown_soft_preference_does_not_block_hard_match(listings, conditions):
    row = next(row for row in listings if row["id"] == "L009")
    row["step_free_access"] = True
    result = search_candidates([row], conditions)
    assert ids(result, "eligible") == ["L009"]
    assert result["needs_verification"] == []
    assert any(r["code"] == "PREFERENCE_UNKNOWN" and r["field"] == "noise_level" for r in reasons(result, "L009"))


def test_elevator_and_access_are_separate_requirements(listings, conditions):
    assert "L011" in ids(search_candidates(listings, conditions), "eligible")
    conditions["hard"]["elevator"] = True
    result = search_candidates(listings, conditions)
    assert "L011" in ids(result, "excluded")
    assert {r["field"] for r in reasons(result, "L013") if r["code"] == "HARD_EVIDENCE_UNKNOWN"} == {"elevator", "step_free_access"}


def test_explicit_false_is_a_hard_value_and_unknown_stays_pending(listings):
    result = search_candidates(listings, {"hard": {"max_deposit_krw": 1_000_000_000, "elevator": False}})
    assert set(ids(result, "eligible")) == {"L006", "L011"}
    assert "L013" in ids(result, "needs_verification")
    assert "L002" in ids(result, "excluded")


def test_search_is_deterministic_and_results_have_no_shared_mutable_state(listings, conditions):
    before_listings, before_conditions = deepcopy(listings), deepcopy(conditions)
    first = search_candidates(listings, conditions)
    assert first == search_candidates(list(reversed(listings)), conditions)
    first["eligible"][0]["listing"]["step_free_access"] = False
    first["eligible"][0]["reasons"].clear()
    assert listings == before_listings and conditions == before_conditions
    assert search_candidates(listings, conditions)["eligible"][0]["listing"]["step_free_access"] is True


def test_empty_catalog_is_valid_but_does_not_fill_missing_budget():
    assert search_candidates([], {}) == {"status": "incomplete", "missing_fields": ["hard.max_deposit_krw"], "eligible": [], "needs_verification": [], "excluded": []}


@pytest.mark.parametrize("field,value", [
    ("deposit_krw", True), ("deposit_krw", 900000000.0),
    ("area_m2", True), ("area_m2", float("nan")),
    ("available_from", "2026-02-30"), ("available_from", "20261125"),
    ("elevator", 1), ("step_free_access", "unknown"),
    ("entry_steps", True), ("entry_steps", -1),
    ("noise_level", []), ("status", []), ("status", "unknown"),
    ("district_code", "11110"), ("verified_at", "2026-11-12T00:00:00"),
])
def test_search_rejects_invalid_listing_types(listings, field, value):
    row = deepcopy(listings[0])
    row[field] = value
    with pytest.raises(DomainError) as caught:
        search_candidates([row], {"hard": {"max_deposit_krw": 1_000_000_000}})
    assert caught.value.code == "INVALID_LISTING"


def test_search_rejects_duplicate_listing_ids_and_nonlist_input(listings):
    with pytest.raises(DomainError):
        search_candidates([listings[0], deepcopy(listings[0])], {})
    with pytest.raises(DomainError):
        search_candidates({"listings": listings}, {})


def test_task_uuid_pending_dedup_and_copy_isolation(listings):
    listing = listings[0]
    before = deepcopy(listing)
    existing = []
    task = make_verification_task(listing, "step_free_access", existing)
    assert UUID(task["id"]).version == 4
    assert task["status"] == "pending" and task["listing_id"] == listing["id"]
    assert "대체" in task["question"]
    assert datetime.fromisoformat(task["created_at"]).utcoffset() is not None
    assert existing == [] and listing == before
    duplicate = make_verification_task(listing, "step_free_access", [task])
    assert duplicate == task and duplicate is not task
    duplicate["question"] = "changed"
    assert task["question"] != "changed"
    second = make_verification_task(listing, "noise_level", [task])
    third = make_verification_task(listings[1], "step_free_access", [task, second])
    assert len({task["id"], second["id"], third["id"]}) == 3


@pytest.mark.parametrize("status", ["confirmed", "cancelled"])
def test_completed_or_cancelled_task_allows_fresh_verification(listings, status):
    old = make_verification_task(listings[0], "step_free_access", [])
    old["status"] = status
    new = make_verification_task(listings[0], "step_free_access", [old])
    assert new["id"] != old["id"] and new["status"] == "pending"
    assert old["status"] == status


@pytest.mark.parametrize("field", ["deposit_krw", "status", "verified_at", "unknown", None, []])
def test_unknown_verification_fields_are_rejected(listings, field):
    with pytest.raises(DomainError) as task_error:
        make_verification_task(listings[0], field, [])
    with pytest.raises(DomainError) as inspection_error:
        apply_inspection(listings[0], field=field, value=True)
    assert task_error.value.code == inspection_error.value.code == "INVALID_VERIFICATION_FIELD"


def test_contracted_listing_rejects_tasks_and_observations(listings):
    row = next(row for row in listings if row["id"] == "L007")
    with pytest.raises(DomainError) as task_error:
        make_verification_task(row, "step_free_access", [])
    with pytest.raises(DomainError) as inspection_error:
        apply_inspection(row, field="step_free_access", value=True)
    assert task_error.value.code == inspection_error.value.code == "LISTING_CONTRACTED"


@pytest.mark.parametrize("tasks", [None, {}, [None], [{}], [{"id": "SECRET_SENTINEL"}]])
def test_malformed_task_history_raises_safe_domain_error(listings, tasks):
    with pytest.raises(DomainError) as caught:
        make_verification_task(listings[0], "step_free_access", tasks)
    assert caught.value.code == "INVALID_VERIFICATION_TASKS"
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("entry_steps", 8), ("entry_steps", 0),
    ("step_free_access", True), ("step_free_access", False),
    ("elevator", True), ("elevator", False), ("noise_level", "medium"),
])
def test_confirmed_observation_changes_only_one_field_and_timestamp(listings, field, value):
    original = deepcopy(listings[0])
    original["elevator"] = None
    before = deepcopy(original)
    updated = apply_inspection(original, field=field, value=value, notes="현장에서 확인한 관찰 메모")
    assert original == before
    assert updated is not original and updated[field] == value
    assert datetime.fromisoformat(updated["verified_at"]).utcoffset() is not None
    for name in original.keys() - {field, "verified_at"}:
        assert updated[name] == original[name]


def test_stairs_do_not_disprove_alternate_route_and_elevator_does_not_prove_access(listings, conditions):
    original = listings[0]
    stairs = apply_inspection(original, field="entry_steps", value=8)
    assert stairs["step_free_access"] is None
    assert ids(search_candidates([stairs], conditions), "needs_verification") == ["L001"]
    elevator = apply_inspection(stairs, field="elevator", value=True)
    assert elevator["step_free_access"] is None
    assert ids(search_candidates([elevator], conditions), "needs_verification") == ["L001"]
    alternative_route = apply_inspection(elevator, field="step_free_access", value=True)
    assert alternative_route["entry_steps"] == 8
    assert ids(search_candidates([alternative_route], conditions), "eligible") == ["L001"]
    assert alternative_route["noise_level"] is None


def test_zero_steps_also_does_not_prove_complete_step_free_route(listings, conditions):
    row = apply_inspection(listings[0], field="entry_steps", value=0)
    assert row["step_free_access"] is None
    assert ids(search_candidates([row], conditions), "needs_verification") == ["L001"]


@pytest.mark.parametrize("field,value", [
    ("entry_steps", None), ("entry_steps", True), ("entry_steps", -1),
    ("entry_steps", 8.0), ("entry_steps", "8"),
    ("step_free_access", None), ("step_free_access", 1), ("step_free_access", "true"),
    ("elevator", None), ("elevator", 0),
    ("noise_level", None), ("noise_level", "quiet"), ("noise_level", []),
])
def test_unconfirmed_and_malformed_observation_is_never_written(listings, field, value):
    before = deepcopy(listings[0])
    with pytest.raises(DomainError) as caught:
        apply_inspection(listings[0], field=field, value=value)
    assert caught.value.code == "INVALID_INSPECTION_VALUE"
    assert listings[0] == before


@pytest.mark.parametrize("notes", [None, [], "x" * 2001])
def test_observation_notes_are_bounded_and_typed(listings, notes):
    with pytest.raises(DomainError) as caught:
        apply_inspection(listings[0], field="elevator", value=True, notes=notes)
    assert caught.value.code == "INVALID_INSPECTION_NOTES"


def test_explicit_confirmed_failure_moves_pending_candidate_to_excluded(listings, conditions):
    row = listings[0]
    assert ids(search_candidates([row], conditions), "needs_verification") == ["L001"]
    updated = apply_inspection(row, field="step_free_access", value=False)
    assert ids(search_candidates([updated], conditions), "excluded") == ["L001"]
    assert row["step_free_access"] is None
