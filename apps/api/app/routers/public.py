"""Public endpoints for the landing page (no session)."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core import ratelimit
from app.core.config import get_settings
from app.core.deps import client_ip
from app.core.errors import ApiError, not_found
from app.services import contact
from app.services import email as mail

router = APIRouter(prefix="/public", tags=["public"])


def _enabled() -> None:
    if not get_settings().contact_email:
        raise not_found("Contact")


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    message: str = Field(min_length=1, max_length=4000)
    locale: str = "en"
    website: str = ""  # a field people never see: bots fill it in

    @field_validator("name", "message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v


@router.get("/contact")
async def contact_enabled():
    """Tells the landing page whether to show the form (404 when no contact address is configured)."""
    _enabled()
    return {"enabled": True}


@router.post("/contact", status_code=202)
async def send_contact(body: ContactIn, request: Request):
    _enabled()
    s = get_settings()
    await ratelimit.hit(f"contact:ip:{client_ip(request) or 'unknown'}", s.rl_contact_ip)
    await ratelimit.hit(f"contact:email:{str(body.email).lower()}", s.rl_contact_email)
    if body.website.strip():
        return {"status": "sent"}  # a bot: answered the same, nothing sent
    try:
        await mail.send_prepared(contact.build(body.name, str(body.email), body.message, body.locale))
    except OSError:
        raise ApiError(502, "send_failed", "The message couldn’t be sent. Try again later.") from None
    return {"status": "sent"}
