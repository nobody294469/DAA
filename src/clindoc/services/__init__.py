"""Services package for ClinDoc2FHIR.

Authority:
  - PROJECT_SPEC.md §3, §12, §13.3
  - PRODUCTIZATION_SPEC.md §3.3, §3.4, §3.5, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 (Secure Model Serving Infrastructure)
"""
from __future__ import annotations

from clindoc.services.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    ConcurrencyLimiter,
    ConcurrencyLimitExceededError,
    ExponentialBackoff,
    FALLBACK_REASON_LLM_SCHEMA_INVALID,
    FALLBACK_REASON_LLM_UNAVAILABLE,
    classify_fallback_reason,
    get_shared_circuit_breaker,
    get_shared_concurrency_limiter,
    is_qualifying_breaker_failure,
    is_retryable_error,
    reset_shared_registries,
)

__all__ = [
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "CircuitState",
    "ConcurrencyLimiter",
    "ConcurrencyLimitExceededError",
    "ExponentialBackoff",
    "FALLBACK_REASON_LLM_SCHEMA_INVALID",
    "FALLBACK_REASON_LLM_UNAVAILABLE",
    "classify_fallback_reason",
    "get_shared_circuit_breaker",
    "get_shared_concurrency_limiter",
    "is_qualifying_breaker_failure",
    "is_retryable_error",
    "reset_shared_registries",
    # FHIR Export (Phase 8.8)
    "FHIRExportService",
    # EHR Integration (Phase 8.12)
    "EHRIntegrationService",
    "EHRIntegrationError",
    "EHRPatientIdentityError",
    "EHRApprovalMissingError",
    "EHRUnresolvedClaimError",
    "EHRTransmissionResult",
    "FHIRTransport",
    "MockFHIRTransport",
    "HTTPFHIRTransport",
    "EHRTransportError",
    "EHRTimeoutError",
    "EHRAuthenticationError",
    "EHRValidationError",
    "EHRDuplicateSubmissionError",
]

from clindoc.services.fhir_export import FHIRExportService
from clindoc.services.ehr_transport import (
    EHRAuthenticationError,
    EHRDuplicateSubmissionError,
    EHRTimeoutError,
    EHRTransmissionResponse,
    EHRTransportError,
    EHRValidationError,
    FHIRTransport,
    HTTPFHIRTransport,
    MockFHIRTransport,
)
from clindoc.services.ehr_integration import (
    EHRApprovalMissingError,
    EHRIntegrationError,
    EHRIntegrationService,
    EHRPatientIdentityError,
    EHRTransmissionResult,
    EHRUnresolvedClaimError,
)

