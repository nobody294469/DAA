"""FastAPI Authentication Dependencies (PD-006, PD-009, PRODUCTIZATION_SPEC §1.2, §5).

Provides get_current_user dependency for FastAPI route handlers, resolving
the authenticated user context and effective roles strictly from the database.
"""

from __future__ import annotations

from typing import Callable, Generator, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from clindoc.api.auth.config import OIDCConfig
from clindoc.api.auth.models import (
    AuthenticatedUser,
    AuthenticationError,
)
from clindoc.api.auth.token_validator import OIDCTokenValidator
from clindoc.db.models import User

# Standard FastAPI HTTPBearer scheme (auto_error=False allows explicit custom 401 handling)
bearer_scheme = HTTPBearer(auto_error=False)

# Module-level singletons / overrides for application lifecycle
_GLOBAL_VALIDATOR: Optional[OIDCTokenValidator] = None
_GLOBAL_SESSION_FACTORY: Optional[Callable[[], Session]] = None


def set_global_token_validator(validator: Optional[OIDCTokenValidator]) -> None:
    """Set the application-wide OIDCTokenValidator instance."""
    global _GLOBAL_VALIDATOR
    _GLOBAL_VALIDATOR = validator


def set_global_session_factory(factory: Optional[Callable[[], Session]]) -> None:
    """Set the application-wide database session factory."""
    global _GLOBAL_SESSION_FACTORY
    _GLOBAL_SESSION_FACTORY = factory


def get_token_validator() -> OIDCTokenValidator:
    """FastAPI dependency to resolve the active OIDCTokenValidator."""
    global _GLOBAL_VALIDATOR
    if _GLOBAL_VALIDATOR is not None:
        return _GLOBAL_VALIDATOR
    # Fallback to environment configuration
    config = OIDCConfig.from_env()
    _GLOBAL_VALIDATOR = OIDCTokenValidator(config)
    return _GLOBAL_VALIDATOR


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency to yield a database session."""
    global _GLOBAL_SESSION_FACTORY
    if _GLOBAL_SESSION_FACTORY is None:
        raise RuntimeError(
            "Database session factory has not been initialized. Call set_global_session_factory() first."
        )
    session = _GLOBAL_SESSION_FACTORY()
    try:
        yield session
    finally:
        session.close()


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme),
    session: Session = Depends(get_db_session),
    validator: OIDCTokenValidator = Depends(get_token_validator),
) -> AuthenticatedUser:
    """FastAPI dependency: Authenticate request bearer token and resolve local User.

    Enforces:
        1. Authorization header exists and has 'Bearer <token>' format -> 401 if missing/malformed.
        2. Token cryptographically verified (signature, exp, iss, aud) -> 401 on failure.
        3. Subject matches a registered local User (User.idp_subject) -> 401 if not found.
        4. User account is active -> 401 if deactivated.
        5. Effective roles are loaded strictly from the database (UserRole -> Role).
           Client-supplied role claims in JWT are ignored for authorization decisions.

    Returns:
        AuthenticatedUser principal with user, roles, subject, and token claims.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = credentials.credentials.strip()

    # 1. Cryptographic JWT Validation
    try:
        claims = validator.validate_token(raw_token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    sub = claims.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 2. Database User Resolution (PD-006)
    db_user = session.query(User).filter(User.idp_subject == str(sub).strip()).first()
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User with subject '{sub}' is not registered in the system",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not db_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is deactivated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Canonical Role Resolution (PD-009)
    # Strictly resolved from database UserRole records, NOT from token claims
    effective_roles = {ur.role_id for ur in db_user.roles if ur.role_id}

    return AuthenticatedUser(
        user=db_user,
        subject=str(sub).strip(),
        roles=effective_roles,
        token_claims=claims,
    )
