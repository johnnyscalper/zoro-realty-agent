"""Offline evidence tests: all records, credentials and fetchers are invented."""
import asyncio
from copy import deepcopy
import json

import pytest

from app.config import Settings
from app import market_evidence
from app.market_evidence import EvidenceError, lookup_evidence
from app.molit import MolitError, parse_transactions


QUERY = {"kind": "trade", "deal_ymd": "202610"}
FAKE_SETTINGS = Settings(
    molit_trade_api_key="OFFLINE-TRADE-KEY", molit_rent_api_key="OFFLINE-RENT-KEY",
    molit_timeout_seconds=7.0,
)


def record(index=0, **changes):
    result = {
        "id": f"OFFLINE-{index}", "apartment_name": "가상 한빛아파트", "legal_dong": "가상동",
        "contract_date": "2026-10-01", "area_m2": 84.9, "floor": 3, "build_year": 2000,
        "price_krw": 1_000_000_000, "deposit_krw": None, "monthly_rent_krw": None,
        "cancelled": False, "cancelled_date": None, "lease_type": "trade",
        "cancellation_status_available": True,
    }
    result.update(changes)
    return result


def page(items, *, total=None, number=1, skipped=0, kind="trade", **changes):
    total = len(items) + skipped if total is None else total
    result = {
        "source": "molit", "synthetic": False, "kind": kind, "lawd_cd": "11680",
        "deal_ymd": "202610", "page_no": number, "num_of_rows": 100,
        "total_count": total, "returned_count": len(items), "skipped_count": skipped,
        "has_next": number * 100 < total, "items": items,
        "warnings": ["UNTRUSTED-PROVIDER-WARNING"],
    }
    result.update(changes)
    return result


def run(arguments=None, *, mode="live", pages=None, fetcher=None, settings=FAKE_SETTINGS):
    calls = []
    async def fake_fetcher(**kwargs):
        calls.append(kwargs)
        return deepcopy(pages[kwargs["page_no"] - 1])
    result = asyncio.run(lookup_evidence(
        QUERY if arguments is None else arguments, mode=mode, settings=settings,
        fetcher=fetcher or fake_fetcher,
    ))
    return result, calls


def test_sample_reuses_existing_data_without_keys_or_fetcher(monkeypatch):
    async def forbidden(**kwargs):
        pytest.fail("sample mode must not call any live client")
    monkeypatch.setattr(market_evidence, "fetch_transactions", forbidden)
    result, calls = run(mode="sample", fetcher=forbidden, settings=object())
    assert calls == []
    assert result["source"] == "sample" and result["synthetic"] is True
    assert result["total_count"] == result["scanned_count"] == 3
    assert result["matched_count"] == 2
    assert result["excluded_cancelled_count"] == 1
    assert result["coverage"] == "complete"
    assert all(item["cancelled"] is False for item in result["items"])
    assert "가상 예시" in result["summary"]
    assert any("실제 국토부 조회 결과가 아닙니다" in text for text in result["warnings"])
    assert any("현재 거래 가능한 매물" in text for text in result["warnings"])
    assert any("현재 시연용 가상 매물" in text for text in result["warnings"])


def test_exact_name_and_inclusive_area_filter_without_mutating_input():
    rows = [
        record(0, area_m2=59.95), record(1, area_m2=84.97),
        record(2, area_m2=84.98), record(3, area_m2=59.94),
        record(4, apartment_name="가상 한빛아파트2"),
        record(5, apartment_name="한빛아파트"),
    ]
    arguments = QUERY | {"apartment_name": "  가상 한빛아파트  ", "min_area_m2": 59.95, "max_area_m2": 84.97}
    original = deepcopy(arguments)
    result, calls = run(arguments, pages=[page(rows)])
    assert arguments == original
    assert [item["id"] for item in result["items"]] == ["OFFLINE-0", "OFFLINE-1"]
    assert result["scanned_count"] == 6 and result["matched_count"] == 2
    assert result["query"]["apartment_name"] == "가상 한빛아파트"
    assert calls == [{
        "kind": "trade", "lawd_cd": "11680", "deal_ymd": "202610",
        "page_no": 1, "num_of_rows": 100, "api_key": "OFFLINE-TRADE-KEY", "timeout_seconds": 7.0,
    }]


