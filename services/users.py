"""Users service: accounts, invites, login throttling and settings.

All operations take a UserContext where an actor exists. Login throttling uses
in-memory counters, which is fine in a single-process deployment.
"""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta
from hashlib import sha256
from zoneinfo import available_timezones

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select

from db.models import Invite, User, utcnow
from db.session import get_session
from services.audit import record_audit
from services.context import UserContext
from services.errors import (
    AuthError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ScopeError,
    ValidationError,
)
from services.schemas import InviteInfo, UserInfo, utc as utc_

log = logging.getLogger("gym_tracker.users")

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 10
INVITE_EXPIRY_DAYS = 7
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 15 * 60

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ROLES = {"member", "admin"}
VALID_UNITS = {"kg", "lb"}

# (email, ip) -> deque of failure timestamps; in-memory, single process.
_login_failures: dict[tuple[str, str], deque] = defaultdict(deque)


# --- password helpers -------------------------------------------------------------


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:  # malformed hash: never authenticate against it
        return False


def _check_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long")


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValidationError("Invalid email address")
    return email


def _hash_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()


def _to_user_info(user: User) -> UserInfo:
    return UserInfo(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        unit_pref=user.unit_pref,
        time_zone=user.time_zone,
        is_active=user.is_active,
        created_at=utc_(user.created_at),
        last_login_at=utc_(user.last_login_at),
    )


def _to_invite_info(invite: Invite) -> InviteInfo:
    return InviteInfo(
        id=invite.id,
        email=invite.email,
        role=invite.role,
        created_at=utc_(invite.created_at),
        expires_at=utc_(invite.expires_at),
        used_at=utc_(invite.used_at),
        used_by=invite.used_by,
        revoked_at=utc_(invite.revoked_at),
    )


def _require_admin(ctx: UserContext) -> None:
    if not ctx.is_admin:
        raise ScopeError("Admin access required")


# --- internal helpers used by the UI layer ------------------------------------------


def get_user(user_id: int) -> UserInfo | None:
    """Fetch one user (no context needed; used by UI auth plumbing)."""
    with get_session() as session:
        user = session.get(User, user_id)
        return _to_user_info(user) if user else None


def is_user_active(user_id: int) -> bool:
    """Cheap check for the auth middleware on every page load."""
    with get_session() as session:
        user = session.get(User, user_id)
        return bool(user and user.is_active)


def _reset_login_throttling() -> None:
    """Test helper: clear in-memory failure counters."""
    _login_failures.clear()


# --- first admin bootstrap ------------------------------------------------------------


def create_first_admin(email: str) -> str | None:
    """Seed the first admin from FIRST_ADMIN_EMAIL.

    Creates a single-use admin invite and returns its path (``/invite/<token>``)
    to be printed to the log. Returns None if any user already exists.
    """
    email = _validate_email(email)
    with get_session() as session:
        if session.scalars(select(User.id).limit(1)).first() is not None:
            return None
        token = secrets.token_urlsafe(32)
        session.add(
            Invite(
                token_hash=_hash_token(token),
                email=email,
                role="admin",
                created_by=None,
                expires_at=utcnow() + timedelta(days=INVITE_EXPIRY_DAYS),
            )
        )
    return f"/invite/{token}"


# --- invites ------------------------------------------------------------------------


def create_invite(ctx: UserContext, email: str, role: str = "member") -> str:
    """Create a single-use invite link. Returns the path ``/invite/<token>``."""
    _require_admin(ctx)
    email = _validate_email(email)
    if role not in VALID_ROLES:
        raise ValidationError(f"Role must be one of: {', '.join(sorted(VALID_ROLES))}")

    with get_session() as session:
        if session.scalars(select(User.id).where(User.email == email)).first() is not None:
            raise ConflictError("A user with this email already exists")
        # Auto-revoke older pending invites for the same email to avoid confusion.
        for old in session.scalars(
            select(Invite).where(
                Invite.email == email,
                Invite.used_at.is_(None),
                Invite.revoked_at.is_(None),
            )
        ):
            old.revoked_at = utcnow()

        token = secrets.token_urlsafe(32)
        invite = Invite(
            token_hash=_hash_token(token),
            email=email,
            role=role,
            created_by=ctx.user_id,
            expires_at=utcnow() + timedelta(days=INVITE_EXPIRY_DAYS),
        )
        session.add(invite)
        record_audit(session, ctx, "invite.create", target=email)
    return f"/invite/{token}"


def list_invites(ctx: UserContext) -> list[InviteInfo]:
    """List invites, newest first (admin only)."""
    _require_admin(ctx)
    with get_session() as session:
        invites = session.scalars(select(Invite).order_by(Invite.id.desc())).all()
        return [_to_invite_info(i) for i in invites]


def revoke_invite(ctx: UserContext, invite_id: int) -> None:
    """Revoke a pending invite (admin only)."""
    _require_admin(ctx)
    with get_session() as session:
        invite = session.get(Invite, invite_id)
        if invite is None:
            raise NotFoundError("Invite not found")
        if invite.used_at is not None:
            raise ValidationError("This invite was already used")
        if invite.revoked_at is None:
            invite.revoked_at = utcnow()
        record_audit(session, ctx, "invite.revoke", target=invite.email)


