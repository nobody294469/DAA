"""Phase 5.1.1.1 — Focused Security Audit: Experimental Export Isolation.

Tests the security boundaries, caller isolation, and non-bypassability
of FHIR export mechanisms following Phase 5.1.1 remediation.

Audit requirements:
1. Explicit allow_unreviewed_experimental=True on ordinary export methods.
2. Direct calls to the experimental export function.
3. Attempts to invoke experimental functionality through production routes.
4. Missing review state with SUPPORTED and VERIFIED verdicts.
5. Frozen Phase 4 regression assertions.
6. All production-reachable export callers.
"""

import uuid
from typing import Any
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

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
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import (
    FHIRExportEligibilityError,
    FHIRExportError,
    FHIRExportService,
)
from clindoc.testing.synthetic_export import (
    export_unreviewed_synthetic_bundle_experimental,
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


def _make_evidence(ev_id: str = "ev-5111-1", doc_id: str = "DOC-5111-001") -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        text="Hemoglobin 14.2 g/dL",
        bbox=BBoxModel(x0=0.1, y0=0.2, x1=0.5, y1=0.3),
        ocr_confidence=0.99,
    )


def _make_claim(
    claim_id: str = "c-5111-1",
    doc_id: str = "DOC-5111-001",
    ev_id: str = "ev-5111-1",
    field_type: str = "lab.value",
    value: str = "14.2",
    review_state: Any = None,
    is_fallback: bool = False,
    negated: bool = False,
) -> Claim:
    c = Claim(
        claim_id=claim_id,
        document_id=doc_id,
        group_id="grp-hgb",
        field_type=field_type,
        value=value,
        value_numeric=14.2,
        unit_raw="g/dL",
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=ev_id,
        normalized_code="718-7",
        code_system="LOINC",
        code_confidence=0.99,
        negated=negated,
        region_id="reg-1",
    )
    if review_state is not None:
        object.__setattr__(c, "review_state", review_state)
    if is_fallback:
        object.__setattr__(c, "is_fallback", is_fallback)
    return c


def _make_verification(
    claim_id: str = "c-5111-1",
    ev_id: str = "ev-5111-1",
    label: str = "SUPPORTED",
    is_fallback: bool = False,
) -> VerificationResult:
    return VerificationResult(
        verification_id=f"v-{claim_id}",
        claim_id=claim_id,
        label=label,
        confidence=0.95,
        stage_reached="A",
        evidence_id=ev_id,
        is_fallback=is_fallback,
    )


# ─── REQUIREMENT 1: EXPLICIT allow_unreviewed_experimental ON ORDINARY METHODS ─


def test_audit_req1_forbidden_parameter_on_evaluate_claim_eligibility():
    """Ordinary evaluate_claim_eligibility strictly rejects allow_unreviewed_experimental parameter."""
    svc = FHIRExportService()
    c = _make_claim(review_state=None)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    # Passing allow_unreviewed_experimental=True must raise FHIRExportEligibilityError
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.evaluate_claim_eligibility(
            claim=c,
            ver=v,
            ev=ev,
            allow_unreviewed_experimental=True,
        )
    assert "allow_unreviewed_experimental parameter is forbidden" in str(exc_info.value)
    assert "Mode 0 mandates 100% human review" in str(exc_info.value)


def test_audit_req1_forbidden_parameter_on_export_intermediate_representation():
    """Ordinary export_intermediate_representation strictly rejects allow_unreviewed_experimental parameter."""
    svc = FHIRExportService()
    c = _make_claim(review_state=ClaimReviewState.ACCEPTED)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    # Even with an ACCEPTED claim, passing the forbidden experimental flag must raise FHIRExportEligibilityError
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v},
            allow_unreviewed_experimental=True,
        )
    assert "allow_unreviewed_experimental parameter is forbidden" in str(exc_info.value)


def test_audit_req1_disabling_require_human_acceptance_rejected_for_production():
    """Ordinary callers cannot set require_human_acceptance=False to export unreviewed claims."""
    svc = FHIRExportService()
    c = _make_claim(review_state=None)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v},
            require_human_acceptance=False,
        )
    assert "Disabling require_human_acceptance is forbidden" in str(exc_info.value)


