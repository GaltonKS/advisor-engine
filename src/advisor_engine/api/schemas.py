"""Request bodies for the API routes (Pydantic models).

The field names, limits and defaults here are the API contract the web app relies on;
change them only together with `web/`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    objective: str = Field(default="", max_length=500)
    tone: str = Field(default="", max_length=100)


class ClientRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RegistrationIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    crd: str = Field(pattern=r"^[0-9]{1,8}$")  # FINRA CRD numbers are digits only
    firm_name: str = Field(min_length=1, max_length=200)


class ReviewIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    decision: Literal["approved", "rejected"]
    checks: list[str] = Field(default_factory=list, max_length=10)
    reason: str | None = Field(default=None, max_length=40)
    note: str = Field(default="", max_length=1000)


class FactorIn(BaseModel):
    factor_id: str = Field(min_length=1, max_length=100)


class TurnIn(BaseModel):
    message: str = Field(default="", max_length=4000)
    draft: bool = False  # ask for a client-ready draft instead of answering `message`


class ApproveIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


class HoldingRow(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    shares: float
    price: float
    sector: str = Field(default="", max_length=60)  # blank -> Unclassified
    asset_class: str = Field(default="", max_length=60)  # blank -> equity


class HoldingsIn(BaseModel):
    holdings: list[HoldingRow] = Field(min_length=1, max_length=500)
    label: str = Field(default="", max_length=120)


class StressIn(BaseModel):
    scenario_id: str | None = None  # a preset, or else the custom shocks below
    sector_shocks: dict[str, float] = Field(default_factory=dict)
    default_shock: float = 0.0
    asset_class_shocks: dict[str, float] = Field(default_factory=dict)  # fixed_income, preferred, cash
    # A custom scenario's own words, and the AI suggestion it started from (if any), so
    # the result can say where its assumptions came from and the audit log records both.
    description: str = Field(default="", max_length=1000)
    suggested: dict | None = None


class SuggestIn(BaseModel):
    description: str = Field(min_length=1, max_length=1000)


class QuestionnaireIn(BaseModel):
    answers: dict[str, int]
