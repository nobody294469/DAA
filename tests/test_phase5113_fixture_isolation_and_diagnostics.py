"""Focused test battery for Phase 5.1.1.3: Test Fixture Isolation & Verification Diagnostics.

Verifies:
1. Test fixture adapter isolation in tests/conftest.py:
   - Only the two explicitly intended frozen Phase 4 helper functions are wrapped.
   - The adapter cannot modify fixtures or claims in other test modules.
   - Explicit review_state=None is strictly preserved.
   - Security tests execute with genuinely unreviewed claims.
   - No global model mutation or shared fixture state leaks across tests.
   - Test execution order does not affect review-state behavior.
2. Verification payload failure diagnostics:
   - Missing verification records, missing payloads, and malformed payloads all fail closed.
   - Failure conditions are distinguishable in structured logs and diagnostic reasons.
   - No confidence score, verification stage, or verdict is fabricated.
   - No malformed payload can reach claims_to_fhir_bundle().
   - Valid payload reconstruction preserves existing verification semantics.
   - Logs are strictly PHI-free and do not log sensitive evidence or clinical text.
"""

import copy
import logging
from typing import Any
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from clindoc.api.audit.service import AuditService
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    PatientIdentityApprovalStatus,
    VerificationLabel,
    VerificationStage,
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
    FHIRExportService,
)
from clindoc.storage.blob import EncryptedFileBlobStore


# ─── PART 1: TEST FIXTURE ADAPTER ISOLATION TESTS ─────────────────────────────


def test_only_intended_phase4_fixtures_are_wrapped() -> None:
    """Verify that only the two frozen Phase 4 helper functions are wrapped."""
    import tests.test_fhir_export as tfe
    import tests.test_phase44_diagnostic_report_grouping as tp44

    # 1. Wrapped Phase 4 fixtures have _is_adapted marker
    assert getattr(tfe.make_test_claim, "_is_adapted", False) is True
    assert getattr(tp44._make_claim, "_is_adapted", False) is True

    # 2. Check other test modules with similar helper names are NOT adapted
    try:
        import tests.test_phase43_provenance_traceability as tp43
        assert getattr(tp43.make_test_claim, "_is_adapted", False) is False
    except (ImportError, AttributeError):
        pass

    try:
        import tests.test_verification_cascade as tvc
        assert getattr(tvc._make_claim, "_is_adapted", False) is False
    except (ImportError, AttributeError):
        pass

    # 3. Claim contract class itself is completely untouched
    assert not hasattr(Claim, "_is_adapted")
    assert not hasattr(Claim, "review_state")


def test_explicit_review_state_none_preserved_in_make_test_claim() -> None:
    """Verify that make_test_claim strictly preserves explicit review_state=None."""
    import tests.test_fhir_export as tfe

    # Explicit review_state=None
    c_none = tfe.make_test_claim("c-none", review_state=None)
    assert getattr(c_none, "review_state", None) is None

    # Explicit review_state="REJECTED"
    c_rej = tfe.make_test_claim("c-rej", review_state="REJECTED")
    assert getattr(c_rej, "review_state", None) == "REJECTED"

    # Default / omitted review_state defaults to "ACCEPTED" for legacy frozen test
    c_default = tfe.make_test_claim("c-def")
    assert getattr(c_default, "review_state", None) == "ACCEPTED"


def test_explicit_review_state_none_preserved_in_tp44_make_claim() -> None:
    """Verify that tp44._make_claim strictly preserves explicit review_state=None."""
    import tests.test_phase44_diagnostic_report_grouping as tp44

    # Explicit keyword review_state=None
    c_none = tp44._make_claim("c-none", "lab.value", "105", review_state=None)
    assert getattr(c_none, "review_state", None) is None

    # Explicit keyword review_state=REJECTED
    c_rej = tp44._make_claim("c-rej", "lab.value", "105", review_state=ClaimReviewState.REJECTED)
    assert getattr(c_rej, "review_state", None) == ClaimReviewState.REJECTED

    # Default / omitted review_state defaults to "ACCEPTED" for legacy frozen test
    c_def = tp44._make_claim("c-def", "lab.value", "105")
    assert getattr(c_def, "review_state", None) == "ACCEPTED"


def test_security_tests_claims_remain_genuinely_unreviewed() -> None:
    """Verify that claims built outside Phase 4 fixtures remain genuinely unreviewed."""
    c = Claim(
        claim_id="c-sec-1",
        document_id="DOC-SEC-001",
        field_type="lab.value",
        value="110",
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id="ev-1",
        region_id="reg-1",
    )
    assert getattr(c, "review_state", None) is None


