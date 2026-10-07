"""SQLAlchemy ORM models for Clindoc2FHIR clinical document review platform.

Implements all 14 core tables mandated by PRODUCTIZATION_SPEC.md §4 and
PRODUCTIZATION_DECISIONS.md (PD-002, PD-005, PD-006, PD-008, PD-009, PD-010,
PD-011, PD-012, PD-015, PD-016, PD-018, PD-019).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from clindoc.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    ContradictionType,
    DocumentApprovalStatus,
    DocumentStatus,
    FHIRExportStatus,
    FHIRVersion,
    IdentityVerificationStatus,
    JobStatus,
    PatientIdentityApprovalStatus,
    RetentionState,
    ReviewActionType,
    VerificationLabel,
    VerificationStage,
    ExtractionRoute,
    TriagePolicyMode,
)


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Platform user account.
    
    Credentials live strictly in the external OIDC Identity Provider (PD-006).
    No passwords, salt, or biometric credentials are saved in this database.
    """

    __tablename__ = "users"

    idp_subject: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
        doc="External OIDC subject identifier matching upstream IdP token claim",
    )
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    roles: Mapped[List[UserRole]] = relationship(
        "UserRole", foreign_keys="UserRole.user_id", back_populates="user", cascade="all, delete-orphan"
    )
    uploaded_documents: Mapped[List[Document]] = relationship(
        "Document", foreign_keys="Document.uploaded_by", back_populates="uploader"
    )
    review_actions: Mapped[List[ReviewAction]] = relationship("ReviewAction", back_populates="actor")
    approvals: Mapped[List[DocumentApproval]] = relationship("DocumentApproval", back_populates="approver")


class Role(Base):
    """Role definition for Role-Based Access Control (RBAC)."""

    __tablename__ = "roles"

    id: Mapped[str] = mapped_column(String(50), primary_key=True, doc="Role code: admin, reviewer, approver, auditor, operator")
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    permissions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    user_assignments: Mapped[List[UserRole]] = relationship("UserRole", back_populates="role")


class UserRole(Base, UUIDPrimaryKeyMixin):
    """User-to-role assignment junction."""

    __tablename__ = "user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_user_roles_user_role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("roles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    assigned_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    user: Mapped[User] = relationship("User", foreign_keys=[user_id], back_populates="roles")
    role: Mapped[Role] = relationship("Role", foreign_keys=[role_id], back_populates="user_assignments")


class Document(Base, UUIDPrimaryKeyMixin):
    """Clinical document record.
    
    Stores ingestion metadata, processing state, storage reference, and patient
    linkage context. Original binary blobs live in encrypted blob storage.
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("file_size_bytes > 0", name="ck_document_size_positive"),
        CheckConstraint("page_count > 0", name="ck_document_page_count_positive"),
        Index("ix_documents_sha256", "sha256"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_uploaded_at", "uploaded_at"),
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Cryptographic SHA-256 hash of original raw document payload",
    )
    storage_ref: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Secure URI or object key referencing document in encrypted object store",
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), default="scanned_pdf", nullable=False)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    status: Mapped[DocumentStatus] = mapped_column(
        String(32),
        default=DocumentStatus.INGESTED,
        nullable=False,
    )

    # Patient Context Linkage (PD-015)
    patient_context_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        doc="Operator-selected target patient reference/MRN from EHR. Not a longitudinal patient record.",
    )
    identity_verification_status: Mapped[IdentityVerificationStatus] = mapped_column(
        String(32),
        default=IdentityVerificationStatus.UNVERIFIED,
        nullable=False,
    )
    identity_confirmed_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    identity_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Document lineage and duplicate detection
    duplicate_of: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    supersedes_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        doc="Reference to previous document version if this is a corrected re-upload",
    )

    # Retention and Legal Hold
    retention_state: Mapped[RetentionState] = mapped_column(
        String(32),
        default=RetentionState.ACTIVE,
        nullable=False,
    )
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retention_class: Mapped[str] = mapped_column(String(50), default="standard", nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="Tombstone timestamp when document and related blobs were purged",
    )

    # Relationships
    uploader: Mapped[User] = relationship("User", foreign_keys=[uploaded_by], back_populates="uploaded_documents")
    processing_jobs: Mapped[List[ProcessingJob]] = relationship("ProcessingJob", back_populates="document", cascade="all, delete-orphan")
    claims: Mapped[List[Claim]] = relationship("Claim", back_populates="document", cascade="all, delete-orphan")
    evidence_records: Mapped[List[Evidence]] = relationship("Evidence", back_populates="document", cascade="all, delete-orphan")
    routing_decisions: Mapped[List[RoutingDecision]] = relationship("RoutingDecision", back_populates="document", cascade="all, delete-orphan")
    approvals: Mapped[List[DocumentApproval]] = relationship("DocumentApproval", back_populates="document", cascade="all, delete-orphan")
    fhir_bundles: Mapped[List[FHIRBundle]] = relationship("FHIRBundle", back_populates="document", cascade="all, delete-orphan")


class QualifiedConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cryptographically anchored Qualified Configuration Manifest (PD-005)."""

    __tablename__ = "qualified_configs"

    manifest_digest: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
        doc="SHA-256 digest of canonical serialized manifest JSON",
    )
    manifest_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    benchmark_claims_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    benchmark_manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    model_runs: Mapped[List[ModelRun]] = relationship("ModelRun", back_populates="qualified_config")


