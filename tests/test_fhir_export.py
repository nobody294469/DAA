"""Comprehensive test suite for Phase 8.8 — FHIR R4B Export & Provenance Grounding.

Authority:
  - PROJECT_SPEC §5 #10, §7.7, §15, §16
  - DEC-008: Hybrid MedicationRequest vs MedicationStatement routing
  - DEC-016: Two-tier provenance hierarchy
  - PRODUCTIZATION_SPEC §1.2, §4.3, §7.2, §9.3, §9.4
  - PRODUCTIZATION_DECISIONS.md PD-002, PD-009, PD-011, PD-012, PD-015
  - Phase 8.8: FHIR R4B Export
"""

import copy
import datetime
import hashlib
import json
from unittest.mock import MagicMock
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
    FHIRExportStatus,
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
    FHIRBundle,
    FHIRExport,
    User,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir.constants import (
    DOC_CATEGORY_SYSTEM,
    EXT_CLAIM_PROVENANCE,
    EXT_SOURCE_EVIDENCE,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import (
    ClaimEligibilityEvaluation,
    FHIRExportEligibilityError,
    FHIRExportError,
    FHIRExportNotFoundError,
    FHIRExportResult,
    FHIRExportService,
    FHIRExportValidationError,
)
from clindoc.storage.blob import EncryptedFileBlobStore


# ─── FIXTURES & BUILDERS ───────────────────────────────────────────────────────


def make_test_evidence(
    doc_id: str = "DOC-TEST-001",
    ev_id: str = "ev-1",
    page: int = 1,
    text: str = "Glucose 105 mg/dL (Reference: 70-99)",
) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=page,
        char_start=0,
        char_end=len(text),
        bbox=BBoxModel(x0=0.1, y0=0.2, x1=0.5, y1=0.25),
        text=text,
        ocr_confidence=0.98,
    )



def make_test_claim(
    claim_id: str = "c-1",
    doc_id: str = "DOC-TEST-001",
    ev_id: str = "ev-1",
    field_type: str = "lab.value",
    value: str = "105",
    value_numeric: float = 105.0,
    unit_raw: str = "mg/dL",
    code: str = "2345-7",
    system: str = "LOINC",
    group_id: str = "grp-glucose",
    temporal_status: str = "unspecified",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id=doc_id,
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=ev_id,
        normalized_code=code,
        code_system=system,
        code_confidence=0.99,
        negated=False,
        temporal_status=temporal_status,
        region_id="reg-1",
    )


def make_test_verification(
    claim_id: str = "c-1",
    ev_id: str = "ev-1",
    label: str = "SUPPORTED",
    confidence: float = 0.96,
    stage: str = "A",
    is_fallback: bool = False,
    fallback_reason: str = None,
    contradiction_type: str = None,
) -> VerificationResult:
    return VerificationResult(
        verification_id=f"ver-{claim_id}",
        claim_id=claim_id,
        evidence_id=ev_id,
        label=label,
        confidence=confidence,
        stage_reached=stage,
        is_fallback=is_fallback,
        fallback_reason=fallback_reason,
        contradiction_type=contradiction_type,
    )


# ─── 1. MAPPING TESTS ─────────────────────────────────────────────────────────


