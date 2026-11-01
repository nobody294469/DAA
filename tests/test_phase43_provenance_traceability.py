"""Comprehensive test suite for Phase 4.3 — FHIR Provenance, Timestamp Integrity & Document Traceability.

Authority:
  - Phase 4.3 Objective & Acceptance Criteria
  - HL7 FHIR R4B:
    - Provenance (recorded, occurred[x], agent.who, entity.what)
    - Device (software device agent, version, property)
    - DocumentReference (content.attachment.hash as base64Binary SHA-256)
  - PRODUCTIZATION_SPEC §1.2, §4.3, §7.2, §9.3, §9.4
  - DEC-016: Two-tier provenance hierarchy
"""

import base64
import datetime
import hashlib
import uuid

import pytest

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir.constants import (
    DOC_CATEGORY_SYSTEM,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.schema.verification import VerificationResult


# ─── TEST FIXTURE BUILDERS ───────────────────────────────────────────────────


def make_test_evidence(
    ev_id: str = "ev-trace-1",
    text: str = "Potassium 4.8 mmol/L on 2026-08-15",
    doc_id: str = "doc-test-trace",
) -> Evidence:
    return Evidence(
        evidence_id=ev_id,
        document_id=doc_id,
        page_number=1,
        char_start=0,
        char_end=len(text),
        text=text,
        bbox=BBoxModel(x0=0.1, y0=0.1, x1=0.5, y1=0.2),
        ocr_confidence=0.99,
    )


def make_test_claim(
    claim_id: str = "c-trace-1",
    ev_id: str = "ev-trace-1",
    doc_id: str = "doc-test-trace",
    field_type: str = "lab.value",
    value: str = "4.8",
    value_numeric: float = 4.8,
    unit_raw: str = "mmol/L",
    code: str = "2823-3",
    system: str = "LOINC",
    group_id: str = "grp-trace-k",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        document_id=doc_id,
        evidence_id=ev_id,
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        normalized_code=code,
        code_system=system,
        extraction_method="deterministic",
        extraction_confidence=0.99,
        region_id="r-1",
    )


def make_test_verification(
    claim_id: str = "c-trace-1",
    ev_id: str = "ev-trace-1",
) -> VerificationResult:
    return VerificationResult(
        verification_id=f"v-{claim_id}",
        claim_id=claim_id,
        evidence_id=ev_id,
        label="SUPPORTED",
        confidence=0.98,
        stage_reached="A",
    )


# ─── 1. PROVENANCE TIMESTAMP INTEGRITY & ACCURACY ────────────────────────────


def test_dynamic_utc_provenance_recorded():
    """Verify Provenance.recorded uses actual dynamic UTC time with valid timezone awareness."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    before_call = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=2)
    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        include_provenance_resource=True,
    )
    after_call = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=2)

    prov = next((e.resource for e in bundle.entry if e.resource.resource_type == "Provenance"), None)
    assert prov is not None
    assert prov.recorded is not None
    # Must be timezone-aware UTC
    assert prov.recorded.tzinfo is not None
    # Must be bounded by actual execution window, not a hardcoded static date
    assert before_call <= prov.recorded <= after_call


def test_no_hardcoded_historical_timestamps():
    """Verify neither Provenance nor DocumentReference contain the legacy hardcoded 2026-01-01 timestamp."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        include_document_reference=True,
        include_provenance_resource=True,
    )

    prov = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance")
    doc_ref = next(e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference")

    hardcoded_dt = datetime.datetime(2026, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
    assert prov.recorded != hardcoded_dt
    assert doc_ref.date != hardcoded_dt


def test_separate_clinical_effective_time_and_activity_time():
    """Verify clinical observation time, pipeline execution period, and provenance recorded time are never confused."""
    ev = make_test_evidence(text="Potassium 4.8 mmol/L on 2026-08-15")
    c = make_test_claim()
    v = make_test_verification()

    clinical_effective_dt = datetime.datetime(2026, 8, 15, 9, 30, 0, tzinfo=datetime.timezone.utc)
    pipe_start = datetime.datetime(2026, 10, 30, 10, 0, 0, tzinfo=datetime.timezone.utc)
    pipe_end = datetime.datetime(2026, 10, 30, 10, 0, 2, tzinfo=datetime.timezone.utc)
    prov_record = datetime.datetime(2026, 10, 30, 10, 0, 3, tzinfo=datetime.timezone.utc)

    timing = {
        "started_at": pipe_start,
        "completed_at": pipe_end,
        "recorded_at": prov_record,
    }

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "report_status": "final",
            "effective_datetime": clinical_effective_dt,
        },
        pipeline_timing=timing,
        include_provenance_resource=True,
    )

    obs = next(e.resource for e in bundle.entry if e.resource.resource_type == "Observation")
    prov = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance")

    # Observation.effectiveDateTime represents clinical specimen/collection time
    assert obs.effectiveDateTime == clinical_effective_dt

    # Provenance.occurredPeriod represents pipeline execution interval
    assert prov.occurredPeriod is not None
    assert prov.occurredPeriod.start == pipe_start
    assert prov.occurredPeriod.end == pipe_end

    # Provenance.recorded represents provenance record creation instant
    assert prov.recorded == prov_record

    # Verify they are distinct and not conflated
    assert obs.effectiveDateTime != prov.occurredPeriod.start
    assert prov.occurredPeriod.end != prov.recorded