def test_two_pages_at_most_100_rows_and_ten_displayed_records():
    pages = [page([record(i) for i in range(100)], total=350),
             page([record(i) for i in range(100, 200)], total=350, number=2)]
    result, calls = run(pages=pages)
    assert [call["page_no"] for call in calls] == [1, 2]
    assert all(call["num_of_rows"] == 100 for call in calls)
    assert result["total_count"] == 350
    assert result["scanned_count"] == result["matched_count"] == 200
    assert len(result["items"]) == 10 and result["coverage"] == "partial"
    assert "일부 범위" in result["summary"]
    assert any("처음 10건" in text for text in result["warnings"])
    assert not any("average" in key or "median" in key for key in result)
    assert "평균" not in result["summary"]
    assert all("UNTRUSTED" not in text for text in result["warnings"])


@pytest.mark.parametrize("count", [0, 1, 100, 101, 200])
def test_coverage_complete_only_for_scanned_advertised_records(count):
    rows = [record(i) for i in range(count)]
    pages = [page(rows[:100], total=count)]
    if count > 100:
        pages.append(page(rows[100:], total=count, number=2))
    result, calls = run(pages=pages)
    assert len(calls) == (2 if count > 100 else 1)
    assert result["coverage"] == "complete"
    assert result["scanned_count"] == result["matched_count"] == count
    assert len(result["items"]) == min(count, 10)
    assert any("미신고·지연신고" in text for text in result["warnings"])


def test_matching_can_be_found_only_on_second_page():
    rows = [record(i, apartment_name="다른 가상 아파트") for i in range(100)]
    result, _ = run(QUERY | {"apartment_name": "가상 한빛아파트"}, pages=[
        page(rows, total=101), page([record(100)], total=101, number=2),
    ])
    assert result["matched_count"] == 1 and result["items"][0]["id"] == "OFFLINE-100"
    assert result["coverage"] == "complete"


def test_cancelled_trade_and_cancellation_date_alone_are_excluded():
    rows = [record(0), record(1, cancelled=True), record(2, cancelled_date="2026-10-20")]
    result, _ = run(pages=[page(rows)])
    assert result["excluded_cancelled_count"] == 2
    assert result["matched_count"] == 1 and result["scanned_count"] == 3
    assert result["items"][0]["id"] == "OFFLINE-0"
    assert any("해제된 거래 2건" in text for text in result["warnings"])


@pytest.mark.parametrize("changes", [
    {"cancellation_status_available": False},
    {"cancelled": None, "cancellation_status_available": True},
])
def test_unknown_cancellation_is_never_presented_as_confirmed_validity(changes):
    result, _ = run(pages=[page([record(**changes)])])
    assert result["unknown_cancellation_count"] == 1
    assert result["items"][0]["cancellation_status_available"] is False
    assert any("계약 유효성을 보장하지 않습니다" in text for text in result["warnings"])


def test_absent_cancellation_availability_stays_unknown():
    row = record()
    del row["cancellation_status_available"]
    result, _ = run(pages=[page([row])])
    assert result["unknown_cancellation_count"] == 1


def test_rent_uses_its_key_and_keeps_deposit_monthly_rent_separate():
    rows = [record(0, price_krw=None, deposit_krw=700_000_000, monthly_rent_krw=0,
                   lease_type="jeonse", cancellation_status_available=False),
            record(1, price_krw=None, deposit_krw=100_000_000, monthly_rent_krw=1_500_000,
                   lease_type="monthly_rent", cancellation_status_available=False)]
    result, calls = run(QUERY | {"kind": "rent"}, pages=[page(rows, kind="rent")])
    assert calls[0]["api_key"] == "OFFLINE-RENT-KEY"
    assert result["items"][0]["deposit_krw"] == 700_000_000
    assert result["items"][1]["monthly_rent_krw"] == 1_500_000
    assert all(item["price_krw"] is None for item in result["items"])
    assert result["unknown_cancellation_count"] == 2


def test_malformed_source_rows_make_coverage_partial_even_when_all_pages_fetched():
    result, _ = run(pages=[page([record()], total=3, skipped=2)])
    assert result["coverage"] == "partial"
    assert result["total_count"] == 3 and result["scanned_count"] == 1
    assert result["skipped_count"] == 2
    assert any("제외된 2건" in text for text in result["warnings"])


