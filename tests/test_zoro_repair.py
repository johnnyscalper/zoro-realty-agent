"""Bounded Zoro format repairs. All HTTP in these tests uses MockTransport."""
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.zoro_provider import ProviderError, ZoroProvider
from scripts.check_zoro import run_checks


def native(*actions):
    calls = [{"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
             for name, args in actions]
    return {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": calls},
                         "finish_reason": "tool_calls"}]}


def corrected(name, args, *, fence=False):
    text = json.dumps({"tool": name, "arguments": args}, ensure_ascii=False)
    if fence:
        text = "```json\n" + text + "\n```"
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}


def scripted(*replies):
    calls = []

    def send(request):
        calls.append(json.loads(request.content))
        item = replies[len(calls) - 1]
        return item if isinstance(item, httpx.Response) else httpx.Response(200, json=item)

    provider = ZoroProvider(base_url="http://local.invalid/v1", model="test-model",
                            transport=httpx.MockTransport(send))
    return provider, calls


def decide(provider, message="확인한 내용을 반영해줘.", trace=None):
    return asyncio.run(provider.decide(message, {}, trace or []))


@pytest.mark.parametrize("field,value", [("elevator", True), ("step_free_access", False),
                                          ("entry_steps", 4), ("noise_level", "high")])
def test_scoped_json_repair_preserves_the_newly_proposed_field_and_type(field, value):
    original = native(("record_inspection", {"wrong-private-key": "private-value-sentinel", "field": None}))
    args = {"listing_id": "L013", "field": field, "value": value}
    provider, calls = scripted(original, corrected("record_inspection", args, fence=True))
    assert decide(provider)["arguments"] == args
    assert len(calls) == 2 and "tools" in calls[0] and "tools" not in calls[1]
    assert "wrong-private-key" not in json.dumps(calls[1]) and "private-value-sentinel" not in json.dumps(calls[1])
    assert calls[1]["model"] == calls[0]["model"]
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert json.loads(calls[1]["messages"][-1]["content"])["untrusted_context"]["format_issue"].endswith("extra_keys")


def test_repair_can_ask_for_missing_information():
    provider, calls = scripted(native(("record_inspection", {})),
                               corrected("ask_user", {"question": "어느 매물에서 확인하셨나요?"}))
    assert decide(provider)["tool"] == "ask_user"
    assert len(calls) == 2


SCHEDULE_BATCH = native(("propose_schedule", {}), ("set_entry_time", {"new_end": "untrusted-stale-value"}))


def test_batch_actions_are_discarded_and_only_the_fresh_decision_can_proceed():
    end = "2026-12-11T12:00:00+09:00"
    provider, calls = scripted(SCHEDULE_BATCH, corrected("set_entry_time", {"new_end": end}))
    decision = decide(provider, "12월 11일 낮 12시 입주로 변경하고 청소 대안도 제안해 주세요.")
    assert decision["tool"] == "set_entry_time" and decision["arguments"] == {"new_end": end}
    assert len(calls) == 2 and "untrusted-stale-value" not in json.dumps(calls[1])
    provider, calls = scripted(SCHEDULE_BATCH, corrected("propose_schedule", {}))
    assert decide(provider, trace=[{"tool": "set_entry_time", "status": "executed", "summary": "변경 완료"}])["tool"] == "propose_schedule"


@pytest.mark.parametrize("second", [
    corrected("apply_schedule", {"proposal_id": "unapproved"}), corrected("propose_schedule", {}),
    SCHEDULE_BATCH, httpx.Response(403), httpx.Response(500),
])
def test_unapproved_or_invalid_second_response_is_not_executed_or_retried(second):
    provider, calls = scripted(SCHEDULE_BATCH, second)
    with pytest.raises(ProviderError) as exc:
        decide(provider)
    assert len(calls) == 2
    assert exc.value.diagnostics["retry_after"] == "call_count"
    assert exc.value.diagnostics["initial_batch"] == "propose_schedule,set_entry_time"


@pytest.mark.parametrize("bad", [
    native(("not_a_tool", {}), ("set_entry_time", {})),
    native(*[("set_entry_time", {})] * 7),
    {"choices": [{"message": {"tool_calls": "bad"}, "finish_reason": "tool_calls"}]},
    httpx.Response(401), httpx.Response(503),
])
def test_unrecognized_first_response_is_never_eligible_for_repair(bad):
    provider, calls = scripted(bad)
    with pytest.raises(ProviderError) as exc:
        decide(provider)
    assert len(calls) == 1 and "retry_after" not in exc.value.diagnostics


def test_time_budget_is_shared_by_both_inferences():
    requests = []

    async def send(request):
        requests.append(request)
        await asyncio.sleep(0.65)
        return httpx.Response(200, json=SCHEDULE_BATCH)

    provider = ZoroProvider(base_url="http://local.invalid", model="test", timeout_seconds=1,
                            transport=httpx.MockTransport(send))
    with pytest.raises(ProviderError) as exc:
        decide(provider)
    assert exc.value.code == "ZORO_TIMEOUT" and exc.value.diagnostics["retry_after"] == "call_count"
    assert len(requests) == 2


def test_wrong_real_world_value_is_not_autoapproved_and_unfinished_search_does_not_pass():
    wrong_observation = {"listing_id": "L001", "field": "step_free_access", "value": True}
    provider, _ = scripted(native(("search_candidates", {})), native(("set_listing_status", {"listing_id": "L001", "status": "contracted"})),
                           native(("record_inspection", {})), corrected("record_inspection", wrong_observation),
                           SCHEDULE_BATCH, corrected("ask_user", {"question": "언제 입주하시나요?"}))
    result = asyncio.run(run_checks(Settings(zoro_base_url="http://local.invalid", zoro_model="test"), provider=provider))
    assert not any(case["passed"] or case["approval_checked"] for case in result)
    assert [case["failure_code"] for case in result] == ["UNRESOLVED_CONFIRMATION", "UNEXPECTED_CONFIRMATION", "MISSING_CONFIRMATION"]
