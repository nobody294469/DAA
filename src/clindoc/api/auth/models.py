"""Authentication Data Models and Exceptions (PD-006, PD-009).

Defines the AuthenticatedUser principal and authentication-specific exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import uuid

from clindoc.db.models import User


class AuthenticationError(Exception):
    """Raised when authentication credentials (JWT) fail validation."""


class UserNotFoundError(AuthenticationError):
    """Raised when a valid token subject does not map to a registered User."""


class UserDeactivatedError(AuthenticationError):
    """Raised when a valid token subject maps to a deactivated User."""


@dataclass(frozen=True)
class AuthenticatedUser:
    """Authenticated user context resolved from cryptographic token + local DB."""

    user: User
    subject: str
    roles: set[str] = field(default_factory=set)
    token_claims: dict[str, Any] = field(default_factory=dict)

    @property
    def user_id(self) -> uuid.UUID:
        """Return the database primary key of the user."""
        return self.user.id

    @property
    def username(self) -> str:
        """Return the username of the user."""
        return self.user.username

    def has_role(self, role: str | Any) -> bool:
        """Check if user holds the specified role in local database."""
        role_str = role.value if hasattr(role, "value") else str(role)
        return role_str in self.roles

    def has_any_role(self, *roles: str | Any) -> bool:
        """Check if user holds at least one of the specified roles."""
        role_strs = {r.value if hasattr(r, "value") else str(r) for r in roles}
        return not self.roles.isdisjoint(role_strs)

    @property
    def is_admin(self) -> bool:
        """Check if user holds the system_admin role."""
        return "system_admin" in self.roles

    @property
    def is_operator(self) -> bool:
        """Check if user holds the document_operator role."""
        return "document_operator" in self.roles

    @property
    def is_reviewer(self) -> bool:
        """Check if user holds the clinical_reviewer role."""
        return "clinical_reviewer" in self.roles

    @property
    def is_approver(self) -> bool:
        """Check if user holds the approver role."""
        return "approver" in self.roles

    @property
    def is_auditor(self) -> bool:
        """Check if user holds the auditor role."""
        return "auditor" in self.roles

    @property
    def is_integration_service(self) -> bool:
        """Check if user holds the integration_service role."""
        return "integration_service" in self.roles
