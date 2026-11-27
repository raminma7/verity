import ipaddress
import uuid
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, forbidden
from app.core.security import hash_token
from app.models import Membership, Session, Subscription, User, Workspace
from app.models.base import utcnow
from app.services.subscriptions import get_subscription, is_active

Db = Annotated[AsyncSession, Depends(get_db)]


@lru_cache(maxsize=8)
def _networks(cidrs: tuple[str, ...]) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return tuple(ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip())


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """An address from a forwarded hop: "203.0.113.7", "203.0.113.7:4711", "[2001:db8::1]:443" or "2001:db8::1"."""
    v = value.strip().strip('"')
    if v.startswith("["):
        v = v[1 : v.find("]")] if "]" in v else ""
    elif v.count(":") == 1:
        v = v.split(":", 1)[0]
    try:
        addr = ipaddress.ip_address(v)
    except ValueError:
        return None
    return addr.ipv4_mapped or addr if isinstance(addr, ipaddress.IPv6Address) else addr


def client_ip(request: Request) -> str | None:
    """The caller's address, for per-IP rate limits and audit logs.

    X-Forwarded-For is only believed when the connection comes from a trusted proxy (VERITY_TRUSTED_PROXIES;
    the Next.js server on the same host by default). Then the right-most hop that isn't a trusted proxy is the
    client: anything to its left was written by the client and can be forged.
    """
    peer = request.client.host if request.client else None
    if peer is None:
        return None
    nets = _networks(tuple(get_settings().trusted_proxies))
    peer_addr = _address(peer)
    if peer_addr is None or not any(peer_addr in n for n in nets):
        return peer
    fwd = ",".join(request.headers.getlist("x-forwarded-for"))
    ip = str(peer_addr)
    for hop in reversed([h for h in fwd.split(",") if h.strip()]):
        addr = _address(hop)
        if addr is None:
            break  # a malformed hop: don't trust anything left of it
        ip = str(addr)
        if not any(addr in n for n in nets):
            return ip
    return ip


async def _load_session(request: Request, db: AsyncSession) -> tuple[Session, User] | None:
    token = request.cookies.get(get_settings().session_cookie)
    if not token:
        return None
    sess = (await db.execute(select(Session).where(Session.token_hash == hash_token(token)))).scalar_one_or_none()
    now = utcnow()
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
        return None
    user = await db.get(User, sess.user_id)
    if user is None:
        return None
    if now - sess.last_seen_at > timedelta(minutes=5):
        sess.last_seen_at = now
        await db.commit()
    return sess, user


async def get_session_user(request: Request, db: Db) -> tuple[Session, User]:
    loaded = await _load_session(request, db)
    if loaded is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue.")
    sess, user = loaded
    if user.status != "active":
        raise ApiError(403, "account_suspended", "This account is suspended. Contact support.")
    return sess, user


async def get_pending_mfa(pair: Annotated[tuple[Session, User], Depends(get_session_user)]) -> tuple[Session, User]:
    return pair


async def current_user(pair: Annotated[tuple[Session, User], Depends(get_session_user)]) -> User:
    sess, user = pair
    if sess.mfa_pending:
        raise ApiError(401, "mfa_required", "Enter your two-factor code to finish signing in.")
    return user


async def verified_user(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_verified:
        raise ApiError(403, "email_not_verified", "Verify your email address to continue.")
    return user


async def system_admin(user: Annotated[User, Depends(verified_user)]) -> User:
    if not user.is_system_admin:
        raise forbidden()
    return user


CurrentUser = Annotated[User, Depends(current_user)]
VerifiedUser = Annotated[User, Depends(verified_user)]
SystemAdmin = Annotated[User, Depends(system_admin)]


@dataclass
class WorkspaceContext:
    user: User
    workspace: Workspace
    membership: Membership
    subscription: Subscription | None

    @property
    def permissions(self) -> set[str]:
        return set(self.membership.role.permissions)

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    @property
    def subscribed(self) -> bool:
        return is_active(self.subscription)


async def workspace_context(
    user: VerifiedUser,
    db: Db,
    x_workspace_id: Annotated[str | None, Header()] = None,
) -> WorkspaceContext:
    """Resolves the workspace from the X-Workspace-Id header and checks membership.

    Every workspace-scoped route goes through here, so a user can never reach another tenant's data by
    changing an ID in the URL: queries filter on ctx.workspace.id, not on client input.
    """
    try:
        ws_id = uuid.UUID(x_workspace_id) if x_workspace_id else user.last_workspace_id
    except ValueError:
        ws_id = None
    if ws_id is None:
        raise ApiError(400, "workspace_required", "Choose a workspace.")
    m = (await db.execute(select(Membership).where(Membership.workspace_id == ws_id, Membership.user_id == user.id))).scalar_one_or_none()
    if m is None:
        raise ApiError(404, "workspace_not_found", "Workspace not found.")
    ws = await db.get(Workspace, ws_id)
    if ws.deletion_requested_at is not None and not user.is_system_admin:
        raise ApiError(403, "workspace_deleting", "This workspace is scheduled for deletion. Its owner can restore it from Workspaces.")
    if ws.status != "active" and not user.is_system_admin:
        raise ApiError(403, "workspace_suspended", "This workspace is suspended. Contact support.")
    return WorkspaceContext(user=user, workspace=ws, membership=m, subscription=await get_subscription(db, ws.id))


Ctx = Annotated[WorkspaceContext, Depends(workspace_context)]


def require(*perms: str, subscription: bool = False):
    """Route dependency: the member's role must grant every permission; optionally an active subscription."""

    async def check(ctx: Ctx) -> WorkspaceContext:
        missing = [p for p in perms if not ctx.can(p)]
        if missing:
            raise forbidden(f"Your role can't do this (needs {', '.join(missing)}).")
        if subscription and not ctx.subscribed:
            raise ApiError(402, "subscription_required", "This needs an active subscription.")
        return ctx

    return Depends(check)
