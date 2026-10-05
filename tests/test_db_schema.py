"""Unit tests for Phase 8.3 Task 1: Relational Persistence & Core Data Model.

Validates schema compilation, check constraints, unique constraints, evidence
deduplication, claim-to-model provenance linkage, routing decisions, deletion
cascades/restrictions, claim versioning lineage, and audit hash chain invariants.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from clindoc.db import (
    AuditEvent,
    Base,
    Claim,
    ClaimOrigin,
    ClaimReviewState,
    Document,
    DocumentApproval,
    DocumentApprovalStatus,
    DocumentStatus,
    Evidence,
    ExtractionRoute,
    FHIRBundle,
    FHIRVersion,
    IdentityVerificationStatus,
    JobStageEvent,
    JobStatus,
    ModelRun,
    PatientIdentityApprovalStatus,
    ProcessingJob,
    QualifiedConfig,
    ReviewAction,
    ReviewActionType,
    Role,
    RoutingDecision,
    User,
    UserRole,
    VerificationLabel,
    VerificationResult,
    VerificationStage,
)


@pytest.fixture
def db_session():
    """Provides an isolated in-memory SQLite database with foreign keys enabled."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _create_test_user(session: Session, username: str = "reviewer_1", email: str = "rev1@example.com") -> User:
    user = User(
        idp_subject=f"sub_{uuid.uuid4()}",
        username=username,
        full_name="Test Clinical Reviewer",
        email=email,
        is_active=True,
    )
    session.add(user)
    session.commit()
    return user


def _create_test_document(session: Session, uploader: User) -> Document:
    doc = Document(
        sha256="a" * 64,
        storage_ref="vault://documents/test_doc_1.pdf",
        original_filename="discharge_summary.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024 * 50,
        page_count=3,
        source_type="scanned_pdf",
        uploaded_by=uploader.id,
        status=DocumentStatus.INGESTED,
    )
    session.add(doc)
    session.commit()
    return doc


# ---------------------------------------------------------------------------
# 1. Metadata and Table Creation DDL
# ---------------------------------------------------------------------------

def test_metadata_ddl_compilation(db_session: Session):
    """Assert all 18 specified relational tables are compiled in metadata."""
    expected_tables = {
        "users",
        "roles",
        "user_roles",
        "documents",
        "qualified_configs",
        "model_runs",
        "processing_jobs",
        "job_stage_events",
        "routing_decisions",
        "claims",
        "evidence",
        "verification_results",
        "review_actions",
        "document_approvals",
        "triage_policies",
        "fhir_bundles",
        "fhir_exports",
        "audit_events",
    }
    compiled_tables = set(Base.metadata.tables.keys())
    assert expected_tables.issubset(compiled_tables)
    assert len(compiled_tables) == 18


# ---------------------------------------------------------------------------
# 2. Check Constraints
# ---------------------------------------------------------------------------

def test_document_check_constraints(db_session: Session):
    """Assert check constraints enforce positive file sizes and page counts."""
    user = _create_test_user(db_session)

    # Negative file size must fail
    with pytest.raises(IntegrityError):
        doc_bad_size = Document(
            sha256="b" * 64,
            storage_ref="vault://bad_size.pdf",
            original_filename="bad.pdf",
            mime_type="application/pdf",
            file_size_bytes=0,  # Violates ck_document_size_positive (> 0)
            page_count=1,
            uploaded_by=user.id,
        )
        db_session.add(doc_bad_size)
        db_session.commit()
    db_session.rollback()

    # Zero or negative page count must fail
    with pytest.raises(IntegrityError):
        doc_bad_pages = Document(
            sha256="c" * 64,
            storage_ref="vault://bad_pages.pdf",
            original_filename="bad.pdf",
            mime_type="application/pdf",
            file_size_bytes=100,
            page_count=0,  # Violates ck_document_page_count_positive (> 0)
            uploaded_by=user.id,
        )
        db_session.add(doc_bad_pages)
        db_session.commit()
    db_session.rollback()


