"""API keys service: creation, listing, revocation and key resolution.

Keys are 32 random bytes from ``secrets`` with a ``gym_`` prefix. The full key
is shown once; the database stores only a SHA-256 hash plus a short prefix.
One function, ``resolve_key``, backs both the FastAPI dependency and the
FastMCP token verifier.
"""

from __future__ import annotations

import secrets
import time
from hashlib import sha256

from sqlalchemy import select

from db.models import ApiKey, User, utcnow
from db.session import get_session
from services.audit import record_audit
from services.context import UserContext, agent_context
from services.errors import AuthError, NotFoundError, ScopeError, ValidationError
from services.schemas import ApiKeyInfo, utc as utc_

KEY_PREFIX = "gym_"
LAST_USED_UPDATE_INTERVAL = 60  # seconds; update last_used_at at most once a minute

VALID_SCOPES = {"read", "write"}


def _hash_key(raw_key: str) -> str:
    return sha256(raw_key.encode()).hexdigest()


def _to_info(row: ApiKey) -> ApiKeyInfo:
    return ApiKeyInfo(
        id=row.id,
        label=row.label,
        key_prefix=row.key_prefix,
        scopes=list(row.scopes or []),
        created_at=utc_(row.created_at),
        last_used_at=utc_(row.last_used_at),
        revoked_at=utc_(row.revoked_at),
    )


def create_key(ctx: UserContext, label: str, scopes: list[str]) -> tuple[ApiKeyInfo, str]:
    """Create a key. Returns (info, full_key); the full key is shown once."""
    label = label.strip()
    if not label:
        raise ValidationError("Please give the key a label")
    scopes = sorted(set(scopes))
    if not scopes or not set(scopes) <= VALID_SCOPES:
        raise ValidationError("Scopes must be a subset of: read, write")
    if "read" not in scopes:
        # Write keys always read; keep the model simple.
        scopes = ["read", *scopes]

    raw_key = KEY_PREFIX + secrets.token_hex(32)
    with get_session() as session:
        row = ApiKey(
            user_id=ctx.user_id,
            label=label,
            key_prefix=raw_key[:10],
            key_hash=_hash_key(raw_key),
            scopes=scopes,
        )
        session.add(row)
        session.flush()
        record_audit(session, ctx, "api_key.create", target=label)
        return _to_info(row), raw_key


def list_keys(ctx: UserContext) -> list[ApiKeyInfo]:
    """List the caller's keys (revoked ones included, newest first)."""
    with get_session() as session:
        rows = session.scalars(
            select(ApiKey)
            .where(ApiKey.user_id == ctx.user_id)
            .order_by(ApiKey.id.desc())
        ).all()
        return [_to_info(r) for r in rows]


def revoke_key(ctx: UserContext, key_id: int) -> None:
    """Revoke one of the caller's keys."""
    with get_session() as session:
        row = session.scalars(
            select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == ctx.user_id)
        ).one_or_none()
        if row is None:
            raise NotFoundError("API key not found")
        if row.revoked_at is None:
            row.revoked_at = utcnow()
        record_audit(session, ctx, "api_key.revoke", target=row.label)


class ResolvedKey:
    """The result of resolving a Bearer token: enough to build a UserContext."""

    def __init__(self, user_id: int, role: str, scopes: set[str], api_key_id: int, last_used_at):
        self.user_id = user_id
        self.role = role
        self.scopes = scopes
        self.api_key_id = api_key_id
        self.last_used_at = last_used_at

    def context(self, actor: str) -> UserContext:
        return agent_context(
            user_id=self.user_id,
            role=self.role,
            scopes=self.scopes,
            actor=actor,
            api_key_id=self.api_key_id,
        )


def resolve_key(raw_key: str) -> ResolvedKey:
    """Resolve a raw API key into the caller's identity and scopes.

    Raises AuthError for unknown, revoked or deactivated keys. Used by both
    the REST API and the MCP server. Updates last_used_at at most once a
    minute per key.
    """
    if not raw_key or not raw_key.startswith(KEY_PREFIX):
        raise AuthError("Invalid API key")
    key_hash = _hash_key(raw_key)
    with get_session() as session:
        row = session.scalars(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        ).one_or_none()
        if row is None or row.revoked_at is not None:
            raise AuthError("Invalid API key")
        user = session.get(User, row.user_id)
        if user is None or not user.is_active:
            # Deactivating a user blocks all of their API keys.
            raise AuthError("This account has been deactivated")

        now = time.time()
        last_used = row.last_used_at.timestamp() if row.last_used_at else 0
        if now - last_used >= LAST_USED_UPDATE_INTERVAL:
            row.last_used_at = utcnow()

        return ResolvedKey(
            user_id=user.id,
            role=user.role,
            scopes=set(row.scopes or []),
            api_key_id=row.id,
            last_used_at=utc_(row.last_used_at),
        )
