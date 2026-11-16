"""Pure local scheduling checks; a suggested plan never books a provider.

Provider slots are supplied availability windows, not inferred availability. An
event may use a window only when the whole event fits inside it. The end of the
entry event is the time at which the dwelling becomes accessible.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import Any


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z"
)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_EVENT_TYPES = frozenset({"entry", "cleaning", "moving"})
_SERVICE_TYPES = frozenset({"cleaning", "moving"})
_SCHEDULE_KEYS = frozenset(
    {"events", "provider_slots", "synthetic", "date", "notice", "conflicts"}
)
_EVENT_KEYS = frozenset({"id", "type", "start", "end", "status", "depends_on"})
_SLOT_KEYS = frozenset({"id", "type", "start", "end"})


class ScheduleError(ValueError):
    """A safe, user-facing scheduling error without raw input values."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid(message: str = "일정 형식을 확인해 주세요.") -> ScheduleError:
    return ScheduleError("INVALID_SCHEDULE", message)


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ScheduleError(
            "INVALID_SCHEDULE_TIME", "일정 시간은 시간대가 포함된 ISO 날짜·시간이어야 합니다."
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Also reject extreme offsets that put the UTC date outside datetime's
        # representable range; later ordering/duration operations must stay safe.
        parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ScheduleError(
            "INVALID_SCHEDULE_TIME", "유효한 일정 날짜·시간을 입력해 주세요."
        ) from None
    return parsed


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise _invalid("일정과 업체 시간의 식별자 형식을 확인해 주세요.")
    return value


def _row(raw: Any, *, slot: bool) -> dict:
    allowed = _SLOT_KEYS if slot else _EVENT_KEYS
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise _invalid()
    if not {"id", "type", "start", "end"} <= raw.keys():
        raise _invalid("일정의 식별자, 종류와 시작·종료 시간이 필요합니다.")
    result = deepcopy(raw)
    result["id"] = _identifier(raw["id"])
    kinds = _SERVICE_TYPES if slot else _EVENT_TYPES
    if not isinstance(raw["type"], str) or raw["type"] not in kinds:
        raise _invalid("지원하는 일정 종류인지 확인해 주세요.")
    start, end = _timestamp(raw["start"]), _timestamp(raw["end"])
    if end < start or (end == start and (slot or raw["type"] != "entry")):
        raise ScheduleError(
            "INVALID_SCHEDULE_DURATION", "청소·이사와 업체 시간에는 양수의 소요 시간이 필요합니다."
        )
    result["start"], result["end"] = start.isoformat(), end.isoformat()
    if not slot:
        status = raw.get("status", "planned")
        if not isinstance(status, str) or not status.strip() or len(status) > 40:
            raise _invalid("일정 상태 형식을 확인해 주세요.")
        result["status"] = status
        dependencies = raw.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise _invalid("선행 일정은 식별자 목록이어야 합니다.")
        result["depends_on"] = [_identifier(item) for item in dependencies]
        if len(set(result["depends_on"])) != len(result["depends_on"]):
            raise _invalid("같은 선행 일정을 중복해서 지정할 수 없습니다.")
    return result


def _topological_events(events: list[dict]) -> list[dict]:
    """Stable Kahn ordering avoids recursion and rejects every dependency cycle."""
    by_id = {event["id"]: event for event in events}
    remaining = {event["id"]: set(event["depends_on"]) for event in events}
    ordered: list[dict] = []
    completed: set[str] = set()
    while remaining:
        ready = [event_id for event_id, deps in remaining.items() if deps <= completed]
        if not ready:
            raise ScheduleError("SCHEDULE_CYCLE", "일정의 선행 관계가 순환하고 있습니다.")
        for event_id in ready:
            ordered.append(by_id[event_id])
            completed.add(event_id)
            del remaining[event_id]
    return ordered


def validate_schedule(schedule: dict) -> dict:
    """Validate structure, ids, timezone-aware times and a dependency DAG.

    A structurally valid schedule may still have temporal conflicts. Call
    :func:`detect_conflicts` to find them. Input dictionaries are never mutated.
    """
    if not isinstance(schedule, dict) or set(schedule) - _SCHEDULE_KEYS:
        raise _invalid()
    if not {"events", "provider_slots"} <= schedule.keys():
        raise _invalid("일정과 업체 가능 시간 목록이 필요합니다.")
    if not isinstance(schedule["events"], list) or not isinstance(schedule["provider_slots"], list):
        raise _invalid("일정과 업체 가능 시간은 목록이어야 합니다.")
    # The local tools operate on a small household schedule. Bounds also prevent
    # malformed input from making graph/proposal exploration unbounded in size.
    if len(schedule["events"]) > 100 or len(schedule["provider_slots"]) > 1000:
        raise _invalid("일정 또는 업체 시간의 개수가 너무 많습니다.")
    if "synthetic" in schedule and type(schedule["synthetic"]) is not bool:
        raise _invalid()
    if "notice" in schedule and not isinstance(schedule["notice"], str):
        raise _invalid()
    if "date" in schedule:
        value = schedule["date"]
        if not isinstance(value, str) or not _DATE.fullmatch(value):
            raise _invalid("일정 날짜는 YYYY-MM-DD 형식이어야 합니다.")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise _invalid("유효한 일정 날짜를 입력해 주세요.") from None
    if "conflicts" in schedule and not isinstance(schedule["conflicts"], list):
        raise _invalid()
    result = deepcopy(schedule)
    result["events"] = [_row(event, slot=False) for event in schedule["events"]]
    result["provider_slots"] = [_row(slot, slot=True) for slot in schedule["provider_slots"]]
    all_ids = [row["id"] for row in result["events"] + result["provider_slots"]]
    if len(all_ids) != len(set(all_ids)):
        raise ScheduleError("DUPLICATE_SCHEDULE_ID", "일정과 업체 시간의 식별자는 중복될 수 없습니다.")
    event_ids = {event["id"] for event in result["events"]}
    for event in result["events"]:
        if any(dependency not in event_ids for dependency in event["depends_on"]):
            raise ScheduleError("UNKNOWN_DEPENDENCY", "존재하지 않는 선행 일정이 지정되었습니다.")
    _topological_events(result["events"])
    return result


def _conflicts(schedule: dict) -> list[dict]:
    """Compute conflicts for a previously normalized schedule."""
    events = schedule["events"]
    by_id = {event["id"]: event for event in events}
    event_times = {
        event["id"]: (_timestamp(event["start"]), _timestamp(event["end"]))
        for event in events
    }
    slots = [
        (slot["type"], _timestamp(slot["start"]), _timestamp(slot["end"]))
        for slot in schedule["provider_slots"]
    ]
    conflicts: list[dict] = []
    for event in events:
        start, end = event_times[event["id"]]
        for dependency in event["depends_on"]:
            if event_times[dependency][1] > start:
                message = (
                    "입주 가능 시각보다 후속 일정이 먼저 시작됩니다."
                    if by_id[dependency]["type"] == "entry"
                    else "선행 일정이 끝나기 전에 후속 일정이 시작됩니다."
                )
                conflicts.append(
                    {"code": "DEPENDENCY_CONFLICT", "message": message,
                     "event_ids": [dependency, event["id"]]}
                )
        if event["type"] in _SERVICE_TYPES and not any(
            kind == event["type"] and slot_start <= start and end <= slot_end
            for kind, slot_start, slot_end in slots
        ):
            conflicts.append(
                {"code": "PROVIDER_UNAVAILABLE",
                 "message": "일정 전체를 수용하는 업체 가능 시간이 없습니다.",
                 "event_ids": [event["id"]]}
            )
    return conflicts


def detect_conflicts(schedule: dict) -> list[dict]:
    """Find dependency and supplied-availability conflicts; equality is allowed."""
    return _conflicts(validate_schedule(schedule))


def shift_entry(schedule: dict, new_end: str) -> dict:
    """Move only entry, retaining its duration and exposing downstream conflicts."""
    result = validate_schedule(schedule)
    entries = [event for event in result["events"] if event["type"] == "entry"]
    if len(entries) != 1:
        raise ScheduleError("ENTRY_NOT_UNIQUE", "입주 일정이 정확히 하나 있어야 변경할 수 있습니다.")
    end = _timestamp(new_end)
    entry = entries[0]
    duration = _timestamp(entry["end"]) - _timestamp(entry["start"])
    try:
        start = end - duration
    except OverflowError:
        raise ScheduleError("INVALID_SCHEDULE_TIME", "변경한 입주 시간이 허용 범위를 벗어납니다.") from None
    entry["start"], entry["end"] = start.isoformat(), end.isoformat()
    # Validation also checks representability after applying the preserved duration.
    result = validate_schedule(result)
    result["conflicts"] = _conflicts(result)
    return result


def _event_signature(events: list[dict]) -> tuple:
    return tuple(
        (event["id"], _timestamp(event["start"]), _timestamp(event["end"]))
        for event in events
    )


def _summary(events: list[dict]) -> str:
    labels = {"cleaning": "청소", "moving": "이사"}
    parts = []
    for event in events:
        if event["type"] in labels:
            start, end = _timestamp(event["start"]), _timestamp(event["end"])
            parts.append(
                f"{labels[event['type']]} {start.isoformat(timespec='minutes')}"
                f" → {end.isoformat(timespec='minutes')}"
            )
    return " / ".join(parts) + " · 내부 일정안이며 업체 예약은 수행하지 않습니다."


def propose_schedules(schedule: dict, *, limit: int = 5) -> list[dict]:
    """Enumerate distinct compatible plans using only supplied provider windows.

    In each chosen window a service starts at the earliest instant compatible
    with its dependencies. Unchanged plans are not alternatives. Ordering is
    deterministic, and even a larger requested limit returns at most five plans.
    """
    if type(limit) is not int or limit < 1:
        raise ScheduleError("INVALID_PROPOSAL_LIMIT", "일정 대안 개수는 양의 정수여야 합니다.")
    limit = min(limit, 5)
    normalized = validate_schedule(schedule)
    events = normalized["events"]
    ordered = _topological_events(events)
    if not any(event["type"] in _SERVICE_TYPES for event in events):
        return []
    slots_by_type = {
        kind: sorted(
            (slot for slot in normalized["provider_slots"] if slot["type"] == kind),
            key=lambda slot: (_timestamp(slot["start"]), _timestamp(slot["end"]), slot["id"]),
        )
        for kind in _SERVICE_TYPES
    }
    original_signature = _event_signature(events)
    seen: set[tuple] = {original_signature}
    proposals: list[dict] = []
    assigned: dict[str, dict] = {}

    def visit(index: int) -> None:
        if len(proposals) >= limit:
            return
        if index == len(ordered):
            candidate_events = [deepcopy(assigned[event["id"]]) for event in events]
            signature = _event_signature(candidate_events)
            if signature in seen:
                return
            seen.add(signature)
            candidate = {**normalized, "events": candidate_events}
            if _conflicts(candidate):
                return
            canonical = json.dumps(candidate_events, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
            proposal_id = "PROPOSAL-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
            proposals.append({"id": proposal_id, "events": candidate_events, "summary": _summary(candidate_events)})
            return
        event = ordered[index]
        predecessor_ends = [_timestamp(assigned[dependency]["end"]) for dependency in event["depends_on"]]
        if event["type"] == "entry":
            if any(end > _timestamp(event["start"]) for end in predecessor_ends):
                return
            assigned[event["id"]] = event
            visit(index + 1)
            del assigned[event["id"]]
            return
        duration = _timestamp(event["end"]) - _timestamp(event["start"])
        candidate_times: set[tuple[datetime, datetime]] = set()
        for slot in slots_by_type[event["type"]]:
            start = max([_timestamp(slot["start"]), *predecessor_ends])
            try:
                end = start + duration
            except OverflowError:
                continue
            if end > _timestamp(slot["end"]) or (start, end) in candidate_times:
                continue
            candidate_times.add((start, end))
            assigned[event["id"]] = {**event, "start": start.isoformat(), "end": end.isoformat()}
            visit(index + 1)
            del assigned[event["id"]]
            if len(proposals) >= limit:
                break

    visit(0)
    return proposals


def apply_proposal(schedule: dict, proposal: dict) -> dict:
    """Recheck a stored proposal and apply only valid timing changes locally.

    Session ownership and revision checks belong to the caller. This function
    independently protects event identity, duration, dependency graph, fixed
    entry times, and the current supplied provider availability.
    """
    current = validate_schedule(schedule)
    if not isinstance(proposal, dict) or not {"id", "events", "summary"} <= proposal.keys():
        raise ScheduleError("INVALID_PROPOSAL", "유효한 저장된 일정 대안을 선택해 주세요.")
    _identifier(proposal["id"])
    if not isinstance(proposal["summary"], str):
        raise ScheduleError("INVALID_PROPOSAL", "일정 대안 형식을 확인해 주세요.")
    candidate = validate_schedule({**current, "events": deepcopy(proposal["events"])})
    proposed = {event["id"]: event for event in candidate["events"]}
    if set(proposed) != {event["id"] for event in current["events"]}:
        raise ScheduleError("INVALID_PROPOSAL", "일정 대안은 기존 일정의 구성을 유지해야 합니다.")
    for original in current["events"]:
        changed = proposed[original["id"]]
        before = {key: value for key, value in original.items() if key not in {"start", "end"}}
        after = {key: value for key, value in changed.items() if key not in {"start", "end"}}
        original_start, original_end = _timestamp(original["start"]), _timestamp(original["end"])
        changed_start, changed_end = _timestamp(changed["start"]), _timestamp(changed["end"])
        if before != after or original_end - original_start != changed_end - changed_start:
            raise ScheduleError("INVALID_PROPOSAL", "일정 대안은 기존 소요 시간과 선행 관계를 유지해야 합니다.")
        if original["type"] == "entry" and (original_start != changed_start or original_end != changed_end):
            raise ScheduleError("INVALID_PROPOSAL", "일정 대안으로 입주 가능 시각을 변경할 수 없습니다.")
    if _conflicts(candidate):
        raise ScheduleError("PROPOSAL_CONFLICT", "현재 조건과 업체 가능 시간에 맞지 않는 일정 대안입니다.")
    candidate["events"] = [proposed[event["id"]] for event in current["events"]]
    candidate["conflicts"] = []
    return candidate