def test_verification_confidence_check_constraint(db_session: Session):
    """Assert verification results enforce calibrated confidence in [0.0, 1.0]."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)
    claim = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="group_1",
        field_type="medication",
        value="Aspirin 81mg",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        payload={"field_type": "medication", "value": "Aspirin 81mg"},
    )
    db_session.add(claim)
    db_session.commit()

    # Confidence > 1.0 must fail
    with pytest.raises(IntegrityError):
        vr_bad = VerificationResult(
            claim_id=claim.id,
            label=VerificationLabel.SUPPORTED,
            calibrated_confidence=1.25,  # Invalid: > 1.0
            verification_stage=VerificationStage.STAGE_B,
            provider_kind="modernbert_vllm",
            payload={"label": "SUPPORTED"},
        )
        db_session.add(vr_bad)
        db_session.commit()
    db_session.rollback()


def test_routing_decision_confidence_check_constraint(db_session: Session):
    """Assert routing decisions enforce confidence in [0.0, 1.0]."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    with pytest.raises(IntegrityError):
        rd_bad = RoutingDecision(
            document_id=doc.id,
            region_id="region_lab_table_1",
            route="deterministic",
            confidence=1.5,  # Invalid: > 1.0
        )
        db_session.add(rd_bad)
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 3. Unique Constraints
# ---------------------------------------------------------------------------

def test_user_role_unique_constraint(db_session: Session):
    """Assert a user cannot be assigned the same role multiple times."""
    user = _create_test_user(db_session)
    role = Role(id="reviewer", name="Clinical Reviewer", description="Can review claims")
    db_session.add(role)
    db_session.commit()

    ur1 = UserRole(user_id=user.id, role_id=role.id)
    db_session.add(ur1)
    db_session.commit()

    with pytest.raises(IntegrityError):
        ur2 = UserRole(user_id=user.id, role_id=role.id)
        db_session.add(ur2)
        db_session.commit()
    db_session.rollback()


def test_claim_version_unique_constraint(db_session: Session):
    """Assert claim_id + version must be uniquely constrained."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)
    cid = uuid.uuid4()

    c1 = Claim(
        claim_id=cid,
        document_id=doc.id,
        group_id="g1",
        field_type="lab_test",
        value="Glucose 95 mg/dL",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        payload={"value": "Glucose 95 mg/dL"},
    )
    db_session.add(c1)
    db_session.commit()

    with pytest.raises(IntegrityError):
        c2 = Claim(
            claim_id=cid,
            document_id=doc.id,
            group_id="g1",
            field_type="lab_test",
            value="Glucose 95 mg/dL (duplicate)",
            version=1,  # Duplicate (claim_id, version)
            origin=ClaimOrigin.PIPELINE,
            payload={"value": "Glucose 95 mg/dL"},
        )
        db_session.add(c2)
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 4. Evidence Deduplication & Reusability Across Claims (§4.3)
# ---------------------------------------------------------------------------

def test_evidence_deduplication_and_claim_reusability(db_session: Session):
    """Assert evidence is document-level, deduplicated by span, and reusable by multiple claims."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    # 1. Create canonical evidence record
    ev = Evidence(
        document_id=doc.id,
        page_number=1,
        char_start=120,
        char_end=165,
        bounding_box={"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.25},
        ocr_confidence=0.99,
        evidence_text="Amoxicillin 500mg PO TID x 10 days",
        payload={"text": "Amoxicillin 500mg PO TID x 10 days"},
    )
    db_session.add(ev)
    db_session.commit()

    # 2. Duplicate span on same document must be rejected by unique constraint
    with pytest.raises(IntegrityError):
        ev_duplicate = Evidence(
            document_id=doc.id,
            page_number=1,
            char_start=120,
            char_end=165,
            bounding_box={"x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.25},
            evidence_text="Amoxicillin 500mg PO TID x 10 days",
            payload={"text": "duplicate"},
        )
        db_session.add(ev_duplicate)
        db_session.commit()
    db_session.rollback()

    # Refresh evidence object after rollback
    ev = db_session.execute(select(Evidence).filter_by(document_id=doc.id)).scalar_one()

    # 3. Multiple claims (e.g. drug name and dosage) ground in the SAME evidence record
    claim_med = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="rx_group_1",
        field_type="medication.name",
        value="Amoxicillin",
        version=1,
        evidence_id=ev.id,
        origin=ClaimOrigin.PIPELINE,
        payload={"value": "Amoxicillin"},
    )
    claim_dose = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="rx_group_1",
        field_type="medication.dose",
        value="500mg",
        version=1,
        evidence_id=ev.id,
        origin=ClaimOrigin.PIPELINE,
        payload={"value": "500mg"},
    )
    db_session.add_all([claim_med, claim_dose])
    db_session.commit()

    # Verify both claims resolve to the same evidence entity
    assert len(ev.claims) == 2
    claim_ids = {c.id for c in ev.claims}
    assert claim_med.id in claim_ids
    assert claim_dose.id in claim_ids
    assert claim_med.evidence.evidence_text == "Amoxicillin 500mg PO TID x 10 days"

    # 4. Mandatory offset enforcement: missing char_start or char_end must fail
    with pytest.raises(IntegrityError):
        ev_no_start = Evidence(
            document_id=doc.id,
            page_number=1,
            char_start=None,
            char_end=165,
            evidence_text="Snippet",
            payload={},
        )
        db_session.add(ev_no_start)
        db_session.commit()
    db_session.rollback()

    with pytest.raises(IntegrityError):
        ev_no_end = Evidence(
            document_id=doc.id,
            page_number=1,
            char_start=120,
            char_end=None,
            evidence_text="Snippet",
            payload={},
        )
        db_session.add(ev_no_end)
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 5. Claim-to-Model Provenance Linkage (§3.7)
# ---------------------------------------------------------------------------

