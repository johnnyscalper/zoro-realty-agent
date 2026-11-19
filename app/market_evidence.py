"""Read-only, bounded historical evidence; never a valuation or live fallback.

``scanned_count`` counts normalized source records before cancellation/filter
exclusions. ``matched_count`` counts matching, non-cancelled records in that
bounded scan, not the provider's full filtered total. ``coverage=complete``
means every record advertised by this response was scanned without parse gaps;
it does not imply the provider contains every contract made in that month.
"""
from __future__ import annotations

from datetime import date
import math
import re

from app.config import Settings
from app.molit import HISTORICAL_NOTICE, MAX_MONEY_KRW, MolitError, fetch_transactions
from app.sample import sample_transactions


MAX_PAGES = 2
PAGE_SIZE = 100
MAX_ITEMS = 10
_ARGUMENTS = frozenset({
    "kind", "deal_ymd", "lawd_cd", "apartment_name", "min_area_m2", "max_area_m2",
})


class EvidenceError(MolitError):
    """Fixed public diagnostics compatible with the existing MOLIT handler."""


def _invalid_query() -> EvidenceError:
    return EvidenceError(
        "INVALID_QUERY", "조회 종류·계약월·강남구 지역코드·아파트명·면적 범위를 확인해 주세요.",
        False, 400,
    )


def _invalid_response() -> EvidenceError:
    return EvidenceError(
        "EVIDENCE_INVALID_RESPONSE", "거래 자료의 출처 또는 형식을 확인할 수 없습니다. 다시 조회해 주세요.",
    )


def _number(value: object, *, minimum: float, maximum: float) -> bool:
    # Exact types exclude booleans and objects with coercion side effects.
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def _string(value: object, maximum: int) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= maximum
            and value == value.strip() and not any(ord(c) < 32 for c in value))


def _query(arguments: dict, mode: str) -> dict:
    if not isinstance(arguments, dict) or not isinstance(mode, str) or mode not in {"sample", "live"}:
        raise _invalid_query()
    if not set(arguments).issubset(_ARGUMENTS):
        raise _invalid_query()
    kind, month = arguments.get("kind"), arguments.get("deal_ymd")
    district = arguments.get("lawd_cd", "11680")
    if (not isinstance(kind, str) or kind not in {"trade", "rent"}
            or not isinstance(district, str) or district != "11680"
            or not isinstance(month, str) or not re.fullmatch(r"[0-9]{6}", month)):
        raise _invalid_query()
    try:
        if date(int(month[:4]), int(month[4:]), 1).year < 2006:
            raise ValueError
    except ValueError:
        raise _invalid_query() from None
    query = {"kind": kind, "lawd_cd": district, "deal_ymd": month}
    if "apartment_name" in arguments:
        name = arguments["apartment_name"]
        if not isinstance(name, str) or not _string(name.strip(), 100):
            raise _invalid_query()
        query["apartment_name"] = name.strip()
    for field in ("min_area_m2", "max_area_m2"):
        if field in arguments:
            value = arguments[field]
            if not _number(value, minimum=0, maximum=1000) or value <= 0:
                raise _invalid_query()
            query[field] = value
    if query.get("min_area_m2", 0) > query.get("max_area_m2", 1000):
        raise _invalid_query()
    return query


