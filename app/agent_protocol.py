"""Small, strict decision language shared by development and model providers."""
from copy import deepcopy
from datetime import date
import math

from app.domain import DomainError, normalize_conditions
from app.scheduling import ScheduleError, _timestamp


class AgentError(ValueError):
    def __init__(self, code, message, http_status=422):
        self.code, self.message, self.http_status = code, message, http_status
        self.retryable = http_status in {409, 502, 503, 504}
        super().__init__(message)


CONFIRM_TOOLS = frozenset({"update_conditions", "record_inspection", "set_listing_status", "set_entry_time", "apply_schedule"})
DESCRIPTIONS = {
    "update_conditions": "필수·선호 조건 변경",
    "search_candidates": "가상 매물 후보 검색",
    "request_verification": "현장 확인 요청 준비",
    "record_inspection": "사용자가 확인한 현장 결과 반영",
    "set_listing_status": "가상 매물 상태 변경",
    "set_entry_time": "입주 가능 시각 변경",
    "propose_schedule": "청소·이사 일정 대안 계산",
    "apply_schedule": "선택한 내부 일정 적용",
    "lookup_transactions": "과거 실거래 참고 자료 조회",
    "ask_user": "누락·모호한 조건 질문",
    "finish": "실행 결과 정리",
}
TOOLS = [{"name": name, "description": description, "requires_confirmation": name in CONFIRM_TOOLS}
         for name, description in DESCRIPTIONS.items()]
FIELDS = frozenset({"entry_steps", "step_free_access", "elevator", "noise_level"})


def invalid():
    return AgentError("INVALID_DECISION", "에이전트가 허용된 행동 형식으로 응답하지 않았습니다. 변경을 반영하지 않았습니다.")


def _text(value, limit=200, *, blank=False):
    if type(value) is not str or len(value) > limit or (not blank and not value.strip()):
        raise invalid()
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise invalid()
    return value.strip()


def _keys(args, required=(), optional=()):
    if type(args) is not dict or not set(required) <= args.keys() or not args.keys() <= set(required) | set(optional):
        raise invalid()


def validate_decision(value):
    if type(value) is not dict or not {"tool", "arguments"} <= value.keys() or not value.keys() <= {"tool", "arguments", "reason"}:
        raise invalid()
    tool = value["tool"]
    if type(tool) is not str or tool not in DESCRIPTIONS:
        raise invalid()
    args = deepcopy(value["arguments"])
    if type(args) is not dict:
        raise invalid()
    reason = _text(value.get("reason", ""), 500, blank=True)
    if tool in {"search_candidates", "propose_schedule", "finish"}:
        _keys(args)
    elif tool == "ask_user":
        _keys(args, ["question"])
        args["question"] = _text(args["question"], 600)
    elif tool == "update_conditions":
        _keys(args, ["conditions"])
        patch = args["conditions"]
        try:
            normalized = normalize_conditions(patch)
            if not any(patch.get(section) for section in ("hard", "preferences")):
                raise invalid()
            hard = normalized["hard"]
            if hard["max_deposit_krw"] is not None and hard["max_deposit_krw"] > 1_000_000_000_000:
                raise invalid()
            if hard["min_area_m2"] is not None and hard["min_area_m2"] > 1000:
                raise invalid()
            if len(normalized["preferences"]["neighborhoods"]) > 20:
                raise invalid()
            args["conditions"] = {section: {key: normalized[section][key] for key in fields}
                                  for section, fields in patch.items()}
        except (DomainError, TypeError, AttributeError):
            raise invalid() from None
    elif tool in {"request_verification", "record_inspection"}:
        required = ["listing_id", "field"] + (["value"] if tool == "record_inspection" else [])
        _keys(args, required, ["notes"] if tool == "record_inspection" else [])
        args["listing_id"] = _text(args["listing_id"], 64)
        if type(args["field"]) is not str or args["field"] not in FIELDS:
            raise invalid()
        if tool == "record_inspection":
            field, observed = args["field"], args["value"]
            if field in {"step_free_access", "elevator"} and type(observed) is not bool:
                raise invalid()
            if field == "entry_steps" and (type(observed) is not int or not 0 <= observed <= 10000):
                raise invalid()
            if field == "noise_level" and (type(observed) is not str or observed not in {"low", "medium", "high"}):
                raise invalid()
            if "notes" in args:
                args["notes"] = _text(args["notes"], 2000, blank=True)
    elif tool == "set_listing_status":
        _keys(args, ["listing_id", "status"])
        args["listing_id"] = _text(args["listing_id"], 64)
        if type(args["status"]) is not str or args["status"] not in {"available", "contracted", "verification_required"}:
            raise invalid()
    elif tool == "set_entry_time":
        _keys(args, ["new_end"])
        args["new_end"] = _text(args["new_end"], 48)
        try:
            parsed = _timestamp(args["new_end"])
            args["new_end"] = parsed.isoformat()
        except ScheduleError:
            raise invalid() from None
    elif tool == "apply_schedule":
        _keys(args, ["proposal_id"])
        args["proposal_id"] = _text(args["proposal_id"], 128)
    elif tool == "lookup_transactions":
        _keys(args, ["kind", "deal_ymd"], ["lawd_cd", "apartment_name", "min_area_m2", "max_area_m2"])
        if type(args["kind"]) is not str or args["kind"] not in {"trade", "rent"}:
            raise invalid()
        month = args["deal_ymd"]
        if type(month) is not str or len(month) != 6 or not month.isascii() or not month.isdigit():
            raise invalid()
        try:
            if date(int(month[:4]), int(month[4:]), 1).year < 2006:
                raise invalid()
        except ValueError:
            raise invalid() from None
        if args.get("lawd_cd", "11680") != "11680":
            raise invalid()
        args["lawd_cd"] = "11680"
        if "apartment_name" in args:
            args["apartment_name"] = _text(args["apartment_name"], 100)
        for key in ("min_area_m2", "max_area_m2"):
            if key in args and (type(args[key]) not in (int, float) or not 0 < args[key] <= 1000 or not math.isfinite(args[key])):
                raise invalid()
        if args.get("min_area_m2", 0) > args.get("max_area_m2", 1000):
            raise invalid()
    return {"tool": tool, "arguments": args, "reason": reason}


def merge_conditions(current, patch):
    merged = deepcopy(current)
    for section, fields in patch.items():
        merged.setdefault(section, {}).update(deepcopy(fields))
    return normalize_conditions(merged)
