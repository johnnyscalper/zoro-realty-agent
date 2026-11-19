"""Bounded observe/decide/execute loop with reviewed writes and one CAS commit.

No provider runs while holding a database transaction. ToolService executes
against an isolated snapshot; a later error discards all business changes.
"""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import hashlib
import inspect
import json
import re
from uuid import uuid4

from app.agent_protocol import AgentError, CONFIRM_TOOLS, DESCRIPTIONS, TOOLS, merge_conditions, validate_decision
from app.counsel_reply import completed_reply, confirmation_reply, question_reply
from app.domain import DomainError
from app.molit import MolitError
from app.scheduling import ScheduleError
from app.store import StateError
from app.tools import ToolService
from app.zoro_provider import PROVIDER_CODES, ProviderError


MAX_STEPS = 6
MAX_REQUESTS = 200
TURN_TIMEOUT_SECONDS = 45


def now():
    return datetime.now(timezone.utc).isoformat()


def metadata(state):
    agent = deepcopy(state.get("agent") or {})
    for key, default in {"messages": [], "turns": [], "pending": None, "requests": [], "last_request_id": None, "last_question": None}.items():
        agent.setdefault(key, deepcopy(default))
    return agent


class WorkingStore:
    """Use the existing tool implementation without persisting intermediate steps."""
    def __init__(self, state):
        self.state = deepcopy(state)

    def get(self, session_id):
        if session_id != self.state["session_id"]:
            raise StateError("SESSION_NOT_FOUND", "상담을 찾을 수 없습니다.", 404)
        return deepcopy(self.state)

    def mutate(self, session_id, expected_revision, operation):
        state = self.get(session_id)
        if expected_revision != state["revision"]:
            raise StateError("REVISION_CONFLICT", "상담 상태를 다시 불러와 주세요.", 409)
        changed = operation(state)
        changed["session_id"] = session_id
        changed["revision"] = expected_revision + 1
        self.state = deepcopy(changed)
        return deepcopy(changed)


def review_summary(tool, args, state):
    if tool == "update_conditions":
        names = {"max_deposit_krw": "최대 보증금(원)", "min_area_m2": "최소 면적(㎡)",
                 "move_in_by": "입주 마감일", "step_free_access": "무단차 접근 필수", "elevator": "엘리베이터 필수",
                 "noise_level": "소음 선호", "neighborhoods": "선호 동네"}
        def label(value):
            if value is None:
                return "미지정"
            if value is True:
                return "있음·가능 필수"
            if value is False:
                return "없음·불가 조건"
            if isinstance(value, list):
                return ", ".join(value) or "미지정"
            if isinstance(value, int):
                return f"{value:,}"
            return str(value)
        changes = []
        for section, fields in args["conditions"].items():
            for key, value in fields.items():
                changes.append(f"{names.get(key, key)}: {label(state['conditions'][section].get(key))} → {label(value)}")
        return "조건 변경을 확인하세요. " + " / ".join(changes)
    if tool == "record_inspection":
        names = {"step_free_access": "전체 이동 경로의 무단차 접근", "elevator": "엘리베이터", "entry_steps": "계단 수", "noise_level": "현장 소음"}
        value = {True: "가능·있음", False: "불가·없음"}.get(args["value"], str(args["value"])) if type(args["value"]) is bool else str(args["value"])
        return f"{args['listing_id']}의 {names[args['field']]} 결과를 '{value}'로 기록합니다. 직접 확인한 결과인지 검토하세요."
    if tool == "set_listing_status":
        label = {"available": "거래 가능", "contracted": "계약 완료", "verification_required": "확인 필요"}[args["status"]]
        return f"가상 매물 {args['listing_id']}의 상태를 '{label}'로 바꿉니다."
    if tool == "set_entry_time":
        return f"입주 가능 시각을 {args['new_end']}로 변경하고 후속 일정 충돌을 확인합니다."
    if tool == "apply_schedule":
        proposal = next((p for p in state["schedule_proposals"] if p["id"] == args["proposal_id"]), None)
        if proposal is None or proposal.get("based_on_revision") != state["revision"]:
            raise AgentError("STALE_PROPOSAL", "현재 상태의 일정 대안을 다시 계산해 주세요.", 409)
        return f"{proposal['summary']} — 내부 일정표에만 반영하며 업체 예약·연락은 하지 않습니다."
    return DESCRIPTIONS[tool]


