"""OIDC JWT Token Validator (PD-006, PRODUCTIZATION_SPEC §1.2, §5).

Validates signed JWT bearer tokens against configured OIDC keys, enforcing
signature validity, issuer, audience, and expiration constraints.
"""

from __future__ import annotations

from typing import Any, Optional

import jwt
from jwt import PyJWK, PyJWKClient

from clindoc.api.auth.config import OIDCConfig
from clindoc.api.auth.models import AuthenticationError


class OIDCTokenValidator:
    """Cryptographic validator for OIDC bearer JWTs."""

    def __init__(self, config: OIDCConfig) -> None:
        self.config = config
        self._jwk_client: Optional[PyJWKClient] = None

        if self.config.jwks_url:
            self._jwk_client = PyJWKClient(
                uri=self.config.jwks_url,
                cache_keys=True,
                max_cached_keys=16,
                lifespan=self.config.jwks_cache_lifespan_seconds,
            )

    def _resolve_signing_key(self, token: str) -> Any:
        """Resolve the cryptographic public key for token verification."""
        # 1. Direct Public Key PEM
        if self.config.public_key_pem:
            return self.config.public_key_pem

        # 2. Local / Fixture JWKS Dictionary
        if self.config.jwks_dict:
            keys = self.config.jwks_dict.get("keys", [])
            if not keys:
                raise AuthenticationError("Configured jwks_dict contains no keys.")

            unverified_header = jwt.get_unverified_header(token)
            token_kid = unverified_header.get("kid")

            target_jwk = None
            if token_kid:
                for k in keys:
                    if k.get("kid") == token_kid:
                        target_jwk = k
                        break
                if target_jwk is None:
                    raise AuthenticationError(
                        f"No key with kid '{token_kid}' found in configured JWKS."
                    )
            else:
                target_jwk = keys[0]

            try:
                pyjwk = PyJWK.from_dict(target_jwk)
                return pyjwk.key
            except Exception as exc:
                raise AuthenticationError(
                    f"Failed to parse JWK into cryptographic key: {exc}"
                ) from exc

        # 3. Remote OIDC JWKS Client
        if self._jwk_client:
            try:
                signing_key = self._jwk_client.get_signing_key_from_jwt(token)
                return signing_key.key
            except Exception as exc:
                raise AuthenticationError(
                    f"Failed to obtain signing key from OIDC JWKS endpoint: {exc}"
                ) from exc

        raise AuthenticationError(
            "No signing key source configured (public_key_pem, jwks_dict, or jwks_url required)."
        )

    def validate_token(self, token: str) -> dict[str, Any]:
        """Cryptographically validate a JWT token and return its verified claims.

        Enforces:
            - Valid signature (RS256 or configured algorithms)
            - Reject unsigned tokens ('none' algorithm strictly forbidden)
            - Issuer match (iss == config.issuer)
            - Audience match (aud == config.audience)
            - Expiration check (exp > now)
            - Temporal claims validation (nbf <= now)
            - Mandatory claims: 'sub', 'iss', 'aud', 'exp'

        Raises:
            AuthenticationError on any signature, structural, or claim mismatch.
        """
        if not token or not isinstance(token, str) or not token.strip():
            raise AuthenticationError("Token must be a non-empty string.")

        clean_token = token.strip()

        # 1. Header structural inspection
        try:
            unverified_header = jwt.get_unverified_header(clean_token)
        except Exception as exc:
            raise AuthenticationError(f"Malformed JWT header: {exc}") from exc

        alg = unverified_header.get("alg")
        if not alg or alg.lower() == "none":
            raise AuthenticationError(
                "Unsigned JWTs with 'none' algorithm are strictly forbidden."
            )
        if alg not in self.config.algorithms:
            raise AuthenticationError(
                f"Unsupported algorithm '{alg}'. Permitted: {self.config.algorithms}"
            )

        # 2. Key resolution
        key = self._resolve_signing_key(clean_token)

        # 3. Cryptographic decode and claim verification
        try:
            claims = jwt.decode(
                clean_token,
                key=key,
                algorithms=self.config.algorithms,
                audience=self.config.audience,
                issuer=self.config.issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": ["sub", "iss", "aud", "exp"],
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError(f"Token has expired: {exc}") from exc
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError(f"Invalid token issuer: {exc}") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError(f"Invalid token audience: {exc}") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError(f"Invalid token signature: {exc}") from exc
        except jwt.InvalidAlgorithmError as exc:
            raise AuthenticationError(f"Invalid algorithm: {exc}") from exc
        except jwt.DecodeError as exc:
            raise AuthenticationError(f"Malformed or undecodable token: {exc}") from exc
        except jwt.PyJWTError as exc:
            raise AuthenticationError(f"JWT validation failed: {exc}") from exc

        sub = claims.get("sub")
        if not sub or not str(sub).strip():
            raise AuthenticationError("Token is missing a non-empty 'sub' claim.")

        return claims
