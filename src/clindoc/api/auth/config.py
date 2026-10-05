"""OIDC & JWT Authentication Configuration (PD-006, PRODUCTIZATION_SPEC §1.2, §5).

Configures OpenID Connect (OIDC) token validation parameters including
issuer, audience, JWKS resolution, and cache parameters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any, Optional, Sequence


@dataclass(frozen=True)
class OIDCConfig:
    """OIDC and JWT authentication configuration parameters."""

    issuer: str
    audience: str
    jwks_url: Optional[str] = None
    algorithms: list[str] = field(default_factory=lambda: ["RS256"])
    jwks_cache_lifespan_seconds: int = 300
    public_key_pem: Optional[str] = None
    jwks_dict: Optional[dict[str, Any]] = None

    @classmethod
    def from_env(
        cls,
        *,
        issuer: Optional[str] = None,
        audience: Optional[str] = None,
        jwks_url: Optional[str] = None,
        algorithms: Optional[Sequence[str]] = None,
        public_key_pem: Optional[str] = None,
        jwks_dict: Optional[dict[str, Any]] = None,
        jwks_cache_lifespan_seconds: Optional[int] = None,
    ) -> OIDCConfig:
        """Create an OIDCConfig instance from explicit parameters or environment variables."""
        iss = (
            issuer
            or os.environ.get("CLINDOC_OIDC_ISSUER")
            or "https://idp.clindoc.local/auth/realms/clinical"
        )
        aud = audience or os.environ.get("CLINDOC_OIDC_AUDIENCE") or "clindoc-api"
        j_url = jwks_url or os.environ.get("CLINDOC_OIDC_JWKS_URL")
        algs = (
            list(algorithms)
            if algorithms is not None
            else [
                a.strip()
                for a in os.environ.get("CLINDOC_OIDC_ALGORITHMS", "RS256").split(",")
                if a.strip()
            ]
        )
        pub_pem = public_key_pem or os.environ.get("CLINDOC_OIDC_PUBLIC_KEY_PEM")
        cache_life = int(
            jwks_cache_lifespan_seconds
            or os.environ.get("CLINDOC_OIDC_JWKS_CACHE_LIFESPAN", "300")
        )

        return cls(
            issuer=iss.strip(),
            audience=aud.strip(),
            jwks_url=j_url.strip() if j_url else None,
            algorithms=algs,
            jwks_cache_lifespan_seconds=cache_life,
            public_key_pem=pub_pem.strip() if pub_pem else None,
            jwks_dict=jwks_dict,
        )
