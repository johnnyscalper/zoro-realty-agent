"""Strict HTTP boundary for local manual execution tools."""
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

router = APIRouter(prefix="/api/sessions", tags=["manual-tools"])


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateRequest(StrictRequest):
    conditions: dict[str, Any] = Field(default_factory=dict)


class RevisionRequest(StrictRequest):
    expected_revision: StrictInt = Field(ge=0)


class ConditionsRequest(RevisionRequest):
    conditions: dict[str, Any]


class VerificationRequest(RevisionRequest):
    listing_id: str = Field(min_length=1, max_length=64)
    field: Literal["entry_steps", "step_free_access", "elevator", "noise_level"]


class InspectionRequest(RevisionRequest):
    task_id: str = Field(min_length=1, max_length=64)
    value: Any
    notes: str = Field(default="", max_length=2000)


class StatusRequest(RevisionRequest):
    status: Literal["available", "contracted", "verification_required"]


class EntryRequest(RevisionRequest):
    new_end: str = Field(min_length=1, max_length=48)


class ApplyRequest(RevisionRequest):
    proposal_id: str = Field(min_length=1, max_length=128)


@router.post("", status_code=201)
def create_session(payload: CreateRequest, request: Request):
    return request.app.state.tool_service.create(payload.conditions)


@router.get("/{session_id}")
def get_session(session_id: str, request: Request):
    return request.app.state.tool_service.get(session_id)


@router.patch("/{session_id}/conditions")
def conditions(session_id: str, payload: ConditionsRequest, request: Request):
    return request.app.state.tool_service.change_conditions(session_id, payload.expected_revision, payload.conditions)


@router.post("/{session_id}/search")
def search(session_id: str, payload: RevisionRequest, request: Request):
    return request.app.state.tool_service.search(session_id, payload.expected_revision)


@router.post("/{session_id}/verification-tasks")
def verification(session_id: str, payload: VerificationRequest, request: Request):
    return request.app.state.tool_service.request_verification(session_id, payload.expected_revision, payload.listing_id, payload.field)


@router.post("/{session_id}/inspections")
def inspection(session_id: str, payload: InspectionRequest, request: Request):
    return request.app.state.tool_service.confirm_inspection(session_id, payload.expected_revision, payload.task_id, payload.value, payload.notes)


@router.patch("/{session_id}/listings/{listing_id}/status")
def listing_status(session_id: str, listing_id: str, payload: StatusRequest, request: Request):
    return request.app.state.tool_service.change_status(session_id, payload.expected_revision, listing_id, payload.status)


@router.patch("/{session_id}/schedule/entry")
def entry(session_id: str, payload: EntryRequest, request: Request):
    return request.app.state.tool_service.change_entry(session_id, payload.expected_revision, payload.new_end)


@router.post("/{session_id}/schedule/proposals")
def proposals(session_id: str, payload: RevisionRequest, request: Request):
    return request.app.state.tool_service.propose(session_id, payload.expected_revision)


@router.post("/{session_id}/schedule/apply")
def apply(session_id: str, payload: ApplyRequest, request: Request):
    return request.app.state.tool_service.apply_schedule(session_id, payload.expected_revision, payload.proposal_id)
