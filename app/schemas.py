"""Shared fixture definitions. KRW is always an integer, not 만원."""
from datetime import date, datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    district_code: Literal["11680"]
    neighborhood: str
    deposit_krw: int = Field(gt=0)
    area_m2: float = Field(gt=0)
    floor: int
    elevator: bool | None
    step_free_access: bool | None
    entry_steps: int | None = Field(ge=0)
    noise_level: Literal["low", "medium", "high"] | None
    available_from: date
    status: Literal["available", "contracted", "verification_required"]
    source_type: Literal["synthetic"]
    notes: str
    verified_at: datetime | None


class ListingsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    synthetic: Literal[True]
    notice: str = "모든 매물은 시연용 가상 자료입니다."
    listings: list[Listing]

    @model_validator(mode="after")
    def unique_ids(self):
        if len({item.id for item in self.listings}) != len(self.listings):
            raise ValueError("Listing identifiers must be unique")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    category: Literal["consultation", "reverification", "schedule", "api"]
    given: dict[str, Any]
    event: dict[str, Any]
    expected_actions: list[str] = Field(min_length=1)
    must_not: list[str] = Field(min_length=1)
    verification_stage: Literal["offline", "zoro_required", "live_api_required"]


class ScenariosFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    synthetic: Literal[True]
    notice: str = "검증 계획이며 모델 통과 결과가 아닙니다."
    scenarios: list[Scenario]
