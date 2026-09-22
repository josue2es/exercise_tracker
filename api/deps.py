"""Shared dependencies: Bearer API key authentication for the REST API."""

from fastapi import Header

import services.api_keys as api_keys
from services.context import UserContext
from services.errors import AuthError, ScopeError


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header; expected 'Bearer <api-key>'")
    return authorization.removeprefix("Bearer ").strip()


def _resolve(authorization: str | None) -> UserContext:
    resolved = api_keys.resolve_key(_extract_bearer(authorization))
    return resolved.context(actor="api")


def read_key(authorization: str | None = Header(default=None)) -> UserContext:
    """Any valid key: read access."""
    return _resolve(authorization)


def write_key(authorization: str | None = Header(default=None)) -> UserContext:
    """A key with the write scope; otherwise 403."""
    ctx = _resolve(authorization)
    if not ctx.can_write:
        raise ScopeError("This action requires a key with write scope")
    return ctx