def _record(raw: dict, query: dict) -> dict:
    """Copy only the existing normalized transaction fields, never raw extras."""
    if not isinstance(raw, dict):
        raise _invalid_response()
    for key, limit in (("id", 200), ("apartment_name", 300), ("legal_dong", 100)):
        if not _string(raw.get(key), limit):
            raise _invalid_response()
    contract_date = raw.get("contract_date")
    try:
        if (not isinstance(contract_date, str)
                or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", contract_date)
                or contract_date[:7].replace("-", "") != query["deal_ymd"]):
            raise ValueError
        date.fromisoformat(contract_date)
    except ValueError:
        raise _invalid_response() from None
    area, floor, year = raw.get("area_m2"), raw.get("floor"), raw.get("build_year")
    if (not _number(area, minimum=0, maximum=10000) or area <= 0
            or type(floor) is not int or not -20 <= floor <= 300
            or (year is not None and (type(year) is not int or not 1800 <= year <= 9999))):
        raise _invalid_response()
    cancelled, cancelled_date = raw.get("cancelled"), raw.get("cancelled_date")
    available = raw.get("cancellation_status_available", False)
    if (cancelled is not None and type(cancelled) is not bool) or type(available) is not bool:
        raise _invalid_response()
    if cancelled_date is not None:
        try:
            if (not isinstance(cancelled_date, str)
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", cancelled_date)):
                raise ValueError
            date.fromisoformat(cancelled_date)
        except ValueError:
            raise _invalid_response() from None
        cancelled = True
    if query["kind"] == "trade":
        price = raw.get("price_krw")
        if type(price) is not int or not 0 < price <= MAX_MONEY_KRW or raw.get("lease_type") != "trade":
            raise _invalid_response()
        deposit = monthly = None
    else:
        price = None
        deposit, monthly = raw.get("deposit_krw"), raw.get("monthly_rent_krw")
        if any(type(value) is not int or not 0 <= value <= MAX_MONEY_KRW for value in (deposit, monthly)):
            raise _invalid_response()
        if raw.get("lease_type") != ("jeonse" if monthly == 0 else "monthly_rent"):
            raise _invalid_response()
        # The rent API cannot establish cancellation, even if a caller adds a
        # trade-shaped status field to a normalized record.
        available = False
    if cancelled is None:
        available = False
    return {
        "id": raw["id"], "apartment_name": raw["apartment_name"], "legal_dong": raw["legal_dong"],
        "contract_date": contract_date, "area_m2": area, "floor": floor, "build_year": year,
        "price_krw": price, "deposit_krw": deposit, "monthly_rent_krw": monthly,
        "cancelled": cancelled, "cancelled_date": cancelled_date,
        "lease_type": raw["lease_type"], "cancellation_status_available": available,
    }


def _page(page: dict, query: dict, mode: str, page_no: int) -> tuple[int, int, list[dict]]:
    if not isinstance(page, dict):
        raise _invalid_response()
    expected_source = "molit" if mode == "live" else "synthetic_sample"
    if page.get("source") != expected_source or page.get("synthetic") is not (mode == "sample"):
        raise _invalid_response()
    for field in ("kind", "lawd_cd", "deal_ymd"):
        if page.get(field) != query[field]:
            raise _invalid_response()
    for field, expected in (("page_no", page_no), ("num_of_rows", PAGE_SIZE)):
        if type(page.get(field)) is not int or page[field] != expected:
            raise _invalid_response()
    total, skipped, raw_items = page.get("total_count"), page.get("skipped_count", 0), page.get("items")
    if (type(total) is not int or not 0 <= total <= MAX_MONEY_KRW
            or type(skipped) is not int or not 0 <= skipped <= PAGE_SIZE
            or not isinstance(raw_items, list) or len(raw_items) + skipped > PAGE_SIZE):
        raise _invalid_response()
    remaining = max(total - (page_no - 1) * PAGE_SIZE, 0)
    count = len(raw_items) + skipped
    if (count > remaining or (remaining > 0 and count == 0)
            or type(page.get("returned_count")) is not int or page["returned_count"] != len(raw_items)
            or type(page.get("has_next")) is not bool or page["has_next"] != (page_no * PAGE_SIZE < total)):
        raise _invalid_response()
    return total, skipped, [_record(item, query) for item in raw_items]


async def lookup_evidence(arguments: dict, *, mode: str, settings: Settings, fetcher=None) -> dict:
    """Read at most two 100-row pages; optional fetcher has MOLIT's async API."""
    query = _query(arguments, mode)
    records, totals, skipped_count = [], [], 0
    seen_ids = set()
    for page_no in range(1, MAX_PAGES + 1):
        request = {key: query[key] for key in ("kind", "lawd_cd", "deal_ymd")}
        request.update(page_no=page_no, num_of_rows=PAGE_SIZE)
        if mode == "sample":
            page = sample_transactions(**request)
        else:
            try:
                key = settings.molit_trade_api_key if query["kind"] == "trade" else settings.molit_rent_api_key
                page = await (fetcher or fetch_transactions)(
                    **request, api_key=key, timeout_seconds=settings.molit_timeout_seconds,
                )
            except MolitError:
                raise
            except Exception:
                # Transport/config exception text may contain credentials.
                raise EvidenceError("EVIDENCE_FETCH_FAILED", "거래 자료를 조회하지 못했습니다. 서버 설정과 연결 상태를 확인해 주세요.") from None
        total, skipped, items = _page(page, query, mode, page_no)
        for item in items:
            if item["id"] in seen_ids:
                raise _invalid_response()
            seen_ids.add(item["id"])
        totals.append(total)
        skipped_count += skipped
        records.extend(items)
        if page_no * PAGE_SIZE >= total:
            break

    scanned_count, total_count = len(records), max(totals)
    coverage = "complete" if len(set(totals)) == 1 and skipped_count == 0 and scanned_count == total_count else "partial"
    excluded_cancelled_count = 0
    unknown_cancellation_count = 0
    matching = []
    for item in records:
        if query["kind"] == "trade" and item["cancelled"] is True:
            excluded_cancelled_count += 1
            continue
        if not item["cancellation_status_available"]:
            unknown_cancellation_count += 1
        if "apartment_name" in query and item["apartment_name"] != query["apartment_name"]:
            continue
        if not query.get("min_area_m2", 0) <= item["area_m2"] <= query.get("max_area_m2", 10000):
            continue
        matching.append(item)

    warnings = [
        HISTORICAL_NOTICE, "현재 시연용 가상 매물과 과거 거래의 동일성을 확인하지 않습니다.",
        "해제 정보는 조회 응답에 한정되며, 해제 표시가 없는 기록도 계약 유효성을 보장하지 않습니다.",
    ]
    if mode == "sample":
        warnings.insert(0, "화면 확인을 위한 가상 거래 자료입니다. 실제 국토부 조회 결과가 아닙니다.")
    if coverage == "partial":
        warnings.append("최대 2페이지·페이지당 100건의 제한 조회입니다. 누락 가능성이 있어 일치 건수는 전체 계약월의 결과가 아닙니다.")
    else:
        warnings.append("조회 응답에 표시된 건수를 확인했습니다. 미신고·지연신고 등으로 실제 계약 전체와 다를 수 있습니다.")
    if len(set(totals)) > 1:
        warnings.append("조회 중 원본의 전체 건수가 바뀌어 조회 범위를 완전하게 확인하지 못했습니다.")
    if skipped_count:
        warnings.append(f"형식이 맞지 않아 원본 처리 과정에서 제외된 {skipped_count}건은 조건 일치 여부를 확인할 수 없습니다.")
    if excluded_cancelled_count:
        warnings.append(f"조회한 매매 기록 중 해제된 거래 {excluded_cancelled_count}건을 비교 대상에서 제외했습니다.")
    if unknown_cancellation_count:
        warnings.append(f"조회한 비교 대상 중 {unknown_cancellation_count}건은 해제 여부를 확인할 수 없습니다. 계약 유효성을 보장하지 않습니다.")
    if len(matching) > MAX_ITEMS:
        warnings.append(f"조건 일치 {len(matching)}건 중 처음 {MAX_ITEMS}건만 표시합니다.")
    label = "가상 예시" if mode == "sample" else "국토부 과거 신고 거래"
    exclusion = f"해제 매매 {excluded_cancelled_count}건 제외 후 " if query["kind"] == "trade" else ""
    summary = (
        f"{label}: 원본 표시 {total_count}건 중 {scanned_count}건을 확인했습니다. "
        f"{exclusion}이름·면적 조건에 일치한 기록은 "
        f"확인 범위에서 {len(matching)}건이며 {min(len(matching), MAX_ITEMS)}건을 표시합니다."
    )
    if coverage == "partial":
        summary += " 일부 범위의 조회 결과입니다."
    return {
        "source": "sample" if mode == "sample" else "molit", "synthetic": mode == "sample",
        "kind": query["kind"], "lawd_cd": query["lawd_cd"], "deal_ymd": query["deal_ymd"],
        "query": query, "total_count": total_count, "scanned_count": scanned_count,
        "matched_count": len(matching), "coverage": coverage, "items": matching[:MAX_ITEMS],
        "excluded_cancelled_count": excluded_cancelled_count, "skipped_count": skipped_count,
        "unknown_cancellation_count": unknown_cancellation_count,
        "summary": summary, "warnings": warnings,
    }
