"""Short Korean counseling copy grounded only in completed tool observations.

No model prose, listing descriptions, or hidden reasoning become an assertion.
This presentation layer never executes tools or changes business state.
"""


def question_reply(question: str) -> str:
    # Keep the original question separately in last_question: development-mode
    # followups depend on its wording. Only the displayed message is decorated.
    if question.startswith(("안녕하세요", "네,", "요청을 조금만")):
        return question
    return "확인할 내용이 있어요.\n\n" + question


def confirmation_reply(summary: str) -> str:
    return ("말씀하신 변경을 먼저 확인할게요.\n\n" + summary
            + "\n\n내용이 맞으면 아래 ‘변경 승인 · 계속’을 눌러 주세요.")


def completed_reply(trace: list[dict]) -> str:
    paragraphs = []
    next_step = ""
    for entry in trace:
        if entry.get("status") != "executed":
            continue
        tool = entry.get("tool")
        obs = entry.get("observation", {})
        if tool in {"finish", "ask_user"}:
            continue
        if tool == "update_conditions":
            paragraphs.append("확인해 주신 조건을 저장했어요. 이 조건을 기준으로 상담을 이어갈게요.")
        elif tool == "search_candidates":
            eligible, unknown, excluded = (obs[key] for key in ("eligible", "needs_verification", "excluded"))
            if eligible:
                text = f"시연용 가상 매물을 살펴보니, 입력한 필수 조건을 충족하는 후보는 {eligible}개예요."
            else:
                text = "시연용 가상 매물을 살펴봤지만, 입력한 필수 조건을 충족한다고 확인된 후보는 없어요."
            text += f" 추가 확인이 필요한 후보는 {unknown}개, 제외된 후보는 {excluded}개예요."
            if unknown:
                text += " 미확인 후보는 필요한 항목을 확인한 뒤 판단할 수 있어요."
                next_step = "‘후보 요약’에서 미확인 항목을 살펴보세요. 현장 확인이 필요하면 ‘L001 무단차 확인 요청’처럼 매물 번호와 항목을 알려 주세요."
            elif eligible:
                next_step = "‘후보 요약’에서 조건과 제외 사유를 살펴보세요. 조건을 바꾸고 싶으면 ‘보증금 9억 이하’처럼 새 값을 알려 주세요."
            else:
                next_step = "검색을 위해 필수 조건을 임의로 바꾸지는 않았어요. 조건 변경을 원하시면 바꿀 항목과 값을 직접 알려 주세요."
            paragraphs.append(text)
        elif tool == "request_verification":
            # Keep the server-authored ID and the explicit unverified status.
            paragraphs.append(entry["summary"])
        elif tool == "record_inspection":
            paragraphs.append("직접 확인해 주신 현장 결과를 반영하고, 기존 후보가 조건에 맞는지 다시 확인했어요.")
        elif tool == "set_listing_status":
            paragraphs.append("가상 매물의 변경된 거래 상태를 반영하고, 기존 후보를 다시 확인했어요.")
        elif tool == "set_entry_time":
            count = len(obs["conflicts"])
            paragraphs.append(f"입주 가능 시각을 변경했어요. 현재 내부 일정에서 확인된 충돌은 {count}개예요.")
            next_step = "새로운 청소·이사 일정이 필요하면 ‘일정을 다시 제안해줘’라고 말씀해 주세요."
        elif tool == "propose_schedule":
            count = len(obs["proposals"])
            if count:
                paragraphs.append(f"가상 업체 시간표에서 가능한 일정 대안 {count}개를 찾았어요. 대안은 아직 내부 일정표에 적용하지 않았고, 실제 업체 예약도 하지 않았어요.")
                next_step = "‘입주 · 청소 · 이사’에서 대안을 살펴보고 ‘첫 번째 일정안 적용해줘’처럼 선택해 주세요. 반영 전에 한 번 더 확인할게요."
            else:
                paragraphs.append("현재 가상 업체 시간표에서는 가능한 일정 대안을 찾지 못했어요. 실제 업체 예약은 하지 않았어요.")
                next_step = "입주 가능 날짜와 시간을 다시 확인해 주세요. 시간이 바뀌면 알려 주신 값으로 대안을 다시 계산할 수 있어요."
        elif tool == "apply_schedule":
            paragraphs.append("선택하신 대안을 내부 일정표에 반영했어요. 실제 업체 예약이나 연락은 하지 않았어요.")
            next_step = "변경된 입주·청소·이사 시간을 확인해 주세요. 입주 시간이 또 바뀌면 말씀해 주세요."
        elif tool == "lookup_transactions":
            # Retain scope, source and counts from the validated evidence layer.
            # A zero in a partial scan must never become 'no contracts exist'.
            paragraphs.append("거래 기록을 확인했어요. " + entry["summary"])
            paragraphs.append("이 자료는 현재 나온 매물이나 확정 시세를 뜻하지 않아요. 해제 여부를 확인할 수 없는 기록도 있어 계약 유효성을 단정하지 않아요.")
            next_step = "다른 계약월도 살펴보려면 ‘2026년 10월 전월세 실거래 조회’처럼 종류와 연월을 함께 알려 주세요."
        else:
            # Only trusted tool summaries, never decision.reason, are displayed.
            paragraphs.append(entry["summary"])
    if not paragraphs:
        return "어떤 부분을 함께 확인해 볼까요? 전세 조건, 매물 확인 결과, 입주 일정 중 필요한 내용을 알려 주세요."
    if next_step:
        paragraphs.append(next_step)
    return "\n\n".join(paragraphs)