def test_patient_mapping_preserves_demographics_without_fabrication():
    """Verify Patient resource mapping preserves supplied ID/name without fabricating demographics."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        patient_id="MRN-44819",
        patient_name="Jane Doe",
    )

    pat_entry = next((e.resource for e in bundle.entry if e.resource.resource_type == "Patient"), None)
    assert pat_entry is not None
    assert pat_entry.id == "MRN-44819"
    assert pat_entry.name[0].text == "Jane Doe"
    assert pat_entry.identifier[0].value == "MRN-44819"
    # Never fabricate birthDate or gender if unprovided
    assert pat_entry.birthDate is None
    assert pat_entry.gender is None


def test_observation_mapping_with_loinc_ucum_and_provenance():
    """Verify Observation resource maps LOINC, UCUM, interpretation, and source reference."""
    ev = make_test_evidence(text="Potassium 5.8 H mEq/L (3.5-5.0)")
    c_name = make_test_claim("c-name", field_type="lab.test_name", value="Potassium", code="2823-3", system="LOINC", group_id="grp-k")
    c_val = make_test_claim("c-val", field_type="lab.value", value="5.8", value_numeric=5.8, unit_raw="mEq/L", code=None, system=None, group_id="grp-k")
    c_unit = make_test_claim("c-unit", field_type="lab.unit", value="mEq/L", code="meq/L", system="UCUM", group_id="grp-k")
    c_flag = make_test_claim("c-flag", field_type="lab.flag", value="H", group_id="grp-k")
    c_ref = make_test_claim("c-ref", field_type="lab.reference_range", value="3.5-5.0", group_id="grp-k")

    claims = [c_name, c_val, c_unit, c_flag, c_ref]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: make_test_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_metadata={"report_status": "final"},
        include_document_reference=True,
    )

    obs = next((e.resource for e in bundle.entry if e.resource.resource_type == "Observation"), None)
    assert obs is not None
    assert obs.status == "final"
    # LOINC code check
    assert obs.code.coding[0].system == LOINC_SYSTEM
    assert obs.code.coding[0].code == "2823-3"
    # Quantity check
    assert float(obs.valueQuantity.value) == 5.8
    assert obs.valueQuantity.unit == "mEq/L"
    # Interpretation check
    assert obs.interpretation[0].coding[0].system == OBS_INTERPRETATION_SYSTEM
    assert obs.interpretation[0].coding[0].code == "H"
    # Reference range check
    assert obs.referenceRange[0].text == "3.5-5.0"
    # Derived from DocumentReference check
    assert obs.derivedFrom is not None
    assert obs.derivedFrom[0].reference.startswith("DocumentReference/")


def test_medication_mapping_prescription_routes_to_medication_request():
    """Verify DEC-008 prescription layout routes to MedicationRequest with RxNorm."""
    ev = make_test_evidence(text="Amoxicillin 500mg PO TID for 10 days")
    c_name = make_test_claim("m-name", field_type="med.name", value="Amoxicillin", code="70618", system="RxNorm", group_id="grp-amox")
    c_dose = make_test_claim("m-dose", field_type="med.dose", value="500", value_numeric=500.0, unit_raw="mg", group_id="grp-amox")
    c_route = make_test_claim("m-route", field_type="med.route", value="Oral", group_id="grp-amox")
    c_freq = make_test_claim("m-freq", field_type="med.frequency", value="TID", group_id="grp-amox")
    c_dur = make_test_claim("m-dur", field_type="med.duration", value="10 days", group_id="grp-amox")

    claims = [c_name, c_dose, c_route, c_freq, c_dur]
    ev_map = {ev.evidence_id: ev}
    ver_map = {c.claim_id: make_test_verification(c.claim_id) for c in claims}

    bundle = claims_to_fhir_bundle(
        claims=claims,
        evidence_map=ev_map,
        verification_map=ver_map,
        document_type="clinic_rx_pad",  # Prescription document type triggers MedicationRequest
        include_document_reference=True,
    )

    medreq = next((e.resource for e in bundle.entry if e.resource.resource_type == "MedicationRequest"), None)
    assert medreq is not None
    assert medreq.status == "active"
    assert medreq.intent == "order"
    assert medreq.medicationCodeableConcept.coding[0].system == RXNORM_SYSTEM
    assert medreq.medicationCodeableConcept.coding[0].code == "70618"
    assert medreq.dosageInstruction[0].route.text == "Oral"
    assert medreq.dosageInstruction[0].timing.code.text == "TID"
    # Supporting information link to DocumentReference
    assert medreq.supportingInformation is not None
    assert medreq.supportingInformation[0].reference.startswith("DocumentReference/")


def test_document_reference_and_encounter_mapping():
    """Verify DocumentReference and Encounter mapping when metadata is supplied."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    doc_meta = {
        "filename": "discharge_summary.pdf",
        "mime_type": "application/pdf",
        "storage_ref": "enc-blobs/123/doc.pdf",
        "sha256": "abc123def456",
    }
    enc_info = {
        "encounter_id": "ENC-9981",
        "encounter_class": "IMP",
        "status": "finished",
    }

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata=doc_meta,
        encounter_info=enc_info,
        include_document_reference=True,
        include_provenance_resource=True,
    )

    doc_ref = next((e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference"), None)
    assert doc_ref is not None
    assert doc_ref.status == "current"
    assert doc_ref.category[0].coding[0].system == DOC_CATEGORY_SYSTEM
    assert doc_ref.content[0].attachment.contentType == "application/pdf"
    assert doc_ref.content[0].attachment.title == "discharge_summary.pdf"

    enc = next((e.resource for e in bundle.entry if e.resource.resource_type == "Encounter"), None)
    assert enc is not None
    assert enc.id == "enc-enc-9981"
    assert enc.status == "finished"
    assert enc.class_fhir.code == "IMP"

    prov = next((e.resource for e in bundle.entry if e.resource.resource_type == "Provenance"), None)
    assert prov is not None
    assert len(prov.target) >= 1
    # Check that Provenance points to DocumentReference as source entity
    assert prov.entity[0].role == "source"
    assert prov.entity[0].what.reference == f"DocumentReference/{doc_ref.id}"


def test_encounter_safely_omitted_when_no_metadata():
    """Verify Encounter is not fabricated when encounter_info is None or incomplete."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        encounter_info=None,
    )

    enc = next((e.resource for e in bundle.entry if e.resource.resource_type == "Encounter"), None)
    assert enc is None


# ─── 2. SAFETY & VERIFICATION BOUNDARY TESTS ──────────────────────────────────


def test_safety_fallback_claim_rejected_from_export():
    """Verify that a claim produced via fallback (is_fallback=True) is NEVER exported."""
    ev = make_test_evidence()
    c = make_test_claim("c-fb")
    v = make_test_verification(
        claim_id="c-fb",
        is_fallback=True,
        fallback_reason="llm_unavailable",
    )

    svc = FHIRExportService()
    eval_res = svc.evaluate_claim_eligibility(claim=c, ver=v, ev=ev)

    assert not eval_res.is_eligible
    assert eval_res.is_fallback is True
    assert "fallback" in eval_res.reason.lower()


def test_safety_pending_and_rejected_claims_rejected_from_export():
    """Verify that claims with PENDING or REJECTED review states are refused for export."""
    svc = FHIRExportService()

    # Case 1: Pending claim
    c_pending = {"claim_id": "c-pend", "value": "120", "review_state": "PENDING"}
    ev = make_test_evidence()
    v = make_test_verification("c-pend")
    eval_pending = svc.evaluate_claim_eligibility(claim=c_pending, ver=v, ev=ev)
    assert not eval_pending.is_eligible
    assert "PENDING" in eval_pending.reason

    # Case 2: Rejected claim
    c_rejected = {"claim_id": "c-rej", "value": "120", "review_state": "REJECTED"}
    eval_rejected = svc.evaluate_claim_eligibility(claim=c_rejected, ver=v, ev=ev)
    assert not eval_rejected.is_eligible
    assert "REJECTED" in eval_rejected.reason

    # Case 3: Accepted claim passes
    c_accepted = {"claim_id": "c-acc", "value": "120", "review_state": "ACCEPTED"}
    eval_accepted = svc.evaluate_claim_eligibility(claim=c_accepted, ver=v, ev=ev)
    assert eval_accepted.is_eligible


def test_safety_discrepancy_verdict_rejected_in_autonomous_mode():
    """Verify that an unresolved discrepancy verdict is refused in autonomous pipeline mode."""
    svc = FHIRExportService()
    ev = make_test_evidence()
    c = make_test_claim("c-disc")
    v = make_test_verification("c-disc", label="DISCREPANCY", contradiction_type="value_mismatch")

    eval_disc = svc.evaluate_claim_eligibility(claim=c, ver=v, ev=ev)
    assert not eval_disc.is_eligible
    assert "DISCREPANCY" in eval_disc.reason


def test_safety_missing_terminology_does_not_fabricate_code():
    """Verify that missing normalized code produces text-only CodeableConcept without fake codings."""
    ev = make_test_evidence(text="UncommonDrug 25mg")
    c = make_test_claim("c-noterm", field_type="med.name", value="UncommonDrug", code=None, system=None, group_id="grp-u")
    v = make_test_verification("c-noterm")

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
    )

    medstat = next((e.resource for e in bundle.entry if e.resource.resource_type == "MedicationStatement"), None)
    assert medstat is not None
    assert medstat.medicationCodeableConcept.coding is None
    assert medstat.medicationCodeableConcept.text == "UncommonDrug"


def test_safety_all_ineligible_claims_raises_clear_error():
    """Verify that if all claims are ineligible, export_intermediate_representation raises FHIRExportEligibilityError."""
    ev = make_test_evidence()
    c = make_test_claim("c-fb")
    v = make_test_verification("c-fb", is_fallback=True)

    svc = FHIRExportService()
    with pytest.raises(FHIRExportEligibilityError) as exc_info:
        svc.export_intermediate_representation(
            claims=[c],
            evidence_map={ev.evidence_id: ev},
            verification_map={c.claim_id: v},
            enforce_eligibility=True,
        )
    assert "No claims" in str(exc_info.value)
    assert "eligible" in str(exc_info.value)


# ─── 3. STRUCTURAL & VALIDATION TESTS ─────────────────────────────────────────


def test_structural_bundle_referential_integrity():
    """Verify that FHIRValidator confirms all references in a complete Bundle resolve."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    svc = FHIRExportService()
    res = svc.export_intermediate_representation(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={"filename": "lab.pdf", "mime_type": "application/pdf"},
        encounter_info={"encounter_id": "ENC-01", "encounter_class": "AMB"},
        include_document_reference=True,
        include_provenance_resource=True,
    )

    assert res.is_valid is True
    assert res.validation_report.is_valid is True
    assert len(res.validation_report.errors) == 0
    # Patient, Org, Pract, DocRef, Encounter, DiagReport, Observation, Provenance
    assert res.validation_report.resource_counts["Patient"] == 1
    assert res.validation_report.resource_counts["Observation"] == 1
    assert res.validation_report.resource_counts["DocumentReference"] == 1
    assert res.validation_report.resource_counts["Encounter"] == 1
    assert res.validation_report.resource_counts["Provenance"] == 1


def test_structural_duplicate_resource_id_detected():
    """Verify FHIRValidator detects duplicate resource keys in a bundle."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
    )

    # Artificially duplicate the first entry
    bundle.entry.append(bundle.entry[0])

    val = FHIRValidator.validate_bundle(bundle)
    assert not val.is_valid
    assert any("Duplicate resource" in err for err in val.errors)


def test_structural_orphan_reference_detected():
    """Verify FHIRValidator detects orphan references."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
    )

    # Break the observation subject reference to an unlisted Patient
    obs = next(e.resource for e in bundle.entry if e.resource.resource_type == "Observation")
    obs.subject.reference = "Patient/non-existent-patient-999"

    val = FHIRValidator.validate_bundle(bundle)
    assert not val.is_valid
    assert any("not found in bundle" in err for err in val.errors)


def test_deterministic_serialization_and_sha256():
    """Verify bundle JSON serialization and SHA-256 calculation are strictly deterministic."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    svc = FHIRExportService()
    fixed_time = datetime.datetime(2026, 10, 30, 12, 0, 0, tzinfo=datetime.timezone.utc)
    res1 = svc.export_intermediate_representation(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        pipeline_timing={"recorded_at": fixed_time},
        document_metadata={"uploaded_at": fixed_time},
    )
    res2 = svc.export_intermediate_representation(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        pipeline_timing={"recorded_at": fixed_time},
        document_metadata={"uploaded_at": fixed_time},
    )

    assert res1.bundle_sha256 == res2.bundle_sha256
    assert res1.bundle_json == res2.bundle_json


# ─── 4. DATABASE SERVICE & FOUR-EYES GATE TESTS ───────────────────────────────


@pytest.fixture
def sqlite_session():
    """Provide an in-memory SQLite database session with complete schema."""
    from sqlalchemy.pool import StaticPool
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



def test_database_export_enforces_four_eyes_approval_gate(sqlite_session: Session):
    """Verify export_document blocks unapproved documents and allows approved documents with audit log."""
    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Create user
    user = User(
        id=user_id,
        idp_subject="sub-user-01",
        username="clinician_01",
        full_name="Dr. Clinician",
        email="clinician@hospital.org",
    )
    sqlite_session.add(user)

    # Create document in IN_REVIEW state
    doc = DBDocument(
        id=doc_id,
        uploaded_by=user_id,
        source_type="scanned_pdf",
        status=DocumentStatus.IN_REVIEW,
        original_filename="lab.pdf",
        storage_ref="blobs/test/lab.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256="abc12345",
        page_count=1,
    )
    sqlite_session.add(doc)
    sqlite_session.flush()

    # Create evidence
    ev_id = uuid.uuid4()
    db_ev = DBEvidence(
        id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Glucose 95 mg/dL",
        payload={
            "document_id": str(doc_id),
            "evidence_id": str(ev_id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Glucose 95 mg/dL",
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2},
            "ocr_confidence": 0.99,
        },
    )
    sqlite_session.add(db_ev)

    # Create active accepted claim
    claim_id = uuid.uuid4()
    db_claim = DBClaim(
        id=claim_id,
        claim_id=claim_id,
        document_id=doc_id,
        evidence_id=ev_id,
        group_id="grp-glucose",
        field_type="lab.value",
        value="95",
        normalized_code="2345-7",
        code_system="LOINC",
        review_state=ClaimReviewState.ACCEPTED,
        payload={
            "claim_id": str(claim_id),
            "document_id": str(doc_id),
            "group_id": "grp-glucose",
            "field_type": "lab.value",
            "value": "95",
            "value_numeric": 95.0,
            "unit_raw": "mg/dL",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.99,
            "evidence_id": str(ev_id),
            "normalized_code": "2345-7",
            "code_system": "LOINC",
            "region_id": "r-1",
        },
    )
    sqlite_session.add(db_claim)

    # Create verification result
    ver_id = uuid.uuid4()
    db_ver = DBVerificationResult(
        id=ver_id,
        claim_id=claim_id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.98,
        verification_stage=VerificationStage.STAGE_A,
        provider_kind="deterministic",
        payload={
            "verification_id": str(ver_id),
            "claim_id": str(claim_id),
            "evidence_id": str(ev_id),
            "label": "SUPPORTED",
            "confidence": 0.98,
            "stage_reached": "A",
        },
    )
    sqlite_session.add(db_ver)
    sqlite_session.flush()

    mock_audit = MagicMock(spec=AuditService)
    svc = FHIRExportService(audit_service=mock_audit)

    # 1. Attempt export WITHOUT DocumentApproval -> Must raise FHIRExportEligibilityError
    with pytest.raises(FHIRExportEligibilityError) as exc_no_appr:
        svc.export_document(
            session=sqlite_session,
            document_id=doc_id,
            exported_by=user_id,
        )
    assert "Four-Eyes approval" in str(exc_no_appr.value)

    # 2. Add DocumentApproval with is_export_eligible=False -> Must still fail
    appr_id = uuid.uuid4()
    approval = DocumentApproval(
        id=appr_id,
        document_id=doc_id,
        approver_id=user_id,
        approval_status=DocumentApprovalStatus.PENDING,
        patient_identity_status=PatientIdentityApprovalStatus.UNVERIFIED if hasattr(PatientIdentityApprovalStatus, "UNVERIFIED") else PatientIdentityApprovalStatus.CONFIRMED,
        policy_version="1.0.0-mode0",
        is_export_eligible=False,
    )
    sqlite_session.add(approval)
    sqlite_session.flush()

    with pytest.raises(FHIRExportEligibilityError) as exc_unverified:
        svc.export_document(
            session=sqlite_session,
            document_id=doc_id,
            exported_by=user_id,
        )
    assert "not export-eligible" in str(exc_unverified.value)

    # 3. Grant full Four-Eyes approval
    approval.approval_status = DocumentApprovalStatus.APPROVED
    approval.patient_identity_status = PatientIdentityApprovalStatus.CONFIRMED
    approval.is_export_eligible = True
    sqlite_session.flush()

    # Now export succeeds
    res = svc.export_document(
        session=sqlite_session,
        document_id=doc_id,
        exported_by=user_id,
    )

    assert res.is_valid is True
    assert res.db_bundle_id is not None
    assert res.db_export_id is not None
    assert doc.status == DocumentStatus.EXPORTED

    # Verify audit event was logged with non-PHI metadata
    assert mock_audit.record_event.called
    audit_call = mock_audit.record_event.call_args[1]
    assert audit_call["event_type"] == "FHIR_BUNDLE_EXPORTED"
    assert audit_call["metadata_json"]["document_id"] == str(doc_id)
    assert audit_call["metadata_json"]["is_valid"] is True


def test_api_export_and_get_bundle_endpoints(sqlite_session: Session):
    """Verify POST /documents/{id}/export and GET /documents/{id}/fhir-bundle API routes."""
    from fastapi.testclient import TestClient
    from clindoc.api.app import create_app
    from clindoc.api.auth.dependencies import get_current_user, get_db_session
    from clindoc.api.auth.models import AuthenticatedUser
    from clindoc.api.auth.roles import CanonicalRole

    doc_id = uuid.uuid4()
    user_id = uuid.uuid4()

    user = User(
        id=user_id,
        idp_subject="sub-user-02",
        username="approver_02",
        full_name="Dr. Approver",
        email="approver@hospital.org",
    )
    sqlite_session.add(user)

    doc = DBDocument(
        id=doc_id,
        uploaded_by=user_id,
        source_type="scanned_pdf",
        status=DocumentStatus.APPROVED,
        original_filename="lab_api.pdf",
        storage_ref="blobs/test/lab_api.pdf",
        mime_type="application/pdf",
        file_size_bytes=1024,
        sha256="abc999",
        page_count=1,
    )
    sqlite_session.add(doc)

    approval = DocumentApproval(
        id=uuid.uuid4(),
        document_id=doc_id,
        approver_id=user_id,
        approval_status=DocumentApprovalStatus.APPROVED,
        patient_identity_status=PatientIdentityApprovalStatus.CONFIRMED,
        policy_version="1.0.0-mode0",
        is_export_eligible=True,
    )
    sqlite_session.add(approval)

    ev_id = uuid.uuid4()
    db_ev = DBEvidence(
        id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=20,
        evidence_text="Glucose 95 mg/dL",
        payload={
            "document_id": str(doc_id),
            "evidence_id": str(ev_id),
            "page_number": 1,
            "char_start": 0,
            "char_end": 20,
            "text": "Glucose 95 mg/dL",
            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.5, "y1": 0.2},
            "ocr_confidence": 0.99,
        },
    )
    sqlite_session.add(db_ev)

    claim_id = uuid.uuid4()
    db_claim = DBClaim(
        id=claim_id,
        claim_id=claim_id,
        document_id=doc_id,
        evidence_id=ev_id,
        group_id="grp-glucose",
        field_type="lab.value",
        value="95",
        normalized_code="2345-7",
        code_system="LOINC",
        review_state=ClaimReviewState.ACCEPTED,
        payload={
            "claim_id": str(claim_id),
            "document_id": str(doc_id),
            "group_id": "grp-glucose",
            "field_type": "lab.value",
            "value": "95",
            "value_numeric": 95.0,
            "unit_raw": "mg/dL",
            "extraction_method": "deterministic",
            "extraction_confidence": 0.99,
            "evidence_id": str(ev_id),
            "normalized_code": "2345-7",
            "code_system": "LOINC",
            "region_id": "r-1",
        },
    )
    sqlite_session.add(db_claim)

    ver_id = uuid.uuid4()
    db_ver = DBVerificationResult(
        id=ver_id,
        claim_id=claim_id,
        label=VerificationLabel.SUPPORTED,
        calibrated_confidence=0.98,
        verification_stage=VerificationStage.STAGE_A,
        provider_kind="deterministic",
        payload={
            "verification_id": str(ver_id),
            "claim_id": str(claim_id),
            "evidence_id": str(ev_id),
            "label": "SUPPORTED",
            "confidence": 0.98,
            "stage_reached": "A",
        },
    )
    sqlite_session.add(db_ver)
    sqlite_session.flush()

    app = create_app()

    # Unauthorized role test (e.g. DOCUMENT_OPERATOR cannot export, requires APPROVER or INTEGRATION_SERVICE)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user=user,
        subject="sub-user-02",
        roles={CanonicalRole.DOCUMENT_OPERATOR.value},
    )
    app.dependency_overrides[get_db_session] = lambda: sqlite_session

    client = TestClient(app)
    resp_unauth = client.post(f"/api/v1/documents/{doc_id}/export")
    assert resp_unauth.status_code == 403

    # Authorized role test (APPROVER)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user=user,
        subject="sub-user-02",
        roles={CanonicalRole.APPROVER.value},
    )

    resp_export = client.post(f"/api/v1/documents/{doc_id}/export")
    assert resp_export.status_code == 200
    export_data = resp_export.json()
    assert export_data["is_valid"] is True
    assert export_data["exportable_claim_count"] == 1
    assert export_data["bundle_id"] == f"bundle-{doc_id}"

    # Retrieve bundle via GET
    resp_bundle = client.get(f"/api/v1/documents/{doc_id}/fhir-bundle")
    assert resp_bundle.status_code == 200
    bundle_data = resp_bundle.json()
    assert bundle_data["resourceType"] == "Bundle"
    assert bundle_data["id"] == f"bundle-{doc_id}"
    assert len(bundle_data["entry"]) >= 4