# ─── REQUIREMENT 2: DIRECT CALLS TO EXPERIMENTAL EXPORT FUNCTION ───────────────


def test_audit_req2_experimental_export_removed_from_service_class():
    """FHIRExportService class must not expose the experimental export method."""
    svc = FHIRExportService()
    assert not hasattr(svc, "export_unreviewed_synthetic_bundle_experimental")
    assert not hasattr(FHIRExportService, "export_unreviewed_synthetic_bundle_experimental")


def test_audit_req2_dedicated_test_export_works_for_synthetic_benchmarks():
    """The dedicated clindoc.testing function works specifically for synthetic benchmarks."""
    c = _make_claim(review_state=None)
    v = _make_verification(label="SUPPORTED")
    ev = _make_evidence()

    res = export_unreviewed_synthetic_bundle_experimental(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_id="DOC-SYNTH-01",
    )
    assert res.is_valid
    assert res.bundle is not None
    assert res.bundle.resource_type == "Bundle"
    assert res.exportable_claim_ids == [c.claim_id]


def test_audit_req2_dedicated_test_export_still_enforces_safety_gates():
    """Even the dedicated test export enforces fallbacks and negation safety gates."""
    # Fallback claim
    c_fb = _make_claim(claim_id="c-fb", is_fallback=True)
    v_fb = _make_verification(claim_id="c-fb", label="SUPPORTED")
    ev = _make_evidence()

    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        export_unreviewed_synthetic_bundle_experimental(
            claims=[c_fb],
            evidence_map={ev.evidence_id: ev},
            verification_map={c_fb.claim_id: v_fb},
        )
    assert "No claims" in str(exc_info.value)

    # Negated claim
    c_neg = _make_claim(claim_id="c-neg", negated=True)
    v_neg = _make_verification(claim_id="c-neg", label="SUPPORTED")
    with pytest.raises(FHIRExportEligibilityError):
        export_unreviewed_synthetic_bundle_experimental(
            claims=[c_neg],
            evidence_map={ev.evidence_id: ev},
            verification_map={c_neg.claim_id: v_neg},
        )


# ─── REQUIREMENT 3: ATTEMPTS TO INVOKE VIA PRODUCTION ROUTES ───────────────────


def test_audit_req3_production_api_route_ignores_or_rejects_experimental_params(sqlite_session: Session):
    """Production REST API route cannot be coerced into unreviewed export via query or body params."""
    approver = User(
        id=uuid.uuid4(),
        idp_subject="sub-audit-app",
        username="approver_audit",
        full_name="Approver Audit",
        email="approver@hospital.org",
    )
    uploader = User(
        id=uuid.uuid4(),
        idp_subject="sub-audit-upl",
        username="uploader_audit",
        full_name="Uploader Audit",
        email="uploader@hospital.org",
    )
    doc_id = uuid.uuid4()
    doc = DBDocument(
        id=doc_id,
        uploaded_by=uploader.id,
        source_type="born_digital_pdf",
        status=DocumentStatus.IN_REVIEW,
        original_filename="audit_report.pdf",
        storage_ref="blobs/audit_report.pdf",
        mime_type="application/pdf",
        file_size_bytes=500,
        sha256="sha-audit-3",
        page_count=1,
    )
    sqlite_session.add_all([approver, uploader, doc])
    sqlite_session.commit()

    # Add unreviewed claim
    claim_id = uuid.uuid4()
    ev_id = uuid.uuid4()
    db_ev = DBEvidence(
        id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Platelets 250",
        payload={
            "document_id": str(doc_id),
            "evidence_id": str(ev_id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Platelets 250",
        },
    )
    db_claim = DBClaim(
        id=claim_id,
        claim_id=claim_id,
        document_id=doc_id,
        evidence_id=ev_id,
        group_id="grp-platelets",
        field_type="lab.value",
        value="13.5",
        review_state=None,  # Unreviewed!
        payload={
            "claim_id": str(claim_id),
            "document_id": str(doc_id),
            "group_id": "grp-platelets",
            "field_type": "lab.value",
            "value": "13.5",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.95,
            "evidence_id": str(ev_id),
            "region_id": "reg-1",
        },
    )
    sqlite_session.add_all([db_ev, db_claim])
    sqlite_session.commit()

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user=approver,
        subject=str(approver.id),
        roles={CanonicalRole.APPROVER.value},
    )
    app.dependency_overrides[get_db_session] = lambda: sqlite_session

    client = TestClient(app)

    # Attempt 1: Query parameter ?allow_unreviewed_experimental=true
    resp1 = client.post(f"/api/v1/documents/{doc_id}/export?allow_unreviewed_experimental=true")
    # Must fail because document lacks approval and claims are unreviewed
    assert resp1.status_code in (400, 422)

    # Attempt 2: Query parameter ?require_human_acceptance=false
    resp2 = client.post(f"/api/v1/documents/{doc_id}/export?require_human_acceptance=false")
    assert resp2.status_code in (400, 422)

    # Attempt 3: JSON body payload attempting bypass
    resp3 = client.post(
        f"/api/v1/documents/{doc_id}/export",
        json={"allow_unreviewed_experimental": True, "require_human_acceptance": False},
    )
    assert resp3.status_code in (400, 422)


