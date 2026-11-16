"""Validate the synthetic evaluation inputs; these are not Zoro inference tests."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest


DATA = Path(__file__).resolve().parents[1] / "data"


def read(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


LISTINGS = read("listings.json")["listings"]
LISTING_BY_ID = {row["id"]: row for row in LISTINGS}
INSPECTIONS = read("inspections.json")["inspections"]
SCHEDULE = read("schedules.json")
SCENARIOS = read("scenarios.json")["scenarios"]


@pytest.mark.parametrize("filename", ["listings.json", "inspections.json", "schedules.json", "scenarios.json"])
def test_every_catalog_is_explicitly_synthetic(filename):
    catalog = read(filename)
    assert catalog["synthetic"] is True
    assert catalog["notice"]
    assert any(marker in catalog["notice"] for marker in ["가상", "시연", "평가 계획"])


def test_catalog_identity_and_expected_size():
    assert len(LISTINGS) == len(LISTING_BY_ID) == 20
    assert {row["id"] for row in LISTINGS} == {f"L{i:03d}" for i in range(1, 21)}
    assert len(SCENARIOS) == 20
    assert {row["id"] for row in SCENARIOS} == {f"S{i:02d}" for i in range(1, 21)}
    assert len({row["id"] for row in INSPECTIONS}) == len(INSPECTIONS)


@pytest.mark.parametrize("row", LISTINGS, ids=lambda row: row["id"])
def test_listing_types_units_and_evidence_are_consistent(row):
    required = {
        "id", "name", "district_code", "neighborhood", "deposit_krw", "area_m2",
        "floor", "elevator", "step_free_access", "entry_steps", "noise_level",
        "available_from", "status", "source_type", "notes", "verified_at",
    }
    assert required <= row.keys()
    assert row["name"].startswith("[가상]")
    assert row["source_type"] == "synthetic"
    assert row["district_code"] == "11680"
    assert type(row["deposit_krw"]) is int and row["deposit_krw"] > 0
    assert row["deposit_krw"] % 10_000 == 0  # display and filtering use integer KRW
    assert isinstance(row["area_m2"], (int, float)) and row["area_m2"] > 0
    assert type(row["floor"]) is int and row["floor"] > 0
    assert row["status"] in {"available", "contracted", "verification_required"}
    assert row["noise_level"] in {None, "low", "medium", "high"}
    assert type(row["elevator"]) in {bool, type(None)}
    assert type(row["step_free_access"]) in {bool, type(None)}
    date.fromisoformat(row["available_from"])
    if row["entry_steps"] is not None:
        assert type(row["entry_steps"]) is int and row["entry_steps"] >= 0
    if row["step_free_access"] is True:
        assert row["entry_steps"] == 0
    if row["step_free_access"] is None:
        assert row["entry_steps"] is None
    if row["verified_at"] is not None:
        assert datetime.fromisoformat(row["verified_at"]).utcoffset() == timedelta(hours=9)


@pytest.mark.parametrize("inspection", INSPECTIONS, ids=lambda row: row["id"])
def test_inspections_reference_existing_listing_and_preserve_unknown(inspection):
    row = LISTING_BY_ID[inspection["listing_id"]]
    assert inspection["field"] in row
    assert inspection["status"] in {"confirmed", "unverified"}
    if inspection["status"] == "unverified":
        assert inspection["observed_value"] is None
        assert inspection["observed_at"] is None
        assert row[inspection["field"]] is None
    else:
        assert inspection["observed_value"] is not None
        assert row[inspection["field"]] == inspection["observed_value"]
        assert datetime.fromisoformat(inspection["observed_at"]).utcoffset() == timedelta(hours=9)


def test_data_contains_distinct_accessibility_cases():
    # These distinctions prevent demo fixtures from teaching a false elevator/access equivalence.
    assert any(row["elevator"] is True and row["step_free_access"] is None for row in LISTINGS)
    assert any(row["elevator"] is True and row["step_free_access"] is False for row in LISTINGS)
    assert any(row["elevator"] is False and row["step_free_access"] is True and row["floor"] == 1 for row in LISTINGS)
    assert any(row["elevator"] is None for row in LISTINGS)


def test_numeric_boundary_cases_are_actually_on_opposite_sides():
    # Validate benchmark premises independently; the future agent still has to solve them.
    assert LISTING_BY_ID["L004"]["deposit_krw"] == 1_000_000_000
    assert LISTING_BY_ID["L005"]["deposit_krw"] == 1_000_100_000
    assert LISTING_BY_ID["L014"]["area_m2"] == 75
    assert LISTING_BY_ID["L017"]["area_m2"] == 74.99
    assert LISTING_BY_ID["L014"]["available_from"] == "2026-11-25"
    assert LISTING_BY_ID["L018"]["available_from"] == "2026-11-26"


def test_default_story_has_confirmed_alternatives_and_exclusions():
    eligible = [
        row for row in LISTINGS
        if row["status"] == "available"
        and row["deposit_krw"] <= 1_000_000_000
        and row["area_m2"] >= 75
        and row["available_from"] <= "2026-11-25"
        and row["step_free_access"] is True
    ]
    assert {"L002", "L004", "L019"} <= {row["id"] for row in eligible}
    assert not {"L001", "L005", "L007", "L008", "L012", "L017", "L018", "L020"} & {row["id"] for row in eligible}
    assert LISTING_BY_ID["L007"]["status"] == LISTING_BY_ID["L016"]["status"] == "contracted"
    assert all(row["deposit_krw"] > 600_000_000 for row in LISTINGS)  # deliberate no-match scenario


def test_schedule_dates_timezone_ids_and_dependencies():
    events = SCHEDULE["events"]
    by_id = {item["id"]: item for item in events}
    assert len(by_id) == len(events)
    assert {event["type"] for event in events} == {"entry", "cleaning", "moving"}
    assert SCHEDULE["date"] == "2026-11-25"
    for event in events:
        start, end = (datetime.fromisoformat(event[key]) for key in ["start", "end"])
        assert start < end
        assert start.utcoffset() == end.utcoffset() == timedelta(hours=9)
        assert start.date().isoformat() == SCHEDULE["date"]
        assert event["status"] == "planned"
        for dependency in event["depends_on"]:
            assert dependency != event["id"] and dependency in by_id
            assert datetime.fromisoformat(by_id[dependency]["end"]) <= start
    visited, active = set(), set()

    def visit(event_id):
        assert event_id not in active, "dependency cycle"
        if event_id in visited:
            return
        active.add(event_id)
        for dependency in by_id[event_id]["depends_on"]:
            visit(dependency)
        active.remove(event_id)
        visited.add(event_id)

    for event_id in by_id:
        visit(event_id)


def test_provider_slots_offer_feasible_delayed_entry_plan():
    slots = SCHEDULE["provider_slots"]
    assert len({slot["id"] for slot in slots}) == len(slots)
    for slot in slots:
        assert slot["type"] in {"cleaning", "moving"}
        start, end = (datetime.fromisoformat(slot[key]) for key in ["start", "end"])
        assert start.utcoffset() == end.utcoffset() == timedelta(hours=9)
        assert end - start == timedelta(hours=3)
    available_after = datetime.fromisoformat("2026-11-25T15:30:00+09:00")
    feasible = [
        (clean["id"], move["id"])
        for clean in slots if clean["type"] == "cleaning"
        for move in slots if move["type"] == "moving"
        if datetime.fromisoformat(clean["start"]) >= available_after
        and datetime.fromisoformat(move["start"]) >= datetime.fromisoformat(clean["end"])
    ]
    assert ("SLOT-CLEAN-PM", "SLOT-MOVE-LATE") in feasible
    assert ("SLOT-CLEAN-NEXT", "SLOT-MOVE-NEXT") in feasible


def walk(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)
    elif isinstance(value, str):
        yield value


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda row: row["id"])
def test_scenario_references_resolve_and_expectations_are_explicit(scenario):
    ids = (
        set(LISTING_BY_ID)
        | {row["id"] for row in INSPECTIONS}
        | {row["id"] for row in SCHEDULE["events"]}
        | {row["id"] for row in SCHEDULE["provider_slots"]}
    )
    assert scenario["category"] in {"consultation", "reverification", "schedule", "api"}
    assert scenario["verification_stage"] in {"offline", "zoro_required", "live_api_required"}
    assert scenario["given"] and scenario["event"]
    assert scenario["expected_actions"] and scenario["must_not"]
    assert set(scenario["expected_actions"]).isdisjoint(scenario["must_not"])
    for value in walk(scenario):
        for reference in re.findall(r"\b(?:L\d{3}|I\d{3}|ENTRY-\d{2}|CLEAN-\d{2}|MOVE-\d{2}|SLOT-(?:CLEAN|MOVE)-[A-Z]+)\b", value):
            assert reference in ids, f"{scenario['id']} references missing {reference}"
    assert "passed" not in scenario and "actual_result" not in scenario


def test_scenario_catalog_distinguishes_future_verification_levels():
    assert {case["category"] for case in SCENARIOS} == {"consultation", "reverification", "schedule", "api"}
    assert {case["verification_stage"] for case in SCENARIOS} == {"offline", "zoro_required", "live_api_required"}
    live = [case for case in SCENARIOS if case["verification_stage"] == "live_api_required"]
    assert len(live) == 1 and live[0]["given"]["api_keys_supplied"] is False
    assert {"trade", "rent"} == set(live[0]["given"]["kinds"])
    assert "통과 결과가 아닙니다" in read("scenarios.json")["notice"]
