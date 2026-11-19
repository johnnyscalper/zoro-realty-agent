"""Bounded Korean command rules for the explicitly labelled development mode.

This module is not a model, does not claim general language understanding and
does not execute tools. Every returned mutation still requires the caller's
review/Confirm API. Only the supplied message, task state and action trace are
read; importing this module never reads configuration, credentials or files.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re


_NEIGHBORHOODS = (
    "개포동", "논현동", "대치동", "도곡동", "삼성동", "세곡동", "수서동",
    "신사동", "압구정동", "역삼동", "율현동", "일원동", "자곡동", "청담동",
)
_ACCESS = r"무단차|무계단|휠체어|계단\s*없(?:는|이)|계단\s*없이"
_ELEVATOR = r"엘리베이터|승강기"
_AREA_UNIT = r"(?:㎡|m²|m2|제곱미터)"
_NUMBER = r"[0-9]+(?:\.[0-9]+)?"
_MONEY = re.compile(
    r"(?<![A-Za-z\d.\-,])(?:\d[\d,]*(?:\.\d+)?\s*억(?:\s*\d[\d,]*(?:\.\d+)?\s*(?:천\s*)?만)?"
    r"|\d[\d,]*(?:\.\d+)?\s*천\s*만|\d[\d,]*(?:\.\d+)?\s*만"
    r"|\d[\d,]*\s*원)(?:\s*원)?"
)
_EXAMPLES = (
    "요청을 조금만 구체적으로 알려주실래요? 개발용 규칙 모드에서는 다음처럼 말씀해 주세요. "
    "예: '보증금 10억 이하, 75㎡ 이상, 무단차 필수', "
    "'L001 무단차 확인 요청', '일정을 다시 제안해줘', "
    "'2026년 10월 전월세 실거래 조회'."
)


def _decision(tool: str, arguments: dict | None = None, reason: str = "") -> dict:
    return {"tool": tool, "arguments": arguments or {}, "reason": reason}


def _ask(question: str) -> dict:
    return _decision("ask_user", {"question": question}, "해석이 불명확한 부분을 먼저 확인합니다.")


def _finish() -> dict:
    return _decision("finish", reason="실행된 도구의 관측 결과를 정리합니다.")


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _question(state: dict) -> str:
    agent = state.get("agent", {})
    question = agent.get("last_question", "") if isinstance(agent, dict) else ""
    if isinstance(question, dict):
        question = question.get("question", question.get("text", ""))
    return question if isinstance(question, str) else ""


def _followup(message: str, state: dict) -> str:
    """Reuse only a narrow, explicitly pending question, never arbitrary history."""
    question = _question(state)
    if not question or len(message) > 100:
        return message
    agent = state.get("agent", {})
    messages = agent.get("messages", []) if isinstance(agent, dict) else []
    previous = ""
    for row in reversed(messages[-12:]):
        if isinstance(row, dict) and row.get("role") == "user":
            text = row.get("text", "")
            if isinstance(text, str) and text.strip() != message:
                previous = text.strip()
                break
    # No state is reconstructed from assistant prose or listing descriptions.
    if "실거래" in question and previous and _has(r"실거래|거래\s*내역", previous):
        if _has(r"조회할 계약 연월", question) and re.fullmatch(
            r"(?:\d{4}년\s*\d{1,2}월|\d{4}[-/]\d{1,2}|\d{6})(?:이야|이요|요)?[.!]?", message
        ):
            return previous + " " + message
        if "매매 또는 전월세" in question and re.fullmatch(r"(?:매매|전월세|전세|월세)(?:야|요|로)?[.!]?", message):
            return previous + " " + message
    prefers = re.fullmatch(r"(?:선호|선호로)(?:\s*(?:해줘|저장해줘|반영해줘|할게|할래|좋아))?[.!]?", message)
    if previous and prefers:
        if question.startswith("소음은 현재 선호 조건"):
            for level, pattern in (("낮음", r"조용|낮음|낮은|적은"), ("보통", r"보통|중간"), ("높음", r"높음|높은|시끄러")):
                if _has(pattern, previous):
                    return f"소음 {level} 선호"
        if question.startswith("동네는 현재 선호 조건"):
            neighborhoods = [name for name in _NEIGHBORHOODS if name in previous]
            if neighborhoods:
                return " ".join(neighborhoods) + " 선호"
    for label in ("무단차", "엘리베이터"):
        if question.startswith(f"{label}는 현재 필수 조건"):
            if re.fullmatch(r"필수(?:야|로|로\s*해줘|로\s*저장해줘)?[.!]?", message):
                return label + " 필수"
            if re.fullmatch(r"(?:조건\s*)?해제(?:해줘)?[.!]?", message):
                return label + " 조건 해제"
    if previous and question.startswith("현장에서 확인한 값을 명시") and re.fullmatch(
        r"(?:가능|불가능|불가|있음|없음|낮음|보통|높음|\d+\s*개)(?:이야|으로|로|요)?[.!]?", message
    ):
        return previous + " " + message
    if previous and question.startswith("오전·오후 또는 24시간제") and re.fullmatch(
        r"(?:오전|오후)\s*\d{1,2}시(?:\s*\d{1,2}분|\s*반)?(?:로|야|요)?[.!]?", message
    ):
        dates = re.findall(r"\d{4}-\d{2}-\d{2}|\d{4}년\s*\d{1,2}월\s*\d{1,2}일", previous)
        return "입주 가능 시각 " + (dates[0] + " " if len(dates) == 1 else "") + message
    return message


def _budget_missing(state: dict) -> bool:
    return state.get("conditions", {}).get("hard", {}).get("max_deposit_krw") is None


def _search(state: dict) -> dict:
    if _budget_missing(state):
        return _ask("최대 보증금 예산을 원 단위 또는 억·만원 단위로 알려 주세요. 예: '보증금 10억 이하'.")
    return _decision("search_candidates", reason="현재 필수 조건과 선호 조건으로 후보를 다시 검사합니다.")


def _trace_followup(state: dict, trace: list[dict], message: str) -> dict | None:
    if not trace:
        return None
    if any(row.get("status") == "error" for row in trace):
        return _ask("도구 실행에 실패했습니다. 표시된 오류를 확인한 뒤 요청을 다시 입력해 주세요. 조건은 자동으로 완화하지 않습니다.")
    if any(row.get("status") == "pending_confirmation" for row in trace):
        return _ask("변경 검토 카드의 확인 또는 취소 버튼으로 결정해 주세요. 채팅의 동의 표현만으로 변경을 실행하지 않습니다.")
    executed = [row for row in trace if row.get("status") == "executed"]
    if not executed:
        return _finish()
    names = [row.get("tool") for row in executed]
    if names[-1] in {"finish", "ask_user", "apply_schedule", "lookup_transactions", "propose_schedule"}:
        return _finish()
    # A confirmed mutation is already present in the trace. Never parse it into
    # the same mutation again, even when the caller reuses its original message.
    if "set_entry_time" in names:
        return _decision("propose_schedule", reason="변경된 입주 가능 시각을 기준으로 청소·이사 대안을 계산합니다.")
    mutations = {"update_conditions", "record_inspection", "set_listing_status"}
    last_mutation = max((i for i, name in enumerate(names) if name in mutations), default=-1)
    last_search = max((i for i, name in enumerate(names) if name == "search_candidates"), default=-1)
    if last_mutation > last_search:
        return _search(state)
    if last_search >= 0:
        result = state.get("search_result")
        if not isinstance(result, dict):
            observation = executed[last_search].get("observation", {})
            if isinstance(observation, dict):
                result = observation.get("search_result", observation)
        result = result if isinstance(result, dict) else {}
        if result.get("status") == "incomplete" or _budget_missing(state):
            return _search(state)
        requested = {
            (row.get("arguments", {}).get("listing_id"), row.get("arguments", {}).get("field"))
            for row in executed if row.get("tool") == "request_verification"
        }
        pending = {
            (row.get("listing_id"), row.get("field"))
            for row in state.get("verification_tasks", [])
            if isinstance(row, dict) and row.get("status") == "pending"
        }
        hard = state.get("conditions", {}).get("hard", {})
        if len(requested) < 2:
            for candidate in result.get("needs_verification", []):
                if not isinstance(candidate, dict):
                    continue
                listing = candidate.get("listing", {})
                if not isinstance(listing, dict) or listing.get("status") == "contracted":
                    continue
                for field in ("step_free_access", "elevator"):
                    pair = (listing.get("id"), field)
                    if (pair[0] and hard.get(field) is not None and listing.get(field) is None
                            and pair not in requested and pair not in pending):
                        return _decision("request_verification", {"listing_id": pair[0], "field": field},
                                         "필수 접근성 항목의 미확인 값을 현장 확인 대상으로 남깁니다.")
        if _has(r"일정|재계획|청소.*이사", message):
            return _decision("propose_schedule", reason="현재 상태에서 가능한 내부 일정 대안을 계산합니다.")
        return _finish()
    return _finish()


def _money_value(text: str) -> int | None:
    if any(
        "," in number and re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", number) is None
        for number in re.findall(r"\d[\d,]*(?:\.\d+)?", text)
    ):
        return None
    compact = re.sub(r"[\s,원]", "", text)
    try:
        total = Decimal(0)
        if "억" in compact:
            head, compact = compact.split("억", 1)
            total += Decimal(head) * 100_000_000
        if compact:
            if compact.endswith("천만"):
                total += Decimal(compact[:-2]) * 10_000_000
            elif compact.endswith("만"):
                total += Decimal(compact[:-1]) * 10_000
            else:
                total += Decimal(compact)
        if total <= 0 or total != total.to_integral_value() or total > 10**15:
            return None
        return int(total)
    except (InvalidOperation, ValueError):
        return None


def _clause(text: str, position: int) -> str:
    """Return local clause to avoid applying one requirement's polarity to all."""
    start = max(text.rfind(mark, 0, position) for mark in (",", ";", "\n", ". ")) + 1
    ends = [index for mark in (",", ";", "\n", ". ") if (index := text.find(mark, position)) >= 0]
    return text[start:min(ends) if ends else len(text)]


