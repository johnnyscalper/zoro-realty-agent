"""Explicit authenticated smoke check; counts only, never raw records or keys.

Run locally after key setup. A successful empty page verifies a response, not
item-level field mapping. See docs/API_REFERENCE.md for the remaining checks.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.molit import MolitError, fetch_transactions
from scripts.configure_keys import KeySetupError, SafeArgumentParser, atomic_write_private


REPORT_PATH = ROOT / "reports" / "api_verification_report.json"
PAGE_SIZE = 5
SAFE_ERRORS = {
    "INVALID_QUERY": "조회 종류·지역코드 5자리·계약월을 확인하세요.",
    "INVALID_TIMEOUT": "응답 대기시간 설정을 확인하세요.",
    "MOLIT_KEY_MISSING": "국토부 인증키를 로컬 키 설정 도구에서 입력하세요.",
    "MOLIT_KEY_INVALID": "일반 인증키(Decoding) 형식을 확인하세요.",
    "MOLIT_AUTH_ERROR": "인증키와 해당 API 활용승인 상태를 확인하세요.",
    "MOLIT_RATE_LIMIT": "호출 허용량을 확인한 뒤 나중에 다시 실행하세요.",
    "MOLIT_TIMEOUT": "응답 대기시간을 초과했습니다. 나중에 다시 실행하세요.",
    "MOLIT_CONNECTION_ERROR": "인터넷 연결과 국토부 서비스 상태를 확인하세요.",
    "MOLIT_REQUEST_REJECTED": "국토부가 요청을 거절했습니다. 서비스와 조회 조건을 확인하세요.",
    "MOLIT_UPSTREAM_ERROR": "국토부 서비스 오류입니다. 나중에 다시 실행하세요.",
    "MOLIT_HTTP_ERROR": "국토부 요청을 처리하지 못했습니다. 서비스 상태를 확인하세요.",
    "MOLIT_REDIRECT_REJECTED": "다른 주소로 이동하는 응답을 거절했습니다. 서비스 주소를 확인하세요.",
    "MOLIT_RESPONSE_TOO_LARGE": "응답 크기가 허용 범위를 초과했습니다.",
    "MOLIT_INVALID_RESPONSE": "응답 형식 또는 건수 정보를 확인하지 못했습니다.",
    "CONFIGURATION_ERROR": "로컬 설정의 형식을 확인하세요.",
    "FAILED": "상세 응답이나 인증키를 공유하지 말고 실행 환경을 확인하세요.",
}
HISTORICAL_WARNING = "신고된 과거 계약 자료이며 현재 거래 가능한 매물이나 입주 가능 여부를 뜻하지 않습니다."
RENT_WARNING = "전월세 응답의 해제 여부는 확인하지 않습니다. 계약 유효성 보장으로 해석하지 마세요."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_query(kind, lawd_cd, month) -> bool:
    if not (isinstance(kind, str) and kind in {"trade", "rent", "both"}
            and isinstance(lawd_cd, str) and re.fullmatch(r"[0-9]{5}", lawd_cd)
            and isinstance(month, str) and re.fullmatch(r"[0-9]{6}", month)):
        return False
    try:
        date(int(month[:4]), int(month[4:]), 1)
    except ValueError:
        return False
    return True


def _entry(status: str, code: str, warnings: list[str]) -> dict:
    return {
        "status": status, "code": code, "checked_at": _now(),
        "total_count": None, "returned_count": None, "skipped_count": None,
        "warnings": warnings,
    }


def _summarize_page(page: dict, *, kind: str, lawd_cd: str, month: str) -> dict:
    """Allowlist scalars; do not copy provider warnings, errors or metadata."""
    try:
        total, returned, skipped = (page[name] for name in ("total_count", "returned_count", "skipped_count"))
        if not (page.get("source") == "molit" and page.get("synthetic") is False
                and page.get("kind") == kind and page.get("lawd_cd") == lawd_cd
                and page.get("deal_ymd") == month
                and all(type(value) is int and 0 <= value <= 2**63 - 1 for value in (total, returned, skipped))
                and returned + skipped <= min(PAGE_SIZE, total)
                and (total == 0 or returned + skipped > 0)):
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError):
        raise MolitError("MOLIT_INVALID_RESPONSE", "") from None
    warnings = [HISTORICAL_WARNING]
    if kind == "rent":
        warnings.append(RENT_WARNING)
    if total == 0:
        warnings.append("정상 응답이지만 거래 0건입니다. 개별 거래 필드의 변환은 이 조회로 검증하지 못했습니다.")
    if skipped:
        warnings.append(f"형식 확인에 실패한 거래 {skipped}건을 제외했습니다. 필드 변환을 추가 점검하세요.")
    result = _entry("PARTIAL" if skipped else "OK", "ROWS_SKIPPED" if skipped else "OK", warnings)
    result.update(total_count=total, returned_count=returned, skipped_count=skipped)
    return result


def _print_entry(kind: str, result: dict) -> None:
    if result["status"] in {"OK", "PARTIAL"}:
        print(f"{kind}: {result['status']} — 총 {result['total_count']}건, 이번 페이지 {result['returned_count']}건, 형식 제외 {result['skipped_count']}건")
    else:
        print(f"{kind}: {result['status']} — {result['code']}")
    for warning in result["warnings"]:
        print(f"  {warning}")


async def check(args, *, settings: Settings | None = None, report_path: Path | None = None) -> int:
    """Keep the existing check(args) CLI contract; explicit invocation is live."""
    started_at = _now()
    kind, lawd_cd, month = (getattr(args, name, None) for name in ("kind", "lawd_cd", "month"))
    valid_query = _valid_query(kind, lawd_cd, month)
    requested = ["trade", "rent"] if kind == "both" else [kind] if kind in ("trade", "rent") else ["trade", "rent"]
    configuration_error = False
    try:
        cfg = settings if settings is not None else Settings.from_env()
    except Exception:
        cfg = None
        configuration_error = True
    results = {}
    for service in ("trade", "rent"):
        if service not in requested:
            results[service] = _entry("NOT_REQUESTED", "NOT_REQUESTED", ["이번 명령에서 선택하지 않은 API입니다."])
            continue
        if not valid_query:
            result = _entry("NOT_RUN", "INVALID_QUERY", [SAFE_ERRORS["INVALID_QUERY"]])
        elif configuration_error:
            result = _entry("NOT_RUN", "CONFIGURATION_ERROR", [SAFE_ERRORS["CONFIGURATION_ERROR"]])
        else:
            key = cfg.molit_trade_api_key if service == "trade" else cfg.molit_rent_api_key
            if not key:
                result = _entry("NOT_RUN", "MOLIT_KEY_MISSING", [SAFE_ERRORS["MOLIT_KEY_MISSING"]])
            else:
                try:
                    page = await fetch_transactions(
                        kind=service, lawd_cd=lawd_cd, deal_ymd=month, page_no=1,
                        num_of_rows=PAGE_SIZE, api_key=key, timeout_seconds=cfg.molit_timeout_seconds,
                    )
                    result = _summarize_page(page, kind=service, lawd_cd=lawd_cd, month=month)
                except MolitError as exc:
                    # Treat even client error strings as untrusted.
                    code = exc.code if isinstance(exc.code, str) and exc.code in SAFE_ERRORS else "FAILED"
                    result = _entry("FAILED", code, [SAFE_ERRORS[code]])
                except Exception:
                    result = _entry("FAILED", "FAILED", [SAFE_ERRORS["FAILED"]])
        results[service] = result
        _print_entry(service, result)
    complete = all(result["status"] == "OK" for result in results.values())
    statuses = {result["status"] for result in results.values()}
    overall = "VERIFIED" if complete else "PARTIAL" if statuses & {"OK", "PARTIAL"} else "FAILED" if "FAILED" in statuses else "NOT_RUN"
    report = {
        "schema_version": "1.1", "started_at": started_at, "finished_at": _now(),
        # Store only fully validated query scalars. Invalid arguments may contain
        # sensitive text and must never be copied into the shareable report.
        "query": {
            "kind": kind, "lawd_cd": lawd_cd, "deal_ymd": month,
            "page_no": 1, "num_of_rows": PAGE_SIZE,
        } if valid_query else None,
        # These describe the requested route and evidence scope, not a claim
        # that NOT_RUN/FAILED entries completed an authenticated request.
        "provenance": {
            "requested_mode": "live", "provider": "molit", "synthetic_fallback": False,
            "verification_scope": "single_page_authenticated_response",
        },
        "status": overall, "both_apis_verified": complete, "services": results,
        "warnings": ["이번 실행 시점의 인증 조회 결과입니다. 이후 키·승인·서비스 상태를 보장하지 않습니다.",
                     "한 페이지의 조회 결과이며 전체 거래 자료·현장 상태·Zoro 실행을 검증하지 않습니다."],
    }
    try:
        atomic_write_private(report_path if report_path is not None else REPORT_PATH, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    except KeySetupError:
        print("검증 보고서를 저장하지 못했습니다. reports 폴더 권한과 남은 공간을 확인하세요.")
        return 1
    print("검증 보고서: reports/api_verification_report.json (인증키·단지명·원본 응답은 기록하지 않습니다.)")
    if complete:
        print("이번 실행에서 매매·전월세 조회에 모두 정상 응답을 받았습니다. 앱을 다시 시작한 뒤 실제 API 모드를 선택하세요.")
    elif kind == "both":
        print("두 API 전체 검증이 완료되지 않았습니다. 각 상태와 안내를 확인한 뒤 다시 실행하세요.")
    else:
        print("다른 API까지 검증하려면 --kind both로 다시 실행하세요.")
    return 0 if all(results[service]["status"] == "OK" for service in requested) else 1


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description="사용자 키로 국토부 API를 실제 조회하고 비밀값 없는 검증 보고서를 저장합니다.")
    parser.add_argument("--kind", choices=["trade", "rent", "both"], default="both")
    parser.add_argument("--lawd-cd", default="11680")
    parser.add_argument("--month", default="202610")
    try:
        return asyncio.run(check(parser.parse_args(argv)))
    except KeyboardInterrupt:
        print("검증을 중단했습니다. 기존 보고서는 이번 실행의 완료 기록이 아닙니다.")
        return 1
    except Exception:
        print("검증을 완료하지 못했습니다. 기존 보고서의 시각을 확인하고 실행 환경을 점검하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
