"""Phase 5.1.1 Adversarial Export Safety & Eligibility Remediation Tests.

Verifies the remediation of the in-memory FHIR export eligibility bypass.
Enforces that under Mode 0 (PD-002, PD-009, PD-015):
  1. No unreviewed SUPPORTED claim can reach FHIR export by default.
  2. No unreviewed VERIFIED claim can reach FHIR export by default.
  3. Claims with missing review state strictly fail closed.
  4. PENDING claims are strictly ineligible across all modes and arguments.
  5. REJECTED claims are strictly ineligible across all modes and arguments.
  6. ACCEPTED claims satisfy review criteria.
  7. EDITED claims satisfy review criteria.
  8. Documents without formal DocumentApproval are blocked from export.
  9. Ineligible DocumentApproval records are blocked from export.
  10. Document uploader cannot self-approve (Four-Eyes violation).
  11. Unconfirmed patient identity blocks document approval and export.
  12. Every in-memory and production-reachable export entry point is secured.
"""

import datetime
from unittest.mock import MagicMock
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from clindoc.api.app import create_app
from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.auth.roles import CanonicalRole
from clindoc.db.enums import (
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    IdentityVerificationStatus,
    PatientIdentityApprovalStatus,
)
from clindoc.db.models import (
    Base,
    Claim as DBClaim,
    Document as DBDocument,
    DocumentApproval,
    Evidence as DBEvidence,
    User,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.review.approval import (
    DocumentApprovalEngine,
    FourEyesViolationError,
    UnconfirmedIdentityError,
)
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import (
    FHIRExportEligibilityError,
    FHIRExportService,
)


# ─── FIXTURES & HELPERS ────────────────────────────────────────────────────────


@pytest.fixture
def sqlite_session():
    """In-memory SQLite database session with complete schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    session = SessionLocal()
    yield session
    session.close()


def _make_evidence(ev_id: str = "ev-511-1", doc_id: str = "DOC-511-001") -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        text="Glucose 110 mg/dL",
        bbox=BBoxModel(x0=0.1, y0=0.2, x1=0.5, y1=0.3),
        ocr_confidence=0.99,
    )


def _make_claim(
    claim_id: str = "c-511-1",
    doc_id: str = "DOC-511-001",
    ev_id: str = "ev-511-1",
    value: str = "110",
    review_state: ClaimReviewState | str | None = None,
) -> Claim:
    c = Claim(
        claim_id=claim_id,
        document_id=doc_id,
        group_id="grp-glucose",
        field_type="lab.value",
        value=value,
        value_numeric=110.0,
        unit_raw="mg/dL",
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=ev_id,
        normalized_code="2345-7",
        code_system="LOINC",
        code_confidence=0.99,
        negated=False,
        region_id="reg-1",
    )
    if review_state is not None:
        object.__setattr__(c, "review_state", review_state)
    return c


def _make_verification(
    claim_id: str = "c-511-1",
    ev_id: str = "ev-511-1",
    label: str = "SUPPORTED",
    is_fallback: bool = False,
) -> VerificationResult:
    return VerificationResult(
        verification_id=str(uuid.uuid4()),
        claim_id=claim_id,
        label=label,
        confidence=0.95,
        stage_reached="A",
        evidence_id=ev_id,
        is_fallback=is_fallback,
    )


# ─── SCENARIO 1: UNREVIEWED SUPPORTED CLAIM ───────────────────────────────────


def test_scenario_1_unreviewed_supported_claim_fails_closed():
    """Unreviewed claim with SUPPORTED verdict cannot bypass review by default."""
    c = _make_claim(review_state=None)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    svc = FHIRExportService()

    # 1. evaluate_claim_eligibility default
    eval_res = svc.evaluate_claim_eligibility(claim=c, ver=v, ev=ev)
    assert not eval_res.is_eligible
    assert "Policy requires human review acceptance" in eval_res.reason
    assert eval_res.verification_label == "SUPPORTED"

    # 2. export_intermediate_representation default
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v},
        )
    assert "No claims from document" in str(exc_info.value)

    # 3. Supplying require_human_acceptance=False fails closed under Mode 0
    eval_bypass_attempt = svc.evaluate_claim_eligibility(
        claim=c,
        ver=v,
        ev=ev,
        require_human_acceptance=False,
    )
    assert not eval_bypass_attempt.is_eligible
    assert "Policy requires human review acceptance" in eval_bypass_attempt.reason

    # 4. Supplying allow_unreviewed_experimental raises FHIRExportEligibilityError
    with pytest.raises(FHIRExportEligibilityError):
        svc.evaluate_claim_eligibility(
            claim=c,
            ver=v,
            ev=ev,
            allow_unreviewed_experimental=True,
        )


# ─── SCENARIO 2: UNREVIEWED VERIFIED CLAIM ─────────────────────────────────────


def test_scenario_2_unreviewed_verified_claim_fails_closed():
    """Unreviewed claim with VERIFIED verdict cannot bypass review by default."""
    c = _make_claim(review_state=None)
    v = {"verification_id": "v-ver-1", "claim_id": c.claim_id, "label": "VERIFIED", "is_fallback": False}
    ev = _make_evidence()

    svc = FHIRExportService()

    # Default evaluation
    eval_res = svc.evaluate_claim_eligibility(claim=c, ver=v, ev=ev)
    assert not eval_res.is_eligible
    assert "Policy requires human review acceptance" in eval_res.reason
    assert eval_res.verification_label == "VERIFIED"

    # In-memory export default with unreviewed claim
    v_model = _make_verification(claim_id=c.claim_id, label="SUPPORTED")
    with pytest.raises(FHIRExportEligibilityError):
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v_model},
        )


# ─── SCENARIO 3: MISSING REVIEW STATE ──────────────────────────────────────────


def test_scenario_3_missing_review_state_fails_closed():
    """Missing or null review_state on claim fails closed with clear diagnostic."""
    c_dict = {
        "claim_id": "c-no-rev",
        "value": "120",
        "field_type": "lab.value",
    }
    v = _make_verification(claim_id="c-no-rev")
    ev = _make_evidence()

    svc = FHIRExportService()
    eval_res = svc.evaluate_claim_eligibility(claim=c_dict, ver=v, ev=ev)
    assert not eval_res.is_eligible
    assert "Policy requires human review acceptance, but claim has no review decision" in eval_res.reason


# ─── SCENARIO 4: PENDING CLAIM ────────────────────────────────────────────────


def test_scenario_4_pending_claim_strictly_ineligible_across_all_flags():
    """PENDING claim cannot be exported even if require_human_acceptance=False and allow_unreviewed_experimental=True."""
    c_pending = _make_claim(review_state=ClaimReviewState.PENDING)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    svc = FHIRExportService()

    # 1. Default arguments
    eval_default = svc.evaluate_claim_eligibility(claim=c_pending, ver=v, ev=ev)
    assert not eval_default.is_eligible
    assert "PENDING" in eval_default.reason

    # 2. Adversarial arguments attempting bypass via allow_unreviewed_experimental must be rejected
    with pytest.raises(FHIRExportEligibilityError):
        svc.evaluate_claim_eligibility(
            claim=c_pending,
            ver=v,
            ev=ev,
            allow_unreviewed_experimental=True,
        )


# ─── SCENARIO 5: REJECTED CLAIM ────────────────────────────────────────────────


def test_scenario_5_rejected_claim_strictly_ineligible_across_all_flags():
    """REJECTED claim cannot be exported under any circumstance."""
    c_rejected = _make_claim(review_state=ClaimReviewState.REJECTED)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    svc = FHIRExportService()

    # 1. Default arguments
    eval_default = svc.evaluate_claim_eligibility(claim=c_rejected, ver=v, ev=ev)
    assert not eval_default.is_eligible
    assert "REJECTED" in eval_default.reason

    # 2. Adversarial arguments attempting bypass via allow_unreviewed_experimental must be rejected
    with pytest.raises(FHIRExportEligibilityError):
        svc.evaluate_claim_eligibility(
            claim=c_rejected,
            ver=v,
            ev=ev,
            allow_unreviewed_experimental=True,
        )


# ─── SCENARIO 6: ACCEPTED CLAIM ────────────────────────────────────────────────


def test_scenario_6_accepted_claim_passes_default_export():
    """ACCEPTED claim passes default eligibility and exports to valid Bundle."""
    c_accepted = _make_claim(review_state=ClaimReviewState.ACCEPTED)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    svc = FHIRExportService()

    eval_res = svc.evaluate_claim_eligibility(claim=c_accepted, ver=v, ev=ev)
    assert eval_res.is_eligible
    assert eval_res.review_state == "ACCEPTED"

    res = svc.export_intermediate_representation(
        claims=[c_accepted],
        evidence_map={ev.evidence_id: ev},
        verification_map={c_accepted.claim_id: v},
    )
    assert res.is_valid
    assert res.exportable_claim_ids == [c_accepted.claim_id]


# ─── SCENARIO 7: EDITED CLAIM ──────────────────────────────────────────────────


def test_scenario_7_edited_claim_passes_default_export():
    """EDITED claim passes default eligibility and exports to valid Bundle."""
    c_edited = _make_claim(review_state=ClaimReviewState.EDITED)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    svc = FHIRExportService()

    eval_res = svc.evaluate_claim_eligibility(claim=c_edited, ver=v, ev=ev)
    assert eval_res.is_eligible
    assert eval_res.review_state == "EDITED"

    res = svc.export_intermediate_representation(
        claims=[c_edited],
        evidence_map={ev.evidence_id: ev},
        verification_map={c_edited.claim_id: v},
    )
    assert res.is_valid
    assert res.exportable_claim_ids == [c_edited.claim_id]


# ─── SCENARIO 8: MISSING DOCUMENT APPROVAL ─────────────────────────────────────


def test_scenario_8_missing_document_approval_blocks_export(sqlite_session: Session):
    """export_document strictly blocks documents without a confirmed DocumentApproval record."""
    doc_id = uuid.uuid4()
    uploader_id = uuid.uuid4()

    uploader = User(
        id=uploader_id,
        idp_subject="sub-up-01",
        username="uploader_01",
        full_name="Uploader One",
        email="up1@hospital.org",
    )
    sqlite_session.add(uploader)

    doc = DBDocument(
        id=doc_id,
        uploaded_by=uploader_id,
        source_type="scanned_pdf",
        status=DocumentStatus.IN_REVIEW,
        original_filename="sample.pdf",
        storage_ref="blobs/test/sample.pdf",
        mime_type="application/pdf",
        file_size_bytes=500,
        sha256="sha-sample",
        page_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.flush()

    svc = FHIRExportService()
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_document(
            session=sqlite_session,
            document_id=doc_id,
            exported_by=uploader_id,
        )
    assert "has not undergone Four-Eyes approval review" in str(exc_info.value)


# ─── SCENARIO 9: INELIGIBLE DOCUMENT APPROVAL ──────────────────────────────────


def test_scenario_9_ineligible_document_approval_blocks_export(sqlite_session: Session):
    """export_document strictly blocks documents when DocumentApproval.is_export_eligible is False."""
    doc_id = uuid.uuid4()
    uploader_id = uuid.uuid4()
    approver_id = uuid.uuid4()

    uploader = User(id=uploader_id, idp_subject="sub-u1", username="u1", full_name="U1", email="u1@hosp.org")
    approver = User(id=approver_id, idp_subject="sub-a1", username="a1", full_name="A1", email="a1@hosp.org")
    sqlite_session.add_all([uploader, approver])

    doc = DBDocument(
        id=doc_id,
        uploaded_by=uploader_id,
        source_type="born_digital_pdf",
        status=DocumentStatus.REJECTED,
        original_filename="rej.pdf",
        storage_ref="blobs/rej.pdf",
        mime_type="application/pdf",
        file_size_bytes=500,
        sha256="sha-rej",
        page_count=1,
    )
    sqlite_session.add(doc)

    approval = DocumentApproval(
        id=uuid.uuid4(),
        document_id=doc_id,
        approver_id=approver_id,
        approval_status=DocumentApprovalStatus.REJECTED,
        patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
        policy_version="1.0.0-mode0",
        is_export_eligible=False,
    )
    sqlite_session.add(approval)
    sqlite_session.flush()

    svc = FHIRExportService()
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_document(
            session=sqlite_session,
            document_id=doc_id,
            exported_by=approver_id,
        )
    assert "approval is not export-eligible" in str(exc_info.value)


# ─── SCENARIO 10: UPLOADER ATTEMPTING SELF-APPROVAL ────────────────────────────


def test_scenario_10_uploader_self_approval_four_eyes_violation(sqlite_session: Session):
    """DocumentApprovalEngine strictly rejects self-approval by the uploader (PD-009)."""
    user_id = uuid.uuid4()
    user = User(id=user_id, idp_subject="sub-self", username="self_user", full_name="Self", email="self@hosp.org")
    sqlite_session.add(user)

    doc_id = uuid.uuid4()
    doc = DBDocument(
        id=doc_id,
        uploaded_by=user_id,
        source_type="born_digital_pdf",
        status=DocumentStatus.IN_REVIEW,
        identity_verification_status=IdentityVerificationStatus.CONFIRMED,
        identity_confirmed_by=user_id,
        identity_confirmed_at=datetime.datetime.now(datetime.timezone.utc),
        original_filename="doc.pdf",
        storage_ref="blobs/doc.pdf",
        mime_type="application/pdf",
        file_size_bytes=500,
        sha256="sha-doc",
        page_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.flush()

    engine = DocumentApprovalEngine(audit_service=MagicMock())
    with pytest.raises(FourEyesViolationError) as exc_info:
        engine.approve_document(
            session=sqlite_session,
            document_id=doc_id,
            approver_id=user_id,
        )
    assert "Four-Eyes policy violation" in str(exc_info.value)
    assert "cannot approve their own document" in str(exc_info.value)


# ─── SCENARIO 11: PATIENT IDENTITY NOT CONFIRMED ───────────────────────────────


def test_scenario_11_unconfirmed_patient_identity_blocks_approval(sqlite_session: Session):
    """Unconfirmed or mismatched patient identity strictly blocks document approval (PD-015)."""
    uploader_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    sqlite_session.add_all([
        User(id=uploader_id, idp_subject="sub-u11", username="u11", full_name="U11", email="u11@hosp.org"),
        User(id=approver_id, idp_subject="sub-a11", username="a11", full_name="A11", email="a11@hosp.org"),
    ])

    doc_id = uuid.uuid4()
    doc = DBDocument(
        id=doc_id,
        uploaded_by=uploader_id,
        source_type="born_digital_pdf",
        status=DocumentStatus.IN_REVIEW,
        identity_verification_status=IdentityVerificationStatus.UNVERIFIED,
        identity_confirmed_by=None,
        original_filename="id_unconf.pdf",
        storage_ref="blobs/id_unconf.pdf",
        mime_type="application/pdf",
        file_size_bytes=500,
        sha256="sha-id",
        page_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.flush()

    engine = DocumentApprovalEngine(audit_service=MagicMock())
    with pytest.raises(UnconfirmedIdentityError) as exc_info:
        engine.approve_document(
            session=sqlite_session,
            document_id=doc_id,
            approver_id=approver_id,
        )
    assert "must be explicitly CONFIRMED or MISMATCH_ACCEPTED" in str(exc_info.value)


# ─── SCENARIO 12: EVERY EXPORT ENTRY POINT AUDIT ──────────────────────────────


def test_scenario_12_all_export_entry_points_secure(sqlite_session: Session):
    """Verifies that all export entry points enforce the intended security policy."""
    uploader_id = uuid.uuid4()
    approver_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    ev_id = uuid.uuid4()

    approver_user = User(id=approver_id, idp_subject="sub-ap-12", username="ap12", full_name="Ap 12", email="ap12@hosp.org")
    sqlite_session.add_all([
        User(id=uploader_id, idp_subject="sub-up-12", username="up12", full_name="Up 12", email="up12@hosp.org"),
        approver_user,
    ])

    doc = DBDocument(
        id=doc_id,
        uploaded_by=uploader_id,
        source_type="born_digital_pdf",
        status=DocumentStatus.APPROVED,
        original_filename="audit.pdf",
        storage_ref="blobs/audit.pdf",
        mime_type="application/pdf",
        file_size_bytes=1000,
        sha256="sha-12",
        page_count=1,
    )
    sqlite_session.add(doc)

    db_ev = DBEvidence(
        id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Platelets 250 K/uL",
        payload={
            "document_id": str(doc_id),
            "evidence_id": str(ev_id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Platelets 250 K/uL",
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2},
            "ocr_confidence": 0.99,
        },
    )
    sqlite_session.add(db_ev)

    # Claim in PENDING review state in database
    db_claim = DBClaim(
        id=claim_id,
        claim_id=claim_id,
        document_id=doc_id,
        evidence_id=ev_id,
        group_id="grp-platelets",
        field_type="lab.value",
        value="250",
        normalized_code="777-3",
        code_system="LOINC",
        review_state=ClaimReviewState.PENDING,
        payload={
            "claim_id": str(claim_id),
            "document_id": str(doc_id),
            "group_id": "grp-platelets",
            "field_type": "lab.value",
            "value": "250",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.95,
            "evidence_id": str(ev_id),
            "region_id": "reg-1",
        },
    )
    sqlite_session.add(db_claim)

    # DocumentApproval valid
    approval = DocumentApproval(
        id=uuid.uuid4(),
        document_id=doc_id,
        approver_id=approver_id,
        approval_status=DocumentApprovalStatus.APPROVED,
        patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
        policy_version="1.0.0-mode0",
        is_export_eligible=True,
    )
    sqlite_session.add(approval)
    sqlite_session.flush()

    svc = FHIRExportService(audit_service=MagicMock())

    # Entry Point A: export_document rejects unreviewed/pending claims
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_document(
            session=sqlite_session,
            document_id=doc_id,
            exported_by=approver_id,
        )
    assert "contains zero export-eligible claims" in str(exc_info.value)

    # Entry Point B: export_intermediate_representation rejects unreviewed claims by default
    contract_claim = Claim(
        claim_id=str(claim_id),
        document_id=str(doc_id),
        group_id="grp-platelets",
        field_type="lab.value",
        value="250",
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=str(ev_id),
        region_id="reg-1",
    )
    ev_contract = _make_evidence(ev_id=str(ev_id), doc_id=str(doc_id))
    ver_contract = _make_verification(claim_id=str(claim_id), ev_id=str(ev_id), label="SUPPORTED")

    with pytest.raises(FHIRExportEligibilityError) as exc_info2:
        svc.export_intermediate_representation(
            claims=[contract_claim],
            evidence_map={ev_contract.evidence_id: ev_contract},
            verification_map={contract_claim.claim_id: ver_contract},
        )
    assert "No claims from document" in str(exc_info2.value)

    # Entry Point C: Dedicated non-production experimental method is isolated in clindoc.testing
    from clindoc.testing.synthetic_export import export_unreviewed_synthetic_bundle_experimental

    assert not hasattr(svc, "export_unreviewed_synthetic_bundle_experimental")
    exp_res = export_unreviewed_synthetic_bundle_experimental(
        claims=[contract_claim],
        evidence_map={ev_contract.evidence_id: ev_contract},
        verification_map={contract_claim.claim_id: ver_contract},
    )
    assert exp_res.is_valid
    assert exp_res.exportable_claim_ids == [str(claim_id)]

    # Entry Point D: REST API route rejects export of unaccepted document claims
    app = create_app()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user=approver_user,
        subject="sub-ap-12",
        roles={CanonicalRole.APPROVER.value},
    )
    app.dependency_overrides[get_db_session] = lambda: sqlite_session

    client = TestClient(app)
    resp = client.post(f"/api/v1/documents/{doc_id}/export")
    assert resp.status_code == 422
    assert "zero export-eligible claims" in resp.json()["detail"]
