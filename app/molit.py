"""Bounded MOLIT XML client. No network calls, keys or response bodies are logged.

The two service endpoints and request parameters are verified against the
official portal. Item-level fixtures are synthetic pending an authenticated
live smoke test. Fetching never substitutes sample data for a live failure.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote

import httpx
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import ParseError


ENDPOINTS = {
    "trade": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    "rent": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# JSON money must remain an exact safe integer in browser JavaScript Numbers.
MAX_MONEY_KRW = 9_007_199_254_740_991
HISTORICAL_NOTICE = "신고된 과거 계약 자료입니다. 현재 거래 가능한 매물이나 입주 가능 여부를 뜻하지 않습니다."


class MolitError(Exception):
    """Public error with intentionally fixed, secret-free diagnostics."""

    def __init__(self, code: str, message: str, retryable: bool = False, http_status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status


def _invalid_response() -> MolitError:
    return MolitError("MOLIT_INVALID_RESPONSE", "국토부 응답 형식을 확인할 수 없습니다. 응답 명세를 점검해 주세요.")


def _validate_query(kind: str, lawd_cd: str, deal_ymd: str, page_no: int, num_of_rows: int) -> None:
    valid = (
        kind in ENDPOINTS
        and isinstance(lawd_cd, str) and re.fullmatch(r"[0-9]{5}", lawd_cd)
        and isinstance(deal_ymd, str) and re.fullmatch(r"[0-9]{6}", deal_ymd)
        and type(page_no) is int and 1 <= page_no <= 10000
        and type(num_of_rows) is int and 1 <= num_of_rows <= 100
    )
    try:
        if not valid:
            raise ValueError
        date(int(deal_ymd[:4]), int(deal_ymd[4:]), 1)
    except (TypeError, ValueError):
        raise MolitError("INVALID_QUERY", "조회 종류·지역코드 5자리·계약월·페이지 범위를 확인해 주세요.", False, 400) from None


def _normalize_key(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise MolitError("MOLIT_KEY_MISSING", "국토부 API 키가 설정되지 않았습니다. 서버 설정 파일을 확인해 주세요.", False, 503)
    # unquote_plus would corrupt a literal '+' in a Decoding key.
    try:
        key = unquote(api_key.strip(), encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        raise MolitError("MOLIT_KEY_INVALID", "인증키 형식을 확인해 주세요. 일반 인증키(Decoding)를 권장합니다.", False, 503) from None
    if len(key) > 1024 or not key.isascii() or any(c.isspace() or ord(c) < 32 for c in key) or "%" in key:
        raise MolitError("MOLIT_KEY_INVALID", "인증키 형식을 확인해 주세요. 일반 인증키(Decoding)를 권장합니다.", False, 503)
    return key


class _DropCredentialLogs(logging.Filter):
    """Drop transport details, including provider echoes without `serviceKey`."""

    def filter(self, record: logging.LogRecord) -> bool:
        # HTTPX logs full URLs and httpcore logs raw headers/exceptions. A key
        # can appear without its parameter name, so content matching is unsafe.
        # Do not format the untrusted record, and leave app/audit logs alone.
        return False


def _protect_transport_logging() -> None:
    # Logger filters do not apply to propagated child records. Cover each
    # transport logger used by the pinned HTTPX/httpcore dependency versions.
    for name in ("httpx", "httpcore", "httpcore.connection", "httpcore.http11",
                 "httpcore.http2", "httpcore.proxy", "httpcore.socks"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _DropCredentialLogs) for f in logger.filters):
            logger.addFilter(_DropCredentialLogs())


def _service_error(code: str, message: str = "") -> MolitError | None:
    """Classify provider codes/tokens; never return its untrusted message."""
    token = message.strip().upper()
    code = code.strip().lstrip("0") or "0"
    if code in {"20", "21", "29", "30", "31", "32", "33"} or any(t in token for t in (
        "SERVICE_KEY", "PERMISSION_DENIED", "SERVICE_ACCESS_DENIED", "ACCESS_DENIED",
        "DEADLINE_HAS_EXPIRED", "UNAUTHORIZED", "BLACKLIST_IP_ACCESS",
    )):
        return MolitError("MOLIT_AUTH_ERROR", "국토부 인증 또는 이용 권한을 확인해 주세요. 키와 두 API의 활용승인 상태를 점검해 주세요.", False, 502)
    if code in {"22", "23"} or "LIMITED_NUMBER_OF_SERVICE_REQUESTS" in token or "QUOTA" in token:
        return MolitError("MOLIT_RATE_LIMIT", "국토부 호출 허용량 또는 초당 요청 한도를 초과했습니다. 잠시 후 이용 한도를 확인해 주세요.", False, 429)
    if code == "5" or "SERVICETIMEOUT" in token:
        return MolitError("MOLIT_TIMEOUT", "국토부 응답 대기시간을 초과했습니다. 잠시 후 다시 시도해 주세요.", True, 504)
    if code in {"10", "11", "12"} or "INVALID_REQUEST_PARAMETER" in token or "NO_OPENAPI_SERVICE" in token:
        return MolitError("MOLIT_REQUEST_REJECTED", "국토부가 요청을 거절했습니다. 서비스 주소와 조회 조건을 확인해 주세요.", False, 502)
    if code != "0":
        return MolitError("MOLIT_UPSTREAM_ERROR", "국토부 서비스에서 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", True, 502)
    return None


def _text(element: Any, tag: str, required: bool = False) -> str | None:
    child = element.find(tag)
    result = None if child is None or child.text is None else child.text.strip()
    if not result:
        if required:
            raise ValueError("missing field")
        return None
    return result


def _int(value: str | None, *, required: bool = True, minimum: int | None = None) -> int | None:
    if value is None and not required:
        return None
    if value is None or not re.fullmatch(r"-?[0-9]+", value):
        raise ValueError("invalid integer")
    number = int(value)
    if minimum is not None and number < minimum:
        raise ValueError("invalid integer range")
    return number


def _money(value: str | None) -> int:
    if value is None or not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]{1,4})?", value):
        raise ValueError("invalid amount")
    amount = Decimal(value.replace(",", "")) * Decimal(10000)
    if not amount.is_finite() or amount < 0 or amount > MAX_MONEY_KRW or amount != amount.to_integral_value():
        raise ValueError("invalid amount range")
    return int(amount)


def _cancel_date(value: str | None) -> str | None:
    if value is None or value == "-":
        return None
    if re.fullmatch(r"[0-9]{8}", value):
        parts = (int(value[:4]), int(value[4:6]), int(value[6:]))
    elif re.fullmatch(r"[0-9]{4}[-.][0-9]{2}[-.][0-9]{2}", value):
        parts = tuple(map(int, re.split(r"[-.]", value)))
    elif re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", value):
        year, month, day = map(int, value.split("."))
        parts = (2000 + year, month, day)
    else:
        raise ValueError("invalid cancellation date")
    return date(*parts).isoformat()


def _item(item: Any, kind: str, lawd_cd: str, deal_ymd: str, absolute_index: int) -> dict:
    contract_date = date(
        _int(_text(item, "dealYear", True)),
        _int(_text(item, "dealMonth", True)),
        _int(_text(item, "dealDay", True)),
    )
    if contract_date.strftime("%Y%m") != deal_ymd:
        raise ValueError("month mismatch")
    district = _text(item, "sggCd")
    if district is not None and district != lawd_cd:
        raise ValueError("district mismatch")
    area = float(_text(item, "excluUseAr", True))
    if not math.isfinite(area) or not 0 < area <= 10000:
        raise ValueError("invalid area")
    apartment = _text(item, "aptNm", True)
    dong = _text(item, "umdNm", True)
    if len(apartment) > 300 or len(dong) > 100:
        raise ValueError("oversized name")
    floor = _int(_text(item, "floor", True))
    if not -20 <= floor <= 300:
        raise ValueError("invalid floor")
    build_year = _int(_text(item, "buildYear"), required=False)
    if build_year is not None and not 1800 <= build_year <= contract_date.year:
        raise ValueError("invalid build year")
    price = deposit = monthly = None
    cancelled = False
    cancelled_date = None
    if kind == "trade":
        price = _money(_text(item, "dealAmount", True))
        if price <= 0:
            raise ValueError("invalid sale amount")
        cancelled_date = _cancel_date(_text(item, "cdealDay"))
        cancellation_flag = (_text(item, "cdealType") or "").upper()
        if cancellation_flag not in {"", "-", "O", "Y", "N", "0", "1"}:
            raise ValueError("unknown cancellation status")
        cancelled = cancellation_flag in {"O", "Y", "1"} or cancelled_date is not None
        lease_type = "trade"
    else:
        deposit = _money(_text(item, "deposit", True))
        monthly = _money(_text(item, "monthlyRent", True))
        lease_type = "jeonse" if monthly == 0 else "monthly_rent"
    identity = "|".join(map(str, (kind, lawd_cd, contract_date, apartment, dong, area, floor, price, deposit, monthly, cancelled, cancelled_date)))
    # Public source has no guaranteed unique transaction identifier. The index
    # preserves distinct identical contracts within one requested page snapshot.
    record_id = f"molit-{kind}-{hashlib.sha256(identity.encode()).hexdigest()[:16]}-{absolute_index}"
    return {
        "id": record_id, "apartment_name": apartment, "legal_dong": dong,
        "contract_date": contract_date.isoformat(), "area_m2": area, "floor": floor,
        "build_year": build_year, "price_krw": price, "deposit_krw": deposit,
        "monthly_rent_krw": monthly, "cancelled": cancelled, "cancelled_date": cancelled_date,
        "lease_type": lease_type, "cancellation_status_available": kind == "trade",
    }


def _page(kind: str, lawd_cd: str, deal_ymd: str, page_no: int, num_of_rows: int, total: int, items: list, skipped: int = 0) -> dict:
    warnings = [HISTORICAL_NOTICE]
    if kind == "rent":
        warnings.append("전월세 자료에서 해제 여부는 이 응답으로 확인하지 않습니다. cancelled=false를 계약 유효성 보장으로 해석하지 마세요.")
    if skipped:
        warnings.append(f"필수 정보가 없거나 형식이 맞지 않는 거래 {skipped}건을 제외했습니다. 제외 건수를 확인해 주세요.")
    return {
        "source": "molit", "synthetic": False, "kind": kind, "lawd_cd": lawd_cd,
        "deal_ymd": deal_ymd, "page_no": page_no, "num_of_rows": num_of_rows,
        "total_count": total, "returned_count": len(items), "skipped_count": skipped,
        "has_next": page_no * num_of_rows < total,
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "items": items, "warnings": warnings,
    }


def parse_transactions(xml_content: bytes | str, *, kind: str, lawd_cd: str, deal_ymd: str, page_no: int, num_of_rows: int) -> dict:
    """Parse an official-format response, distinguishing empty from malformed.

    Missing/invalid rows are explicitly counted; the provider's total_count is
    never rewritten to pretend that every row was usable.
    """
    _validate_query(kind, lawd_cd, deal_ymd, page_no, num_of_rows)
    raw = xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise MolitError("MOLIT_RESPONSE_TOO_LARGE", "응답 크기가 허용 범위를 초과했습니다. 페이지당 건수를 줄여 주세요.")
    try:
        root = SafeET.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (ParseError, DefusedXmlException, ValueError, UnicodeError):
        # Some gateways use a bare error token rather than XML.
        token = raw[:4096].decode("utf-8", "replace").strip().upper()
        if token and "<" not in token:
            if token in {"NODATA_ERROR", "NO_DATA"}:
                return _page(kind, lawd_cd, deal_ymd, page_no, num_of_rows, 0, [])
            classified = _service_error("0", token)
            if classified:
                raise classified from None
        raise _invalid_response() from None
    for node in root.iter():
        if isinstance(node.tag, str):
            node.tag = node.tag.rsplit("}", 1)[-1]
    header = root.find("header")
    gateway = root.find("cmmMsgHeader")
    if gateway is not None:
        code = _text(gateway, "returnReasonCode")
        auth_message = _text(gateway, "returnAuthMsg")
        gateway_message = _text(gateway, "errMsg")
        message = auth_message or gateway_message or ""
        # Both fields can carry error tokens. A no-data message in one must
        # not hide an authentication, quota, or timeout error in the other.
        error_message = " ".join(value for value in (auth_message, gateway_message) if value)
    elif header is not None:
        code = _text(header, "resultCode")
        message = _text(header, "resultMsg") or ""
        error_message = message
    else:
        raise _invalid_response()
    if code is None:
        raise _invalid_response()
    no_data_code = code.lstrip("0") == "3"
    # A no-data message must not hide an explicit authentication, quota, or
    # service failure. Code 03 is the provider's recognized no-data response.
    error = _service_error("0" if no_data_code else code, error_message)
    if error:
        raise error
    body = root.find("body")
    if no_data_code or message.upper() in {"NODATA_ERROR", "NO_DATA"}:
        # Header-only no-data replies are supported. When a body is present,
        # do not discard rows or a contradictory total to manufacture 0 rows.
        if body is not None:
            try:
                if body.findall("items/item"):
                    raise ValueError("no-data response contains records")
                if body.find("totalCount") is not None and _int(_text(body, "totalCount", True), minimum=0) != 0:
                    raise ValueError("no-data response has nonzero total")
            except (TypeError, ValueError):
                raise _invalid_response() from None
        return _page(kind, lawd_cd, deal_ymd, page_no, num_of_rows, 0, [])
    if body is None:
        raise _invalid_response()
    try:
        total = _int(_text(body, "totalCount", True), minimum=0)
        actual_page = _int(_text(body, "pageNo", True), minimum=1)
        actual_size = _int(_text(body, "numOfRows", True), minimum=1)
        elements = body.findall("items/item")
        offset = (page_no - 1) * num_of_rows
        remaining = max(total - offset, 0)
        if actual_page != page_no or actual_size != num_of_rows or len(elements) > num_of_rows or len(elements) > remaining:
            raise ValueError("pagination mismatch")
        if remaining > 0 and not elements:
            raise ValueError("missing records")
    except (TypeError, ValueError):
        raise _invalid_response() from None
    items, skipped = [], 0
    for index, element in enumerate(elements):
        try:
            items.append(_item(element, kind, lawd_cd, deal_ymd, (page_no - 1) * num_of_rows + index))
        except (ValueError, TypeError, InvalidOperation, OverflowError):
            skipped += 1
    return _page(kind, lawd_cd, deal_ymd, page_no, num_of_rows, total, items, skipped)


async def fetch_transactions(*, kind: str, lawd_cd: str, deal_ymd: str, page_no: int, num_of_rows: int, api_key: str, client: httpx.AsyncClient | None = None, timeout_seconds: float = 10.0) -> dict:
    _validate_query(kind, lawd_cd, deal_ymd, page_no, num_of_rows)
    key = _normalize_key(api_key)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 30:
        raise MolitError("INVALID_TIMEOUT", "응답 대기시간은 1초에서 30초 사이여야 합니다.", False, 400)
    _protect_transport_logging()
    params = {"serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd, "pageNo": page_no, "numOfRows": num_of_rows}
    owned = client is None
    active = client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
    try:
        async with active.stream("GET", ENDPOINTS[kind], params=params, timeout=timeout_seconds, follow_redirects=False, headers={"Accept": "application/xml", "User-Agent": "ZoroRealtyPrep/0.1"}) as response:
            status = response.status_code
            if status in {401, 403}:
                raise _service_error("30")
            if status == 429:
                raise _service_error("22")
            if 300 <= status < 400:
                raise MolitError("MOLIT_REDIRECT_REJECTED", "국토부 응답이 다른 주소로 이동을 요청했습니다. 서비스 주소를 확인해 주세요.")
            if status in {408, 504}:
                raise _service_error("05")
            if status >= 500:
                raise MolitError("MOLIT_UPSTREAM_ERROR", "국토부 서비스에 일시적인 오류가 있습니다. 잠시 후 다시 시도해 주세요.", True, 502)
            if status != 200:
                raise MolitError("MOLIT_HTTP_ERROR", "국토부 조회 요청을 처리하지 못했습니다. 조회 조건과 서비스 상태를 확인해 주세요.", False, 502)
            length = response.headers.get("content-length")
            if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                raise MolitError("MOLIT_RESPONSE_TOO_LARGE", "응답 크기가 허용 범위를 초과했습니다. 페이지당 건수를 줄여 주세요.")
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise MolitError("MOLIT_RESPONSE_TOO_LARGE", "응답 크기가 허용 범위를 초과했습니다. 페이지당 건수를 줄여 주세요.")
                chunks.append(chunk)
            return parse_transactions(b"".join(chunks), kind=kind, lawd_cd=lawd_cd, deal_ymd=deal_ymd, page_no=page_no, num_of_rows=num_of_rows)
    except MolitError:
        raise
    except httpx.TimeoutException:
        raise MolitError("MOLIT_TIMEOUT", "국토부 응답 대기시간을 초과했습니다. 잠시 후 다시 시도해 주세요.", True, 504) from None
    except httpx.RequestError:
        raise MolitError("MOLIT_CONNECTION_ERROR", "국토부 서비스에 연결할 수 없습니다. 인터넷 연결과 서비스 상태를 확인해 주세요.", True, 502) from None
    finally:
        if owned:
            await active.aclose()