def _conditions(message: str) -> dict | None:
    hard: dict = {}
    preferences: dict = {}
    if _has(r"없어도\s*안\s*(?:돼|되)|아니어도\s*안\s*(?:돼|되)|필수\s*아니면\s*안", message):
        return _ask("부정 표현이 겹쳐 필수 여부가 불명확합니다. '무단차 필수' 또는 '무단차 조건 해제'처럼 원하는 조건을 직접 알려 주세요.")
    if _has(r"매매\s*(?:가|예산|가격|\d)|월세\s*(?:가격|예산|\d)", message):
        return _ask("현재 가상 후보 검색의 예산은 최대 보증금 기준입니다. 매매가나 월세를 보증금으로 바꾸어 해석하지 않습니다.")
    if _has(r"주차|반려동물|반려견|반려묘|역세권|학군|남향|북향|베란다|테라스|햇빛|일조|방\s*\d|계단\s*\d+\s*개\s*(?:이하|필수)|\d+층\s*(?:이하|이상|필수)", message):
        return _ask("요청에 현재 검색 조건으로 지원하지 않는 항목이 있습니다. 최대 보증금·최소 전용 면적·입주 마감일·무단차·엘리베이터와 소음·동네 선호를 지정해 주세요. 다른 필수 조건은 자동으로 무시하지 않습니다.")
    matches = list(_MONEY.finditer(message))
    if len(matches) > 1:
        return _ask("최대 보증금 금액을 하나로 정해 주세요. 예: '보증금 9억 5천만원 이하'.")
    if matches:
        match = matches[0]
        value = _money_value(match.group())
        if re.match(r"\s*\d+(?:[.,]\d+)?\s*(?:백|천|십)", message[match.end():]):
            return _ask("금액 일부를 생략하지 않고 만원 또는 원 단위로 알려 주세요. 예: '보증금 90500만원'.")
        if (value is None or _has(r"(?:억|만원?|원)\s*(?:이상|초과|미만)|[~～]|에서\s*$", message[match.start():])
                or _has(r"(?:최소|하한)\s*(?:보증금\s*)?$", message[:match.start()])):
            return _ask("최대 보증금 상한을 하나의 금액으로 알려 주세요. 예: '보증금 10억 이하'.")
        amount_tail = re.split(r",|;|\n|면적|입주|무단차|무계단|엘리베이터|소음|조용", message[match.start():], maxsplit=1)[0]
        if _has(r"(?:억|만원?|원)\s*(?:정도|쯤|가량|내외)|선호|있으면\s*좋", amount_tail):
            return _ask("검색에 적용할 필수 최대 보증금을 확정해 주세요. 예: '최대 보증금 10억'.")
        hard["max_deposit_krw"] = value
    elif _has(r"(?:예산|보증금).*(?:해제|삭제|초기화)", message):
        hard["max_deposit_krw"] = None
    elif _has(r"예산|보증금", message):
        return _ask("최대 보증금 금액과 단위를 알려 주세요. 예: '보증금 10억 이하' 또는 '보증금 95000만원'.")

    area_matches = list(re.finditer(rf"(?<![\d.\-])({_NUMBER})\s*{_AREA_UNIT}", message, re.IGNORECASE))
    if _has(rf"-\s*{_NUMBER}\s*{_AREA_UNIT}", message):
        return _ask("최소 전용 면적은 0보다 큰 ㎡ 값으로 알려 주세요.")
    if _has(r"\d\s*평", message):
        return _ask("전용 면적 기준의 최소 ㎡ 값을 알려 주세요. 평수만으로 전용·공급 면적을 추정하지 않습니다.")
    if len(area_matches) > 1:
        return _ask("필수 최소 전용 면적을 하나의 ㎡ 값으로 알려 주세요.")
    if area_matches:
        match = area_matches[0]
        value = float(match.group(1))
        nearby = re.split(r",|;|\n|입주|무단차|무계단|엘리베이터|소음|조용|동네", message[match.start():], maxsplit=1)[0]
        if value <= 0 or value > 100_000 or _has(rf"{_AREA_UNIT}\s*(?:이하|미만|초과)|[~～]|선호|있으면\s*좋", nearby):
            return _ask("현재 면적 조건은 필수 최소 면적만 지원합니다. 최소 전용 면적을 ㎡로 지정해 주세요.")
        hard["min_area_m2"] = value
    elif _has(r"면적.*(?:해제|삭제|상관없|상관\s*없)", message):
        hard["min_area_m2"] = None

    if _has(r"입주", message):
        found = re.findall(r"(?<!\d)(\d{4})[-년]\s*(\d{1,2})[-월]\s*(\d{1,2})(?:일)?(?!\d)", message)
        if len(found) > 1:
            return _ask("입주 마감일을 YYYY-MM-DD 형식의 날짜 하나로 알려 주세요.")
        if found:
            if _has(r"(?:\d{2}|일)\s*(?:이후|부터|넘어서)", message):
                return _ask("현재 입주 조건은 입주 마감일까지 가능한 매물을 찾는 조건입니다. 가장 늦게 입주할 수 있는 마감일을 지정해 주세요.")
            try:
                year, month, day = map(int, found[0])
                hard["move_in_by"] = date(year, month, day).isoformat()
            except ValueError:
                return _ask("입주 마감일을 실제 존재하는 YYYY-MM-DD 날짜로 알려 주세요.")
        elif _has(r"입주.*(?:마감|날짜|조건).*(?:해제|삭제|상관\s*없)", message):
            hard["move_in_by"] = None
        elif _has(r"\d{1,2}\s*월\s*\d{1,2}\s*일|이번\s*달|다음\s*달|내일", message):
            return _ask("입주 마감일의 연도까지 알려 주세요. 예: '2026-11-25까지 입주'.")

    for field, pattern, label in (("step_free_access", _ACCESS, "무단차"), ("elevator", _ELEVATOR, "엘리베이터")):
        match = re.search(pattern, message)
        if not match:
            continue
        nearby = _clause(message, match.start())
        # Local tail stops at another condition, so '무단차 필수, 조용한 곳
        # 선호' never downgrades access, nor promotes noise to a hard condition.
        tail = message[match.start():]
        tail = re.split(r",|;|\n|(?:그리고|이고|이며)|소음|조용|동네|보증금|예산", tail, maxsplit=1)[0]
        other_pattern = _ELEVATOR if field == "step_free_access" else _ACCESS
        tail = re.split(other_pattern, tail, maxsplit=1)[0]
        if _has(r"상관\s*없|(?:조건\s*)?(?:해제|삭제)|필수\s*(?:아니|아님)|없어도|아니어도|필요\s*없", tail):
            hard[field] = None
        elif _has(r"선호|있으면\s*좋|가능하면|되도록", tail):
            return _ask(f"{label}는 현재 필수 조건으로만 저장할 수 있습니다. 필수 여부를 명시하거나 '{label} 조건 해제'라고 알려 주세요.")
        elif _has(r"미확인|모르|아마|듯|추정", nearby):
            return _ask(f"{label}를 필수 조건으로 원하는지 알려 주세요. 특정 매물의 미확인 상태는 별도의 확인 요청으로 처리합니다.")
        elif field == "elevator" and _has(r"없(?:는|어야|음)|불필요한", tail):
            hard[field] = False
        elif _has(r"불가|불가능|없어|아닌|아님", tail):
            return _ask(f"{label} 필수 조건의 의미를 명확히 알려 주세요. 확인되지 않은 매물 정보와 원하는 조건을 구분해 주세요.")
        else:
            hard[field] = True

    noise = re.search(r"조용|소음|시끄러", message)
    if noise:
        nearby = _clause(message, noise.start())
        local = re.split(r",|;|\n", message[noise.start():], maxsplit=1)[0]
        if (_has(r"필수|반드시|무조건|아니면\s*제외|않으면\s*제외|곳(?:으로)?만", local)
                or _has(r"(?:반드시|무조건)\s*(?:조용|소음|시끄러)", nearby)):
            return _ask("소음은 현재 선호 조건으로만 반영할 수 있습니다. '조용한 곳 선호'로 저장할지 알려 주세요. 필수 조건을 자동으로 선호로 바꾸지 않습니다.")
        if _has(r"상관\s*없|해제|삭제", nearby):
            preferences["noise_level"] = None
        elif _has(r"조용|낮음|낮은|적은", nearby):
            preferences["noise_level"] = "low"
        elif _has(r"보통|중간", nearby):
            preferences["noise_level"] = "medium"
        elif _has(r"높음|높은|시끄러", nearby):
            preferences["noise_level"] = "high"
        else:
            return _ask("소음 선호를 낮음·보통·높음 중에서 알려 주세요.")

    neighborhoods = [match.group() for match in re.finditer("|".join(_NEIGHBORHOODS), message)]
    unknown = [name for name in re.findall(r"[가-힣]{2,8}동(?=\s|[,.;!?]|만|을|이|과|와|도|$)", message)
               if name not in _NEIGHBORHOODS and name not in {"이동", "자동", "활동"}]
    if unknown:
        return _ask("현재 가상 매물은 강남구 동네를 대상으로 합니다. 청담동·삼성동·대치동 등 강남구 선호 동네를 지정해 주세요.")
    if neighborhoods:
        if _has(r"말고|제외한|빼고|아닌", message):
            return _ask("선호할 동네 이름만 다시 알려 주세요. 제외 표현을 기존 동네 선호에 자동으로 합치지 않습니다.")
        if any(_has(re.escape(name) + r"(?:에서)?\s*(?:만|필수|반드시)|(?:반드시|무조건)\s*" + re.escape(name), message) for name in neighborhoods):
            return _ask("동네는 현재 선호 조건으로만 저장할 수 있습니다. 강남구 동네 선호로 반영할지 알려 주세요. 필수 동네 제한으로 처리하지 않습니다.")
        preferences["neighborhoods"] = list(dict.fromkeys(neighborhoods))
    elif _has(r"(?:동네|지역).*?(?:상관\s*없|해제|삭제)", message):
        preferences["neighborhoods"] = []
    if not hard and not preferences:
        return None
    conditions = {}
    if hard:
        conditions["hard"] = hard
    if preferences:
        conditions["preferences"] = preferences
    return _decision("update_conditions", {"conditions": conditions}, "명시된 조건만 변경하도록 검토를 요청합니다. 나머지 조건은 유지합니다.")