def test_claim_model_provenance_linkage(db_session: Session):
    """Assert LLM-extracted claims and verifications link directly to model_runs (§3.7, §4.3)."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    # 1. Create a qualified model run with full §3.7 provenance fields
    model_run = ModelRun(
        is_qualified=True,
        runtime_profile="production",
        component="pipeline_cascade",
        provider_kind="vllm",
        stage_b_model_id="BioClinical-ModernBERT-base",
        stage_b_checkpoint_hash="d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902",
        stage_b_temperature=1.146522,
        stage_b_escalation_threshold=0.60,
        stage_b_autonomous_threshold=0.85,
        stage_c_model_id="Qwen/Qwen2.5-7B-Instruct-AWQ",
        model_revision_hash=None,  # Documented Colab limitation
        quantization="AWQ_4BIT",
        serving_engine_version="0.6.3",
        prompt_id="V8_STAGE_C_LABFLAG_PROMPT_V1",
        stage_c_prompt_version="V8_STAGE_C_LABFLAG_PROMPT_V1",
        stage_c_prompt_hash="dcd6be9c502ec0b05a7858a438d67d929de792987d57ac7f52d0d0c21f868ae3",
        stage_c_serving_engine="vLLM",
        stage_c_parameters={"temperature": 0.0, "seed": 42, "max_tokens": 256},
        calibrator_version="v3_fold0_seed42",
        schema_version="1.0.0",
        extraction_model_id="Qwen/Qwen2.5-7B-Instruct-AWQ",
        extraction_prompt_version="V1_NARRATIVE_EXTRACTION_PROMPT",
        extraction_prompt_hash="11223344" * 8,
        extraction_serving_engine="vLLM",
        extraction_parameters={"temperature": 0.0, "seed": 42, "max_tokens": 512},
    )
    db_session.add(model_run)
    db_session.commit()

    # 2. Create an LLM-extracted claim linking to model_run provenance
    claim = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="llm_group_1",
        field_type="diagnosis",
        value="Type 2 Diabetes Mellitus",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        model_run_id=model_run.id,
        payload={"value": "Type 2 Diabetes Mellitus"},
    )
    db_session.add(claim)
    db_session.commit()

    # 3. Create a verification result linking to the same model_run
    vr = VerificationResult(
        claim_id=claim.id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.985,
        verification_stage=VerificationStage.STAGE_B,
        provider_kind="modernbert_vllm",
        model_run_id=model_run.id,
        payload={"label": "SUPPORTED"},
    )
    db_session.add(vr)
    db_session.commit()

    # Assert authoritative foreign key columns and table name
    assert claim.model_run_id == model_run.id
    assert vr.model_run_id == model_run.id
    assert ModelRun.__tablename__ == "model_runs"

    # Assert bidirectional relationship and §3.7 field persistence
    assert claim.model_run.extraction_model_id == "Qwen/Qwen2.5-7B-Instruct-AWQ"
    assert claim.model_run.component == "pipeline_cascade"
    assert claim.model_run.provider_kind == "vllm"
    assert claim.model_run.quantization == "AWQ_4BIT"
    assert claim.model_run.prompt_id == "V8_STAGE_C_LABFLAG_PROMPT_V1"
    assert claim.model_run.schema_version == "1.0.0"
    assert claim.model_run.stage_c_parameters["temperature"] == 0.0
    assert claim.model_run.stage_c_parameters["seed"] == 42
    assert claim in model_run.claims
    assert vr in model_run.verification_results


# ---------------------------------------------------------------------------
# 6. Routing Decisions (§4.3 / PROJECT_SPEC §7.2)
# ---------------------------------------------------------------------------

def test_routing_decision_persistence_and_relationships(db_session: Session):
    """Assert routing decisions record router choices per region and link to document."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    rd1 = RoutingDecision(
        document_id=doc.id,
        region_id="region_0_header",
        route=ExtractionRoute.DETERMINISTIC,
        reason="Header key-value regex match",
        confidence=1.0,
    )
    rd2 = RoutingDecision(
        document_id=doc.id,
        region_id="region_1_narrative",
        route=ExtractionRoute.LLM_NARRATIVE,
        reason="Unstructured free-text clinical paragraph",
        confidence=0.95,
    )
    db_session.add_all([rd1, rd2])
    db_session.commit()

    assert len(doc.routing_decisions) == 2
    routes = {rd.route for rd in doc.routing_decisions}
    assert ExtractionRoute.DETERMINISTIC in routes
    assert ExtractionRoute.LLM_NARRATIVE in routes