def test_missing_activity_time_does_not_invent_occurred():
    """Verify that when pipeline execution time is unavailable, occurredPeriod and occurredDateTime are omitted."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        pipeline_timing=None,
        include_provenance_resource=True,
    )

    prov = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance")
    assert prov.occurredPeriod is None
    assert prov.occurredDateTime is None
    assert prov.recorded is not None  # Dynamic record instant remains valid


# ─── 2. SOFTWARE DEVICE AGENT & IDENTITY ─────────────────────────────────────


def test_software_device_agent_identity():
    """Verify FHIR Device representing the ClinicalDoc pipeline is created and referenced by Provenance assembler."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    pipe_info = {
        "pipeline_name": "ClinicalDoc Pipeline",
        "pipeline_version": "1.4.2",
        "git_commit_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "models": {
            "ner": "BioClinical-ModernBERT",
            "relation": "Qwen2.5-7B-Instruct-AWQ",
            "fallback": "GLiNER-BioMed",
        },
    }

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        pipeline_info=pipe_info,
        include_provenance_resource=True,
    )

    device = next((e.resource for e in bundle.entry if e.resource.resource_type == "Device"), None)
    assert device is not None
    assert device.status == "active"
    assert device.deviceName[0].name == "ClinicalDoc Pipeline"
    assert device.deviceName[0].type == "user-friendly-name"

    # Version check
    ver_values = [v.value for v in device.version]
    assert "1.4.2" in ver_values
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678" in ver_values

    # Model property check
    prop_values = {p.type.text: p.valueCode[0].text for p in device.property if p.type and p.valueCode}
    assert prop_values.get("model.ner") == "BioClinical-ModernBERT"
    assert prop_values.get("model.relation") == "Qwen2.5-7B-Instruct-AWQ"

    # Provenance agent linkage
    prov = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance")
    assembler_agent = next(a for a in prov.agent if a.type.coding[0].code == "assembler")
    assert assembler_agent.who.reference == f"Device/{device.id}"


def test_missing_runtime_commit_and_model_metadata():
    """Verify that when git commit or model metadata is unavailable, it is gracefully omitted without fabrication."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    # Explicitly supply None / empty for runtime commit and models
    pipe_info = {
        "pipeline_name": "ClinicalDoc Pipeline",
        "pipeline_version": "1.0.0",
        "git_commit_sha": "",  # Empty
        "models": {},          # None
    }

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        pipeline_info=pipe_info,
        include_provenance_resource=True,
    )

    device = next(e.resource for e in bundle.entry if e.resource.resource_type == "Device")
    # Only the pipeline version is present; no fake commit SHA
    commit_versions = [v for v in device.version if v.type and v.type.text == "git-commit"]
    assert len(commit_versions) == 0

    # No fake properties
    assert not device.property

    # Bundle still passes structural validation
    val_report = FHIRValidator.validate_bundle(bundle)
    assert val_report.is_valid is True


# ─── 3. SOURCE AUTHOR & HUMAN REVIEWER AGENTS ────────────────────────────────


def test_source_author_known_vs_unknown():
    """Verify author Practitioner is created ONLY when explicitly identified, never fabricated."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    # Case A: Author unknown -> No Practitioner created, no author agent in Provenance
    bundle_unauth = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        include_provenance_resource=True,
    )
    practitioners_a = [e.resource for e in bundle_unauth.entry if e.resource.resource_type == "Practitioner"]
    assert len(practitioners_a) == 0

    prov_a = next(e.resource for e in bundle_unauth.entry if e.resource.resource_type == "Provenance")
    agent_codes_a = [a.type.coding[0].code for a in prov_a.agent]
    assert "author" not in agent_codes_a
    assert "assembler" in agent_codes_a

    # Case B: Author known -> Genuine Practitioner created with author agent in Provenance
    bundle_auth = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "author_name": "Dr. Meredith Grey, MD",
            "author_npi": "1122334455",
        },
        include_provenance_resource=True,
    )
    practitioners_b = [e.resource for e in bundle_auth.entry if e.resource.resource_type == "Practitioner"]
    assert len(practitioners_b) == 1
    author_pract = practitioners_b[0]
    assert author_pract.name[0].text == "Dr. Meredith Grey, MD"

    prov_b = next(e.resource for e in bundle_auth.entry if e.resource.resource_type == "Provenance")
    author_agent = next((a for a in prov_b.agent if a.type.coding[0].code == "author"), None)
    assert author_agent is not None
    assert author_agent.who.reference == f"Practitioner/{author_pract.id}"