_INSPECTION_FIELDS = {"step_free_access": _ACCESS, "elevator": _ELEVATOR,
                      "entry_steps": r"계단\s*(?:수|개수)|계단\s*\d+\s*개",
                      "noise_level": r"소음|조용|시끄러"}


def inspection_fields(message: str) -> list[str]:
    return [field for field, pattern in _INSPECTION_FIELDS.items() if _has(pattern, message)]


# This deliberately small vocabulary only recognizes an affirmative request
# to record one observation. Any remaining text keeps the full decision loop.
_REPORT_WORDS = re.compile(
    r"[\s.,!·]+|현장에서|현장|방금|오늘|직접|제가|매물|아파트|전체|경로|접근|이용|수준|"
    r"불가능|가능|불가|있음|없음|있어|없어|높음|낮음|보통|[0-9]+\s*개|"
    r"것으로|걸로|사실|확인|결과|내용|기록|반영|저장|등록|정정|"
    r"했습니다|했어요|했어|해주세요|해줘요|해줘|주세요|해|하다고|하다는|한|함|"
    r"으로|라고|로|해당|에서|에|이|그|가|의|은|는|을|를|만"
)


def only_record_requested(message: str, listing_id: str, field: str) -> bool:
    if not re.search(r"(?:기록|반영|저장|등록|정정)\s*해", message):
        return False
    text, id_count = re.subn(rf"(?<![a-z0-9]){re.escape(listing_id)}(?![a-z0-9])", " ", message, flags=re.I)
    text, field_count = re.subn(_INSPECTION_FIELDS[field], " ", text, flags=re.I)
    return bool(id_count and field_count and not _REPORT_WORDS.sub("", text))