def test_short_source_page_cannot_create_false_complete_coverage():
    result, _ = run(pages=[page([record()], total=5)])
    assert result["coverage"] == "partial"
    assert result["total_count"] == 5 and result["scanned_count"] == 1


def test_changing_source_total_cannot_create_false_complete_coverage():
    result, _ = run(pages=[
        page([record(i) for i in range(100)], total=101),
        page([record(i) for i in range(100, 102)], total=102, number=2),
    ])
    assert result["total_count"] == result["scanned_count"] == 102
    assert result["coverage"] == "partial"
    assert any("전체 건수가 바뀌어" in text for text in result["warnings"])


@pytest.mark.parametrize("arguments", [
    None, [], "trade", {}, {"kind": "trade"}, {"deal_ymd": "202610"},
    QUERY | {"kind": True}, QUERY | {"kind": []}, QUERY | {"kind": "sell"},
    QUERY | {"deal_ymd": 202610}, QUERY | {"deal_ymd": "202613"},
    QUERY | {"deal_ymd": "000010"}, QUERY | {"deal_ymd": "２０２６１０"},
    QUERY | {"deal_ymd": "200512"},
    QUERY | {"deal_ymd": "20261"}, QUERY | {"deal_ymd": "202610\n"},
    QUERY | {"lawd_cd": "11110"}, QUERY | {"lawd_cd": 11680}, QUERY | {"lawd_cd": True},
    QUERY | {"lawd_cd": None}, QUERY | {"apartment_name": " "},
    QUERY | {"apartment_name": 12}, QUERY | {"apartment_name": None},
    QUERY | {"apartment_name": "x" * 301}, QUERY | {"apartment_name": "a\nb"},
    QUERY | {"apartment_name": "x" * 101},
    QUERY | {"min_area_m2": True}, QUERY | {"max_area_m2": False},
    QUERY | {"min_area_m2": "84"}, QUERY | {"max_area_m2": None},
    QUERY | {"min_area_m2": float("nan")}, QUERY | {"max_area_m2": float("inf")},
    QUERY | {"min_area_m2": -1}, QUERY | {"min_area_m2": 0},
    QUERY | {"max_area_m2": 10001}, QUERY | {"max_area_m2": 10 ** 1000},
    QUERY | {"max_area_m2": 1001},
    QUERY | {"min_area_m2": 85, "max_area_m2": 84},
    QUERY | {"api_key": "NO-CLIENT-KEYS"}, QUERY | {"page_no": 3}, QUERY | {"url": "https://example.invalid"},
])
def test_bad_arguments_never_fetch(arguments):
    async def forbidden(**kwargs):
        pytest.fail("invalid queries must never fetch")
    with pytest.raises(EvidenceError) as failure:
        asyncio.run(lookup_evidence(arguments, mode="live", settings=FAKE_SETTINGS, fetcher=forbidden))
    assert failure.value.code == "INVALID_QUERY"
    assert failure.value.http_status == 400


@pytest.mark.parametrize("mode", [None, [], True, "auto", "LIVE"])
def test_invalid_mode_never_fetches(mode):
    with pytest.raises(EvidenceError):
        run(mode=mode, pages=[])


