"""Strict same-origin HTTP entry points; clients cannot supply model credentials."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator


router = APIRouter(tags=["agent"])


class RequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: StrictInt = Field(ge=0)
    request_id: str = Field(min_length=36, max_length=36)

    @field_validator("request_id")
    @classmethod
    def valid_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("invalid request id")
        return value


class ChatRequest(RequestBase):
    message: str = Field(min_length=1, max_length=2000)
    mode: Literal["development", "zoro"] = "development"
    data_mode: Literal["sample", "live"] = "sample"


class ConfirmRequest(RequestBase):
    pending_id: str = Field(min_length=1, max_length=64)
    approve: StrictBool


@router.get("/api/agent/status")
def status(request: Request):
    return request.app.state.agent_service.public_status()


@router.post("/api/sessions/{session_id}/chat")
async def chat(session_id: str, payload: ChatRequest, request: Request):
    return await request.app.state.agent_service.chat(session_id, payload.model_dump(), molit_fetcher=request.app.state.molit_fetcher)


@router.post("/api/sessions/{session_id}/chat/confirm")
async def confirm(session_id: str, payload: ConfirmRequest, request: Request):
    return await request.app.state.agent_service.confirm(session_id, payload.model_dump(), molit_fetcher=request.app.state.molit_fetcher)
