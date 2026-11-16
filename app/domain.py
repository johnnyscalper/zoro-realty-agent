"""Deterministic, copy-on-write tools for the synthetic real-estate workspace.

The listing's individual fields are the evidence. A timestamp, an elevator, a
stair count, or a descriptive note never establishes a different field's value.
No operation reads files, changes global fixtures, relaxes a condition, or calls
a model. The caller owns persistence and the history of confirmed observations.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
import math
import re
from typing import Any
from uuid import uuid4


class DomainError(ValueError):
    """An error safe to present to a user, without echoing input values."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


_HARD_FIELDS = (
    "max_deposit_krw", "min_area_m2", "move_in_by", "step_free_access", "elevator",
)
_PREFERENCE_FIELDS = ("noise_level", "neighborhoods")
_NOISE_LEVELS = frozenset({"low", "medium", "high"})
_LISTING_STATUSES = frozenset({"available", "contracted", "verification_required"})
_LISTING_FIELDS = frozenset({
    "id", "name", "district_code", "neighborhood", "deposit_krw", "area_m2",
    "floor", "elevator", "step_free_access", "entry_steps", "noise_level",
    "available_from", "status", "source_type", "notes", "verified_at",
})
_VERIFICATION_QUESTIONS = {
    "entry_steps": "도로에서 세대 현관까지 이동 경로에 계단이 몇 개 있나요? 대체 경로의 무단차 여부는 별도로 확인해 주세요.",
    "step_free_access": "도로에서 세대 현관까지 모든 구간을 계단 없이 이동할 수 있나요? 경사로와 대체 출입구를 포함해 전체 경로를 확인해 주세요.",
    "elevator": "해당 건물에 이용 가능한 엘리베이터가 있나요? 도로에서 현관까지 무단차인지는 별도로 확인해 주세요.",
    "noise_level": "실제 거주할 시간대의 현장 소음은 낮음·보통·높음 중 어느 수준인가요? 확인 시간과 상황을 메모해 주세요.",
}
_MAX_NOTES_LENGTH = 2_000


def _is_positive_number(value: Any) -> bool:
    return (type(value) is int and value > 0) or (
        type(value) is float and math.isfinite(value) and value > 0
    )


def _is_date(value: Any) -> bool:
    if type(value) is not str or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_timestamp(value: Any) -> bool:
    if type(value) is not str or len(value) > 64:
        return False
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.tzinfo is not None and stamp.utcoffset() is not None
    except ValueError:
        return False


def _is_identifier(value: Any) -> bool:
    return type(value) is str and 0 < len(value) <= 128 and bool(value.strip())


def _is_noise(value: Any, *, nullable: bool = True) -> bool:
    return (nullable and value is None) or (
        type(value) is str and value in _NOISE_LEVELS
    )