def test_human_reviewer_present_vs_absent():
    """Verify reviewer agent is never created for autonomous extraction, and only created with genuine review decision."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    # Case A: Autonomous extraction (review_info is None)
    bundle_auto = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        review_info=None,
        include_provenance_resource=True,
    )
    provs_auto = [e.resource for e in bundle_auto.entry if e.resource.resource_type == "Provenance"]
    assert len(provs_auto) == 1
    assert provs_auto[0].activity.coding[0].code == "CREATE"

    # Case B: Human reviewer present with ACCEPTED decision
    rev_info = {
        "reviewer_id": "usr-lead-doc",
        "reviewer_name": "Dr. Lisa Cuddy",
        "review_decision": "ACCEPTED",
        "reviewed_at": datetime.datetime(2026, 10, 30, 11, 45, 0, tzinfo=datetime.timezone.utc),
    }
    bundle_reviewed = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        review_info=rev_info,
        include_provenance_resource=True,
    )
    provs_reviewed = [e.resource for e in bundle_reviewed.entry if e.resource.resource_type == "Provenance"]
    assert len(provs_reviewed) == 2

    # Extraction Provenance
    prov_extract = next(p for p in provs_reviewed if p.activity.coding[0].code == "CREATE")
    assert prov_extract.agent[0].type.coding[0].code == "assembler"

    # Review Provenance
    prov_review = next(p for p in provs_reviewed if p.activity.coding[0].code == "AU")
    assert prov_review.agent[0].type.coding[0].code == "verifier"
    assert prov_review.agent[0].who.reference.startswith("Practitioner/pract-reviewer-usr-lead-doc")
    assert prov_review.activity.text == "Human Review: ACCEPTED"
    assert prov_review.occurredDateTime == rev_info["reviewed_at"]


# ─── 4. SOURCE DOCUMENT TRACEABILITY & CRYPTOGRAPHIC INTEGRITY ───────────────


def test_sha256_hash_calculated_from_exact_source_bytes():
    """Verify SHA-256 hash is computed directly from exact ingested source bytes and encoded as base64Binary."""
    raw_source = b"%PDF-1.5 \xde\xad\xbe\xef Diagnostic Report Sample Clinical Document Bytes"
    expected_digest = hashlib.sha256(raw_source).digest()
    expected_b64 = base64.b64encode(expected_digest).decode("ascii")

    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "filename": "diagnostic_lab.pdf",
            "mime_type": "application/pdf",
            "source_bytes": raw_source,
        },
        include_document_reference=True,
    )

    doc_ref = next(e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference")
    attachment = doc_ref.content[0].attachment
    raw_hash_b64 = attachment.hash.decode("ascii") if isinstance(attachment.hash, bytes) else attachment.hash
    assert raw_hash_b64 == expected_b64

    # Verify decoded byte length is strictly 32 bytes for SHA-256
    decoded_hash = base64.b64decode(raw_hash_b64)
    assert len(decoded_hash) == 32
    assert decoded_hash == expected_digest


def test_modified_source_bytes_produces_different_hash():
    """Verify modified source bytes produce a strictly different SHA-256 hash and fail comparison."""
    original_bytes = b"Original Patient Lab Report 2026-10-30: Glucose 95 mg/dL"
    tampered_bytes = b"Modified Patient Lab Report 2026-10-30: Glucose 195 mg/dL"

    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle_orig = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={"source_bytes": original_bytes},
        include_document_reference=True,
    )
    bundle_tamp = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={"source_bytes": tampered_bytes},
        include_document_reference=True,
    )

    hash_orig = next(e.resource for e in bundle_orig.entry if e.resource.resource_type == "DocumentReference").content[0].attachment.hash
    hash_tamp = next(e.resource for e in bundle_tamp.entry if e.resource.resource_type == "DocumentReference").content[0].attachment.hash

    assert hash_orig != hash_tamp


def test_missing_source_bytes_does_not_fabricate_hash():
    """Verify that when original binary bytes and pre-screened hash are unavailable, hash is omitted rather than fabricated."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "filename": "clinical_note.txt",
            "mime_type": "text/plain",
            # No source_bytes, no sha256
        },
        include_document_reference=True,
    )

    doc_ref = next(e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference")
    assert doc_ref.content[0].attachment.hash is None


def test_document_reference_and_entity_linkage():
    """Verify multiple Observation resources stably link to source DocumentReference via derivedFrom, and Provenance links via entity."""
    ev = make_test_evidence()
    c1 = make_test_claim(claim_id="c-k", field_type="lab.value", value="4.8", code="2823-3")
    c2 = make_test_claim(claim_id="c-na", field_type="lab.value", value="140", code="2951-2", group_id="grp-na")
    v1 = make_test_verification(claim_id="c-k")
    v2 = make_test_verification(claim_id="c-na")

    bundle = claims_to_fhir_bundle(
        claims=[c1, c2],
        evidence_map={ev.evidence_id: ev},
        verification_map={"c-k": v1, "c-na": v2},
        document_metadata={
            "filename": "metabolic_panel.pdf",
            "mime_type": "application/pdf",
            "source_bytes": b"Metabolic Panel Raw Bytes",
        },
        include_document_reference=True,
        include_provenance_resource=True,
    )

    doc_ref = next(e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference")
    observations = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"]
    assert len(observations) == 2

    # DerivedFrom check on each observation
    for obs in observations:
        assert obs.derivedFrom is not None
        assert any(ref.reference == f"DocumentReference/{doc_ref.id}" for ref in obs.derivedFrom)

    # Entity check on Provenance
    prov = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance")
    assert prov.entity is not None
    source_entity = next(ent for ent in prov.entity if ent.role == "source")
    assert source_entity.what.reference == f"DocumentReference/{doc_ref.id}"


# ─── 5. REFERENTIAL INTEGRITY & VALIDATION ────────────────────────────────────


def test_provenance_and_bundle_referential_integrity():
    """Verify that all target, agent, entity, and derivedFrom references in the bundle resolve cleanly."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    pipe_info = {
        "pipeline_name": "ClinicalDoc Pipeline",
        "pipeline_version": "1.0.0",
        "git_commit_sha": "1234567890abcdef1234567890abcdef12345678",
    }
    rev_info = {
        "reviewer_id": "rev-101",
        "reviewer_name": "Dr. Gregory House",
        "review_decision": "ACCEPTED",
        "reviewed_at": datetime.datetime(2026, 10, 30, 15, 0, 0, tzinfo=datetime.timezone.utc),
    }

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "filename": "cbc.pdf",
            "author_name": "Dr. James Wilson",
            "source_bytes": b"Complete Blood Count Bytes",
            "report_status": "final",
        },
        pipeline_info=pipe_info,
        review_info=rev_info,
        include_document_reference=True,
        include_provenance_resource=True,
    )

    val_report = FHIRValidator.validate_bundle(bundle)
    assert val_report.is_valid is True, f"Validation errors: {val_report.errors}"
    assert val_report.errors == []

    # Map of all resource IDs in bundle
    bundle_resources = {f"{e.resource.resource_type}/{e.resource.id}" for e in bundle.entry}

    # Verify Provenance targets and agents
    provs = [e.resource for e in bundle.entry if e.resource.resource_type == "Provenance"]
    assert len(provs) == 2

    for p in provs:
        for t in p.target:
            assert t.reference in bundle_resources, f"Dangling target: {t.reference}"
        for a in p.agent:
            assert a.who.reference in bundle_resources, f"Dangling agent: {a.who.reference}"
        if p.entity:
            for ent in p.entity:
                assert ent.what.reference in bundle_resources, f"Dangling entity: {ent.what.reference}"


def test_extraction_and_review_activities_at_different_times():
    """Verify extraction and review activities occurring at different times maintain separate timestamps."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    t_extract_start = datetime.datetime(2026, 10, 30, 8, 0, 0, tzinfo=datetime.timezone.utc)
    t_extract_end = datetime.datetime(2026, 10, 30, 8, 0, 2, tzinfo=datetime.timezone.utc)
    t_extract_rec = datetime.datetime(2026, 10, 30, 8, 0, 2, tzinfo=datetime.timezone.utc)

    t_review = datetime.datetime(2026, 10, 30, 14, 30, 0, tzinfo=datetime.timezone.utc)
    t_review_rec = datetime.datetime(2026, 10, 30, 14, 30, 1, tzinfo=datetime.timezone.utc)

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        pipeline_timing={
            "started_at": t_extract_start,
            "completed_at": t_extract_end,
            "recorded_at": t_extract_rec,
        },
        review_info={
            "reviewer_id": "usr-reviewer-9",
            "reviewer_name": "Dr. Allison Cameron",
            "review_decision": "ACCEPTED",
            "reviewed_at": t_review,
            "recorded_at": t_review_rec,
        },
        include_provenance_resource=True,
    )

    prov_extract = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance" and e.resource.activity.coding[0].code == "CREATE")
    prov_review = next(e.resource for e in bundle.entry if e.resource.resource_type == "Provenance" and e.resource.activity.coding[0].code == "AU")

    assert prov_extract.occurredPeriod.start == t_extract_start
    assert prov_extract.occurredPeriod.end == t_extract_end
    assert prov_extract.recorded == t_extract_rec

    assert prov_review.occurredDateTime == t_review
    assert prov_review.recorded == t_review_rec
    assert prov_review.recorded != prov_extract.recorded


def test_document_reference_date_semantics_separate_from_authoring_date():
    """Verify DocumentReference.date is reference registration date, not conflated with clinical authoring date."""
    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    authoring_dt = datetime.datetime(2026, 5, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    upload_dt = datetime.datetime(2026, 10, 30, 12, 0, 0, tzinfo=datetime.timezone.utc)

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "filename": "clinical_note.pdf",
            "mime_type": "application/pdf",
            "doc_date": authoring_dt,       # Clinical document authoring date
            "uploaded_at": upload_dt,       # System registration / ingestion date
        },
        include_document_reference=True,
    )

    doc_ref = next(e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference")
    # DocumentReference.date must reflect registration / indexing instant
    assert doc_ref.date == upload_dt
    # Attachment.creation represents clinical authoring instant
    assert doc_ref.content[0].attachment.creation == authoring_dt
    # They are distinct and must not be conflated
    assert doc_ref.date != doc_ref.content[0].attachment.creation


def test_precomputed_sha256_trust_and_source_precedence():
    """Verify exact source bytes take strict precedence over any external or conflicting precomputed hash."""
    real_source = b"Authentic signed pathology report exact bytes"
    computed_digest = hashlib.sha256(real_source).digest()
    expected_b64 = base64.b64encode(computed_digest).decode("ascii")

    # Conflicting / stale precomputed hash
    conflicting_hex = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

    ev = make_test_evidence()
    c = make_test_claim()
    v = make_test_verification()

    bundle = claims_to_fhir_bundle(
        claims=[c],
        evidence_map={ev.evidence_id: ev},
        verification_map={c.claim_id: v},
        document_metadata={
            "source_bytes": real_source,
            "sha256": conflicting_hex,
        },
        include_document_reference=True,
    )

    doc_ref = next(e.resource for e in bundle.entry if e.resource.resource_type == "DocumentReference")
    raw_hash_b64 = doc_ref.content[0].attachment.hash
    if isinstance(raw_hash_b64, bytes):
        raw_hash_b64 = raw_hash_b64.decode("ascii")

    # Must equal hash of exact source bytes, NOT the conflicting precomputed hash
    assert raw_hash_b64 == expected_b64
    assert raw_hash_b64 != base64.b64encode(bytes.fromhex(conflicting_hex)).decode("ascii")