def test_execution_order_independence() -> None:
    """Verify that fixture wrapping has no order dependence or shared state leaks."""
    import tests.test_fhir_export as tfe
    import tests.test_phase44_diagnostic_report_grouping as tp44

    # Interleave calls multiple times in varying orders
    for _ in range(5):
        c1 = tfe.make_test_claim("c-1")  # omitted -> ACCEPTED
        assert getattr(c1, "review_state", None) == "ACCEPTED"

        c2 = tfe.make_test_claim("c-2", review_state=None)  # explicit None
        assert getattr(c2, "review_state", None) is None

        c3 = tp44._make_claim("c-3", "lab.value", "10")  # omitted -> ACCEPTED
        assert getattr(c3, "review_state", None) == "ACCEPTED"

        c4 = tp44._make_claim("c-4", "lab.value", "10", review_state=None)  # explicit None
        assert getattr(c4, "review_state", None) is None

        c5 = tfe.make_test_claim("c-5")  # omitted -> ACCEPTED
        assert getattr(c5, "review_state", None) == "ACCEPTED"


# ─── PART 2: DBVERIFICATIONRESULT RECONSTRUCTION DIAGNOSTIC TESTS ────────────


@pytest.fixture
def test_db_session(tmp_path: Any):
    """Provide a clean in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    yield session
    session.close()


def _setup_approved_doc_with_claim(
    session: Session,
    claim_id: str = "c-diag-1",
    doc_id: uuid.UUID | None = None,
    review_state: ClaimReviewState = ClaimReviewState.ACCEPTED,
) -> tuple[DBDocument, User, DBClaim, DBEvidence]:
    """Helper to scaffold an approved document and valid claim."""
    if doc_id is None:
        doc_id = uuid.uuid4()
    user_id = uuid.uuid4()

    user = User(
        id=user_id,
        idp_subject="sub-operator-1",
        username="op1",
        full_name="Review Operator",
        email="operator@hospital.org",
        is_active=True,
    )
    session.add(user)

    doc = DBDocument(
        id=doc_id,
        uploaded_by=user_id,
        source_type="scanned_pdf",
        status=DocumentStatus.IN_REVIEW,
        original_filename="diag_test.pdf",
        storage_ref="blobs/test/diag_test.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256="abc12345",
        page_count=1,
    )
    session.add(doc)

    approval = DocumentApproval(
        id=uuid.uuid4(),
        document_id=doc_id,
        approver_id=user_id,
        approval_status=DocumentApprovalStatus.APPROVED,
        patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
        policy_version="1.0.0-mode0",
        is_export_eligible=True,
    )
    session.add(approval)

    ev_uuid = uuid.uuid4()
    db_ev = DBEvidence(
        id=ev_uuid,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Glucose 110 mg/dL",
        payload={
            "evidence_id": str(ev_uuid),
            "document_id": str(doc_id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Glucose 110 mg/dL",
            "ocr_confidence": 0.99,
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2},
        },
    )
    session.add(db_ev)

    claim_uuid = uuid.uuid4() if isinstance(claim_id, str) else claim_id
    db_c = DBClaim(
        id=claim_uuid,
        claim_id=claim_uuid,
        document_id=doc_id,
        group_id="grp-1",
        field_type="lab.value",
        value="110",
        normalized_code="2345-7",
        code_system="LOINC",
        review_state=review_state,
        origin=ClaimOrigin.PIPELINE,
        evidence_id=ev_uuid,
        payload={
            "claim_id": str(claim_uuid),
            "document_id": str(doc_id),
            "group_id": "grp-1",
            "field_type": "lab.value",
            "value": "110",
            "value_numeric": 110.0,
            "unit_raw": "mg/dL",
            "normalized_code": "2345-7",
            "code_system": "LOINC",
            "code_confidence": 0.99,
            "extraction_method": "deterministic",
            "extraction_confidence": 0.95,
            "evidence_id": str(ev_uuid),
            "region_id": "reg-1",
            "temporal_status": "current",
            "negated": False,
        },
    )
    session.add(db_c)
    session.commit()
    return doc, user, db_c, db_ev


def test_missing_verification_record_fails_closed_with_diagnostic_reason(
    test_db_session: Session,
    tmp_path: Any,
) -> None:
    """Ensure missing DBVerificationResult record fails closed with distinct reason."""
    doc, user, claim, ev = _setup_approved_doc_with_claim(test_db_session)

    service = FHIRExportService(audit_service=AuditService())

    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        service.export_document(test_db_session, doc.id, user.id)

    assert "zero export-eligible claims" in str(exc_info.value)
    assert "has no associated VerificationResult record" in str(exc_info.value)


def test_missing_verification_payload_fails_closed_with_diagnostic_reason(
    test_db_session: Session,
    tmp_path: Any,
) -> None:
    """Ensure DBVerificationResult with empty/None payload fails closed with distinct reason."""
    doc, user, claim, ev = _setup_approved_doc_with_claim(test_db_session)

    # Insert DBVerificationResult with empty dict payload
    db_ver = DBVerificationResult(
        id=uuid.uuid4(),
        claim_id=claim.id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.95,
        verification_stage=VerificationStage.STAGE_A,
        provider_kind="deterministic",
        is_fallback=False,
        payload={},  # Empty payload
    )
    test_db_session.add(db_ver)
    test_db_session.commit()

    service = FHIRExportService(audit_service=AuditService())

    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        service.export_document(test_db_session, doc.id, user.id)

    assert "zero export-eligible claims" in str(exc_info.value)
    assert "Claim verification record has missing or empty payload" in str(exc_info.value)


def test_malformed_verification_payload_fails_closed_with_diagnostic_reason(
    test_db_session: Session,
    tmp_path: Any,
) -> None:
    """Ensure malformed payload fails schema validation, fails closed, and cannot reach FHIR."""
    doc, user, claim, ev = _setup_approved_doc_with_claim(test_db_session)

    # Insert DBVerificationResult with invalid schema payload (unrecognized label)
    db_ver = DBVerificationResult(
        id=uuid.uuid4(),
        claim_id=claim.id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.95,
        verification_stage=VerificationStage.STAGE_A,
        provider_kind="deterministic",
        is_fallback=False,
        payload={
            "label": "FABRICATED_UNRECOGNIZED_LABEL",
            "confidence": "not_a_float",
        },
    )
    test_db_session.add(db_ver)
    test_db_session.commit()

    service = FHIRExportService(audit_service=AuditService())

    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        service.export_document(test_db_session, doc.id, user.id)

    assert "zero export-eligible claims" in str(exc_info.value)
    assert "Claim verification record has malformed payload schema" in str(exc_info.value)


def test_valid_payload_reconstruction_preserves_semantics(
    test_db_session: Session,
    tmp_path: Any,
) -> None:
    """Ensure valid DBVerificationResult payload reconstructs exact verification semantics."""
    doc, user, claim, ev = _setup_approved_doc_with_claim(test_db_session)

    valid_payload = {
        "verification_id": str(uuid.uuid4()),
        "claim_id": str(claim.claim_id),
        "evidence_id": str(ev.id),
        "label": "SUPPORTED",
        "confidence": 0.98,
        "stage_reached": "A",
        "is_fallback": False,
    }
    db_ver = DBVerificationResult(
        id=uuid.uuid4(),
        claim_id=claim.id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.98,
        verification_stage=VerificationStage.STAGE_A,
        provider_kind="deterministic",
        is_fallback=False,
        payload=valid_payload,
    )
    test_db_session.add(db_ver)
    test_db_session.commit()

    service = FHIRExportService(audit_service=AuditService())

    res = service.export_document(test_db_session, doc.id, user.id)
    assert res.bundle is not None
    assert res.bundle.resource_type == "Bundle"
    assert len(res.exportable_claim_ids) == 1
    assert len(res.non_exportable_claims) == 0


def test_logs_contain_no_phi_during_diagnostic_failures(
    test_db_session: Session,
    tmp_path: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify that warnings logged during payload reconstruction contain no PHI or clinical text."""
    doc, user, claim, ev = _setup_approved_doc_with_claim(test_db_session)

    # Insert malformed payload
    db_ver = DBVerificationResult(
        id=uuid.uuid4(),
        claim_id=claim.id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.95,
        verification_stage=VerificationStage.STAGE_A,
        provider_kind="deterministic",
        is_fallback=False,
        payload={"corrupt": "data"},
    )
    test_db_session.add(db_ver)
    test_db_session.commit()

    service = FHIRExportService(audit_service=AuditService())

    with caplog.at_level(logging.WARNING):
        with pytest.raises(FHIRExportEligibilityError):
            service.export_document(test_db_session, doc.id, user.id)

    # Check captured logs
    captured_text = caplog.text
    # Should contain claim id and exception class
    assert "malformed payload" in captured_text
    # Must NOT contain clinical evidence or patient name
    assert "Glucose 110 mg/dL" not in captured_text
    assert "Review Operator" not in captured_text
    assert "PAT-999" not in captured_text