def test_live_error_is_not_replaced_with_sample(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("a live failure must not load synthetic records")
    monkeypatch.setattr(market_evidence, "sample_transactions", forbidden)
    async def failure(**kwargs):
        raise MolitError("MOLIT_TIMEOUT", "국토부 응답 대기시간을 초과했습니다.", True, 504)
    with pytest.raises(MolitError) as caught:
        run(fetcher=failure)
    assert caught.value.code == "MOLIT_TIMEOUT" and caught.value.retryable is True


def test_second_page_failure_does_not_return_first_page_as_success():
    calls = []
    async def failure(**kwargs):
        calls.append(kwargs["page_no"])
        if kwargs["page_no"] == 1:
            return page([record(i) for i in range(100)], total=101)
        raise MolitError("MOLIT_RATE_LIMIT", "국토부 호출 허용량을 초과했습니다.", False, 429)
    with pytest.raises(MolitError) as caught:
        run(fetcher=failure)
    assert caught.value.code == "MOLIT_RATE_LIMIT" and calls == [1, 2]


def test_unknown_transport_exception_has_fixed_secret_free_error():
    async def failure(**kwargs):
        raise RuntimeError(f"https://example.invalid/?serviceKey={kwargs['api_key']}")
    with pytest.raises(EvidenceError) as caught:
        run(fetcher=failure)
    error = caught.value
    assert error.code == "EVIDENCE_FETCH_FAILED"
    assert error.message == str(error)
    assert error.__suppress_context__ is True
    assert "OFFLINE" not in error.message and "example.invalid" not in error.message


@pytest.mark.parametrize("changes", [
    {"source": "sample"}, {"synthetic": True}, {"kind": "rent"},
    {"lawd_cd": "11110"}, {"deal_ymd": "202609"}, {"page_no": True},
    {"page_no": 2}, {"num_of_rows": 101}, {"total_count": True},
    {"total_count": -1}, {"total_count": 0}, {"skipped_count": True},
    {"skipped_count": 100}, {"returned_count": True}, {"returned_count": 2},
    {"has_next": True}, {"has_next": 0}, {"items": {}},
])
def test_inconsistent_page_metadata_is_rejected(changes):
    with pytest.raises(EvidenceError) as caught:
        run(pages=[page([record()]) | changes])
    assert caught.value.code == "EVIDENCE_INVALID_RESPONSE"


@pytest.mark.parametrize("changes", [
    {"area_m2": True}, {"area_m2": float("nan")}, {"area_m2": 0},
    {"floor": False}, {"floor": 301}, {"build_year": True},
    {"contract_date": "2026-10-32"}, {"contract_date": "2026-09-30"},
    {"apartment_name": ""}, {"legal_dong": "x" * 101},
    {"price_krw": True}, {"price_krw": -1}, {"price_krw": 10 ** 20},
    {"cancelled": "false"}, {"cancelled_date": "unknown"},
    {"cancellation_status_available": 1}, {"lease_type": "jeonse"},
])
def test_malformed_normalized_rows_are_rejected(changes):
    with pytest.raises(EvidenceError) as caught:
        run(pages=[page([record(**changes)])])
    assert caught.value.code == "EVIDENCE_INVALID_RESPONSE"


def test_provider_extra_fields_and_warnings_do_not_escape():
    result, _ = run(pages=[page([record(raw_response="PRIVATE", api_key="PRIVATE")], api_key="PRIVATE")])
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "PRIVATE" not in encoded and "OFFLINE-TRADE-KEY" not in encoded
    assert "UNTRUSTED-PROVIDER" not in encoded


def test_actual_normalized_client_fixture_can_be_reused_without_network():
    from pathlib import Path
    raw = (Path(__file__).parent / "fixtures" / "molit_trade.xml").read_bytes()
    raw = raw.replace(b"<numOfRows>20</numOfRows>", b"<numOfRows>100</numOfRows>")
    normalized = parse_transactions(raw, kind="trade", lawd_cd="11680", deal_ymd="202610", page_no=1, num_of_rows=100)
    result, _ = run(pages=[normalized])
    assert result["source"] == "molit" and result["synthetic"] is False
    assert result["total_count"] == 2 and result["matched_count"] == 1
    assert result["excluded_cancelled_count"] == 1 and result["coverage"] == "complete"


def test_default_fetcher_is_existing_molit_client(monkeypatch):
    calls = []
    async def fake(**kwargs):
        calls.append(kwargs)
        return page([record()])
    monkeypatch.setattr(market_evidence, "fetch_transactions", fake)
    result = asyncio.run(lookup_evidence(QUERY, mode="live", settings=FAKE_SETTINGS))
    assert len(calls) == 1 and result["matched_count"] == 1


def test_sample_historical_month_does_not_require_current_dates():
    result, _ = run(QUERY | {"deal_ymd": "201010"}, mode="sample", settings=object())
    assert result["source"] == "sample"
    assert all(item["contract_date"].startswith("2010-10") for item in result["items"])


def test_duplicate_record_ids_cannot_overstate_scan_coverage():
    with pytest.raises(EvidenceError) as failure:
        run(pages=[page([record(), record()])])
    assert failure.value.code == "EVIDENCE_INVALID_RESPONSE"


def test_oversized_record_page_is_rejected():
    with pytest.raises(EvidenceError) as failure:
        run(pages=[page([record(i) for i in range(101)])])
    assert failure.value.code == "EVIDENCE_INVALID_RESPONSE"