# ---------------------------------------------------------------------------
# 7. Document Deletion Cascade Behavior
# ---------------------------------------------------------------------------

def test_document_deletion_cascades(db_session: Session):
    """Assert deleting a document cascades to all child claims, evidence, jobs, and routing decisions."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    ev = Evidence(
        document_id=doc.id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Sample text snippet",
        payload={},
    )
    claim = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="g1",
        field_type="lab",
        value="100",
        version=1,
        payload={},
    )
    rd = RoutingDecision(
        document_id=doc.id,
        region_id="reg_1",
        route="deterministic",
        confidence=0.9,
    )
    job = ProcessingJob(
        document_id=doc.id,
        status=JobStatus.PENDING,
    )
    db_session.add_all([ev, claim, rd, job])
    db_session.commit()

    doc_id = doc.id
    ev_id = ev.id
    claim_id = claim.id
    rd_id = rd.id
    job_id = job.id

    # Delete parent document
    db_session.delete(doc)
    db_session.commit()

    # All children must be deleted
    assert db_session.get(Evidence, ev_id) is None
    assert db_session.get(Claim, claim_id) is None
    assert db_session.get(RoutingDecision, rd_id) is None
    assert db_session.get(ProcessingJob, job_id) is None


# ---------------------------------------------------------------------------
# 8. User Deletion Restrictions (PD-009 / PD-012)
# ---------------------------------------------------------------------------

def test_user_deletion_restrictions(db_session: Session):
    """Assert users who uploaded documents or performed reviews cannot be deleted (ondelete=RESTRICT)."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    # Attempting to delete the uploader must raise IntegrityError
    with pytest.raises(IntegrityError):
        db_session.delete(user)
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 9. Claim Versioning and Review Lineage (PD-010)
# ---------------------------------------------------------------------------

def test_claim_immutability_and_version_lineage(db_session: Session):
    """Assert reviewer edits insert a new claim revision with supersedes_claim_id (PD-010)."""
    user = _create_test_user(db_session, username="reviewer_smith", email="smith@hospital.org")
    doc = _create_test_document(db_session, user)
    stable_claim_uuid = uuid.uuid4()

    # 1. Pipeline extraction (Version 1)
    claim_v1 = Claim(
        claim_id=stable_claim_uuid,
        document_id=doc.id,
        group_id="rx_line_4",
        field_type="medication.dose",
        value="50mg",
        version=1,
        origin=ClaimOrigin.PIPELINE,
        review_state=ClaimReviewState.PENDING,
        payload={"value": "50mg", "source": "pipeline_ocr"},
    )
    db_session.add(claim_v1)
    db_session.commit()

    # 2. Reviewer corrects 50mg to 500mg (Version 2)
    claim_v2 = Claim(
        claim_id=stable_claim_uuid,
        document_id=doc.id,
        group_id="rx_line_4",
        field_type="medication.dose",
        value="500mg",
        version=2,
        supersedes_claim_id=claim_v1.id,
        origin=ClaimOrigin.REVIEWER,
        review_state=ClaimReviewState.EDITED,
        created_by=user.id,
        payload={"value": "500mg", "correction_reason": "OCR digit misread"},
    )
    db_session.add(claim_v2)
    db_session.flush()

    action = ReviewAction(
        claim_id=claim_v2.id,
        actor_id=user.id,
        action_type=ReviewActionType.EDIT,
        previous_state="PENDING",
        new_state="EDITED",
        reason_code="OCR_CORRECTION",
        before_payload=claim_v1.payload,
        after_payload=claim_v2.payload,
    )
    db_session.add(action)
    db_session.commit()

    # Assert immutability: v1 still exists unchanged in the database
    v1_refetched = db_session.get(Claim, claim_v1.id)
    assert v1_refetched.value == "50mg"
    assert v1_refetched.version == 1

    # Assert v2 points to v1 as predecessor
    v2_refetched = db_session.get(Claim, claim_v2.id)
    assert v2_refetched.value == "500mg"
    assert v2_refetched.version == 2
    assert v2_refetched.supersedes_claim_id == claim_v1.id
    assert v2_refetched.origin == ClaimOrigin.REVIEWER


