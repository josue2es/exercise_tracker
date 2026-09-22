"""Tests for the users service: invites, first admin, login throttling, settings."""


import pytest
from sqlalchemy import select

import services.users as users
from db.models import AuditLog, Invite, User
from db.session import get_session
from services.context import UserContext, ui_context
from services.errors import (
    AuthError,
    ConflictError,
    RateLimitError,
    ScopeError,
    ValidationError,
)
from tests.conftest import make_user


@pytest.fixture(autouse=True)
def clear_throttling():
    users._reset_login_throttling()
    yield
    users._reset_login_throttling()


def admin_ctx(user_id=1):
    return UserContext(user_id=user_id, role="admin", actor="ui")


def member_ctx(user_id=2):
    return UserContext(user_id=user_id, role="member", actor="ui")


@pytest.fixture
def admin(engine):
    """An admin user (id 1) and a member (id 2)."""
    from tests.conftest import make_user

    admin_id = make_user(email="admin@example.com", role="admin")
    member_id = make_user(email="member@example.com", role="member")
    return admin_id, member_id


# --- first admin bootstrap --------------------------------------------------------


def test_create_first_admin(engine):
    link = users.create_first_admin("Root@example.com ")
    assert link.startswith("/invite/")
    with get_session() as session:
        invite = session.scalars(select(Invite)).one()
    assert invite.email == "root@example.com"
    assert invite.role == "admin"
    assert invite.created_by is None


def test_create_first_admin_skips_when_users_exist(engine, admin):
    assert users.create_first_admin("newadmin@example.com") is None


# --- invites -----------------------------------------------------------------------


def test_create_and_accept_invite(engine, admin):
    admin_id, _ = admin
    path = users.create_invite(admin_ctx(admin_id), "Friend@example.com", "member")
    assert path.startswith("/invite/")
    token = path.removeprefix("/invite/")

    user_id = users.accept_invite(token, "Friend", "supersecret123")
    assert user_id > 0
    info = users.get_user(user_id)
    assert info.email == "friend@example.com"
    assert info.role == "member"
    assert info.is_active

    # Single use: the same token cannot be used again.
    with pytest.raises(ValidationError):
        users.accept_invite(token, "Impostor", "supersecret123")


def test_invite_rejects_existing_email(engine, admin):
    admin_id, _ = admin
    with pytest.raises(ConflictError):
        users.create_invite(admin_ctx(admin_id), "member@example.com", "member")


def test_invite_requires_admin(engine, admin):
    _, member_id = admin
    with pytest.raises(ScopeError):
        users.create_invite(member_ctx(member_id), "x@example.com", "member")


def test_expired_invite_rejected(engine, admin):
    from datetime import timedelta

    from db.models import Invite, utcnow
    from db.session import get_session
    from sqlalchemy import select

    admin_id, _ = admin
    path = users.create_invite(admin_ctx(admin_id), "slow@example.com", "member")
    token = path.removeprefix("/invite/")
    with get_session() as session:
        invite = session.scalars(select(Invite).where(Invite.email == "slow@example.com")).one()
        invite.expires_at = utcnow() - timedelta(days=1)
    with pytest.raises(ValidationError, match="expired"):
        users.accept_invite(token, "Slow", "supersecret123")


def test_revoked_invite_rejected(engine, admin):
    admin_id, _ = admin
    path = users.create_invite(admin_ctx(admin_id), "gone@example.com", "member")
    token = path.removeprefix("/invite/")
    invites = users.list_invites(admin_ctx(admin_id))
    users.revoke_invite(admin_ctx(admin_id), invites[0].id)
    with pytest.raises(ValidationError, match="revoked"):
        users.accept_invite(token, "Gone", "supersecret123")


def test_accept_invite_password_policy(engine, admin):
    admin_id, _ = admin
    token = users.create_invite(admin_ctx(admin_id), "weak@example.com").removeprefix("/invite/")
    with pytest.raises(ValidationError, match="at least 10"):
        users.accept_invite(token, "Weak", "short")


def test_invite_creating_new_revokes_old_pending(engine, admin):
    admin_id, _ = admin
    users.create_invite(admin_ctx(admin_id), "twice@example.com")
    users.create_invite(admin_ctx(admin_id), "twice@example.com")
    invites = [i for i in users.list_invites(admin_ctx(admin_id)) if i.email == "twice@example.com"]
    assert len(invites) == 2
    assert sum(1 for i in invites if i.revoked_at) == 1