def _listing_command(message: str, state: dict) -> dict | None:
    identifiers = list(dict.fromkeys(value.upper() for value in re.findall(r"(?<![A-Za-z0-9])L\d{3}(?!\d)", message, re.IGNORECASE)))
    if not identifiers:
        return None
    if len(identifiers) != 1:
        return _ask("변경하거나 확인할 매물을 한 번에 하나씩 지정해 주세요. 예: 'L001 무단차 확인 요청'.")
    if _MONEY.search(message) or _has(rf"\d\s*{_AREA_UNIT}", message):
        return _ask("매물 상태·현장 결과와 검색 조건 변경은 한 번에 하나씩 요청해 주세요.")
    listing_id = identifiers[0]
    listing = next((row for row in state.get("listings", []) if isinstance(row, dict) and row.get("id") == listing_id), None)
    if listing is None:
        return _ask("이 상담에 없는 매물 번호입니다. 현재 목록의 매물 번호를 지정해 주세요.")
    if _has(r"계약\s*(?:완료|됐|되었|됨)|상태|거래\s*가능|판매\s*가능", message):
        if _has(r"계약.*(?:아니|않|안\s*됐|미완료|취소)|계약.*\?|(?:됐|되었|가능한|완료인|완료됐는)지|여부|확인해", message):
            return _ask("변경할 상태를 거래 가능·계약 완료·확인 필요 중 하나로 명시해 주세요.")
        if _has(r"계약\s*(?:완료|됐|되었|됨)", message):
            status = "contracted"
        elif _has(r"거래\s*가능|판매\s*가능|available", message):
            status = "available"
        elif _has(r"확인\s*필요|verification_required", message):
            status = "verification_required"
        else:
            return _ask("변경할 상태를 거래 가능·계약 완료·확인 필요 중 하나로 명시해 주세요.")
        return _decision("set_listing_status", {"listing_id": listing_id, "status": status}, "명시된 가상 매물 상태 변경을 검토하도록 요청합니다.")
    fields = inspection_fields(message)
    if not fields and _has(r"재검사|재검증|재검색|다시\s*(?:검색|검사)|조건.*(?:충족|맞)", message):
        return _search(state)
    if len(fields) != 1:
        return _ask("확인할 항목을 하나만 지정해 주세요: 무단차 접근·엘리베이터·계단 수·소음. 엘리베이터 유무나 계단 수만으로 전체 경로의 무단차 여부를 판단하지 않습니다.")
    field = fields[0]
    if listing.get("status") == "contracted":
        return _ask("계약 완료 매물에는 새 현장 확인을 등록할 수 없습니다. 활성 매물 번호를 지정해 주세요.")
    if _has(r"미확인|모르|확실하지|아마|같아|같은데|추정|듯", message):
        return _decision("request_verification", {"listing_id": listing_id, "field": field}, "미확인 값은 확인 완료로 기록하지 않고 확인 요청으로 남깁니다.")
    if _has(r"여부|(?:가능한|불가능한|있는|없는|됐는|되었는)지|확인\s*(?:해|하러|요청)|현장.*요청", message):
        return _decision("request_verification", {"listing_id": listing_id, "field": field}, "현장 확인을 요청한 문장은 관측 결과로 기록하지 않습니다.")
    observed = _has(r"확인\s*(?:했|완료|됐|되었|결과)|현장|기록|등록|반영|확인\s*:", message)
    if observed:
        value = None
        if field == "entry_steps":
            found = re.search(r"계단\s*(?:수|개수)?\s*[:=은는]?\s*(\d+)\s*개?", message)
            if found:
                value = int(found.group(1))
        elif field == "noise_level":
            values = [level for level, pattern in (("low", r"조용|낮음|낮은|낮다"), ("medium", r"보통|중간"), ("high", r"높음|높은|높다|시끄러")) if _has(pattern, message)]
            if len(values) == 1:
                value = values[0]
        else:
            if _has(r"없지\s*않|불가능하지\s*않|아니지\s*않|계단\s*없", message):
                return _ask("무단차 여부는 전체 경로에서 직접 확인한 가능·불가능 값으로 명시해 주세요. 계단 수나 이중 부정에서 결과를 추정하지 않습니다.")
            negative_pattern = r"가능하지\s*않\S*|있지\s*않\S*|불가능|불가|없(?:음|어|다|는)|아니(?:야|다|라고)|아님|안\s*됨|불충족|false"
            negative = _has(negative_pattern, message)
            positive_text = re.sub(negative_pattern, "", message, flags=re.IGNORECASE)
            positive = _has(r"가능|있(?:음|어|다|는)|충족|true", positive_text)
            if positive and negative:
                return _ask("긍정·부정 결과가 함께 있습니다. 현장 확인값을 가능 또는 불가능 중 하나로 명확히 알려 주세요.")
            if negative:
                value = False
            elif positive:
                value = True
        if value is None:
            return _ask("현장에서 확인한 값을 명시해 주세요. 예: 'L001 무단차 접근 불가능으로 확인했어', 'L001 계단 수 3개 기록'.")
        return _decision("record_inspection", {"listing_id": listing_id, "field": field, "value": value}, "명시적으로 보고한 현장 결과 한 항목의 기록을 검토하도록 요청합니다.")
    return _decision("request_verification", {"listing_id": listing_id, "field": field}, "선택한 항목의 현장 확인 요청을 만듭니다. 확인 완료를 의미하지 않습니다.")