def normalize_conditions(payload: dict) -> dict:
    """Validate JSON types and fill omitted conditions without guessing values.

    A boolean ``False`` remains an explicit hard requirement; only ``None``
    means unspecified. Neighborhood preferences are trimmed and deduplicated
    in their supplied order. No coercion of prices, dates or booleans occurs.
    """
    if type(payload) is not dict or not payload.keys() <= {"hard", "preferences"}:
        raise DomainError("INVALID_CONDITIONS", "조건은 필수 조건과 선호 조건만 포함하는 객체여야 합니다.")
    hard = payload.get("hard", {})
    preferences = payload.get("preferences", {})
    if type(hard) is not dict or not hard.keys() <= set(_HARD_FIELDS):
        raise DomainError("INVALID_CONDITIONS", "지원하지 않는 필수 조건이 있거나 필수 조건 형식이 잘못되었습니다.")
    if type(preferences) is not dict or not preferences.keys() <= set(_PREFERENCE_FIELDS):
        raise DomainError("INVALID_CONDITIONS", "지원하지 않는 선호 조건이 있거나 선호 조건 형식이 잘못되었습니다.")

    normalized_hard = {field: hard.get(field) for field in _HARD_FIELDS}
    budget = normalized_hard["max_deposit_krw"]
    if budget is not None and (type(budget) is not int or budget <= 0):
        raise DomainError("INVALID_CONDITIONS", "최대 보증금은 원 단위의 양의 정수 또는 미입력이어야 합니다.")
    area = normalized_hard["min_area_m2"]
    if area is not None and not _is_positive_number(area):
        raise DomainError("INVALID_CONDITIONS", "최소 면적은 유한한 양수 또는 미입력이어야 합니다.")
    deadline = normalized_hard["move_in_by"]
    if deadline is not None and not _is_date(deadline):
        raise DomainError("INVALID_CONDITIONS", "입주 마감일은 실제 존재하는 YYYY-MM-DD 날짜여야 합니다.")
    for field in ("step_free_access", "elevator"):
        if normalized_hard[field] is not None and type(normalized_hard[field]) is not bool:
            raise DomainError("INVALID_CONDITIONS", "접근성 필수 조건은 참·거짓 또는 미입력이어야 합니다.")

    noise = preferences.get("noise_level")
    if not _is_noise(noise):
        raise DomainError("INVALID_CONDITIONS", "소음 선호는 낮음·보통·높음 또는 미입력이어야 합니다.")
    neighborhoods = preferences.get("neighborhoods", [])
    if type(neighborhoods) is not list or any(
        type(item) is not str or not item.strip() or len(item) > 100
        for item in neighborhoods
    ):
        raise DomainError("INVALID_CONDITIONS", "선호 동네는 비어 있지 않은 짧은 이름의 목록이어야 합니다.")
    return {
        "hard": normalized_hard,
        "preferences": {
            "noise_level": noise,
            "neighborhoods": list(dict.fromkeys(item.strip() for item in neighborhoods)),
        },
    }


def _validate_listing(listing: Any) -> None:
    """Validate the original fixture schema without permissive type coercion."""
    invalid = DomainError("INVALID_LISTING", "매물의 필드 또는 값 형식이 올바르지 않습니다.")
    if type(listing) is not dict or listing.keys() != _LISTING_FIELDS:
        raise invalid
    if not _is_identifier(listing["id"]):
        raise invalid
    if any(type(listing[key]) is not str or not listing[key].strip() for key in ("name", "neighborhood")):
        raise invalid
    if listing["district_code"] != "11680" or listing["source_type"] != "synthetic":
        raise invalid
    if type(listing["deposit_krw"]) is not int or listing["deposit_krw"] <= 0:
        raise invalid
    if not _is_positive_number(listing["area_m2"]) or type(listing["floor"]) is not int:
        raise invalid
    if any(listing[key] is not None and type(listing[key]) is not bool for key in ("elevator", "step_free_access")):
        raise invalid
    steps = listing["entry_steps"]
    if steps is not None and (type(steps) is not int or steps < 0):
        raise invalid
    if not _is_noise(listing["noise_level"]) or not _is_date(listing["available_from"]):
        raise invalid
    if type(listing["status"]) is not str or listing["status"] not in _LISTING_STATUSES:
        raise invalid
    if type(listing["notes"]) is not str:
        raise invalid
    if listing["verified_at"] is not None and not _is_timestamp(listing["verified_at"]):
        raise invalid


def _reason(code: str, message: str, field: str) -> dict:
    return {"code": code, "message": message, "field": field}