def accept_invite(token: str, display_name: str, password: str) -> int:
    """Accept an invite: create the account with the invite's email and role."""
    display_name = display_name.strip()
    if not display_name:
        raise ValidationError("Please enter your name")
    _check_password_policy(password)
    token_hash = _hash_token(token)

    with get_session() as session:
        invite = session.scalars(
            select(Invite).where(Invite.token_hash == token_hash)
        ).one_or_none()
        if invite is None:
            raise NotFoundError("Invalid invite link")
        if invite.used_at is not None:
            raise ValidationError("This invite link has already been used")
        if invite.revoked_at is not None:
            raise ValidationError("This invite link has been revoked")
        if utcnow() > invite.expires_at:
            raise ValidationError("This invite link has expired. Ask for a new one.")

        if session.scalars(select(User.id).where(User.email == invite.email)).first() is not None:
            raise ConflictError("A user with this email already exists")

        user = User(
            email=invite.email,
            display_name=display_name,
            password_hash=hash_password(password),
            role=invite.role,
        )
        session.add(user)
        session.flush()
        invite.used_at = utcnow()
        invite.used_by = user.id
        record_audit(
            session, UserContext(user_id=user.id, role=user.role, actor="ui"),
            "invite.accept", target=invite.email,
        )
        return user.id


# --- authentication --------------------------------------------------------------------


def _login_locked(email: str, ip: str) -> int | None:
    """Return remaining lock seconds if this (email, ip) is locked, else None."""
    key = (email, ip)
    failures = _login_failures[key]
    cutoff = time.time() - LOGIN_LOCK_SECONDS
    while failures and failures[0] < cutoff:
        failures.popleft()
    if len(failures) >= LOGIN_MAX_FAILURES:
        return int(LOGIN_LOCK_SECONDS - (time.time() - failures[-1]))
    return None


def _record_login_failure(email: str, ip: str) -> None:
    _login_failures[(email, ip)].append(time.time())


def authenticate(email: str, password: str, ip: str = "unknown") -> UserInfo:
    """Verify email and password. Raises AuthError / RateLimitError."""
    email = email.strip().lower()
    locked_for = _login_locked(email, ip)
    if locked_for is not None:
        log.warning("Login locked for %s from %s (%d s remaining)", email, ip, locked_for)
        raise RateLimitError(
            f"Too many failed attempts. Try again in {max(locked_for // 60, 1)} minute(s)."
        )

    with get_session() as session:
        user = session.scalars(select(User).where(User.email == email)).one_or_none()
        password_ok = user is not None and verify_password(user.password_hash, password)

    if not password_ok:
        _record_login_failure(email, ip)
        log.warning("Failed login for %s from %s", email, ip)
        raise AuthError("Invalid email or password")
    if not user.is_active:
        log.warning("Login attempt for deactivated account %s from %s", email, ip)
        raise AuthError("This account has been deactivated")

    _login_failures.pop((email, ip), None)
    with get_session() as session:
        db_user = session.get(User, user.id)
        db_user.last_login_at = utcnow()
    return _to_user_info(user)


# --- account management ------------------------------------------------------------------


def list_users(ctx: UserContext) -> list[UserInfo]:
    """List all accounts (admin only; grants no access to training data)."""
    _require_admin(ctx)
    with get_session() as session:
        users = session.scalars(select(User).order_by(User.id)).all()
        return [_to_user_info(u) for u in users]


def set_user_active(ctx: UserContext, user_id: int, active: bool) -> None:
    """Deactivate or reactivate an account (admin only).

    Deactivating blocks login, open UI sessions (checked per page load) and
    all of the user's API keys.
    """
    _require_admin(ctx)
    if user_id == ctx.user_id and not active:
        raise ValidationError("You cannot deactivate your own account")
    with get_session() as session:
        user = session.get(User, user_id)
        if user is None:
            raise NotFoundError("User not found")
        user.is_active = active
        record_audit(
            session, ctx, "user.deactivate" if not active else "user.activate", target=user.email
        )


def update_settings(ctx: UserContext, unit_pref: str | None = None, time_zone: str | None = None) -> UserInfo:
    """Update the caller's own settings (unit and time zone)."""
    if unit_pref is not None and unit_pref not in VALID_UNITS:
        raise ValidationError("Unit must be 'kg' or 'lb'")
    if time_zone is not None and time_zone not in available_timezones():
        raise ValidationError("Unknown time zone")
    with get_session() as session:
        user = session.get(User, ctx.user_id)
        if user is None:
            raise NotFoundError("User not found")
        if unit_pref is not None:
            user.unit_pref = unit_pref
        if time_zone is not None:
            user.time_zone = time_zone
        return _to_user_info(user)


def change_password(ctx: UserContext, current_password: str, new_password: str) -> None:
    """Change the caller's own password; requires the current password."""
    _check_password_policy(new_password)
    with get_session() as session:
        user = session.get(User, ctx.user_id)
        if user is None:
            raise NotFoundError("User not found")
        if not verify_password(user.password_hash, current_password):
            raise AuthError("Current password is incorrect")
        user.password_hash = hash_password(new_password)
        record_audit(session, ctx, "user.change_password", target=user.email)
