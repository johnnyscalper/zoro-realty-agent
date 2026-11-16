"""Executable tools and session coordination, without a model or outside writes."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from app.domain import DomainError, normalize_conditions, search_candidates, make_verification_task, apply_inspection
from app.scheduling import validate_schedule, detect_conflicts, shift_entry, propose_schedules, apply_proposal
from app.store import SessionStore, StateError


def now():
    return datetime.now(timezone.utc).isoformat()


class ToolService:
    def __init__(self, store: SessionStore, fixtures: dict):
        self.store = store
        self.fixtures = deepcopy(fixtures)

    def create(self, conditions=None):
        schedule = validate_schedule(deepcopy(self.fixtures["schedules"]))
        schedule["conflicts"] = detect_conflicts(schedule)
        state = {
            "conditions": normalize_conditions(conditions or {}),
            "listings": deepcopy(self.fixtures["listings"]["listings"]),
            "verification_tasks": [],
            "inspections": deepcopy(self.fixtures["inspections"]["inspections"]),
            "search_result": None, "search_stale": True,
            "schedule": schedule, "schedule_proposals": [],
            "audit": [{"id": str(uuid4()), "timestamp": now(), "action": "session_created",
                       "summary": "가상 자료로 새 상담 실행실을 만들었습니다.", "revision": 0}],
            "synthetic": True, "execution_mode": "manual_tools", "zoro_used": False,
        }
        return self.store.create(state)

    def get(self, session_id):
        return self.store.get(session_id)

    def _mutate(self, session_id, revision, action, summary, operation):
        def mutate(state):
            operation(state)
            if isinstance(state.get("agent"), dict):
                state["agent"]["pending"] = None
            state["audit"].append({"id": str(uuid4()), "timestamp": now(), "action": action,
                                    "summary": summary, "revision": state["revision"] + 1})
            state["audit"] = state["audit"][-200:]
            return state
        return self.store.mutate(session_id, revision, mutate)

    @staticmethod
    def _listing(state, listing_id):
        for listing in state["listings"]:
            if listing["id"] == listing_id:
                return listing
        raise StateError("LISTING_NOT_FOUND", "이 상담에 해당 매물이 없습니다.", 404)

    @staticmethod
    def _refresh_search(state):
        if state["search_result"] is not None:
            state["search_result"] = search_candidates(state["listings"], state["conditions"])
            state["search_stale"] = False
        state["schedule_proposals"] = []

    def change_conditions(self, session_id, revision, conditions):
        normalized = normalize_conditions(conditions)
        def operation(state):
            state["conditions"] = normalized
            state["search_result"] = None
            state["search_stale"] = True
            state["schedule_proposals"] = []
        return self._mutate(session_id, revision, "conditions_updated",
                            "상담 조건을 저장하고 이전 검색·일정 대안을 무효화했습니다.", operation)

    def search(self, session_id, revision):
        def operation(state):
            state["search_result"] = search_candidates(state["listings"], state["conditions"])
            state["search_stale"] = False
            state["schedule_proposals"] = []
        return self._mutate(session_id, revision, "candidates_searched",
                            "현재 조건으로 후보를 충족·확인 필요·제외로 나눴습니다.", operation)

    def request_verification(self, session_id, revision, listing_id, field):
        def operation(state):
            listing = self._listing(state, listing_id)
            task = make_verification_task(listing, field, state["verification_tasks"])
            if not any(existing["id"] == task["id"] for existing in state["verification_tasks"]):
                if len(state["verification_tasks"]) >= 500:
                    raise StateError("TASK_LIMIT", "이 상담의 확인 요청이 많습니다. 새 상담을 만들어주세요.", 422)
                state["verification_tasks"].append(task)
            state["schedule_proposals"] = []
        return self._mutate(session_id, revision, "verification_requested",
                            "현장 확인 요청을 준비했습니다. 요청만으로 확인 완료가 되지 않습니다.", operation)

    def confirm_inspection(self, session_id, revision, task_id, value, notes=""):
        def operation(state):
            task = next((t for t in state["verification_tasks"] if t["id"] == task_id), None)
            if task is None:
                raise StateError("TASK_NOT_FOUND", "해당 확인 요청이 없습니다.", 404)
            if task["status"] != "pending":
                raise StateError("TASK_ALREADY_RESOLVED", "이미 처리되거나 취소된 확인 요청입니다. 새 요청을 만들어주세요.", 409)
            listing = self._listing(state, task["listing_id"])
            if listing["status"] == "contracted":
                raise StateError("LISTING_CONTRACTED", "계약 완료된 매물에는 새 확인 결과를 반영할 수 없습니다.", 409)
            updated = apply_inspection(listing, field=task["field"], value=value, notes=notes)
            state["listings"] = [updated if x["id"] == listing["id"] else x for x in state["listings"]]
            task["status"] = "confirmed"
            task["confirmed_at"] = now()
            state["inspections"].append({"id": str(uuid4()), "task_id": task_id,
                "listing_id": listing["id"], "field": task["field"], "observed_value": value,
                "status": "confirmed", "observed_at": task["confirmed_at"], "notes": notes})
            if len(state["inspections"]) > 1000:
                raise StateError("EVIDENCE_LIMIT", "이 상담의 확인 기록이 많습니다. 새 상담을 만들어주세요.", 422)
            self._refresh_search(state)
        return self._mutate(session_id, revision, "inspection_confirmed",
                            "입력한 현장 결과를 반영하고 기존 검색 결과를 다시 검사했습니다.", operation)

    def change_status(self, session_id, revision, listing_id, status):
        if status not in {"available", "contracted", "verification_required"}:
            raise DomainError("INVALID_STATUS", "올바른 매물 상태를 선택해주세요.")
        def operation(state):
            listing = self._listing(state, listing_id)
            listing["status"] = status
            if status == "contracted":
                for task in state["verification_tasks"]:
                    if task["listing_id"] == listing_id and task["status"] == "pending":
                        task["status"] = "cancelled"
                        task["cancelled_at"] = now()
            self._refresh_search(state)
        return self._mutate(session_id, revision, "listing_status_updated",
                            "가상 매물 상태를 변경하고 검색 결과를 다시 검사했습니다.", operation)

    def change_entry(self, session_id, revision, new_end):
        def operation(state):
            state["schedule"] = shift_entry(state["schedule"], new_end)
            state["schedule"]["conflicts"] = detect_conflicts(state["schedule"])
            state["schedule_proposals"] = []
        return self._mutate(session_id, revision, "entry_time_updated",
                            "입주 가능 시각을 변경하고 청소·이사 일정 충돌을 계산했습니다.", operation)

    def propose(self, session_id, revision):
        def operation(state):
            alternatives = propose_schedules(state["schedule"], limit=5)
            for proposal in alternatives:
                proposal["based_on_revision"] = state["revision"] + 1
            state["schedule_proposals"] = alternatives
        return self._mutate(session_id, revision, "schedule_proposals_created",
                            "입력된 가상 업체 시간표에서 실행 가능한 일정 대안을 계산했습니다.", operation)

    def apply_schedule(self, session_id, revision, proposal_id):
        def operation(state):
            proposal = next((p for p in state["schedule_proposals"] if p["id"] == proposal_id), None)
            if proposal is None or proposal.get("based_on_revision") != state["revision"]:
                raise StateError("STALE_PROPOSAL", "현재 상태에 맞는 일정 대안이 아닙니다. 대안을 다시 계산해주세요.", 409)
            state["schedule"] = apply_proposal(state["schedule"], proposal)
            state["schedule"]["conflicts"] = detect_conflicts(state["schedule"])
            state["schedule_proposals"] = []
        return self._mutate(session_id, revision, "schedule_applied",
                            "선택한 대안을 내부 일정표에 반영했습니다. 업체 예약·연락은 하지 않았습니다.", operation)
