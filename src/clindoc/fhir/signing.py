"""Three-Tier Bundle Integrity, Cryptographic JWS Signing, and Transport Byte Binding.

Authority:
  - CLINICALDOC_STEP3_DEF003_IMPLEMENTATION_EXECUTION_PLAN.md (Revision 2.7 §7, §11)
  - HL7 FHIR R4B Bundle.signature / RFC 7515 (JWS Compact Serialization) / RFC 8785 (Canonical JSON)
"""

from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.types import (
    PrivateKeyTypes,
    PublicKeyTypes,
)

logger = logging.getLogger(__name__)

AUDIT_PROVENANCE_EXTENSION_URL = "http://clindoc.internal/fhir/StructureDefinition/audit-provenance"


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────

class BundleSigningError(Exception):
    """Base exception for bundle signing, signature verification, and attestation failures."""


class MissingBundleSignatureError(BundleSigningError):
    """Raised when a FHIR bundle lacks the mandatory Bundle.signature element."""


class MalformedBundleSignatureError(BundleSigningError):
    """Raised when Bundle.signature is structurally invalid, malformed base64url, or has unrecognized JWS header."""


class InvalidBundleSignatureError(BundleSigningError):
    """Raised when cryptographic signature verification against the public key fails."""


class UntrustedSigningKeyError(BundleSigningError):
    """Raised when the key ID is unrecognized, expired, revoked, or untrusted."""


class AuditProvenanceBindingMismatchError(BundleSigningError):
    """Raised when embedded audit provenance extension does not match DB event or JSONL ledger."""


class AttestationEquivalenceMismatchError(BundleSigningError):
    """Raised when recomputed clinical content digest or ledger payload does not match DB state."""


class EHRBundlePayloadTamperedError(BundleSigningError):
    """Raised when transport wire boundary byte digest does not match verified transport_sha256."""


class IncompleteAttestationError(BundleSigningError):
    """Raised when bundle is not fully committed, projected, or checkpoint-sealed."""


# ─── BASE64URL & CANONICAL SERIALIZATION ─────────────────────────────────────

def b64url_encode(data: bytes) -> str:
    """Encode bytes using RFC 7515 Base64url encoding without trailing padding '='."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def b64url_decode(data: str) -> bytes:
    """Decode RFC 7515 Base64url encoded string with automatic padding restoration."""
    rem = len(data) % 4
    if rem:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data)


def serialize_canonical_json(obj: Any) -> bytes:
    """Serialize Python object to RFC 8785 Canonical JSON bytes.

    Guarantees:
      - Lexicographically sorted dictionary keys.
      - Minimal separators (',', ':') with zero extraneous whitespace.
      - UTF-8 encoding without BOM.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# ─── TIER 1: CLINICAL CONTENT DIGEST ─────────────────────────────────────────

def compute_clinical_content_digest(bundle_dict: dict[str, Any]) -> str:
    """Compute Tier 1 SHA-256 digest of pure clinical bundle content.

    Strictly excludes:
      1. `Bundle.signature` element.
      2. The audit provenance extension (`http://clindoc.internal/fhir/StructureDefinition/audit-provenance`).
    """
    clean_bundle = copy.deepcopy(bundle_dict)
    clean_bundle.pop("signature", None)

    # Filter out audit provenance extension if present at bundle root
    if "extension" in clean_bundle and isinstance(clean_bundle["extension"], list):
        clean_bundle["extension"] = [
            ext for ext in clean_bundle["extension"]
            if not (isinstance(ext, dict) and ext.get("url") == AUDIT_PROVENANCE_EXTENSION_URL)
        ]
        if not clean_bundle["extension"]:
            clean_bundle.pop("extension", None)

    canonical_bytes = serialize_canonical_json(clean_bundle)
    return hashlib.sha256(canonical_bytes).hexdigest()


# ─── TIER 2: AUGMENTED SIGNED BUNDLE ─────────────────────────────────────────

def embed_audit_provenance(
    bundle_dict: dict[str, Any],
    audit_sequence_number: int,
    audit_event_hash: str,
    clinical_content_digest: str,
) -> dict[str, Any]:
    """Augment bundle with immutable audit provenance extension linking to PostgreSQL event."""
    augmented = copy.deepcopy(bundle_dict)
    extensions = list(augmented.get("extension", []))

    # Remove existing provenance extension if present
    extensions = [
        ext for ext in extensions
        if not (isinstance(ext, dict) and ext.get("url") == AUDIT_PROVENANCE_EXTENSION_URL)
    ]

    prov_ext = {
        "url": AUDIT_PROVENANCE_EXTENSION_URL,
        "extension": [
            {"url": "auditSequenceNumber", "valueInteger": int(audit_sequence_number)},
            {"url": "auditEventHash", "valueString": str(audit_event_hash).strip()},
            {"url": "clinicalContentDigest", "valueString": str(clinical_content_digest).strip()},
        ],
    }
    extensions.append(prov_ext)
    augmented["extension"] = extensions
    return augmented