class ModelRun(Base, UUIDPrimaryKeyMixin):
    """Pipeline and model execution parameters recorded for every processing job."""

    __tablename__ = "model_runs"

    qualified_config_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("qualified_configs.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_qualified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        doc="True if execution strictly matched audited qualified configuration",
    )
    runtime_profile: Mapped[str] = mapped_column(String(32), nullable=False, doc="'dev' or 'production'")
    stage_b_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage_b_checkpoint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_b_temperature: Mapped[float] = mapped_column(Float, nullable=False)
    stage_b_escalation_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    stage_b_autonomous_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    stage_c_model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage_c_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_c_prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_c_serving_engine: Mapped[str] = mapped_column(String(64), nullable=False)
    calibrator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", nullable=False)

    # Extraction Model Provenance (PRODUCTIZATION_SPEC.md §3.7)
    extraction_model_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, doc="Extraction LLM model ID per §3.7"
    )
    extraction_prompt_version: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="Extraction prompt version per §3.7"
    )
    extraction_prompt_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="SHA-256 of extraction prompt per §3.7"
    )
    extraction_serving_engine: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="Extraction serving engine per §3.7"
    )
    extraction_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False, doc="Decoding parameters: temperature, seed, max_tokens, etc."
    )

    # Provenance Completeness Fields (PRODUCTIZATION_SPEC.md §3.7)
    component: Mapped[str] = mapped_column(
        String(50), default="pipeline_cascade", nullable=False, doc="Component identifier per §3.7: 'pipeline_cascade', 'stage_b', 'stage_c', or 'extractor'"
    )
    provider_kind: Mapped[str] = mapped_column(
        String(50), default="vllm", nullable=False, doc="Inference provider kind per §3.7: 'vllm', 'mock', 'heuristic'"
    )
    model_revision_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="Model Git revision hash per §3.7 (None for unpinned Colab runs)"
    )
    quantization: Mapped[Optional[str]] = mapped_column(
        String(32), default="AWQ_4BIT", nullable=True, doc="Model quantization per §3.7 (e.g. 'AWQ_4BIT', 'FP16')"
    )
    serving_engine_version: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, doc="Serving engine version per §3.7"
    )
    prompt_id: Mapped[Optional[str]] = mapped_column(
        String(64), default="V8_STAGE_C_LABFLAG_PROMPT_V1", nullable=True, doc="Canonical prompt template ID per §3.7"
    )
    schema_version: Mapped[str] = mapped_column(
        String(32), default="1.0.0", nullable=False, doc="Contract schema version per §3.7"
    )
    stage_c_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=lambda: {"temperature": 0.0, "seed": 42, "max_tokens": 256}, nullable=False, doc="Stage C decoding parameters per §3.7: temperature, seed, max_tokens"
    )

    qualified_config: Mapped[Optional[QualifiedConfig]] = relationship("QualifiedConfig", back_populates="model_runs")
    processing_jobs: Mapped[List[ProcessingJob]] = relationship("ProcessingJob", back_populates="model_run")
    verification_results: Mapped[List[VerificationResult]] = relationship("VerificationResult", back_populates="model_run")
    claims: Mapped[List[Claim]] = relationship("Claim", back_populates="model_run")