def _transactions(message: str) -> dict | None:
    if not _has(r"실거래|거래\s*내역|국토부|MOLIT", message):
        return None
    if _has(r"예측|전망|오를|내릴|투자|수익", message):
        return _ask("개발용 조회는 과거 신고 실거래만 제공합니다. 조회할 연월과 매매 또는 전월세를 명시해 주세요.")
    filter_text = re.sub(r'["“\'][^"”\']*["”\']', "", message)
    if _MONEY.search(filter_text) or any(name in filter_text for name in _NEIGHBORHOODS):
        return _ask("실거래 조회는 강남구 전체에 대해 정확한 아파트명과 전용 면적 범위만 필터링할 수 있습니다. 동네·금액 필터를 제외하거나 정확한 아파트명을 지정해 주세요.")
    districts = re.findall(r"([가-힣]{2,6}구)(?=\s|의|에서|[,.;!?]|$)", message)
    if any(district != "강남구" for district in districts) or _has(r"서초|송파|마포|용산|부산|성동|강동|강서|은평", message):
        return _ask("현재 실거래 조회는 강남구(11680)만 지원합니다. 강남구의 조회 연월과 거래 종류를 알려 주세요.")
    codes = re.findall(r"(?:법정동|지역|구)\s*(?:코드)?\s*[:=]?\s*(\d{5})(?!\d)", message)
    if any(code != "11680" for code in codes):
        return _ask("현재 실거래 조회의 지역 코드는 강남구 11680만 지원합니다.")
    trade, rent = _has(r"매매|\btrade\b", message), _has(r"전월세|임대차|전세|월세|\brent\b", message)
    if trade == rent:
        return _ask("실거래 종류를 매매 또는 전월세 중 하나로 알려 주세요.")
    periods = re.findall(r"(?<!\d)(\d{4})(?:년\s*|[-/])(\d{1,2})(?:월)?(?!\d)", message)
    periods += [(token[:4], token[4:]) for token in re.findall(r"(?<!\d)(\d{6})(?!\d)", message)]
    periods = list(dict.fromkeys(periods))
    if len(periods) != 1:
        return _ask("실거래 조회할 계약 연월을 하나로 알려 주세요. 예: '2026년 10월 매매 실거래 조회'.")
    year, month = map(int, periods[0])
    if not 1900 <= year <= 2100 or not 1 <= month <= 12:
        return _ask("실거래 조회할 계약 연월을 올바른 YYYYMM 형식으로 알려 주세요.")
    arguments: dict = {"kind": "trade" if trade else "rent", "deal_ymd": f"{year:04d}{month:02d}", "lawd_cd": "11680"}
    quoted = re.findall(r'["“\']([^"”\']{1,100})["”\']', message)
    if len(quoted) > 1:
        return _ask("실거래 아파트명 필터는 정확한 이름 하나만 따옴표로 지정해 주세요.")
    if quoted:
        arguments["apartment_name"] = quoted[0].strip()
    elif _has(r"아파트명|단지명", message):
        return _ask("실거래의 정확한 아파트명 필터를 따옴표로 감싸 주세요. 예: '아파트명 \"정확한 단지명\"'. 가상 매물과 자동 연결하지 않습니다.")
    area_range = re.search(rf"({_NUMBER})\s*(?:~|～|부터|-)\s*({_NUMBER})\s*{_AREA_UNIT}", message, re.IGNORECASE)
    if not area_range and _has(rf"-\s*{_NUMBER}\s*{_AREA_UNIT}", message):
        return _ask("실거래 면적 필터는 0보다 큰 전용 ㎡ 값으로 알려 주세요.")
    if _has(rf"{_AREA_UNIT}\s*(?:초과|미만|정도|내외)", message):
        return _ask("실거래 면적 필터는 경계를 포함하는 이상·이하 범위만 지원합니다. 포함할 최소·최대 ㎡ 값을 지정해 주세요.")
    areas = list(re.finditer(rf"({_NUMBER})\s*{_AREA_UNIT}\s*(이상|이하)?", message, re.IGNORECASE))
    if area_range:
        minimum, maximum = map(float, area_range.groups())
        if minimum <= 0 or maximum < minimum or maximum > 100_000:
            return _ask("실거래 전용 면적 범위는 0보다 큰 최소값부터 최대값 순서로 알려 주세요.")
        arguments.update(min_area_m2=minimum, max_area_m2=maximum)
    elif len(areas) == 1:
        value = float(areas[0].group(1))
        if value <= 0 or value > 100_000:
            return _ask("실거래 면적 필터는 0보다 큰 ㎡ 값으로 알려 주세요.")
        bound = areas[0].group(2)
        if bound != "이하":
            arguments["min_area_m2"] = value
        if bound != "이상":
            arguments["max_area_m2"] = value
    elif len(areas) > 1:
        return _ask("실거래 면적 범위는 '84~85㎡'처럼 최소·최대 값을 명확히 알려 주세요.")
    if _has(r"\d\s*평", message):
        return _ask("실거래 면적 필터는 전용 ㎡로 지정해 주세요.")
    return _decision("lookup_transactions", arguments, "명시된 종류와 계약 연월의 과거 신고 자료를 읽기 전용으로 조회합니다.")