def additional_inspection_requested(args, reviewed, message, listings):
    """A second observation needs a new target grounded in this user's request."""
    from app.dev_planner import inspection_fields

    listing_id, field = args["listing_id"], args["field"]
    if [listing_id, field] in reviewed:
        return False
    known_listings, known_fields = zip(*reviewed)
    if listing_id not in known_listings:
        name = next((row["name"] for row in listings if row["id"] == listing_id), "")
        named_id = re.search(rf"(?<![a-z0-9]){re.escape(listing_id)}(?![a-z0-9])", message, re.I)
        if not named_id and not (name and name in message):
            return False
    return field in known_fields or field in inspection_fields(message) or field in message.casefold()


class ChatService:
    def __init__(self, tool_service, settings):
        from app.dev_planner import next_decision
        self.tool_service = tool_service
        self.store = tool_service.store
        self.settings = settings
        self.planner = next_decision
        self.provider = None

    def public_status(self):
        configured = bool(self.settings.zoro_base_url and self.settings.zoro_model)
        return {"available": True, "default_mode": "development",
                "modes": {"development": {"available": True, "label": "개발용 규칙 모드"},
                          "zoro": {"available": configured, "configured": configured, "verified": False, "label": "Zoro 서버 모드"}},
                "tools": deepcopy(TOOLS), "limits": {"max_steps": MAX_STEPS, "max_requests_per_session": MAX_REQUESTS},
                "notice": "개발용 규칙 모드는 Zoro가 아닙니다. 실제 모델은 행사 서버와 호출 규격 확인 후 연결합니다. 추천 매물·업체 시간표는 가상 자료입니다."}

    def _sensitive(self, value, *, key_patterns=False):
        text = json.dumps(value, ensure_ascii=False, default=lambda _: "")
        secrets = (self.settings.molit_trade_api_key, self.settings.molit_rent_api_key,
                   self.settings.zoro_api_key, self.settings.zoro_base_url)
        if any(secret and len(secret) >= 8 and secret in text for secret in secrets):
            raise AgentError("SENSITIVE_INPUT", "인증키나 서버 비밀값은 상담에 넣지 마세요. 이번 내용을 저장하지 않았습니다.")
        if key_patterns and re.search(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])|\bsk-[A-Za-z0-9_-]{20,}", text):
            raise AgentError("SENSITIVE_INPUT", "인증키로 보이는 문자열이 있습니다. 로컬 키 설정 창을 사용해 주세요.")

    @staticmethod
    def _fingerprint(operation, body):
        value = {key: item for key, item in body.items() if key != "expected_revision"}
        return hashlib.sha256(json.dumps([operation, value], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _check(self, session_id, payload, operation):
        state = self.store.get(session_id)
        agent = metadata(state)
        fingerprint = self._fingerprint(operation, payload)
        replay = self._replay(state, payload["request_id"], fingerprint)
        if replay:
            return state, agent, fingerprint, True
        if type(payload["expected_revision"]) is not int or payload["expected_revision"] != state["revision"]:
            raise AgentError("REVISION_CONFLICT", "다른 작업으로 상담이 바뀌었습니다. 최신 상태를 불러와 주세요.", 409)
        # Reserve one final slot so a pending review at the boundary can finish.
        limit = MAX_REQUESTS if operation == "confirm" else MAX_REQUESTS - 1
        if len(agent["requests"]) >= limit:
            raise AgentError("CHAT_LIMIT", "이 상담의 대화 한도에 도달했습니다. 새 상담에서 계속해 주세요.", 409)
        return state, agent, fingerprint, False

    @staticmethod
    def _replay(state, request_id, fingerprint):
        previous = next((r for r in metadata(state)["requests"] if r["request_id"] == request_id), None)
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise AgentError("REQUEST_ID_REUSED", "이미 사용한 요청 번호의 내용이 다릅니다. 새 요청으로 보내주세요.", 409)
            return True
        return False

    def _mode(self, mode):
        if mode not in {"development", "zoro"}:
            raise AgentError("INVALID_MODE", "실행 모드를 확인해 주세요.")
        if mode == "zoro" and not (self.settings.zoro_base_url and self.settings.zoro_model):
            raise AgentError("ZORO_NOT_CONFIGURED", "Zoro 서버 주소와 모델 식별자를 설정한 뒤 다시 시도해 주세요. 개발용 모드로 자동 전환하지 않습니다.", 503)

    async def chat(self, session_id, payload, *, molit_fetcher=None):
        self._sensitive(payload["message"], key_patterns=True)
        state, agent, fingerprint, replay = self._check(session_id, payload, "chat")
        if replay:
            return state
        self._mode(payload["mode"])
        message = payload["message"].strip()
        if not message:
            raise AgentError("EMPTY_MESSAGE", "상담 내용을 입력해 주세요.")
        agent["pending"] = None
        return await self._turn(state, agent, payload, fingerprint, message, molit_fetcher=molit_fetcher)

    async def confirm(self, session_id, payload, *, molit_fetcher=None):
        state, agent, fingerprint, replay = self._check(session_id, payload, "confirm")
        if replay:
            return state
        pending = agent["pending"]
        if not pending or pending.get("id") != payload["pending_id"] or pending.get("created_revision") != state["revision"]:
            raise AgentError("STALE_CONFIRMATION", "이 확인 요청은 만료됐습니다. 최신 상태에서 다시 요청해 주세요.", 409)
        original = pending.get("message", "")
        mode, data_mode = pending["mode"], pending["data_mode"]
        if payload["approve"]:
            self._mode(mode)
        agent["pending"] = None
        body = {**payload, "mode": mode, "data_mode": data_mode}
        return await self._turn(state, agent, body, fingerprint, original,
                                molit_fetcher=molit_fetcher, approval=pending if payload["approve"] else False)

    async def _decide(self, mode, message, state, trace):
        if mode == "development":
            value = self.planner(message, deepcopy(state), deepcopy(trace))
            return await value if inspect.isawaitable(value) else value
        if self.provider is None:
            from app.zoro_provider import ZoroProvider
            self.provider = ZoroProvider(base_url=self.settings.zoro_base_url, model=self.settings.zoro_model,
                                         api_key=self.settings.zoro_api_key, timeout_seconds=self.settings.zoro_timeout_seconds)
        return await self.provider.decide(message, deepcopy(state), deepcopy(trace))

    async def _execute(self, working, decision, *, data_mode, molit_fetcher):
        from app.market_evidence import lookup_evidence
        tool, args = decision["tool"], decision["arguments"]
        svc = ToolService(working, {})
        sid, revision = working.state["session_id"], working.state["revision"]
        if tool == "update_conditions":
            merged = merge_conditions(working.state["conditions"], args["conditions"])
            svc.change_conditions(sid, revision, merged)
            return {"conditions": merged, "source": "user_confirmed"}, "확인한 필수·선호 조건을 저장했습니다."
        if tool == "search_candidates":
            result = svc.search(sid, revision)["search_result"]
            counts = {key: len(result[key]) for key in ("eligible", "needs_verification", "excluded")}
            obs = {"status": result["status"], **counts, "source": "synthetic", "missing_fields": result["missing_fields"]}
            return obs, f"가상 매물: 조건 충족 {counts['eligible']}개, 확인 필요 {counts['needs_verification']}개, 제외 {counts['excluded']}개입니다."
        if tool == "request_verification":
            saved = svc.request_verification(sid, revision, args["listing_id"], args["field"])
            task = next(t for t in saved["verification_tasks"] if t["listing_id"] == args["listing_id"] and t["field"] == args["field"] and t["status"] == "pending")
            return {key: task[key] for key in ("id", "listing_id", "field", "status", "question")}, f"{args['listing_id']}의 확인 요청을 준비했습니다. 아직 확인 완료가 아닙니다."
        if tool == "record_inspection":
            task = next((t for t in working.state["verification_tasks"] if t["listing_id"] == args["listing_id"] and t["field"] == args["field"] and t["status"] == "pending"), None)
            if task is None:
                saved = svc.request_verification(sid, revision, args["listing_id"], args["field"])
                task = next(t for t in saved["verification_tasks"] if t["listing_id"] == args["listing_id"] and t["field"] == args["field"] and t["status"] == "pending")
            saved = svc.confirm_inspection(sid, working.state["revision"], task["id"], args["value"], args.get("notes", ""))
            result = saved["search_result"] or {}
            buckets = {"eligible": "조건을 충족하는 후보", "needs_verification": "추가 확인이 필요한 후보", "excluded": "제외된 후보"}
            bucket = next((key for key in buckets if any(row["listing"]["id"] == args["listing_id"]
                           for row in result.get(key, []))), None)
            summary = f"{args['listing_id']}의 현장 확인 결과를 기록했어요."
            if bucket is not None:
                summary += f" 현재 조건으로 다시 확인한 결과, 이 매물은 {buckets[bucket]}예요."
            return {"listing_id": args["listing_id"], "field": args["field"], "value": args["value"],
                    "candidate_bucket": bucket, "source": "user_confirmed_demo"}, summary
        if tool == "set_listing_status":
            svc.change_status(sid, revision, args["listing_id"], args["status"])
            return {**args, "source": "synthetic"}, "가상 매물 상태를 변경하고 기존 후보를 재검증했습니다."
        if tool == "set_entry_time":
            schedule = svc.change_entry(sid, revision, args["new_end"])["schedule"]
            return {"conflicts": schedule["conflicts"], "events": schedule["events"], "source": "synthetic"}, f"입주 시간을 반영했습니다. 일정 충돌 {len(schedule['conflicts'])}개를 확인했습니다."
        if tool == "propose_schedule":
            proposals = svc.propose(sid, revision)["schedule_proposals"]
            return {"proposals": proposals, "source": "synthetic"}, f"주어진 가상 업체 시간표에서 일정 대안 {len(proposals)}개를 찾았습니다. 실제 예약은 하지 않았습니다."
        if tool == "apply_schedule":
            schedule = svc.apply_schedule(sid, revision, args["proposal_id"])["schedule"]
            return {"events": schedule["events"], "conflicts": schedule["conflicts"], "source": "synthetic"}, "선택한 대안을 내부 일정표에 반영했습니다. 업체 예약·연락은 하지 않았습니다."
        if tool == "lookup_transactions":
            result = await lookup_evidence(args, mode=data_mode, settings=self.settings, fetcher=molit_fetcher)
            return result, result["summary"]
        raise AgentError("INVALID_DECISION", "허용되지 않은 행동입니다.")

    @staticmethod
    def _safe_failure(exc):
        code = getattr(exc, "code", "")
        if isinstance(exc, ProviderError) and code in PROVIDER_CODES:
            return code, "Zoro 응답을 처리하지 못했습니다. 행동 기록에서 오류 코드를 확인해 주세요."
        if isinstance(exc, AgentError) and code == "REPEATED_ACTION":
            return code, "같은 행동이 반복되어 이번 요청의 변경을 반영하지 않았습니다."
        if code == "INVALID_DECISION":
            return code, "허용된 행동 형식을 확인하지 못해 이번 요청의 변경을 반영하지 않았습니다."
        if code == "SENSITIVE_INPUT":
            return code, "응답에 비밀값이 포함될 수 있어 저장하지 않았습니다."
        if isinstance(exc, (MolitError,)):
            return "MARKET_LOOKUP_FAILED", "실거래 조회를 완료하지 못했습니다. 예시 자료로 바꾸지 않았으며 이번 요청의 변경도 반영하지 않았습니다."
        if isinstance(exc, (DomainError, ScheduleError, StateError, AgentError)):
            return "TOOL_EXECUTION_FAILED", "현재 조건·확인 항목·일정 상태로 행동을 완료하지 못했습니다. 이번 요청의 변경은 반영하지 않았습니다."
        if isinstance(exc, TimeoutError):
            return "AGENT_TIMEOUT", "응답 대기시간을 초과했습니다. 이번 요청의 변경은 반영하지 않았습니다."
        return "AGENT_EXECUTION_FAILED", "에이전트 응답 또는 도구 실행을 완료하지 못했습니다. 이번 요청의 변경은 반영하지 않았습니다."

    async def _turn(self, state, agent, payload, fingerprint, message, *, molit_fetcher, approval=None):
        from app.dev_planner import only_record_requested

        working = WorkingStore(state)
        trace, seen = [], set()
        verified_search_revision = None
        reviewed_inspections = list(approval.get("reviewed_inspections", [])) if approval else []
        mode, data_mode = payload["mode"], payload["data_mode"]
        status, reply, zoro_used = "completed", "", False
        active_tool = None
        user_text = message if approval is None else "제안한 변경을 확인하고 반영합니다." if approval else "제안한 변경을 취소합니다."
        agent["messages"].append({"id": str(uuid4()), "role": "user", "text": user_text, "created_at": now()})
        try:
            async with asyncio.timeout(TURN_TIMEOUT_SECONDS):
                if approval is False:
                    reply = "제안한 변경을 취소했습니다. 상담 조건과 일정은 유지했습니다."
                else:
                    if approval:
                        decision = validate_decision({key: approval[key] for key in ("tool", "arguments")})
                        active_tool = decision["tool"]
                        self._sensitive(decision, key_patterns=True)
                        observation, summary = await self._execute(working, decision, data_mode=data_mode, molit_fetcher=molit_fetcher)
                        trace.append({**decision, "status": "executed", "summary": summary, "observation": observation})
                        if active_tool == "record_inspection":
                            reviewed_inspections.append([observation["listing_id"], observation["field"]])
                            if (observation["candidate_bucket"] and not working.state["search_stale"]
                                    and working.state["search_result"]["status"] == "ready"
                                    and only_record_requested(message, observation["listing_id"], observation["field"])):
                                # The approved fact and the reclassified candidate are
                                # in the same snapshot. The CAS below commits them together.
                                reply = summary
                    final_approval = bool(approval) and len(agent["requests"]) >= MAX_REQUESTS - 1
                    for _ in range(0 if final_approval else MAX_STEPS - len(trace)):
                        if reply:
                            break
                        active_tool = None
                        context = {**working.state, "agent": deepcopy(agent)}
                        # Manual tools and restored sessions also feed this context.
                        # The provider knows its own key, but not MOLIT credentials.
                        # Reject known configured secrets before any model request.
                        self._sensitive(context)
                        self._sensitive(trace)
                        raw = await self._decide(mode, message, context, trace)
                        zoro_used = zoro_used or mode == "zoro"
                        self._sensitive(raw, key_patterns=True)
                        decision = validate_decision(raw)
                        tool, args = decision["tool"], decision["arguments"]
                        active_tool = tool
                        # The user's click approves one saved fact. Keep the real
                        # result when the model asks to record it again or adds
                        # an observation that the user never supplied.
                        if tool == "record_inspection" and reviewed_inspections and not additional_inspection_requested(
                                args, reviewed_inspections, message, working.state["listings"]):
                            break
                        if tool == "apply_schedule":
                            if approval and approval["tool"] == tool and approval["arguments"] == args:
                                break
                            freshly_planned = any(item["tool"] == "propose_schedule" for item in trace)
                            if freshly_planned:
                                # A fresh user message can refer to an option already
                                # displayed. A reviewed mutation invalidates old options.
                                already_shown = not approval and any(
                                    p["id"] == args["proposal_id"] and p["based_on_revision"] == state["revision"]
                                    for p in state["schedule_proposals"]
                                )
                                if not already_shown:
                                    break
                        signature = json.dumps([tool, args], sort_keys=True, ensure_ascii=False)
                        if signature in seen:
                            # In a longer inspection request Zoro can ask for a
                            # separate search. A second lookup on the same verified
                            # snapshot can return the first result without running it again.
                            if tool == "search_candidates" and verified_search_revision == working.state["revision"]:
                                break
                            raise AgentError("REPEATED_ACTION", "같은 행동을 반복해 중단했습니다.")
                        seen.add(signature)
                        if tool == "ask_user":
                            decision["reason"] = "필요한 정보를 사용자에게 확인합니다."
                            reply, status = question_reply(args["question"]), "needs_input"
                            agent["last_question"] = args["question"]
                            trace.append({**decision, "status": "executed", "summary": "필요한 정보를 질문했습니다.", "observation": {}})
                            break
                        if tool == "finish":
                            decision["reason"] = "실제 실행 결과를 바탕으로 정리합니다."
                            trace.append({**decision, "status": "executed", "summary": "실행 결과를 정리했습니다.", "observation": {}})
                            break
                        if tool in CONFIRM_TOOLS:
                            # Check feasibility without persisting a proposed write.
                            await self._execute(WorkingStore(working.state), decision,
                                                data_mode=data_mode, molit_fetcher=molit_fetcher)
                            summary = review_summary(tool, args, working.state)
                            agent["pending"] = {"id": str(uuid4()), "tool": tool, "arguments": args, "summary": summary,
                                                "created_revision": state["revision"] + 1, "message": message,
                                                "mode": mode, "data_mode": data_mode}
                            if reviewed_inspections:
                                agent["pending"]["reviewed_inspections"] = reviewed_inspections
                            trace.append({**decision, "status": "pending_confirmation", "summary": summary, "observation": {}})
                            reply, status = confirmation_reply(summary), "awaiting_confirmation"
                            break
                        observation, summary = await self._execute(working, decision, data_mode=data_mode, molit_fetcher=molit_fetcher)
                        self._sensitive(observation)
                        trace.append({**decision, "status": "executed", "summary": summary, "observation": observation})
                        if (tool == "search_candidates" and approval and approval["tool"] == "record_inspection"
                                and observation["status"] == "ready"):
                            verified_search_revision = working.state["revision"]
                    else:
                        if final_approval:
                            reply = trace[-1]["summary"] + " 이 상담의 대화 한도에 도달했습니다. 새 상담에서 계속해 주세요."
                        else:
                            status = "limit_reached"
                            reply = "한 번에 실행할 수 있는 행동 수에 도달했습니다. 이번 요청의 변경은 반영하지 않았습니다."
                            working = WorkingStore(state)
                            agent["pending"] = None
                            for item in trace:
                                item["status"] = "rolled_back"
                                item["summary"] = "행동 한도로 이번 요청의 상태 변경을 반영하지 않았습니다."
        except Exception as exc:
            code, reply = self._safe_failure(exc)
            working = WorkingStore(state)
            agent["pending"] = None
            status = "error"
            for item in trace:
                item["status"] = "rolled_back"
                item["summary"] = "후속 처리 실패로 이번 요청의 상태 변경을 반영하지 않았습니다."
            observation = {"code": code}
            if isinstance(exc, ProviderError) and code in PROVIDER_CODES:
                observation.update(exc.diagnostics)
            elif active_tool is not None:
                observation["attempted_tool"] = active_tool
            trace.append({"tool": "error", "arguments": {}, "status": "error", "summary": reply, "observation": observation})
            trace = trace[-MAX_STEPS:]
        if not reply:
            reply = completed_reply(trace)
        self._sensitive(reply)
        self._sensitive(trace)
        if status != "needs_input":
            agent["last_question"] = None
        turn = {"id": str(uuid4()), "mode": mode, "data_mode": data_mode, "status": status,
                "zoro_used": zoro_used, "trace": trace, "reply": reply, "created_at": now()}
        agent["messages"].append({"id": str(uuid4()), "role": "assistant", "text": reply, "created_at": now()})
        agent["messages"] = agent["messages"][-60:]
        agent["turns"] = (agent["turns"] + [turn])[-20:]
        agent["requests"].append({"request_id": payload["request_id"], "fingerprint": fingerprint, "revision": state["revision"] + 1})
        agent["last_request_id"] = payload["request_id"]
        updated = deepcopy(working.state)
        # Conversation-only commits do not make an otherwise-current schedule stale.
        for proposal in updated["schedule_proposals"]:
            if proposal.get("based_on_revision") == updated["revision"]:
                proposal["based_on_revision"] = state["revision"] + 1
        current_proposals = {p["id"]: p for p in updated["schedule_proposals"]}
        for entry in trace:
            for proposal in entry.get("observation", {}).get("proposals", []):
                if proposal.get("id") in current_proposals:
                    proposal["based_on_revision"] = current_proposals[proposal["id"]]["based_on_revision"]
        old_audit_ids = {entry["id"] for entry in state["audit"]}
        for entry in updated["audit"]:
            if entry["id"] not in old_audit_ids:
                entry["revision"] = state["revision"] + 1
        updated["audit"].append({"id": str(uuid4()), "timestamp": now(), "action": "agent_turn",
                                 "summary": "대화 실행 결과를 저장했습니다. " + ("개발용 규칙 모드." if mode == "development" else "Zoro 서버 모드."),
                                 "revision": state["revision"] + 1})
        updated["audit"] = updated["audit"][-200:]
        updated["agent"] = agent
        updated["execution_mode"] = "agent_tools"
        updated["zoro_used"] = zoro_used
        try:
            return self.store.mutate(state["session_id"], state["revision"], lambda _: updated)
        except StateError as exc:
            if exc.code == "REVISION_CONFLICT":
                fresh = self.store.get(state["session_id"])
                if self._replay(fresh, payload["request_id"], fingerprint):
                    return fresh
            raise