class ProcessingJob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Background document conversion and verification job queue entry."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="ck_job_attempts_nonnegative"),
        Index("ix_processing_jobs_status", "status"),
        Index("ix_processing_jobs_document_id", "document_id"),
        Index("ix_processing_jobs_lease_expires", "lease_expires_at"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[JobStatus] = mapped_column(String(32), default=JobStatus.PENDING, nullable=False)
    current_stage: Mapped[str] = mapped_column(String(50), default="INIT", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    worker_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        doc="Lease expiration timestamp for worker crash recovery (F-17)",
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parent_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )

    document: Mapped[Document] = relationship("Document", back_populates="processing_jobs")
    model_run: Mapped[Optional[ModelRun]] = relationship("ModelRun", back_populates="processing_jobs")
    stage_events: Mapped[List[JobStageEvent]] = relationship("JobStageEvent", back_populates="job", cascade="all, delete-orphan")


class JobStageEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Granular execution stage metrics and telemetry per processing job."""

    __tablename__ = "job_stage_events"
    __table_args__ = (
        Index("ix_job_stage_events_job_id", "job_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage_name: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    job: Mapped[ProcessingJob] = relationship("ProcessingJob", back_populates="stage_events")


class RoutingDecision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Layout block routing decision record (PRODUCTIZATION_SPEC.md §4.3, PROJECT_SPEC.md §7.2).
    
    Records router branch selection (deterministic, gliner, llm_narrative) for
    every region, including regions that yield zero claims.
    """

    __tablename__ = "routing_decisions"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_routing_decision_confidence_range",
        ),
        Index("ix_routing_decisions_document_id", "document_id"),
        Index("ix_routing_decisions_region_id", "region_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    region_id: Mapped[str] = mapped_column(String(100), nullable=False, doc="Region identifier or block name")
    route: Mapped[str] = mapped_column(String(50), nullable=False, doc="'deterministic', 'gliner', 'llm_narrative'")
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, doc="Decision rationale or trigger regex")
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False, doc="Router branch confidence")

    # Relationship
    document: Mapped[Document] = relationship("Document", back_populates="routing_decisions")


class Claim(Base, UUIDPrimaryKeyMixin):
    """Clinical claim version record.
    
    Implements immutable claim history (PD-010). When a human reviewer edits a
    claim, a new Claim record is inserted with origin='reviewer' and
    supersedes_claim_id pointing to the prior version. Prior versions are NEVER
    overwritten in-place.
    """

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_claim_version_positive"),
        UniqueConstraint("claim_id", "version", name="uq_claims_claim_version"),
        Index("ix_claims_claim_id", "claim_id"),
        Index("ix_claims_document_id", "document_id"),
        Index("ix_claims_review_state", "review_state"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        doc="Stable UUID identity of the clinical entity across all its edits and versions",
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[str] = mapped_column(String(100), nullable=False, doc="Visual or field grouping ID")
    field_type: Mapped[str] = mapped_column(String(100), nullable=False, doc="Clinical field name (e.g. medication, lab_test)")
    value: Mapped[str] = mapped_column(Text, nullable=False, doc="Extracted or edited claim value")
    normalized_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    code_system: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes_claim_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="SET NULL"),
        nullable=True,
        doc="Links to prior row if this version was created by an edit",
    )
    origin: Mapped[ClaimOrigin] = mapped_column(
        String(32),
        default=ClaimOrigin.PIPELINE,
        nullable=False,
        doc="'pipeline' or 'reviewer'",
    )
    review_state: Mapped[ClaimReviewState] = mapped_column(
        String(32),
        default=ClaimReviewState.PENDING,
        nullable=False,
    )
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="NULL for pipeline extraction; User UUID for reviewer-created or edited claim",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    evidence_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Grounding evidence reference per PRODUCTIZATION_SPEC §4.3",
    )
    model_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="Provenance record for LLM-extracted claims per PRODUCTIZATION_SPEC §3.7",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, doc="Full canonical claim JSON payload")

    # Relationships
    document: Mapped[Document] = relationship("Document", back_populates="claims")
    evidence: Mapped[Optional[Evidence]] = relationship("Evidence", back_populates="claims")
    model_run: Mapped[Optional[ModelRun]] = relationship("ModelRun", back_populates="claims")
    verification_results: Mapped[List[VerificationResult]] = relationship("VerificationResult", back_populates="claim", cascade="all, delete-orphan")
    review_actions: Mapped[List[ReviewAction]] = relationship("ReviewAction", back_populates="claim", cascade="all, delete-orphan")


class Evidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Grounding evidence span in source document (PRODUCTIZATION_SPEC.md §4.3, PROJECT_SPEC.md §9).
    
    Document-level entity representing a specific text span / bounding box in a document.
    Reusable across multiple claims that ground in the same source text.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "page_number",
            "char_start",
            "char_end",
            name="uq_evidence_document_span",
        ),
        Index("ix_evidence_document_id", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    bounding_box: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True, doc="Normalized bbox: [x0, y0, x1, y1]")
    ocr_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(50), default="ocr_text", nullable=False)
    evidence_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
        doc="Verbatim source span. Nullable for privacy minimization / data stripping (PD-011)",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    # Relationships
    document: Mapped[Document] = relationship("Document", back_populates="evidence_records")
    claims: Mapped[List[Claim]] = relationship("Claim", back_populates="evidence")


# Backwards compatibility alias
ClaimEvidence = Evidence
ConfigurationRun = ModelRun


class VerificationResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Categorical verification verdict and calibrated score for a claim version."""

    __tablename__ = "verification_results"
    __table_args__ = (
        CheckConstraint(
            "calibrated_confidence >= 0.0 AND calibrated_confidence <= 1.0",
            name="ck_verification_confidence_range",
        ),
        Index("ix_verification_results_claim_id", "claim_id"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[VerificationLabel] = mapped_column(String(32), nullable=False)
    calibrated_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verification_stage: Mapped[VerificationStage] = mapped_column(String(32), nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fallback_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, doc="Machine-readable fallback code: 'llm_unavailable' or 'llm_schema_invalid'")
    provider_kind: Mapped[str] = mapped_column(String(50), nullable=False, doc="'modernbert_vllm', 'mock_dev', 'heuristic'")
    model_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("model_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    contradiction_type: Mapped[Optional[ContradictionType]] = mapped_column(String(32), nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stage_a_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    stage_b_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    stage_c_details: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    claim: Mapped[Claim] = relationship("Claim", back_populates="verification_results")
    model_run: Mapped[Optional[ModelRun]] = relationship("ModelRun", back_populates="verification_results")


class ReviewAction(Base, UUIDPrimaryKeyMixin):
    """Immutable audit entry of human reviewer actions on a clinical claim."""

    __tablename__ = "review_actions"
    __table_args__ = (
        Index("ix_review_actions_claim_id", "claim_id"),
        Index("ix_review_actions_actor_id", "actor_id"),
        Index("ix_review_actions_created_at", "created_at"),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    action_type: Mapped[ReviewActionType] = mapped_column(String(32), nullable=False)
    previous_state: Mapped[str] = mapped_column(String(50), nullable=False)
    new_state: Mapped[str] = mapped_column(String(50), nullable=False)
    reason_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, doc="Reviewer rationale (sensitive)")
    before_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    after_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    claim: Mapped[Claim] = relationship("Claim", back_populates="review_actions")
    actor: Mapped[User] = relationship("User", back_populates="review_actions")


class DocumentApproval(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Formal Four-Eyes document approval required prior to FHIR export (PD-002, PD-009)."""

    __tablename__ = "document_approvals"
    __table_args__ = (
        Index("ix_document_approvals_document_id", "document_id"),
        Index("ix_document_approvals_approver_id", "approver_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    approver_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        doc="Must not be document uploader or sole claim reviewer (Four-Eyes check)",
    )
    approval_status: Mapped[DocumentApprovalStatus] = mapped_column(String(32), nullable=False)
    patient_identity_status: Mapped[PatientIdentityApprovalStatus] = mapped_column(
        String(32),
        nullable=False,
        doc="Explicit operator signoff confirming patient identity matches document demographics (PD-015)",
    )
    identity_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    is_export_eligible: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        doc="True only when approval is APPROVED and patient identity is verified",
    )

    document: Mapped[Document] = relationship("Document", back_populates="approvals")
    approver: Mapped[User] = relationship("User", back_populates="approvals")
    fhir_bundles: Mapped[List[FHIRBundle]] = relationship("FHIRBundle", back_populates="approval")


class TriagePolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Versioned triage policy configuration (PRODUCTIZATION_SPEC §4.3, §7.2).

    Controls review categorization, risk prioritization, and bulk-action rules.
    Under Mode 0 (default/launch), 100% of claims require human decision before export.
    """

    __tablename__ = "triage_policies"
    __table_args__ = (
        Index("ix_triage_policies_version", "version", unique=True),
        Index("ix_triage_policies_is_active", "is_active"),
    )

    version: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        unique=True,
        doc="Semantic policy version, e.g. '1.0.0-mode0'",
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[TriagePolicyMode] = mapped_column(
        String(20),
        default=TriagePolicyMode.MODE_0,
        nullable=False,
        doc="Policy mode: 'MODE_0' (full review), 'MODE_1' (assisted)",
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)



class FHIRBundle(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Generated FHIR resource bundle (R4/R4B) linked to approved claim snapshots."""

    __tablename__ = "fhir_bundles"
    __table_args__ = (
        Index("ix_fhir_bundles_document_id", "document_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_approvals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fhir_version: Mapped[FHIRVersion] = mapped_column(String(32), default=FHIRVersion.R4B, nullable=False)
    provenance_minimized: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        doc="True if evidenceText was stripped from FHIR Provenance resource (PD-011)",
    )
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_ref: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Object store key or secure file path to finalized JSON bundle",
    )
    bundle_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)

    document: Mapped[Document] = relationship("Document", back_populates="fhir_bundles")
    approval: Mapped[DocumentApproval] = relationship("DocumentApproval", back_populates="fhir_bundles")
    exports: Mapped[List[FHIRExport]] = relationship("FHIRExport", back_populates="bundle", cascade="all, delete-orphan")


class FHIRExport(Base, UUIDPrimaryKeyMixin):
    """Idempotent record of FHIR bundle export or transmission."""

    __tablename__ = "fhir_exports"
    __table_args__ = (
        Index("ix_fhir_exports_bundle_id", "bundle_id"),
    )

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("fhir_bundles.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(50), default="FILE_DOWNLOAD", nullable=False)
    export_status: Mapped[FHIRExportStatus] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    destination_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    exported_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    exported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    bundle: Mapped[FHIRBundle] = relationship("FHIRBundle", back_populates="exports")
    exporter: Mapped[User] = relationship("User", foreign_keys=[exported_by])


class AuditEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only, tamper-evident cryptographic hash chain audit log (PD-012).
    
    Contains NO raw PHI values. The application database role must have INSERT-only
    permissions on this table in PostgreSQL production deployment.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("sequence_number > 0", name="ck_audit_sequence_positive"),
        UniqueConstraint("sequence_number", name="uq_audit_sequence_number"),
        UniqueConstraint("current_event_hash", name="uq_audit_current_event_hash"),
        Index("ix_audit_events_sequence", "sequence_number"),
        Index("ix_audit_events_timestamp", "timestamp"),
        Index("ix_audit_events_event_type", "event_type"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
    )

    sequence_number: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Strictly monotonically increasing positive integer index",
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="User ID or internal system service identity",
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Standardized event code (e.g. DOCUMENT_INGESTED, CLAIM_REVIEWED, APPROVAL_GRANTED)",
    )
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, doc="Target entity type (e.g. document, claim, bundle)")
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False, doc="String/UUID identifier of target entity")
    action: Mapped[str] = mapped_column(String(50), nullable=False, doc="Operation: CREATE, UPDATE, READ, EXPORT, APPROVE")
    source_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=dict,
        nullable=False,
        doc="Non-PHI audit payload, identifiers, checksums, and parameters",
    )
    previous_event_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 hash of the preceding sequence audit event (NULL for genesis event)",
    )
    current_event_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="SHA-256(sequence || timestamp_iso || actor || event_type || entity_type || entity_id || action || metadata_json || prev_hash)",
    )