# ---------------------------------------------------------------------------
# 10. Audit Event Cryptographic Hash Chain Invariants (PD-012)
# ---------------------------------------------------------------------------

def test_audit_event_hash_chain_invariants(db_session: Session):
    """Assert audit log enforces sequence monotonicity and tamper-evident SHA-256 chaining."""
    def compute_hash(seq: int, ts_iso: str, actor: str, ev_type: str, action: str, prev_hash: str | None) -> str:
        payload = f"{seq}|{ts_iso}|{actor}|{ev_type}|{action}|{prev_hash or ''}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    ts1 = datetime.now(timezone.utc)
    ts1_iso = ts1.isoformat()
    h1 = compute_hash(1, ts1_iso, "user_admin", "SYSTEM_START", "INITIALIZE", None)

    # 1. Genesis event
    ev1 = AuditEvent(
        sequence_number=1,
        timestamp=ts1,
        actor_id="user_admin",
        event_type="SYSTEM_START",
        entity_type="system",
        entity_id="node_01",
        action="INITIALIZE",
        previous_event_hash=None,
        current_event_hash=h1,
        metadata_json={"build": "1.0.0"},
    )
    db_session.add(ev1)
    db_session.commit()

    # 2. Second chained event
    ts2 = datetime.now(timezone.utc)
    ts2_iso = ts2.isoformat()
    h2 = compute_hash(2, ts2_iso, "reviewer_1", "CLAIM_ACCEPT", "APPROVE", h1)
    ev2 = AuditEvent(
        sequence_number=2,
        timestamp=ts2,
        actor_id="reviewer_1",
        event_type="CLAIM_ACCEPT",
        entity_type="claim",
        entity_id=str(uuid.uuid4()),
        action="APPROVE",
        previous_event_hash=h1,
        current_event_hash=h2,
        metadata_json={"claim_version": 1},
    )
    db_session.add(ev2)
    db_session.commit()

    # 3. Assert uniqueness of sequence_number
    with pytest.raises(IntegrityError):
        ev_duplicate_seq = AuditEvent(
            sequence_number=1,  # Duplicate sequence number
            timestamp=datetime.now(timezone.utc),
            actor_id="reviewer_1",
            event_type="OTHER",
            entity_type="claim",
            entity_id="c1",
            action="READ",
            current_event_hash="hash_unique",
        )
        db_session.add(ev_duplicate_seq)
        db_session.commit()
    db_session.rollback()

    # 4. Assert uniqueness of current_event_hash
    with pytest.raises(IntegrityError):
        ev_duplicate_hash = AuditEvent(
            sequence_number=3,
            timestamp=datetime.now(timezone.utc),
            actor_id="reviewer_1",
            event_type="OTHER",
            entity_type="claim",
            entity_id="c1",
            action="READ",
            current_event_hash=h1,  # Duplicate hash
        )
        db_session.add(ev_duplicate_hash)
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 11. JSONB and Complex Payload Storage
# ---------------------------------------------------------------------------

def test_jsonb_payload_storage(db_session: Session):
    """Assert complex nested dictionaries serialize and deserialize faithfully."""
    user = _create_test_user(db_session)
    doc = _create_test_document(db_session, user)

    complex_claim_payload = {
        "claim_id": str(uuid.uuid4()),
        "field_type": "lab.value",
        "value": "12.4",
        "value_numeric": 12.4,
        "unit_raw": "g/dL",
        "unit_canonical": "g/dL",
        "negated": False,
        "temporal_status": "current",
        "coding": {
            "code": "718-7",
            "system": "http://loinc.org",
            "display": "Hemoglobin [Mass/volume] in Blood",
        },
    }

    claim = Claim(
        claim_id=uuid.uuid4(),
        document_id=doc.id,
        group_id="lab_group_cbc",
        field_type="lab.value",
        value="12.4",
        version=1,
        payload=complex_claim_payload,
    )
    db_session.add(claim)
    db_session.commit()

    refetched = db_session.get(Claim, claim.id)
    assert refetched.payload["value_numeric"] == 12.4
    assert refetched.payload["coding"]["code"] == "718-7"
    assert refetched.payload["coding"]["display"] == "Hemoglobin [Mass/volume] in Blood"