def extract_audit_provenance(bundle_dict: dict[str, Any]) -> Tuple[int, str, str]:
    """Extract (audit_sequence_number, audit_event_hash, clinical_content_digest) from bundle.

    Raises:
        IncompleteAttestationError if extension is missing or invalid.
    """
    extensions = bundle_dict.get("extension")
    if not isinstance(extensions, list):
        raise IncompleteAttestationError("Bundle lacks root extensions; audit provenance missing.")

    for ext in extensions:
        if isinstance(ext, dict) and ext.get("url") == AUDIT_PROVENANCE_EXTENSION_URL:
            sub_exts = ext.get("extension")
            if not isinstance(sub_exts, list):
                break
            seq_num = None
            evt_hash = None
            clin_digest = None
            for s in sub_exts:
                if isinstance(s, dict):
                    if s.get("url") == "auditSequenceNumber":
                        seq_num = s.get("valueInteger")
                    elif s.get("url") == "auditEventHash":
                        evt_hash = s.get("valueString")
                    elif s.get("url") == "clinicalContentDigest":
                        clin_digest = s.get("valueString")

            if isinstance(seq_num, int) and evt_hash and clin_digest:
                return seq_num, str(evt_hash).strip(), str(clin_digest).strip()

    raise IncompleteAttestationError("Bundle lacks valid audit provenance extension.")


