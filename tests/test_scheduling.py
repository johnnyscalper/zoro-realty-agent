"""Schedule feasibility tests exercise supplied availability, never bookings."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from app.scheduling import (
    ScheduleError,
    apply_proposal,
    detect_conflicts,
    propose_schedules,
    shift_entry,
    validate_schedule,
)


@pytest.fixture
def schedule():
    path = Path(__file__).resolve().parents[1] / "data" / "schedules.json"
    return json.loads(path.read_text(encoding="utf-8"))


def times(event):
    return datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])


def by_id(events):
    return {event["id"]: event for event in events}


def assert_feasible_against_supplied_windows(original, proposal):
    """Independently verify invariants without using the implementation checker."""
    before, after = by_id(original["events"]), by_id(proposal["events"])
    assert before.keys() == after.keys()
    for event_id, event in after.items():
        start, end = times(event)
        old_start, old_end = times(before[event_id])
        assert end - start == old_end - old_start
        assert event["depends_on"] == before[event_id]["depends_on"]
        assert event["type"] == before[event_id]["type"]
        assert event["status"] == before[event_id]["status"]
        assert all(times(after[dep])[1] <= start for dep in event["depends_on"])
        if event["type"] == "entry":
            assert (start, end) == (old_start, old_end)
        else:
            assert any(
                slot["type"] == event["type"]
                and times(slot)[0] <= start <= end <= times(slot)[1]
                for slot in original["provider_slots"]
            )


def test_baseline_is_valid_and_deeply_detached(schedule):
    original = deepcopy(schedule)
    result = validate_schedule(schedule)
    assert result == original
    assert detect_conflicts(result) == []
    result["events"][1]["depends_on"].clear()
    result["provider_slots"][0]["start"] = "changed"
    assert schedule == original


def test_adjacent_dependencies_and_exact_provider_boundaries_are_allowed(schedule):
    # Cleaning starts exactly when the 30-minute entry event has ended.
    assert schedule["events"][0]["end"] == schedule["events"][1]["start"]
    schedule["events"][2]["start"] = "2026-11-25T12:00:00+09:00"
    schedule["events"][2]["end"] = "2026-11-25T15:00:00+09:00"
    schedule["provider_slots"][3]["start"] = schedule["events"][2]["start"]
    schedule["provider_slots"][3]["end"] = schedule["events"][2]["end"]
    assert detect_conflicts(schedule) == []


def test_ordering_uses_absolute_time_across_offsets(schedule):
    schedule["events"][1]["start"] = "2026-11-25T00:00:00Z"
    schedule["events"][1]["end"] = "2026-11-25T03:00:00Z"
    schedule["events"][2]["start"] = "2026-11-25T04:00:00+00:00"
    schedule["events"][2]["end"] = "2026-11-25T07:00:00+00:00"
    normalized = validate_schedule(schedule)
    assert normalized["events"][1]["start"].endswith("+00:00")
    assert detect_conflicts(normalized) == []


def test_shift_preserves_entry_duration_and_does_not_move_downstream(schedule):
    original = deepcopy(schedule)
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    entry = shifted["events"][0]
    assert entry["start"] == "2026-11-25T15:00:00+09:00"
    assert times(entry)[1] - times(entry)[0] == timedelta(minutes=30)
    assert shifted["events"][1:] == original["events"][1:]
    assert shifted["provider_slots"] == original["provider_slots"]
    assert {tuple(item["event_ids"]) for item in shifted["conflicts"]} == {
        ("ENTRY-01", "CLEAN-01"), ("ENTRY-01", "MOVE-01")
    }
    assert all(item["code"] == "DEPENDENCY_CONFLICT" for item in shifted["conflicts"])
    assert schedule == original


def test_shift_entry_crosses_midnight_without_losing_duration(schedule):
    shifted = shift_entry(schedule, "2026-11-26T00:15+09:00")
    assert shifted["events"][0]["start"] == "2026-11-25T23:45:00+09:00"
    assert shifted["events"][0]["end"] == "2026-11-26T00:15:00+09:00"
    proposals = propose_schedules(shifted)
    assert len(proposals) == 1
    assert by_id(proposals[0]["events"])["CLEAN-01"]["start"] == "2026-11-26T09:00:00+09:00"
    assert_feasible_against_supplied_windows(shifted, proposals[0])


def test_point_entry_is_valid_and_stays_a_point(schedule):
    schedule["events"][0]["start"] = schedule["events"][0]["end"]
    assert detect_conflicts(schedule) == []
    shifted = shift_entry(schedule, "2026-11-26T09:00:00+09:00")
    assert shifted["events"][0]["start"] == shifted["events"][0]["end"]


def test_cleaning_cannot_begin_during_entry(schedule):
    shifted = shift_entry(schedule, "2026-11-25T09:01:00+09:00")
    assert shifted["events"][0]["start"] < schedule["events"][1]["start"]
    assert shifted["conflicts"] == [{
        "code": "DEPENDENCY_CONFLICT",
        "message": "입주 가능 시각보다 후속 일정이 먼저 시작됩니다.",
        "event_ids": ["ENTRY-01", "CLEAN-01"],
    }]


@pytest.mark.parametrize("start,end", [
    ("2026-11-25T08:59:59+09:00", "2026-11-25T11:59:59+09:00"),
    ("2026-11-25T09:00:01+09:00", "2026-11-25T12:00:01+09:00"),
    ("2026-11-24T09:00:00+09:00", "2026-11-24T12:00:00+09:00"),
])
def test_provider_availability_requires_full_containment_and_date(schedule, start, end):
    schedule["events"][1].update(start=start, end=end)
    conflicts = detect_conflicts(schedule)
    assert any(item["code"] == "PROVIDER_UNAVAILABLE" and item["event_ids"] == ["CLEAN-01"] for item in conflicts)


def test_missing_provider_availability_is_a_conflict_and_no_alternative(schedule):
    schedule["provider_slots"] = []
    conflicts = detect_conflicts(schedule)
    assert [(item["code"], item["event_ids"]) for item in conflicts] == [
        ("PROVIDER_UNAVAILABLE", ["CLEAN-01"]),
        ("PROVIDER_UNAVAILABLE", ["MOVE-01"]),
    ]
    assert propose_schedules(schedule) == []


def test_partial_windows_cannot_be_stitched_into_invented_availability(schedule):
    schedule["provider_slots"] = [
        {"id": "C1", "type": "cleaning", "start": "2026-11-25T09:00+09:00", "end": "2026-11-25T10:00+09:00"},
        {"id": "C2", "type": "cleaning", "start": "2026-11-25T10:00+09:00", "end": "2026-11-25T12:00+09:00"},
        schedule["provider_slots"][3],
    ]
    assert any(item["code"] == "PROVIDER_UNAVAILABLE" for item in detect_conflicts(schedule))
    assert propose_schedules(schedule) == []


def test_delay_enumerates_all_three_genuine_combinations(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    original = deepcopy(shifted)
    proposals = propose_schedules(shifted)
    assert len(proposals) == 3
    assert [
        (by_id(plan["events"])["CLEAN-01"]["start"], by_id(plan["events"])["MOVE-01"]["start"])
        for plan in proposals
    ] == [
        ("2026-11-25T15:30:00+09:00", "2026-11-25T19:00:00+09:00"),
        ("2026-11-25T15:30:00+09:00", "2026-11-26T13:00:00+09:00"),
        ("2026-11-26T09:00:00+09:00", "2026-11-26T13:00:00+09:00"),
    ]
    assert len({plan["id"] for plan in proposals}) == 3
    assert proposals == propose_schedules(shifted)
    for plan in proposals:
        assert "예약은 수행하지 않습니다" in plan["summary"]
        assert_feasible_against_supplied_windows(shifted, plan)
        assert apply_proposal(shifted, plan)["conflicts"] == []
    assert shifted == original


def test_plan_fits_within_wide_window_after_dependency_ends(schedule):
    shifted = shift_entry(schedule, "2026-11-25T10:00:00+09:00")
    shifted["provider_slots"] = [shifted["provider_slots"][0], shifted["provider_slots"][3]]
    shifted["provider_slots"][0]["end"] = "2026-11-25T14:00:00+09:00"
    proposals = propose_schedules(shifted)
    assert len(proposals) == 1
    clean = by_id(proposals[0]["events"])["CLEAN-01"]
    assert clean["start"] == "2026-11-25T10:00:00+09:00"
    assert clean["end"] == "2026-11-25T13:00:00+09:00"
    assert_feasible_against_supplied_windows(shifted, proposals[0])


def test_no_plan_when_every_listed_slot_is_before_access_time(schedule):
    shifted = shift_entry(schedule, "2026-11-27T09:00:00+09:00")
    assert propose_schedules(shifted) == []


def test_no_plan_when_cleaning_duration_does_not_fit_any_window(schedule):
    schedule["events"][1]["end"] = "2026-11-25T12:00:01+09:00"
    assert propose_schedules(schedule) == []


def test_no_plan_when_only_moving_slot_is_before_cleaning_finishes(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    shifted["provider_slots"] = [shifted["provider_slots"][1], shifted["provider_slots"][3]]
    assert propose_schedules(shifted) == []


def test_proposals_are_capped_and_unchanged_current_schedule_is_excluded(schedule):
    proposals = propose_schedules(schedule, limit=500)
    assert len(proposals) == 5
    for plan in proposals:
        assert plan["events"] != schedule["events"]
        assert_feasible_against_supplied_windows(schedule, plan)
    assert propose_schedules(schedule, limit=1) == proposals[:1]


def test_duplicate_windows_do_not_create_duplicate_plans(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    duplicate = deepcopy(shifted["provider_slots"][1])
    duplicate["id"] = "SLOT-CLEAN-PM-OTHER"
    shifted["provider_slots"].append(duplicate)
    plans = propose_schedules(shifted)
    assert len(plans) == 3
    assert len({plan["id"] for plan in plans}) == 3


def test_event_list_need_not_be_in_dependency_order(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    shifted["events"].reverse()
    proposals = propose_schedules(shifted)
    assert len(proposals) == 3
    for plan in proposals:
        assert [event["id"] for event in plan["events"]] == [event["id"] for event in shifted["events"]]
        assert_feasible_against_supplied_windows(shifted, plan)


@pytest.mark.parametrize("limit", [True, False, 0, -1, 1.5, "2", None])
def test_invalid_proposal_limits_are_rejected(schedule, limit):
    with pytest.raises(ScheduleError) as exc:
        propose_schedules(schedule, limit=limit)
    assert exc.value.code == "INVALID_PROPOSAL_LIMIT"


def test_apply_preserves_inputs_and_returns_detached_state(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    plan = propose_schedules(shifted)[0]
    plan["based_on_revision"] = 3  # caller-owned metadata must not affect feasibility
    originals = deepcopy((shifted, plan))
    applied = apply_proposal(shifted, plan)
    assert detect_conflicts(applied) == []
    assert applied["provider_slots"] == shifted["provider_slots"]
    assert applied["notice"] == schedule["notice"]
    assert (shifted, plan) == originals
    applied["events"][1]["depends_on"].clear()
    assert (shifted, plan) == originals


def test_apply_rechecks_current_provider_availability(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    plan = propose_schedules(shifted)[0]
    shifted["provider_slots"] = [slot for slot in shifted["provider_slots"] if slot["id"] != "SLOT-MOVE-LATE"]
    with pytest.raises(ScheduleError) as exc:
        apply_proposal(shifted, plan)
    assert exc.value.code == "PROPOSAL_CONFLICT"


@pytest.mark.parametrize("alteration", ["duration", "dependencies", "type", "status", "entry", "missing_event"])
def test_apply_rejects_changes_to_schedule_invariants(schedule, alteration):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    plan = propose_schedules(shifted)[0]
    events = by_id(plan["events"])
    if alteration == "duration":
        events["CLEAN-01"]["end"] = "2026-11-25T17:30:00+09:00"
    elif alteration == "dependencies":
        events["MOVE-01"]["depends_on"] = ["CLEAN-01"]
    elif alteration == "type":
        events["CLEAN-01"]["type"] = "moving"
    elif alteration == "status":
        events["MOVE-01"]["status"] = "booked"
    elif alteration == "entry":
        events["ENTRY-01"]["start"] = "2026-11-25T14:00:00+09:00"
        events["ENTRY-01"]["end"] = "2026-11-25T14:30:00+09:00"
    elif alteration == "missing_event":
        plan["events"] = [event for event in plan["events"] if event["id"] != "MOVE-01"]
    with pytest.raises(ScheduleError) as exc:
        apply_proposal(shifted, plan)
    assert exc.value.code == "INVALID_PROPOSAL"


def test_apply_rejects_dependency_conflict_even_inside_available_windows(schedule):
    shifted = shift_entry(schedule, "2026-11-25T15:30:00+09:00")
    plan = propose_schedules(shifted)[0]
    moving = by_id(plan["events"])["MOVE-01"]
    moving.update(start="2026-11-25T13:00:00+09:00", end="2026-11-25T16:00:00+09:00")
    with pytest.raises(ScheduleError) as exc:
        apply_proposal(shifted, plan)
    assert exc.value.code == "PROPOSAL_CONFLICT"


@pytest.mark.parametrize("bad_time", [
    "2026-11-25T09:00:00", "2026-11-25", "2026-11-25 09:00:00+09:00",
    "2026-02-30T09:00:00+09:00", "2026-11-25T24:00:00+09:00",
    "2026-11-25T09:00:00+24:00", "2026-11-25T09:00:00+09:60",
    "2026-11-25T09:00:00+09:00:30", "0001-01-01T00:00:00+09:00",
    None, True, 123, "secret-in-invalid-time",
])
def test_invalid_times_are_safe_errors_and_do_not_mutate_input(schedule, bad_time):
    original = deepcopy(schedule)
    with pytest.raises(ScheduleError) as exc:
        shift_entry(schedule, bad_time)
    assert exc.value.code == "INVALID_SCHEDULE_TIME"
    assert "secret-in-invalid-time" not in str(exc.value)
    assert schedule == original


@pytest.mark.parametrize("target", ["service", "entry", "slot"])
def test_negative_durations_are_rejected(schedule, target):
    row = schedule["events"][1 if target == "service" else 0] if target != "slot" else schedule["provider_slots"][0]
    row["end"] = "2026-11-24T08:00:00+09:00"
    with pytest.raises(ScheduleError) as exc:
        validate_schedule(schedule)
    assert exc.value.code == "INVALID_SCHEDULE_DURATION"


@pytest.mark.parametrize("target", ["service", "slot"])
def test_zero_duration_service_or_slot_is_rejected(schedule, target):
    row = schedule["events"][1] if target == "service" else schedule["provider_slots"][0]
    row["end"] = row["start"]
    with pytest.raises(ScheduleError) as exc:
        validate_schedule(schedule)
    assert exc.value.code == "INVALID_SCHEDULE_DURATION"


@pytest.mark.parametrize("cycle", ["self", "two", "three"])
def test_dependency_cycles_are_rejected(schedule, cycle):
    if cycle == "self":
        schedule["events"][0]["depends_on"] = ["ENTRY-01"]
    elif cycle == "two":
        schedule["events"][0]["depends_on"] = ["CLEAN-01"]
    else:
        schedule["events"][0]["depends_on"] = ["MOVE-01"]
    with pytest.raises(ScheduleError) as exc:
        validate_schedule(schedule)
    assert exc.value.code == "SCHEDULE_CYCLE"


@pytest.mark.parametrize("dependency", ["NO-SUCH-EVENT", "SLOT-CLEAN-AM"])
def test_dependencies_must_reference_events(schedule, dependency):
    schedule["events"][1]["depends_on"] = [dependency]
    with pytest.raises(ScheduleError) as exc:
        validate_schedule(schedule)
    assert exc.value.code == "UNKNOWN_DEPENDENCY"


@pytest.mark.parametrize("target", ["event", "slot", "cross_kind"])
def test_duplicate_identifiers_are_rejected(schedule, target):
    if target == "event":
        schedule["events"][1]["id"] = schedule["events"][0]["id"]
    elif target == "slot":
        schedule["provider_slots"][1]["id"] = schedule["provider_slots"][0]["id"]
    else:
        schedule["provider_slots"][0]["id"] = schedule["events"][0]["id"]
    with pytest.raises(ScheduleError) as exc:
        validate_schedule(schedule)
    assert exc.value.code == "DUPLICATE_SCHEDULE_ID"


@pytest.mark.parametrize("identifier", ["", True, 123, "a b", "x\n", "x" * 81])
def test_invalid_identifiers_are_rejected(schedule, identifier):
    schedule["events"][0]["id"] = identifier
    with pytest.raises(ScheduleError):
        validate_schedule(schedule)


@pytest.mark.parametrize("payload", [None, [], {}, {"events": {}, "provider_slots": []}, {"events": [], "provider_slots": None}])
def test_invalid_schedule_containers_are_rejected(payload):
    with pytest.raises(ScheduleError):
        validate_schedule(payload)


def test_empty_schedule_is_valid_but_has_no_entry_or_alternatives():
    empty = {"events": [], "provider_slots": []}
    assert validate_schedule(empty) == empty
    assert detect_conflicts(empty) == []
    assert propose_schedules(empty) == []
    with pytest.raises(ScheduleError) as exc:
        shift_entry(empty, "2026-11-25T09:00+09:00")
    assert exc.value.code == "ENTRY_NOT_UNIQUE"


def test_ambiguous_entry_cannot_be_shifted(schedule):
    extra = deepcopy(schedule["events"][0])
    extra["id"] = "ENTRY-02"
    schedule["events"].append(extra)
    with pytest.raises(ScheduleError) as exc:
        shift_entry(schedule, "2026-11-25T09:00+09:00")
    assert exc.value.code == "ENTRY_NOT_UNIQUE"


@pytest.mark.parametrize("location", ["schedule", "event", "slot"])
def test_unknown_fields_are_not_silently_treated_as_availability(schedule, location):
    target = schedule if location == "schedule" else schedule["events" if location == "event" else "provider_slots"][0]
    target["available"] = False
    with pytest.raises(ScheduleError):
        validate_schedule(schedule)


def test_shift_rejects_date_underflow_without_raw_exception(schedule):
    with pytest.raises(ScheduleError) as exc:
        shift_entry(schedule, "0001-01-01T00:00:00+00:00")
    assert exc.value.code == "INVALID_SCHEDULE_TIME"
