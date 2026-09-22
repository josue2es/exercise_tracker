"""Audit log helper shared by all services.

Every write through REST or MCP, and every admin action, adds an audit row.
"""

from sqlalchemy.orm import Session

from db.models import AuditLog
from services.context import UserContext


def record_audit(session: Session, ctx: UserContext, action: str, target: str | None = None) -> None:
    """Append an audit row to the given session (committed with the caller's transaction)."""
    session.add(
        AuditLog(
            user_id=ctx.user_id,
            api_key_id=ctx.api_key_id,
            actor=ctx.actor,
            action=action,
            target=target,
        )
    )