def _entry(message: str, state: dict) -> dict | None:
    if not _has(r"입주|입실|출입", message) or not _has(r"시간|시각|늦|지연|\d\s*시|\d:\d|T\d", message):
        return None
    if _MONEY.search(message) or _has(rf"\d\s*{_AREA_UNIT}", message):
        return _ask("입주 일정 시각과 검색 조건 변경은 한 번에 하나씩 요청해 주세요.")
    if _has(r"시작\s*(?:시간|시각)", message):
        return _ask("이 도구는 입주 절차가 끝나 청소를 시작할 수 있는 입주 가능 시각을 변경합니다. 그 시각을 명시해 주세요.")
    entries = [row for row in state.get("schedule", {}).get("events", []) if isinstance(row, dict) and row.get("type") == "entry"]
    base = None
    if len(entries) == 1:
        try:
            base = datetime.fromisoformat(entries[0]["end"])
            if base.tzinfo is None or base.utcoffset() is None:
                base = None
        except (ValueError, TypeError, KeyError):
            pass
    iso = re.findall(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?", message)
    target = None
    if len(iso) > 1:
        return _ask("새 입주 가능 시각을 날짜와 시간대가 포함된 값 하나로 알려 주세요.")
    if iso:
        try:
            offset = re.search(r"([+-])(\d{2}):(\d{2})$", iso[0])
            if offset and (int(offset.group(2)) > 23 or int(offset.group(3)) > 59):
                raise ValueError
            suffix = message[message.find(iso[0]) + len(iso[0]):]
            if re.match(r"[\d:+.\-]", suffix):
                raise ValueError
            target = datetime.fromisoformat(iso[0])
            if target.tzinfo is None:
                if base is None:
                    raise ValueError
                target = target.replace(tzinfo=base.tzinfo)
        except ValueError:
            return _ask("입주 가능 시각을 올바른 시간대 포함 ISO 값으로 알려 주세요. 예: '2026-11-25T15:00:00+09:00'.")
    else:
        delay = re.search(r"(\d+(?:\.\d+)?)\s*(시간|분)\s*(?:더\s*)?(?:늦|지연|미뤄|미루)", message)
        if delay:
            if base is None:
                return _ask("기준 입주 일정이 하나로 정해져 있지 않습니다. 새 입주 가능 시각을 시간대 포함 ISO 값으로 알려 주세요.")
            try:
                amount = float(delay.group(1))
                if not 0 < amount <= 10_000:
                    raise ValueError
                target = base + timedelta(**{"hours" if delay.group(2) == "시간" else "minutes": amount})
            except (ValueError, OverflowError):
                return _ask("새 입주 가능 시각을 날짜와 시간대가 포함된 값으로 알려 주세요.")
        else:
            clocks = re.findall(r"(?:(오전|오후)\s*)?(\d{1,2})(?::(\d{2})|시\s*(?:(\d{1,2})\s*분|(반))?)", message)
            if len(clocks) != 1 or base is None:
                return _ask("새 입주 가능 시각을 시간대 포함 ISO 값으로 알려 주세요. 예: '2026-11-25T15:00:00+09:00'.")
            ampm, hour_text, colon_minutes, minutes, half = clocks[0]
            hour, minute = int(hour_text), int(colon_minutes or minutes or ("30" if half else "0"))
            if (ampm and not 1 <= hour <= 12) or (not ampm and not colon_minutes and hour <= 12):
                return _ask("오전·오후 또는 24시간제로 새 입주 가능 시각을 알려 주세요.")
            if ampm:
                hour = hour % 12 + (12 if ampm == "오후" else 0)
            dates = re.findall(r"(\d{4})[-년]\s*(\d{1,2})[-월]\s*(\d{1,2})(?:일)?", message)
            if len(dates) > 1 or (not dates and _has(r"내일|모레|다음|\d+월\s*\d+일", message)):
                return _ask("변경할 날짜의 연도까지 명시해 주세요. 예: '2026-11-25T15:00:00+09:00'.")
            try:
                calendar = tuple(map(int, dates[0])) if dates else (base.year, base.month, base.day)
                zone = timezone(timedelta(hours=9)) if _has(r"한국\s*시간|KST", message) else base.tzinfo
                target = datetime(*calendar, hour, minute, tzinfo=zone)
            except ValueError:
                return _ask("실제 존재하는 날짜와 00:00~23:59 범위의 입주 가능 시각을 알려 주세요.")
    if target is None:
        return None
    return _decision("set_entry_time", {"new_end": target.isoformat()}, "새 입주 가능 시각의 변경을 검토하도록 요청합니다. 생략된 날짜·시간대는 현재 입주 일정을 따릅니다.")


def _schedule(message: str, state: dict) -> dict | None:
    proposals = state.get("schedule_proposals", [])
    ordinal = re.search(r"(?:(첫|두|둘|세|셋|네|넷|다섯)\s*번째|(?<!\d)([1-5])\s*(?:번(?:째)?|안))", message)
    wants_apply = _has(r"적용|반영|선택|확정", message)
    schedule_context = _has(r"일정|대안|계획|청소|이사", message)
    if ordinal and (wants_apply or schedule_context or re.fullmatch(r"(?:첫|두|둘|세|셋|네|넷|다섯)\s*번째(?:로|요)?", message)):
        if len(re.findall(r"(?:첫|두|둘|세|셋|네|넷|다섯)\s*번째|(?<!\d)[1-5]\s*(?:번(?:째)?|안)", message)) != 1 or _has(r"말고|아닌|제외", message):
            return _ask("적용할 일정 대안 번호를 하나만 명시해 주세요. 예: '두 번째 일정안 적용해줘'.")
        if not proposals:
            return _ask("현재 적용할 일정 대안이 없습니다. '일정을 다시 제안해줘'로 대안을 계산한 뒤 번호를 선택해 주세요.")
        index = int(ordinal.group(2)) - 1 if ordinal.group(2) else {"첫": 0, "두": 1, "둘": 1, "세": 2, "셋": 2, "네": 3, "넷": 3, "다섯": 4}[ordinal.group(1)]
        if index >= len(proposals):
            return _ask("현재 표시된 일정 대안 범위 안에서 번호를 선택해 주세요.")
        proposal = proposals[index]
        if not isinstance(proposal, dict) or not isinstance(proposal.get("id"), str):
            return _ask("일정 대안을 다시 계산한 뒤 선택해 주세요.")
        if proposal.get("based_on_revision") != state.get("revision"):
            return _decision("propose_schedule", reason="기존 대안이 현재 상태와 달라 최신 일정 대안을 다시 계산합니다. 새 대안은 다시 선택해야 합니다.")
        return _decision("apply_schedule", {"proposal_id": proposal["id"]}, "선택한 대안을 내부 일정표에 반영하도록 검토를 요청합니다. 실제 예약은 하지 않습니다.")
    if schedule_context and wants_apply:
        return _ask("적용할 일정 대안 번호를 선택해 주세요. 예: '첫 번째 일정안 적용해줘'.")
    if schedule_context and _has(r"제안|대안|다시|재계획|재조정|충돌|짜|조정|계산|맞춰", message):
        return _decision("propose_schedule", reason="입력된 업체 시간표와 선후 관계를 만족하는 내부 일정 대안을 계산합니다.")
    return None


def next_decision(message: str, state: dict, trace: list[dict]) -> dict:
    """Choose one allowlisted action; neither input nor business state is changed."""
    if not isinstance(message, str) or not isinstance(state, dict) or not isinstance(trace, list):
        return _ask(_EXAMPLES)
    message = message.strip()
    if not message or len(message) > 2_000 or any(not isinstance(row, dict) for row in trace):
        return _ask(_EXAMPLES)
    continuation = _trace_followup(state, trace, message)
    if continuation is not None:
        return continuation
    if state.get("agent", {}).get("pending"):
        return _ask("검토 중인 변경이 있습니다. 먼저 검토 카드에서 확인 또는 취소해 주세요.")
    if re.fullmatch(r"(?:안녕(?:하세요|하십니까)?|반가워요|상담\s*(?:시작|하고\s*싶어요|받고\s*싶어요))[.!?~\s]*", message):
        if _budget_missing(state):
            return _ask("안녕하세요. 강남구 아파트 전세 조건을 함께 정리해 볼게요. 최대 보증금은 얼마까지 생각하고 계세요? 예: '보증금 10억 이하'.")
        return _ask("안녕하세요. 저장된 조건으로 상담을 이어갈게요. 후보를 다시 보려면 '후보 다시 찾아줘', 조건을 바꾸려면 새 값을 알려 주세요.")
    if re.fullmatch(r"(?:고마워(?:요)?|감사(?:해요|합니다)|수고했어(?:요)?)[.!?~\s]*", message):
        return _ask("네, 천천히 살펴보세요. 조건이나 일정이 바뀌면 이어서 알려 주세요. 현재 조건으로 다시 보려면 '후보 다시 찾아줘'라고 말씀해 주세요.")
    if _has(r"(?:하지|바꾸지|찾지|원하지)\s*(?:마|말)|(?:조회|변경|적용|검색|기록|등록|반영|요청|완화|수정|확인)하지\s*않", message):
        return _ask("하지 말라는 요청이 포함되어 작업을 실행하지 않았습니다. 실행할 요청이 있으면 원하는 작업만 명확히 알려 주세요.")
    if _has(r"가정|간주|추측.*(?:기록|반영)|(?:확인\s*없이|미확인).*통과", message):
        return _ask("가정이나 추측을 확인된 사실로 기록하지 않습니다. 실제로 확인한 결과 또는 현장 확인 요청을 명시해 주세요.")
    if _has(r"예약(?:해|하|을|\s*진행)|문자\s*(?:보내|전송)|이메일\s*(?:보내|전송)|전화\s*해", message):
        return _ask("실제 예약이나 외부 연락은 수행하지 않습니다. 내부 일정 대안이 필요하면 '일정을 다시 제안해줘'라고 입력해 주세요.")
    message = _followup(message, state)
    for parser in (_transactions,):
        result = parser(message)
        if result is not None:
            return result
    for parser in (_listing_command, _entry):
        result = parser(message, state)
        if result is not None:
            return result
    if _has(r"완화|늘려|올려|줄여|낮춰|빼줘|빼\s*줘", message) and not (_MONEY.search(message) or _has(rf"\d\s*{_AREA_UNIT}|조건\s*(?:해제|삭제)", message)):
        return _ask("바꿀 조건과 새 값을 명확히 알려 주세요. 예산·면적·무단차 필수 조건은 자동으로 완화하지 않습니다.")
    conditions = _conditions(message)
    if conditions is not None:
        # Avoid a redundant review for an already-saved value; explicit search
        # still recomputes eligibility from current evidence.
        patch = conditions.get("arguments", {}).get("conditions")
        if isinstance(patch, dict) and all(state.get("conditions", {}).get(group, {}).get(key) == value for group, values in patch.items() for key, value in values.items()):
            return _search(state)
        return conditions
    schedule = _schedule(message, state)
    if schedule is not None:
        return schedule
    if _has(r"찾아|검색|추천|후보|재검사|재검증|다시\s*(?:확인|검토)|조건.*매물", message):
        return _search(state)
    if _has(r"확인\s*요청|현장\s*(?:확인|기록)", message):
        return _ask("매물 번호와 확인할 항목을 알려 주세요. 예: 'L001 무단차 확인 요청'.")
    if re.fullmatch(r"(?:네|예|응|좋아|확인|동의|진행|취소)[.!요]*", message):
        return _ask("동의 표현만으로 변경하지 않습니다. 검토 카드의 확인·취소 버튼을 사용하거나 처리할 조건·매물·일정을 구체적으로 적어 주세요.")
    return _ask(_EXAMPLES)
