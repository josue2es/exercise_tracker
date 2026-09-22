"""UserContext: the authorization context passed to every service call.

The user always comes from the auth context (browser session or API key) and
never from a request body or tool argument.
"""

from dataclasses import dataclass, field

from services.errors import ScopeError


@dataclass
class UserContext:
    user_id: int
    role: str = "member"
    scopes: set[str] = field(default_factory=set)
    # One of: "ui", "api", "mcp".
    actor: str = "ui"
    api_key_id: int | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_write(self) -> bool:
        """UI users can always write; agents need the write scope."""
        return self.actor == "ui" or "write" in self.scopes

    def require_write(self) -> None:
        if not self.can_write:
            raise ScopeError("This action requires a key with write scope")


def ui_context(user_id: int, role: str = "member") -> UserContext:
    """Context for a browser user (no API key, full write access)."""
    return UserContext(user_id=user_id, role=role, actor="ui")


def agent_context(
    user_id: int,
    role: str,
    scopes: set[str],
    actor: str,
    api_key_id: int,
) -> UserContext:
    """Context for a REST or MCP caller authenticated with an API key."""
    return UserContext(
        user_id=user_id,
        role=role,
        scopes=scopes,
        actor=actor,
        api_key_id=api_key_id,
    )
