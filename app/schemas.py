"""Pydantic request models with input validation and normalisation."""

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Indian mobile numbers: 10 digits starting 6-9, optional +91 / 0 prefix.
_PHONE_RE = re.compile(r"^[6-9]\d{9}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,59}$")
_CITY_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,39}$")

Timeline = Literal["2_weeks", "1_month", "3_months", "just_exploring"]
PoolStatus = Literal["open", "negotiating", "deal_secured", "closed"]


def normalise_phone(raw: str) -> str:
    digits = re.sub(r"[\s\-()]", "", raw)
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if not _PHONE_RE.match(digits):
        raise ValueError("Enter a valid 10-digit Indian mobile number")
    return digits


class JoinRequest(BaseModel):
    bike_id: int = Field(gt=0)
    city: str
    name: str
    phone: str
    email: Optional[str] = None
    timeline: Timeline = "1_month"

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        v = " ".join(v.split())
        if not _CITY_RE.match(v):
            raise ValueError("City must be 2-40 letters")
        return v.title()

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not _NAME_RE.match(v):
            raise ValueError("Name must be 2-60 letters")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)

    @field_validator("email")
    @classmethod
    def _email(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if len(v) > 120 or not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address")
        return v


class PoolUpdate(BaseModel):
    status: Optional[PoolStatus] = None
    target_size: Optional[int] = Field(default=None, ge=2, le=500)
    dealer_name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    discount_inr: Optional[int] = Field(default=None, gt=0, le=1_000_000)
    deal_valid_until: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