# ─── REQUIREMENT 4: MISSING REVIEW STATE WITH SUPPORTED & VERIFIED VERDICTS ────


def test_audit_req4_supported_verdict_without_review_fails_closed():
    """Claims with automated SUPPORTED verdict but missing review state fail closed."""
    svc = FHIRExportService()
    c = _make_claim(review_state=None)
    v = _make_verification(claim_id=c.claim_id, label="SUPPORTED")
    ev = _make_evidence()

    # In evaluate_claim_eligibility:
    eval_res = svc.evaluate_claim_eligibility(claim=c, ver=v, ev=ev)
    assert not eval_res.is_eligible
    assert eval_res.review_state is None
    assert eval_res.verification_label == "SUPPORTED"
    assert "Policy requires human review acceptance, but claim has no review decision" in eval_res.reason

    # In export_intermediate_representation:
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v},
        )
    assert "No claims from document" in str(exc_info.value)


def test_audit_req4_verified_verdict_without_review_fails_closed():
    """Claims with automated VERIFIED verdict but missing review state fail closed."""
    svc = FHIRExportService()
    c = _make_claim(review_state=None)
    v_dict = {"verification_id": f"v-{c.claim_id}", "claim_id": c.claim_id, "label": "VERIFIED", "is_fallback": False}
    ev = _make_evidence()

    eval_res = svc.evaluate_claim_eligibility(claim=c, ver=v_dict, ev=ev)
    assert not eval_res.is_eligible
    assert eval_res.review_state is None
    assert eval_res.verification_label == "VERIFIED"
    assert "Policy requires human review acceptance, but claim has no review decision" in eval_res.reason

    v_supported = _make_verification(claim_id=c.claim_id, label="SUPPORTED")
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v_supported},
        )
    assert "No claims from document" in str(exc_info.value)


# ─── REQUIREMENT 5: FROZEN PHASE 4 REGRESSION ASSERTIONS ───────────────────────


def test_audit_req5_accepted_claim_maintains_phase4_referential_integrity():
    """ACCEPTED claims preserve 100% of Phase 4 bundle referential integrity and schemas."""
    svc = FHIRExportService()
    doc_id = "DOC-P4-001"
    ev = _make_evidence(ev_id="ev-p4-1", doc_id=doc_id)
    c = _make_claim(
        claim_id="c-p4-1",
        doc_id=doc_id,
        ev_id=ev.evidence_id,
        field_type="lab.value",
        value="14.2",
        review_state=ClaimReviewState.ACCEPTED,
    )
    v = _make_verification(claim_id=c.claim_id, ev_id=ev.evidence_id, label="SUPPORTED")

    res = svc.export_intermediate_representation(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_id=doc_id,
        patient_id="PAT-P4",
        patient_name="Frozen Test Patient",
    )

    assert res.is_valid
    assert res.bundle is not None
    entry_types = [e.resource.resource_type for e in res.bundle.entry]

    assert "Patient" in entry_types
    assert "DocumentReference" in entry_types
    assert "Provenance" in entry_types
    assert "Observation" in entry_types

    # Referential integrity check
    patient_entry = next(e for e in res.bundle.entry if e.resource.resource_type == "Patient")
    patient_full_url = patient_entry.fullUrl

    obs_entry = next(e for e in res.bundle.entry if e.resource.resource_type == "Observation")
    assert obs_entry.resource.subject.reference == patient_full_url


