"""Domain errors raised by the service layer.

Adapters translate these into transport responses: REST uses HTTP status
codes, MCP returns plain-language error messages. The service layer itself
stays transport-agnostic.
"""


class ServiceError(Exception):
    """Base class for all service-layer errors."""


class AuthError(ServiceError):
    """Authentication failed (bad credentials or API key)."""

    http_status = 401


class ScopeError(ServiceError):
    """The actor is not allowed to perform this write (missing scope)."""

    http_status = 403


class NotFoundError(ServiceError):
    """The object does not exist, or belongs to another user.

    Existence is never revealed: a foreign ID looks the same as a missing one.
    """

    http_status = 404


class ValidationError(ServiceError):
    """Input failed a business rule."""

    http_status = 422


class ConflictError(ServiceError):
    """The request conflicts with existing state."""

    http_status = 409


class RateLimitError(ServiceError):
    """Too many attempts (e.g. login throttling)."""

    http_status = 429