def search_candidates(listings: list, conditions: dict) -> dict:
    """Partition every listing by hard evidence; use preferences only to rank.

    An explicit field value is evaluated independently of the generic
    ``verification_required`` marker and of ``verified_at``. Unknown optional
    preferences never become hard exclusions. If the budget is missing, no
    candidate is called eligible even if all other hard conditions match.
    """
    normalized = normalize_conditions(conditions)
    if type(listings) is not list:
        raise DomainError("INVALID_LISTINGS", "매물은 목록 형식이어야 합니다.")
    seen_ids: set[str] = set()
    for listing in listings:
        _validate_listing(listing)
        if listing["id"] in seen_ids:
            raise DomainError("INVALID_LISTINGS", "중복된 매물 식별자가 있습니다.")
        seen_ids.add(listing["id"])

    hard = normalized["hard"]
    preferences = normalized["preferences"]
    missing_budget = hard["max_deposit_krw"] is None
    result = {
        "status": "incomplete" if missing_budget else "ready",
        "missing_fields": ["hard.max_deposit_krw"] if missing_budget else [],
        "eligible": [], "needs_verification": [], "excluded": [],
    }
    for listing in listings:
        reasons: list[dict] = []
        excluded = listing["status"] == "contracted"
        pending = missing_budget
        score = 0
        if excluded:
            reasons.append(_reason("CONTRACTED", "계약이 완료된 매물은 후보에서 제외합니다.", "status"))
        if missing_budget:
            reasons.append(_reason("BUDGET_REQUIRED", "최대 보증금이 없어 적합한 후보로 판단할 수 없습니다. 예산을 입력해 주세요.", "max_deposit_krw"))
        elif listing["deposit_krw"] > hard["max_deposit_krw"]:
            excluded = True
            reasons.append(_reason("BUDGET_EXCEEDED", "최대 보증금을 초과합니다. 예산 조건은 자동으로 완화하지 않습니다.", "deposit_krw"))
        else:
            reasons.append(_reason("BUDGET_MATCH", "보증금이 입력한 최대 예산 이내입니다.", "deposit_krw"))

        if hard["min_area_m2"] is not None:
            if listing["area_m2"] < hard["min_area_m2"]:
                excluded = True
                reasons.append(_reason("AREA_TOO_SMALL", "전용 면적이 필수 최소 면적에 미달합니다.", "area_m2"))
            else:
                reasons.append(_reason("AREA_MATCH", "전용 면적이 필수 최소 면적을 충족합니다.", "area_m2"))
        if hard["move_in_by"] is not None:
            if listing["available_from"] > hard["move_in_by"]:
                excluded = True
                reasons.append(_reason("MOVE_IN_TOO_LATE", "입주 가능일이 필수 입주 마감일보다 늦습니다.", "available_from"))
            else:
                reasons.append(_reason("MOVE_IN_MATCH", "필수 입주 마감일까지 입주 가능합니다.", "available_from"))

        for field, label in (("step_free_access", "무단차 접근"), ("elevator", "엘리베이터")):
            expected = hard[field]
            if expected is None:
                continue
            actual = listing[field]
            if actual is None:
                pending = True
                reasons.append(_reason("HARD_EVIDENCE_UNKNOWN", f"{label} 여부가 미확인입니다. 직접 확인하기 전에는 조건 충족으로 판단하지 않습니다.", field))
            elif actual is not expected:
                excluded = True
                reasons.append(_reason("HARD_REQUIREMENT_MISMATCH", f"확인된 {label} 여부가 입력한 필수 조건과 다릅니다.", field))
            else:
                reasons.append(_reason("HARD_REQUIREMENT_MATCH", f"확인된 {label} 여부가 입력한 필수 조건과 일치합니다.", field))

        if preferences["noise_level"] is not None:
            if listing["noise_level"] is None:
                reasons.append(_reason("PREFERENCE_UNKNOWN", "소음 수준은 미확인입니다. 소음 선호가 충족됐다고 판단하지 않습니다.", "noise_level"))
            elif listing["noise_level"] == preferences["noise_level"]:
                score += 1
                reasons.append(_reason("PREFERENCE_MATCH", "확인된 소음 수준이 선호와 일치합니다.", "noise_level"))
            else:
                reasons.append(_reason("PREFERENCE_MISMATCH", "소음 수준이 선호와 다르지만 선호 조건만으로 제외하지 않습니다.", "noise_level"))
        if preferences["neighborhoods"]:
            if listing["neighborhood"] in preferences["neighborhoods"]:
                score += 1
                reasons.append(_reason("PREFERENCE_MATCH", "선호하는 동네에 있는 매물입니다.", "neighborhood"))
            else:
                reasons.append(_reason("PREFERENCE_MISMATCH", "선호 동네 밖의 매물이지만 선호 조건만으로 제외하지 않습니다.", "neighborhood"))

        bucket = "excluded" if excluded else ("needs_verification" if pending else "eligible")
        result[bucket].append({
            "listing": deepcopy(listing), "reasons": reasons, "preference_score": score,
        })

    for bucket in ("eligible", "needs_verification", "excluded"):
        result[bucket].sort(key=lambda candidate: (-candidate["preference_score"], candidate["listing"]["id"]))
    return result


