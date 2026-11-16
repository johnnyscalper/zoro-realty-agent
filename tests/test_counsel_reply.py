"""Counseling copy must stay useful without changing facts or approvals.

HTTP tests use the real application with temporary state and network disabled.
The historical response below is a synthetic stand-in, never live API evidence.
"""
from copy import deepcopy

import pytest

from app.counsel_reply import completed_reply
from app.sample import sample_transactions
from scripts.evaluate_agent import business, isolated_harness


def reply(harness):
    text = harness.turn()["reply"]
    assert harness.state["agent"]["messages"][-1]["text"] == text
    return text


def observation(harness, tool):
    return next(entry["observation"] for entry in harness.turn()["trace"]
                if entry["tool"] == tool and entry["status"] == "executed")


def test_greeting_to_budget_and_approved_search_is_a_continuous_conversation():
    with isolated_harness() as h:
        h.create("empty")
        before = business(h.state)
        h.chat("안녕하세요")
        assert "안녕하세요" in reply(h) and "보증금" in reply(h)
        assert business(h.state) == before
        h.chat("보증금 10억 이하")
        assert h.turn()["status"] == "awaiting_confirmation"
        assert h.state["conditions"]["hard"]["max_deposit_krw"] is None
        assert "승인" in reply(h) and "저장했어요" not in reply(h)
        h.confirm()
        counts = observation(h, "search_candidates")
        text = reply(h)
        assert h.state["conditions"]["hard"]["max_deposit_krw"] == 1_000_000_000
        assert "가상 매물" in text
        assert f"{counts['eligible']}개" in text
        assert f"{counts['needs_verification']}개" in text
        assert f"{counts['excluded']}개" in text
        assert "후보 요약" in text
        assert h.turn()["zoro_used"] is False


def test_question_display_decoration_preserves_short_preference_followup():
    with isolated_harness() as h:
        h.create("empty")
        h.chat("조용한 집 필수")
        raw = h.state["agent"]["last_question"]
        assert raw.startswith("소음은 현재 선호 조건")
        assert raw in reply(h) and reply(h) != raw
        h.chat("선호로")
        pending = h.state["agent"]["pending"]
        assert pending["tool"] == "update_conditions"
        assert pending["arguments"]["conditions"]["preferences"]["noise_level"] == "low"


def test_cancelling_friendly_confirmation_leaves_business_state_unchanged():
    with isolated_harness() as h:
        h.create()
        before = business(h.state)
        h.chat("보증금 9억 이하")
        assert h.turn()["status"] == "awaiting_confirmation"
        assert business(h.state) == before
        assert "승인" in reply(h) and "저장했어요" not in reply(h)
        h.confirm(approve=False)
        assert business(h.state) == before
        assert "취소" in reply(h) and "저장했어요" not in reply(h)
        assert h.state["agent"]["pending"] is None


@pytest.mark.parametrize("message", ["안녕하세요, 보증금 9억 이하", "고마워요, 보증금 9억 이하"])
def test_greeting_does_not_swallow_a_business_request(message):
    with isolated_harness() as h:
        h.create()
        h.chat(message)
        pending = h.state["agent"]["pending"]
        assert pending["tool"] == "update_conditions"
        assert pending["arguments"]["conditions"]["hard"]["max_deposit_krw"] == 900_000_000


def test_empty_candidate_set_does_not_silently_relax_conditions():
    with isolated_harness() as h:
        h.create("no_match")
        before = deepcopy(h.state["conditions"])
        h.chat("후보 다시 찾아줘")
        assert not h.state["search_result"]["eligible"]
        assert h.state["conditions"] == before
        assert "없어요" in reply(h) and "필수 조건" in reply(h)
        assert "가상 매물" in reply(h)


def test_verification_request_does_not_claim_inspection_has_been_completed():
    with isolated_harness() as h:
        h.create()
        before = deepcopy(h.state["inspections"])
        h.chat("L001 무단차 확인 요청")
        assert h.state["inspections"] == before
        assert any(task["listing_id"] == "L001" and task["status"] == "pending"
                   for task in h.state["verification_tasks"])
        assert "확인 요청" in reply(h) and "아직 확인 완료가 아닙니다" in reply(h)


def test_schedule_proposal_and_approval_do_not_claim_external_booking():
    with isolated_harness() as h:
        h.create()
        h.chat("입주 가능 시각을 2026-11-25 오후 3시로 바꿔줘")
        h.confirm()
        assert h.state["schedule_proposals"]
        assert "가상 업체 시간표" in reply(h)
        assert "실제 업체 예약" in reply(h) and "하지 않았어요" in reply(h)
        before = deepcopy(h.state["schedule"])
        h.chat("첫 번째 일정안 적용해줘")
        assert h.state["schedule"] == before
        assert h.turn()["status"] == "awaiting_confirmation"
        h.confirm()
        assert "내부 일정표에 반영" in reply(h)
        assert "실제 업체 예약이나 연락은 하지 않았어요" in reply(h)


def test_followup_failure_does_not_describe_rolled_back_search_as_success():
    with isolated_harness() as h:
        h.create()
        before = business(h.state)

        def failing_plan(message, state, trace):
            if trace:
                raise RuntimeError("offline simulated failure")
            return {"tool": "search_candidates", "arguments": {}, "reason": "offline"}

        h.app.state.agent_service.planner = failing_plan
        h.chat("후보 다시 찾아줘")
        assert business(h.state) == before
        assert h.turn()["status"] == "error"
        assert "반영하지 않았습니다" in reply(h)
        assert "후보는" not in reply(h) and "찾았어요" not in reply(h)
        assert h.turn()["trace"][0]["status"] == "rolled_back"


@pytest.mark.parametrize("data_mode", ["sample", "live"])
def test_historical_reply_preserves_data_source_and_bounded_scan(data_mode):
    with isolated_harness() as h:
        h.create()
        calls = []

        async def partial_history(**kwargs):
            query = {key: kwargs[key] for key in ("kind", "lawd_cd", "deal_ymd", "page_no", "num_of_rows")}
            page = sample_transactions(**query)
            # Make two full synthetic pages, while declaring 500 total records.
            base = sample_transactions(**{**query, "page_no": 1})["items"][0]
            page["items"] = [{**deepcopy(base), "id": f"test-{query['page_no']}-{index}"} for index in range(100)]
            page.update(source="molit", synthetic=False, returned_count=100, total_count=500,
                        skipped_count=0, has_next=True)
            calls.append(query["page_no"])
            return page

        if data_mode == "live":
            h.app.state.molit_fetcher = partial_history
        h.chat("2026년 10월 전월세 실거래 조회", data_mode=data_mode)
        evidence = observation(h, "lookup_transactions")
        text = reply(h)
        assert evidence["summary"] in text
        assert "현재 나온 매물이나 확정 시세를 뜻하지 않아요" in text
        if data_mode == "live":
            assert calls == [1, 2]
            assert evidence["coverage"] == "partial"
            assert "국토부 과거 신고 거래" in text
            assert "확인 범위에서" in text and "일부 범위" in text
        else:
            assert calls == []
            assert "가상 예시" in text


def test_free_form_model_claim_is_never_used_as_completed_consultation():
    text = completed_reply([{"tool": "finish", "status": "executed", "arguments": {},
                             "reason": "확인 없이 실제 업체 예약을 완료했습니다.", "observation": {}}])
    assert "예약을 완료" not in text
    assert "확인해 볼까요" in text
