import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Enter your name.")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)


class EmailIn(BaseModel):
    email: EmailStr


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ResetIn(TokenIn):
    password: str = Field(min_length=1, max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class UserOut(Out):
    id: uuid.UUID
    full_name: str
    email: str
    email_verified: bool
    is_system_admin: bool
    locale: str
    timezone: str
    notification_prefs: dict
    totp_enabled: bool
    last_workspace_id: uuid.UUID | None
    created_at: datetime


class ProfileIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    locale: str | None = Field(default=None, max_length=10)
    timezone: str | None = Field(default=None, max_length=64)
    notification_prefs: dict[str, bool] | None = None


class PasswordChangeIn(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class Disable2faIn(BaseModel):
    password: str = Field(max_length=256)
    code: str = Field(min_length=6, max_length=32)


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    settings: dict | None = None


class InviteIn(BaseModel):
    email: EmailStr
    role_id: uuid.UUID


class RoleChangeIn(BaseModel):
    role_id: uuid.UUID


class RoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    permissions: list[str]


class CheckoutIn(BaseModel):
    plan_id: uuid.UUID


class PlanIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9-]{2,50}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    price_cents: int = Field(ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    billing_cycle: str = Field(default="month", pattern=r"^(month|year)$")
    token_allowance: int = Field(ge=0)
    limits: dict[str, int] = {}
    features: list[str] = []
    is_public: bool = True
    is_active: bool = True
    sort_order: int = 0


class PlanUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    price_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    billing_cycle: str | None = Field(default=None, pattern=r"^(month|year)$")
    token_allowance: int | None = Field(default=None, ge=0)
    limits: dict[str, int] | None = None
    features: list[str] | None = None
    is_public: bool | None = None
    is_active: bool | None = None
    sort_order: int | None = None


class PlanOut(Out):
    id: uuid.UUID
    key: str
    name: str
    description: str
    price_cents: int
    currency: str
    billing_cycle: str
    token_allowance: int
    limits: dict
    features: list
    is_public: bool
    is_active: bool
    sort_order: int


class MultiplierIn(BaseModel):
    model: str = Field(min_length=1, max_length=80)
    operation: str = Field(default="*", max_length=40)
    input_multiplier: float = Field(ge=0, le=1000)
    output_multiplier: float = Field(ge=0, le=1000)
    input_cost_per_m: float = Field(default=0, ge=0)
    output_cost_per_m: float = Field(default=0, ge=0)


class AdjustTokensIn(BaseModel):
    delta: int
    reason: str = Field(min_length=3, max_length=300)


class StatusIn(BaseModel):
    status: str = Field(pattern=r"^(active|suspended)$")
    reason: str = Field(default="", max_length=300)