def _validate_verification_field(field: Any) -> None:
    if type(field) is not str or field not in _VERIFICATION_QUESTIONS:
        raise DomainError("INVALID_VERIFICATION_FIELD", "확인할 수 있는 항목은 계단 수·무단차 접근·엘리베이터·소음 수준입니다.")


def _require_active_listing(listing: dict) -> None:
    _validate_listing(listing)
    if listing["status"] == "contracted":
        raise DomainError("LISTING_CONTRACTED", "계약 완료 매물에는 새로운 확인 요청이나 현장 결과를 등록할 수 없습니다.")


def make_verification_task(listing: dict, field: str, existing_tasks: list) -> dict:
    """Return a new pending task, or a copy of the matching pending task."""
    _validate_verification_field(field)
    _require_active_listing(listing)
    if type(existing_tasks) is not list:
        raise DomainError("INVALID_VERIFICATION_TASKS", "확인 요청 목록 형식이 올바르지 않습니다.")
    existing_ids: set[str] = set()
    pending_match = None
    for task in existing_tasks:
        if (
            type(task) is not dict
            or not _is_identifier(task.get("id"))
            or not _is_identifier(task.get("listing_id"))
            or type(task.get("field")) is not str
            or task["field"] not in _VERIFICATION_QUESTIONS
            or type(task.get("status")) is not str
            or task["status"] not in {"pending", "confirmed", "cancelled"}
            or type(task.get("question")) is not str
            or not _is_timestamp(task.get("created_at"))
            or task["id"] in existing_ids
        ):
            raise DomainError("INVALID_VERIFICATION_TASKS", "확인 요청 목록에 올바르지 않은 항목이 있습니다.")
        existing_ids.add(task["id"])
        if task["listing_id"] == listing["id"] and task["field"] == field and task["status"] == "pending":
            pending_match = pending_match or task
    if pending_match is not None:
        return deepcopy(pending_match)
    task_id = str(uuid4())
    while task_id in existing_ids:
        task_id = str(uuid4())
    return {
        "id": task_id, "listing_id": listing["id"], "field": field,
        "status": "pending", "question": _VERIFICATION_QUESTIONS[field],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def apply_inspection(listing: dict, *, field: str, value: Any, notes: str = "") -> dict:
    """Apply one confirmed observation, preserving every other evidence field.

    ``notes`` is validated here and stored in the caller's evidence history;
    it does not replace the original listing description. The timestamp records
    this update, not verification of every other field on the listing.
    """
    _validate_verification_field(field)
    _require_active_listing(listing)
    if type(notes) is not str or len(notes) > _MAX_NOTES_LENGTH:
        raise DomainError("INVALID_INSPECTION_NOTES", "현장 확인 메모는 2,000자 이하의 문자열이어야 합니다.")
    valid_value = False
    if field == "entry_steps":
        valid_value = type(value) is int and value >= 0
    elif field in {"step_free_access", "elevator"}:
        valid_value = type(value) is bool
    elif field == "noise_level":
        valid_value = _is_noise(value, nullable=False)
    if not valid_value:
        raise DomainError("INVALID_INSPECTION_VALUE", "확인 결과 형식이 올바르지 않습니다. 미확인 값은 확인 완료로 등록할 수 없습니다.")
    updated = deepcopy(listing)
    updated[field] = value
    updated["verified_at"] = datetime.now(timezone.utc).isoformat()
    return updated