def sign_fhir_bundle(
    bundle_dict: dict[str, Any],
    private_key: PrivateKeyTypes,
    key_id: str,
    algorithm: str = "RS256",
    who_reference: str = "Organization/clindoc-authoritative-export",
    when: Optional[datetime] = None,
) -> dict[str, Any]:
    """Sign augmented FHIR Bundle producing standards-compliant Bundle.signature element.

    Standard: RFC 7515 JWS Compact Serialization over RFC 8785 canonical JSON
    representation with signature.data stripped/omitted.
    """
    if algorithm not in ("RS256", "ES256"):
        raise ValueError(f"Unsupported signing algorithm: {algorithm}. Must be RS256 or ES256.")

    signed_bundle = copy.deepcopy(bundle_dict)

    ts = when or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Construct signature element placeholder without data
    sig_element = {
        "type": [
            {
                "system": "urn:iso-astm:E1762-95:2013",
                "code": "1.2.840.10065.1.12.1.1",
                "display": "Author's Signature",
            }
        ],
        "when": ts_iso,
        "who": {"reference": who_reference},
        "sigFormat": "application/jose",
    }
    signed_bundle["signature"] = sig_element

    # 1. Canonicalize bundle with signature element (excluding data)
    canonical_signing_input = serialize_canonical_json(signed_bundle)

    # 2. Construct JWS Protected Header
    header = {"alg": algorithm, "typ": "JOSE", "kid": str(key_id)}
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64_header = b64url_encode(header_bytes)
    b64_payload = b64url_encode(canonical_signing_input)

    jws_signing_input = f"{b64_header}.{b64_payload}".encode("ascii")

    # 3. Compute Cryptographic Signature
    if algorithm == "RS256":
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise TypeError("RS256 requires rsa.RSAPrivateKey")
        raw_sig = private_key.sign(
            jws_signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    elif algorithm == "ES256":
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise TypeError("ES256 requires ec.EllipticCurvePrivateKey")
        raw_sig = private_key.sign(
            jws_signing_input,
            ec.ECDSA(hashes.SHA256()),
        )

    b64_sig = b64url_encode(raw_sig)
    jws_compact = f"{b64_header}.{b64_payload}.{b64_sig}"

    # Embed data in Bundle.signature
    signed_bundle["signature"]["data"] = jws_compact
    return signed_bundle


def verify_bundle_signature(
    bundle_dict: dict[str, Any],
    trust_store: Dict[str, PublicKeyTypes],
    revoked_key_ids: Optional[Set[str]] = None,
) -> Tuple[bool, str, dict[str, Any]]:
    """Cryptographically verify Bundle.signature against trusted public key store.

    Returns:
        Tuple of (is_valid, key_id, verified_header).
    Raises:
        MissingBundleSignatureError
        MalformedBundleSignatureError
        UntrustedSigningKeyError
        InvalidBundleSignatureError
    """
    if "signature" not in bundle_dict or not isinstance(bundle_dict["signature"], dict):
        raise MissingBundleSignatureError("Bundle lacks required 'signature' element.")

    sig_obj = bundle_dict["signature"]
    jws_data = sig_obj.get("data")
    if not jws_data or not isinstance(jws_data, str):
        raise MissingBundleSignatureError("Bundle.signature lacks 'data' field.")

    parts = jws_data.split(".")
    if len(parts) != 3:
        raise MalformedBundleSignatureError(f"Bundle.signature data is not a valid 3-part JWS: {jws_data[:30]}...")

    b64_header, b64_payload, b64_sig = parts

    # 1. Decode and parse Header
    try:
        header_bytes = b64url_decode(b64_header)
        header = json.loads(header_bytes.decode("utf-8"))
    except Exception as exc:
        raise MalformedBundleSignatureError(f"Malformed JWS protected header: {exc}") from exc

    alg = header.get("alg")
    kid = header.get("kid")

    if alg not in ("RS256", "ES256"):
        raise MalformedBundleSignatureError(f"Insecure or unsupported JWS algorithm: '{alg}'.")
    if not kid or not isinstance(kid, str):
        raise MalformedBundleSignatureError("JWS protected header missing 'kid'.")

    # 2. Check Trust Store & Revocation
    revoked = revoked_key_ids or set()
    if kid in revoked:
        raise UntrustedSigningKeyError(f"Signing key '{kid}' has been revoked.")

    if kid not in trust_store:
        raise UntrustedSigningKeyError(f"Signing key '{kid}' not found in trusted key store.")

    public_key = trust_store[kid]

    # 3. Decode Signature Bytes
    try:
        sig_bytes = b64url_decode(b64_sig)
    except Exception as exc:
        raise MalformedBundleSignatureError(f"Malformed base64url signature data: {exc}") from exc

    # 4. Reconstruct Canonical Signing Input
    bundle_to_verify = copy.deepcopy(bundle_dict)
    # Strip signature.data per FHIR specification
    bundle_to_verify["signature"].pop("data", None)
    canonical_signed_bytes = serialize_canonical_json(bundle_to_verify)

    expected_b64_payload = b64url_encode(canonical_signed_bytes)
    if b64_payload != expected_b64_payload:
        raise InvalidBundleSignatureError("JWS payload does not match canonical bundle content.")

    jws_signing_input = f"{b64_header}.{b64_payload}".encode("ascii")

    # 5. Cryptographic Signature Verification
    try:
        if alg == "RS256":
            if not isinstance(public_key, rsa.RSAPublicKey):
                raise UntrustedSigningKeyError("Public key is not an RSAPublicKey for RS256.")
            public_key.verify(
                sig_bytes,
                jws_signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        elif alg == "ES256":
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise UntrustedSigningKeyError("Public key is not an EllipticCurvePublicKey for ES256.")
            public_key.verify(
                sig_bytes,
                jws_signing_input,
                ec.ECDSA(hashes.SHA256()),
            )
    except InvalidSignature as exc:
        raise InvalidBundleSignatureError("Cryptographic signature verification failed.") from exc

    return True, kid, header


# ─── TIER 3: EXACT TRANSPORT WIRE PAYLOAD BINDING ────────────────────────────

def serialize_transport_payload(bundle_dict: dict[str, Any]) -> Tuple[bytes, str]:
    """Serialize the augmented signed bundle ONCE into an immutable transport bytes buffer.

    Returns:
        Tuple of (exact_payload_bytes, transport_sha256).
    """
    exact_payload_bytes = json.dumps(
        bundle_dict,
        separators=(",", ":"),
        sort_keys=False,
    ).encode("utf-8")
    transport_sha256 = hashlib.sha256(exact_payload_bytes).hexdigest()
    return exact_payload_bytes, transport_sha256


def verify_transport_boundary(payload_bytes: bytes, expected_transport_sha256: str) -> None:
    """Verify that raw bytes at physical transport boundary strictly match expected transport_sha256.

    Fails closed with EHRBundlePayloadTamperedError if even 1 byte was altered.
    """
    computed = hashlib.sha256(payload_bytes).hexdigest()
    if computed != expected_transport_sha256:
        raise EHRBundlePayloadTamperedError(
            f"Physical transport boundary payload tampering detected: "
            f"wire SHA-256 '{computed}' != expected '{expected_transport_sha256}'. "
            f"Transmission aborted; 0 corrupted bytes dispatched."
        )