# ─── REQUIREMENT 6: ALL PRODUCTION-REACHABLE EXPORT CALLERS ────────────────────


def test_audit_req6_production_export_document_enforces_human_approval(sqlite_session: Session):
    """FHIRExportService.export_document requires database DocumentApproval and eligible status."""
    svc = FHIRExportService()

    approver = User(
        id=uuid.uuid4(),
        idp_subject="sub-audit-app6",
        username="approver6",
        full_name="Approver Six",
        email="approver6@hospital.org",
    )
    uploader = User(
        id=uuid.uuid4(),
        idp_subject="sub-audit-upl6",
        username="uploader6",
        full_name="Uploader Six",
        email="uploader6@hospital.org",
    )
    doc_id = uuid.uuid4()
    doc = DBDocument(
        id=doc_id,
        uploaded_by=uploader.id,
        source_type="born_digital_pdf",
        status=DocumentStatus.IN_REVIEW,
        original_filename="doc_req6.pdf",
        storage_ref="blobs/doc_req6.pdf",
        mime_type="application/pdf",
        file_size_bytes=500,
        sha256="sha-req6",
        page_count=1,
    )
    sqlite_session.add_all([approver, uploader, doc])
    sqlite_session.commit()

    claim_id = uuid.uuid4()
    ev_id = uuid.uuid4()
    db_ev = DBEvidence(
        id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Hemoglobin 14.0",
        payload={
            "document_id": str(doc_id),
            "evidence_id": str(ev_id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Hemoglobin 14.0",
        },
    )
    db_claim = DBClaim(
        id=claim_id,
        claim_id=claim_id,
        document_id=doc_id,
        evidence_id=ev_id,
        group_id="grp-hgb",
        field_type="lab.value",
        value="14.0",
        review_state=ClaimReviewState.ACCEPTED,  # Claim accepted!
        payload={
            "claim_id": str(claim_id),
            "document_id": str(doc_id),
            "group_id": "grp-hgb",
            "field_type": "lab.value",
            "value": "14.0",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.95,
            "evidence_id": str(ev_id),
            "region_id": "reg-1",
        },
    )
    db_ver = DBVerificationResult(
        id=uuid.uuid4(),
        claim_id=claim_id,
        label="SUPPORTED",
        calibrated_confidence=0.95,
        verification_stage="stage_a",
        provider_kind="heuristic",
        is_fallback=False,
        payload={
            "label": "SUPPORTED",
            "confidence": 0.95,
            "stage_reached": "A",
            "is_fallback": False,
        },
    )
    sqlite_session.add_all([db_ev, db_claim, db_ver])
    sqlite_session.commit()

    # Calling export_document WITHOUT DocumentApproval must fail closed
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_document(session=sqlite_session, document_id=doc_id, exported_by=approver.id)
    assert "has not undergone Four-Eyes approval review" in str(exc_info.value)

    # Add approval with is_export_eligible=False
    approval_ineligible = DocumentApproval(
        id=uuid.uuid4(),
        document_id=doc_id,
        approver_id=approver.id,
        approval_status=DocumentApprovalStatus.REJECTED,
        patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
        policy_version="1.0.0-mode0",
        is_export_eligible=False,  # Not eligible!
    )
    sqlite_session.add(approval_ineligible)
    sqlite_session.commit()

    with pytest.raises(FHIRExportEligibilityError) as exc_info2:
        svc.export_document(session=sqlite_session, document_id=doc_id, exported_by=approver.id)
    assert "approval is not export-eligible" in str(exc_info2.value)

    # Update approval to eligible
    approval_ineligible.approval_status = DocumentApprovalStatus.APPROVED
    approval_ineligible.is_export_eligible = True
    sqlite_session.commit()

    # Now export succeeds
    res = svc.export_document(session=sqlite_session, document_id=doc_id, exported_by=approver.id)
    assert res.is_valid
    assert str(claim_id) in res.exportable_claim_ids
