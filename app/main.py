"""Local preparation app. Only static assets are exposed as files."""
from datetime import datetime
from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.config import PROJECT_ROOT, Settings
from app.sample import sample_transactions
from app.schemas import ListingsFile, ScenariosFile
from app.molit import MolitError, fetch_transactions
from app.domain import DomainError
from app.scheduling import ScheduleError
from app.store import SessionStore, StateError
from app.tools import ToolService
from app.tools_routes import router as tools_router
from app.http_safety import BodySizeLimitMiddleware
from app.agent_protocol import AgentError
from app.agent_service import ChatService
from app.agent_routes import router as agent_router

# httpx request URLs contain serviceKey; disable its request-line INFO logging.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@lru_cache(maxsize=4)
def read_fixture(name: str) -> dict:
    allowed = {"listings", "inspections", "schedules", "scenarios"}
    if name not in allowed:
        raise ValueError("Unknown fixture")
    data = json.loads((PROJECT_ROOT / "data" / f"{name}.json").read_text(encoding="utf-8"))
    if name == "listings":
        return ListingsFile.model_validate(data).model_dump(mode="json")
    if name == "scenarios":
        return ScenariosFile.model_validate(data).model_dump(mode="json")
    return data


def error_response(code: str, message: str, status: int, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "retryable": retryable,
    }})


def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="집의 다음 · 사전 준비", version=__version__,
                          description="가상 자료와 실거래 조회를 분리한 Zoro 연결 전 준비 서버")
    application.state.settings = settings if settings is not None else Settings.from_env()
    application.state.molit_fetcher = fetch_transactions
    application.state.session_store = SessionStore(application.state.settings.state_db_path)
    application.state.tool_service = ToolService(application.state.session_store, {
        key: read_fixture(key) for key in ("listings", "inspections", "schedules")
    })
    application.state.agent_service = ChatService(application.state.tool_service, application.state.settings)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") == "cross-site":
            return error_response("CROSS_SITE_REQUEST", "이 화면에서 직접 요청해주세요.", 403)
        if request.url.path.startswith("/api/") and request.method in {"POST", "PATCH", "PUT", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return error_response("CROSS_SITE_REQUEST", "같은 프로그램 화면에서 요청해주세요.", 403)
            if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return error_response("JSON_REQUIRED", "JSON 형식으로 요청해주세요.", 415)
            length = request.headers.get("content-length", "")
            if not length.isdigit() or int(length) > 65536:
                return error_response("REQUEST_TOO_LARGE", "요청 크기를 줄여주세요. 최대 64KB까지 처리합니다.", 413)
        try:
            response = await call_next(request)
        except Exception:
            return error_response("INTERNAL_ERROR", "작업을 완료하지 못했습니다. 현재 상태를 새로 불러온 뒤 다시 시도해주세요.", 500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        # Pydantic error bodies can echo secrets supplied in arbitrary input.
        if _request.url.path.startswith("/api/sessions"):
            return error_response("INVALID_REQUEST", "조건·시간·확인값 형식과 현재 작업 버전을 확인해주세요.", 422)
        return error_response("INVALID_REQUEST", "입력값을 확인해주세요. 지역코드 5자리, 계약월 YYYYMM, 페이지 1~10000, 건수 1~100을 사용합니다.", 422)

    @application.exception_handler(StateError)
    async def state_error(_request, exc):
        return error_response(exc.code, exc.message, exc.http_status, exc.http_status == 503)

    @application.exception_handler(AgentError)
    async def agent_error(_request, exc):
        return error_response(exc.code, exc.message, exc.http_status, exc.retryable)

    @application.exception_handler(DomainError)
    @application.exception_handler(ScheduleError)
    async def tool_error(_request, exc):
        return error_response(exc.code, exc.message, 422)

    @application.exception_handler(HTTPException)
    async def http_error(_request, exc):
        return error_response("NOT_FOUND" if exc.status_code == 404 else "REQUEST_ERROR",
                              "요청한 경로 또는 기능을 찾을 수 없습니다." if exc.status_code == 404 else "요청을 처리할 수 없습니다.",
                              exc.status_code)

    @application.get("/", include_in_schema=False)
    async def index():
        return FileResponse(PROJECT_ROOT / "static" / "index.html")

    @application.get("/api/health")
    async def health():
        return {
            "app_name": "집의 다음", "version": __version__, "mode": "preparation",
            **application.state.settings.public_status(),
            "counts": {"listings": len(read_fixture("listings")["listings"]),
                       "scenarios": len(read_fixture("scenarios")["scenarios"])},
            "notice": "개발용 대화 실행과 수동 도구를 사용할 수 있습니다. Zoro 실제 모델은 서버 설정과 현장 검증이 필요합니다.",
            "tools": {"available": True, "mode": "manual_tools", "persistent": True},
        }

    @application.get("/api/listings")
    async def listings(
        max_deposit_krw: Annotated[int | None, Query(ge=0, le=1000000000000)] = None,
        min_area_m2: Annotated[float | None, Query(ge=0, le=1000, allow_inf_nan=False)] = None,
        status: Literal["available", "contracted", "verification_required"] | None = None,
    ):
        data = read_fixture("listings")
        filtered = [item for item in data["listings"]
                    if (max_deposit_krw is None or item["deposit_krw"] <= max_deposit_krw)
                    and (min_area_m2 is None or item["area_m2"] >= min_area_m2)
                    and (status is None or item["status"] == status)]
        return {**data, "listings": filtered}

    @application.get("/api/preparation")
    async def preparation():
        return {key: read_fixture(key) for key in ("inspections", "schedules", "scenarios")}

    @application.get("/api/transactions")
    async def transactions(
        kind: Literal["trade", "rent"] = "rent",
        lawd_cd: Annotated[str, Query(pattern=r"^[0-9]{5}$")] = "11680",
        deal_ymd: Annotated[str, Query(pattern=r"^[0-9]{6}$")] = "202610",
        page_no: Annotated[int, Query(ge=1, le=10000)] = 1,
        num_of_rows: Annotated[int, Query(ge=1, le=100)] = 20,
        mode: Literal["sample", "live"] = "sample",
    ):
        try:
            value = datetime.strptime(deal_ymd, "%Y%m")
            if value.year < 2006:
                raise ValueError
        except ValueError:
            return error_response("INVALID_REQUEST", "계약월은 2006년 이후의 유효한 YYYYMM 형식으로 입력해주세요.", 422)
        query = dict(kind=kind, lawd_cd=lawd_cd, deal_ymd=deal_ymd,
                     page_no=page_no, num_of_rows=num_of_rows)
        if mode == "sample":
            return sample_transactions(**query)
        cfg = application.state.settings
        key = cfg.molit_trade_api_key if kind == "trade" else cfg.molit_rent_api_key
        if not key:
            return error_response("MISSING_API_KEY", "실제 조회에는 서버의 .env에 해당 국토부 인증키를 입력한 뒤 서버를 다시 시작해야 합니다. 채팅이나 화면에 키를 입력하지 마세요.", 503)
        try:
            return await application.state.molit_fetcher(**query, api_key=key,
                                                        timeout_seconds=cfg.molit_timeout_seconds)
        except MolitError as exc:
            return error_response(exc.code, exc.message, exc.http_status, exc.retryable)
        except Exception:
            # Never return/log exceptions: transport exceptions often embed the key-bearing URL.
            return error_response("UPSTREAM_FAILURE", "외부 조회를 완료하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해주세요.", 502, True)

    application.include_router(tools_router)
    application.include_router(agent_router)
    application.add_middleware(BodySizeLimitMiddleware)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    application.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")
    return application


app = create_app()