# --- authentication ------------------------------------------------------------------


def test_authenticate_success_and_last_login(engine):
    user_id = make_user(email="login@example.com", role="member")
    info = users.authenticate("LOGIN@example.com", "password123", ip="1.1.1.1")
    assert info.id == user_id
    with get_session() as session:
        user = session.get(User, user_id)
        assert user.last_login_at is not None


def test_authenticate_wrong_password(engine):
    from tests.conftest import make_user

    make_user(email="login@example.com")
    with pytest.raises(AuthError):
        users.authenticate("login@example.com", "wrong-password", ip="1.1.1.1")


def test_authenticate_deactivated_user_blocked(engine):
    user_id = make_user(email="off@example.com")
    with get_session() as session:
        session.get(User, user_id).is_active = False
    with pytest.raises(AuthError, match="deactivated"):
        users.authenticate("off@example.com", "password123", ip="1.1.1.1")


def test_login_lockout_after_five_failures(engine):
    make_user(email="locked@example.com")
    for _ in range(5):
        with pytest.raises(AuthError):
            users.authenticate("locked@example.com", "wrong-password", ip="9.9.9.9")
    # Sixth attempt is locked out even with the right password.
    with pytest.raises(RateLimitError):
        users.authenticate("locked@example.com", "password123", ip="9.9.9.9")
    # A different IP is not affected.
    assert users.authenticate("locked@example.com", "password123", ip="8.8.8.8")


def test_login_success_clears_failures(engine):
    make_user(email="flaky@example.com")
    for _ in range(4):
        with pytest.raises(AuthError):
            users.authenticate("flaky@example.com", "wrong", ip="7.7.7.7")
    assert users.authenticate("flaky@example.com", "password123", ip="7.7.7.7")
    # Counter cleared: four more failures still allowed.
    for _ in range(4):
        with pytest.raises(AuthError):
            users.authenticate("flaky@example.com", "wrong", ip="7.7.7.7")
    with pytest.raises(AuthError):
        users.authenticate("flaky@example.com", "wrong", ip="7.7.7.7")
    with pytest.raises(RateLimitError):
        users.authenticate("flaky@example.com", "password123", ip="7.7.7.7")


# --- account management ----------------------------------------------------------------


def test_set_user_active_and_self_guard(engine, admin):
    admin_id, member_id = admin
    users.set_user_active(admin_ctx(admin_id), member_id, False)
    assert users.get_user(member_id).is_active is False
    with pytest.raises(ValidationError):
        users.set_user_active(admin_ctx(admin_id), admin_id, False)


def test_update_settings(engine, admin):
    _, member_id = admin
    ctx = ui_context(member_id)
    updated = users.update_settings(ctx, unit_pref="lb", time_zone="Europe/Berlin")
    assert updated.unit_pref == "lb"
    assert updated.time_zone == "Europe/Berlin"
    with pytest.raises(ValidationError):
        users.update_settings(ctx, unit_pref="stone")
    with pytest.raises(ValidationError):
        users.update_settings(ctx, time_zone="Mars/Olympus")


def test_change_password(engine, admin):
    _, member_id = admin
    ctx = ui_context(member_id)
    users.change_password(ctx, "password123", "newpassword456")
    # Old password no longer works, new one does.
    with pytest.raises(AuthError):
        users.authenticate("member@example.com", "password123", ip="1.1.1.1")
    assert users.authenticate("member@example.com", "newpassword456", ip="1.1.1.1").id == member_id
    with pytest.raises(AuthError):
        users.change_password(ctx, "wrong-current", "anotherpassword789")


def test_list_users_admin_only(engine, admin):
    admin_id, member_id = admin
    names = [u.email for u in users.list_users(admin_ctx(admin_id))]
    assert "member@example.com" in names
    with pytest.raises(ScopeError):
        users.list_users(member_ctx(member_id))


def test_audit_rows_for_admin_actions(engine, admin):
    admin_id, _ = admin
    users.create_invite(admin_ctx(admin_id), "audited@example.com")
    with get_session() as session:
        actions = [row.action for row in session.scalars(select(AuditLog)).all()]
    assert "invite.create" in actions
