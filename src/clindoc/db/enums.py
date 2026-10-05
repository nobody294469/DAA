"""Domain enumerations for Clindoc2FHIR relational database schema."""

from __future__ import annotations

from enum import Enum


class DocumentStatus(str, Enum):
    """Lifecycle state of a clinical document within the processing platform."""

    INGESTED = "INGESTED"
    QUARANTINED = "QUARANTINED"
    FAILED_INGEST = "FAILED_INGEST"
    PROCESSING = "PROCESSING"
    PENDING_REVIEW = "PENDING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPORTED = "EXPORTED"
    FAILED = "FAILED"


class IdentityVerificationStatus(str, Enum):
    """Status of operator confirmation matching document demographics to patient context."""

    UNVERIFIED = "UNVERIFIED"
    CONFIRMED = "CONFIRMED"
    MISMATCH = "MISMATCH"
    MISMATCH_ACCEPTED = "MISMATCH_ACCEPTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RetentionState(str, Enum):
    """Lifecycle state for data retention, legal hold, and secure deletion."""

    ACTIVE = "ACTIVE"
    RETENTION_HOLD = "RETENTION_HOLD"
    SCHEDULED_PURGE = "SCHEDULED_PURGE"
    PURGED = "PURGED"


class ClaimReviewState(str, Enum):
    """State of an individual clinical claim within the human review workflow."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EDITED = "EDITED"
    ESCALATED = "ESCALATED"


class ClaimOrigin(str, Enum):
    """Origin of a clinical claim version."""

    PIPELINE = "pipeline"
    REVIEWER = "reviewer"


class VerificationLabel(str, Enum):
    """Categorical verdict emitted by the verification cascade."""

    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class VerificationStage(str, Enum):
    """Stage within the cascade that resolved the claim verification."""

    STAGE_A = "STAGE_A"
    STAGE_B = "STAGE_B"
    STAGE_C = "STAGE_C"
    HUMAN_REVERIFIED = "HUMAN_REVERIFIED"


class ContradictionType(str, Enum):
    """Specific category of contradiction detected when a claim is REFUTED."""

    NUMERICAL_MISMATCH = "NUMERICAL_MISMATCH"
    NEGATION = "NEGATION"
    ENTITY_MISMATCH = "ENTITY_MISMATCH"
    TEMPORAL_MISMATCH = "TEMPORAL_MISMATCH"
    OTHER = "OTHER"


class ReviewActionType(str, Enum):
    """Action performed by a human reviewer on a clinical claim."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    EDIT = "EDIT"
    ESCALATE = "ESCALATE"
    REOPEN = "REOPEN"
    COMMENT = "COMMENT"


class DocumentApprovalStatus(str, Enum):
    """Formal Four-Eyes approval decision on a processed document."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"


class PatientIdentityApprovalStatus(str, Enum):
    """Explicit signoff status for patient identity matching prior to export."""

    CONFIRMED = "CONFIRMED"
    MISMATCH_ACCEPTED = "MISMATCH_ACCEPTED"
    OVERRIDDEN = "OVERRIDDEN"


class FHIRVersion(str, Enum):
    """Target FHIR specification version for generated resource bundles."""

    R4 = "R4"
    R4B = "R4B"


class FHIRExportStatus(str, Enum):
    """Status of FHIR bundle export execution."""

    GENERATED = "GENERATED"
    EXPORTED_FILE = "EXPORTED_FILE"
    TRANSMITTED = "TRANSMITTED"
    FAILED = "FAILED"


class JobStatus(str, Enum):
    """Status of an asynchronous document processing job."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExtractionRoute(str, Enum):
    """Routing branch selected by the Extraction Router (PROJECT_SPEC §7.2)."""

    DETERMINISTIC = "deterministic"
    GLINER = "gliner"
    LLM_NARRATIVE = "llm_narrative"


class TriagePolicyMode(str, Enum):
    """Triage policy operational mode (PRODUCTIZATION_SPEC §7.2)."""

    MODE_0 = "MODE_0"  # Full human review: 100% of claims require human decision
    MODE_1 = "MODE_1"  # Assisted: Auto-processed claims pre-accepted with random QA sample (deferred)
    MODE_2 = "MODE_2"  # Fully autonomous (forbidden)


class TriageCategory(str, Enum):
    """Triage classification category for clinical claims (PRODUCTIZATION_SPEC §7.2)."""

    AUTO_PROCESSED_AGREED = "AUTO_PROCESSED_AGREED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class TriageRiskTier(str, Enum):
    """Risk tier for review queue prioritization (PRODUCTIZATION_SPEC §7.2)."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

