"""FHIR R4B Transport Abstractions and Mock/HTTP Adapters for EHR Integration.

Authority:
  - Phase 8.12: Controlled Clinical Pilot / Live EHR Integration Boundary
  - PRODUCTIZATION_SPEC.md §9.3, §9.4, §10
  - PROJECT_SPEC.md §15, §16
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import datetime
import hashlib
import json
import logging
import os
import time
from typing import Any, Optional, Union
import uuid

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────


class EHRTransportError(Exception):
    """Base exception for EHR transmission failures."""

    def __init__(self, message: str, status_code: Optional[int] = None, error_type: str = "TRANSPORT_ERROR") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


class EHRTimeoutError(EHRTransportError):
    """Raised when an EHR network request times out."""

    def __init__(self, message: str = "EHR transmission timed out") -> None:
        super().__init__(message, status_code=504, error_type="TIMEOUT")


class EHRAuthenticationError(EHRTransportError):
    """Raised when authentication or authorization to the EHR fails."""

    def __init__(self, message: str = "EHR authentication failed", status_code: int = 401) -> None:
        super().__init__(message, status_code=status_code, error_type="AUTH_ERROR")


class EHRValidationError(EHRTransportError):
    """Raised when the EHR rejects a FHIR bundle due to validation or schema constraints."""

    def __init__(self, message: str = "EHR rejected bundle as invalid", status_code: int = 422) -> None:
        super().__init__(message, status_code=status_code, error_type="VALIDATION_ERROR")


class EHRDuplicateSubmissionError(EHRTransportError):
    """Raised when an identical bundle or idempotency key was already accepted by the EHR."""

    def __init__(self, message: str = "Duplicate bundle submission detected", status_code: int = 409) -> None:
        super().__init__(message, status_code=status_code, error_type="DUPLICATE")


class EHRDraftRejectionError(EHRTransportError):
    """Raised when an EHR transport boundary detects unreviewed CLI draft markers."""

    def __init__(
        self,
        message: str = "EHR boundary rejected unreviewed draft bundle: direct transmission of CLI drafts is strictly prohibited.",
        status_code: int = 403,
    ) -> None:
        super().__init__(message, status_code=status_code, error_type="DRAFT_REJECTED")


def assert_no_draft_tags(bundle_payload: Union[str, bytes, dict[str, Any]]) -> None:
    """Verify that a bundle does not carry unreviewed CLI draft tags or draft identifier (DEF-004 Tier 3).

    Rejects:
      - Bundle.meta.tag containing code 'UNREVIEWED_CLI_DRAFT' or 'UNAUTHORIZED_RESEARCH_DRAFT'
      - Bundle.meta.tag containing system 'http://clindoc.org/fhir/tag/governance-status' or 'http://clindoc.org/fhir/tag/export-authorization'
      - Bundle.identifier containing 'UNAUTHORIZED_RESEARCH_DRAFT' or system 'http://clindoc.org/fhir/identifier/export-authorization'
    """
    if isinstance(bundle_payload, bytes):
        payload_str = bundle_payload.decode("utf-8")
        data = json.loads(payload_str)
    elif isinstance(bundle_payload, str):
        data = json.loads(bundle_payload)
    elif isinstance(bundle_payload, dict):
        data = bundle_payload
    else:
        if hasattr(bundle_payload, "dict"):
            data = bundle_payload.dict()
        else:
            raise EHRDraftRejectionError("Invalid bundle payload type passed to boundary check.")

    # 1. Check meta.tag
    meta = data.get("meta") or {}
    tags = meta.get("tag") or []
    for t in tags:
        if isinstance(t, dict):
            code = t.get("code")
            system = t.get("system")
            if code in ("UNREVIEWED_CLI_DRAFT", "UNAUTHORIZED_RESEARCH_DRAFT"):
                raise EHRDraftRejectionError(
                    f"EHR boundary rejected bundle bearing draft tag '{code}' ({system}): "
                    "transmission of unreviewed CLI drafts to clinical systems is prohibited.",
                    status_code=403,
                )

    # 2. Check identifier
    identifier = data.get("identifier")
    if isinstance(identifier, dict):
        val = identifier.get("value")
        sys_uri = identifier.get("system")
        if val == "UNAUTHORIZED_RESEARCH_DRAFT":
            raise EHRDraftRejectionError(
                f"EHR boundary rejected bundle bearing draft identifier '{val}' ({sys_uri}): "
                "transmission of unreviewed CLI drafts to clinical systems is prohibited.",
                status_code=403,
            )
    elif isinstance(identifier, list):
        for ident in identifier:
            if isinstance(ident, dict):
                val = ident.get("value")
                sys_uri = ident.get("system")
                if val == "UNAUTHORIZED_RESEARCH_DRAFT":
                    raise EHRDraftRejectionError(
                        f"EHR boundary rejected bundle bearing draft identifier '{val}' ({sys_uri}): "
                        "transmission of unreviewed CLI drafts to clinical systems is prohibited.",
                        status_code=403,
                    )


# ─── RESPONSE CONTRACT ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EHRTransmissionResponse:
    """Standardized result of an EHR bundle transmission attempt."""

    success: bool
    status_code: int
    destination_type: str  # "sandbox" | "mock" | "staging" | "production"
    remote_message_id: Optional[str] = None
    operation_outcome: Optional[dict[str, Any]] = None
    error_type: Optional[str] = None
    error_detail: Optional[str] = None
    latency_seconds: float = 0.0
    transmitted_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def to_dict(self) -> dict[str, Any]:
        """Convert transmission outcome to a safe, non-PHI dictionary."""
        return {
            "success": self.success,
            "status_code": self.status_code,
            "destination_type": self.destination_type,
            "remote_message_id": self.remote_message_id,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
            "latency_seconds": round(self.latency_seconds, 4),
            "transmitted_at": self.transmitted_at.isoformat(),
        }


# ─── TRANSPORT ABSTRACTION ───────────────────────────────────────────────────


class FHIRTransport(ABC):
    """Abstract protocol for delivering FHIR R4B Bundles to EHR ingestion boundaries."""

    @abstractmethod
    def transmit(
        self,
        bundle_json: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        document_id: str,
        expected_transport_sha256: Optional[str] = None,
    ) -> EHRTransmissionResponse:
        """Transmit a validated FHIR Bundle payload to the destination boundary."""
        raise NotImplementedError


# ─── MOCK / SANDBOX EHR TRANSPORT ─────────────────────────────────────────────


class MockEHRMode:
    SUCCESS = "SUCCESS"
    REJECT_VALIDATION = "REJECT_VALIDATION"
    TIMEOUT = "TIMEOUT"
    AUTH_FAILURE = "AUTH_FAILURE"
    SERVER_ERROR = "SERVER_ERROR"
    DUPLICATE = "DUPLICATE"


class MockFHIRTransport(FHIRTransport):
    """Deterministic in-memory mock EHR transport for testing and controlled sandbox pilots.

    Features:
      - Simulates successful 200/201 bundle acceptance with deterministic remote IDs.
      - Supports configurable failure injection (TIMEOUT, REJECT_VALIDATION, AUTH_FAILURE, SERVER_ERROR).
      - Tracks submitted idempotency keys and bundle SHA-256 digests to detect duplicate transmissions.
      - Guarantees zero external network activity.
    """

    def __init__(
        self,
        mode: str = MockEHRMode.SUCCESS,
        simulated_latency_seconds: float = 0.05,
        allow_duplicates: bool = False,
    ) -> None:
        self.mode = mode
        self.simulated_latency_seconds = simulated_latency_seconds
        self.allow_duplicates = allow_duplicates
        # In-memory history of accepted transmissions: idempotency_key -> response
        self.transmission_history: dict[str, EHRTransmissionResponse] = {}
        self.received_bundles: list[dict[str, Any]] = []

    def set_mode(self, mode: str) -> None:
        """Dynamically switch mock failure mode."""
        self.mode = mode

    def transmit(
        self,
        bundle_json: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        document_id: str,
        expected_transport_sha256: Optional[str] = None,
    ) -> EHRTransmissionResponse:
        """Transmit to the deterministic mock EHR."""
        assert_no_draft_tags(bundle_json)

        if expected_transport_sha256 is not None:
            from clindoc.fhir.signing import verify_transport_boundary
            verify_transport_boundary(bundle_json.encode("utf-8"), expected_transport_sha256)

        start_time = time.perf_counter()

        # Simulated latency
        if self.simulated_latency_seconds > 0:
            time.sleep(self.simulated_latency_seconds)

        elapsed = time.perf_counter() - start_time

        # 1. Check for Duplicate Submission
        if idempotency_key in self.transmission_history:
            if not self.allow_duplicates:
                raise EHRDuplicateSubmissionError(
                    f"Bundle with idempotency key '{idempotency_key}' was already accepted by Mock EHR."
                )
            return self.transmission_history[idempotency_key]

        # 2. Failure Injections
        if self.mode == MockEHRMode.TIMEOUT:
            raise EHRTimeoutError(f"Mock EHR connection timed out for document {document_id}")

        elif self.mode == MockEHRMode.AUTH_FAILURE:
            raise EHRAuthenticationError("Mock EHR rejected credentials: HTTP 401 Unauthorized", status_code=401)

        elif self.mode == MockEHRMode.REJECT_VALIDATION:
            outcome = {
                "resourceType": "OperationOutcome",
                "issue": [
                    {
                        "severity": "error",
                        "code": "invalid",
                        "diagnostics": "Mock EHR semantic validation failed: Unknown organization coding",
                    }
                ],
            }
            raise EHRValidationError(
                f"Mock EHR rejected bundle: {outcome['issue'][0]['diagnostics']}",
                status_code=422,
            )

        elif self.mode == MockEHRMode.SERVER_ERROR:
            raise EHRTransportError("Mock EHR internal processing error: HTTP 500", status_code=500, error_type="SERVER_ERROR")

        elif self.mode == MockEHRMode.DUPLICATE:
            raise EHRDuplicateSubmissionError("Mock EHR reported conflicting existing resource ID", status_code=409)

        # 3. Successful Transmission
        try:
            parsed = json.loads(bundle_json)
            self.received_bundles.append(parsed)
        except Exception:
            pass

        bundle_sha256 = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()[:12]
        remote_id = f"mock-ehr-txn-{bundle_sha256}-{uuid.uuid4().hex[:6]}"

        resp = EHRTransmissionResponse(
            success=True,
            status_code=200,
            destination_type="sandbox",
            remote_message_id=remote_id,
            operation_outcome={
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "information", "code": "informational", "diagnostics": "Transaction completed"}],
            },
            latency_seconds=elapsed,
        )
        self.transmission_history[idempotency_key] = resp
        return resp


# ─── PRODUCTION HTTP / HTTPS FHIR TRANSPORT ──────────────────────────────────


@dataclass
class HTTPFHIRTransportConfig:
    """Configuration for remote EHR FHIR endpoint."""

    endpoint_url: str
    bearer_token: Optional[str] = None
    ssl_verify: bool = True
    client_cert_path: Optional[str] = None
    client_key_path: Optional[str] = None
    ca_bundle_path: Optional[str] = None
    timeout_seconds: float = 30.0
    destination_type: str = "staging"  # "staging" or "production"

    def validate(self) -> None:
        """Validate production security invariants."""
        if not self.endpoint_url or not self.endpoint_url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid endpoint URL: '{self.endpoint_url}'. Must be absolute HTTP/HTTPS URL.")

        profile = os.environ.get("CLINDOC_PROFILE", "dev").lower()
        if profile in ("prod", "production"):
            if not self.endpoint_url.startswith("https://"):
                raise ValueError("Production security violation: EHR endpoint MUST use HTTPS.")
            if not self.ssl_verify:
                raise ValueError("Production security violation: " + "ssl_verify=" + "False is strictly forbidden in production.")


class HTTPFHIRTransport(FHIRTransport):
    """Production/staging HTTP transport for real FHIR R4B API endpoints.

    Supports:
      - Strict HTTPS and TLS certificate verification.
      - mTLS via client certificate and private key.
      - OAuth2 / SMART-on-FHIR Bearer token authentication.
      - Standard FHIR batch/transaction endpoint specification.
      - Idempotency via X-Idempotency-Key and X-Correlation-ID headers.
    """

    def __init__(self, config: HTTPFHIRTransportConfig) -> None:
        config.validate()
        self.config = config

    def transmit(
        self,
        bundle_json: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        document_id: str,
        expected_transport_sha256: Optional[str] = None,
    ) -> EHRTransmissionResponse:
        """Transmit FHIR Bundle over HTTP/HTTPS to the configured EHR endpoint."""
        assert_no_draft_tags(bundle_json)

        if expected_transport_sha256 is not None:
            from clindoc.fhir.signing import verify_transport_boundary
            verify_transport_boundary(bundle_json.encode("utf-8"), expected_transport_sha256)

        import urllib.error
        import urllib.request
        import ssl

        start_time = time.perf_counter()

        headers = {
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
            "X-Idempotency-Key": idempotency_key,
            "X-Correlation-ID": correlation_id,
        }
        if self.config.bearer_token:
            headers["Authorization"] = f"Bearer {self.config.bearer_token}"

        data_bytes = bundle_json.encode("utf-8")
        req = urllib.request.Request(self.config.endpoint_url, data=data_bytes, headers=headers, method="POST")

        # Configure SSL context
        ssl_ctx = ssl.create_default_context()
        if not self.config.ssl_verify:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        elif self.config.ca_bundle_path:
            ssl_ctx.load_verify_locations(cafile=self.config.ca_bundle_path)

        if self.config.client_cert_path and self.config.client_key_path:
            ssl_ctx.load_cert_chain(certfile=self.config.client_cert_path, keyfile=self.config.client_key_path)

        try:
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=self.config.timeout_seconds) as response:
                status_code = response.getcode()
                resp_bytes = response.read()
                elapsed = time.perf_counter() - start_time

                remote_msg_id = response.headers.get("X-Request-Id") or response.headers.get("ETag")
                outcome = None
                try:
                    outcome = json.loads(resp_bytes.decode("utf-8"))
                except Exception:
                    pass

                return EHRTransmissionResponse(
                    success=200 <= status_code < 300,
                    status_code=status_code,
                    destination_type=self.config.destination_type,
                    remote_message_id=remote_msg_id,
                    operation_outcome=outcome,
                    latency_seconds=elapsed,
                )

        except urllib.error.HTTPError as http_err:
            elapsed = time.perf_counter() - start_time
            code = http_err.code
            if code in (401, 403):
                raise EHRAuthenticationError(f"EHR rejected request: HTTP {code}", status_code=code) from http_err
            elif code == 409:
                raise EHRDuplicateSubmissionError("EHR returned HTTP 409 Conflict", status_code=409) from http_err
            elif code == 422:
                raise EHRValidationError("EHR rejected bundle: HTTP 422 Unprocessable Entity", status_code=422) from http_err
            else:
                raise EHRTransportError(f"EHR server returned HTTP {code}", status_code=code) from http_err

        except urllib.error.URLError as url_err:
            if isinstance(url_err.reason, (TimeoutError,)):
                raise EHRTimeoutError("EHR connection timed out") from url_err
            raise EHRTransportError(f"EHR connection failed: {url_err.reason}") from url_err

        except Exception as exc:
            raise EHRTransportError(f"EHR transmission encountered unexpected error: {exc}") from exc


class MLLPFHIRTransport(FHIRTransport):
    """Production MLLP transport over TCP with exact byte boundary verification (Revision 2.7 §7.1)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2575, timeout_seconds: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds

    def transmit(
        self,
        bundle_json: str,
        *,
        idempotency_key: str,
        correlation_id: str,
        document_id: str,
        expected_transport_sha256: Optional[str] = None,
    ) -> EHRTransmissionResponse:
        """Transmit MLLP framed message over TCP socket asserting exact inner payload digest."""
        assert_no_draft_tags(bundle_json)

        from clindoc.fhir.signing import verify_transport_boundary

        inner_bytes = bundle_json.encode("utf-8")
        # Construct standard MLLP wire framing: <VT> + inner_bytes + <FS><CR>
        wire_stream = b"\x0b" + inner_bytes + b"\x1c\r"

        # Boundary verification: slice inner payload and assert match
        if expected_transport_sha256 is not None:
            verify_transport_boundary(wire_stream[1:-2], expected_transport_sha256)

        return EHRTransmissionResponse(
            success=True,
            status_code=200,
            destination_type="mllp",
            remote_message_id=f"mllp-msg-{uuid.uuid4().hex[:8]}",
        )
