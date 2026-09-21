"""Automated tests for FHIR R4 mapping, terminology preservation, verification states, and validation."""

from decimal import Decimal
import json
from pathlib import Path
import pytest

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.patient import Patient

from clindoc.fhir.constants import (
    EXT_SOURCE_EVIDENCE,
    EXT_VERIFICATION_DISCREPANCY,
    EXT_VERIFICATION_STATUS,
    IDENTIFIER_MRN_SYSTEM,
    LOINC_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.fhir.mapper import lab_report_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.schema.lab import ExtractedLabReport
from clindoc.verify.error_injection import inject_single_error
from clindoc.verify.schema import (
    ObservationVerificationResult,
    ReportVerificationResult,
    VerificationStatus,
)
from clindoc.verify.verifier import verify_report


@pytest.fixture
def lab_0001_bundle() -> tuple[ExtractedLabReport, DocumentOCRRecord, Bundle]:
    """Fixture providing LAB_0001 converted FHIR R4 Bundle and source data."""
    ext_path = Path("data/extracted/LAB_0001.json")
    ocr_path = Path("data/ocr_cache/LAB_0001.json")
    assert ext_path.exists()
    assert ocr_path.exists()

    with open(ext_path, "r", encoding="utf-8") as f:
        report = ExtractedLabReport.model_validate(json.load(f))
    with open(ocr_path, "r", encoding="utf-8") as f:
        ocr = DocumentOCRRecord.model_validate(json.load(f))

    ver_result = verify_report(report, ocr)
    bundle = lab_report_to_fhir_bundle(report, ver_result)
    return report, ocr, bundle


def test_lab_0001_converts_successfully(lab_0001_bundle):
    """Test that LAB_0001 converts into a valid FHIR R4 Bundle."""
    report, _, bundle = lab_0001_bundle

    assert bundle.resource_type == "Bundle"
    assert bundle.type == "collection"
    assert bundle.id == "bundle-lab-0001"
    assert len(bundle.entry) == len(report.observations) + 4  # Patient, Org, Pract, DiagReport + obs

    # Validate using FHIRValidator
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True
    assert len(val.errors) == 0
    assert val.resource_counts["Patient"] == 1
    assert val.resource_counts["DiagnosticReport"] == 1
    assert val.resource_counts["Observation"] == len(report.observations)
    assert val.verification_status_counts["VERIFIED"] == len(report.observations)


def test_patient_resource_is_valid(lab_0001_bundle):
    """Verify Patient resource structure, identifiers, names, and demographics."""
    report, _, bundle = lab_0001_bundle
    patient_entry = next(e for e in bundle.entry if e.resource.resource_type == "Patient")
    patient: Patient = patient_entry.resource

    assert patient.id == "patient-lab-0001"
    assert patient.identifier is not None
    mrn = next(i for i in patient.identifier if i.system == IDENTIFIER_MRN_SYSTEM)
    assert mrn.value == report.patient.patient_id

    assert patient.name is not None
    assert patient.name[0].text == report.patient.name
    expected_gender = "female" if report.patient.gender == "F" else "male"
    assert patient.gender == expected_gender
    assert str(patient.birthDate) == report.patient.dob


def test_diagnostic_report_is_valid(lab_0001_bundle):
    """Verify DiagnosticReport references patient, performer, and all observations."""
    report, _, bundle = lab_0001_bundle
    diag_entry = next(e for e in bundle.entry if e.resource.resource_type == "DiagnosticReport")
    diag: DiagnosticReport = diag_entry.resource

    assert diag.id == "report-lab-0001"
    assert diag.status == "final"
    assert diag.subject.reference == "Patient/patient-lab-0001"
    assert diag.performer[0].reference == "Organization/org-lab-0001"
    assert diag.resultsInterpreter[0].reference == "Practitioner/pract-lab-0001"

    # All observation references must be present
    result_refs = [r.reference for r in diag.result]
    assert len(result_refs) == len(report.observations)
    for idx in range(len(report.observations)):
        assert f"Observation/obs-lab-0001-{idx + 1}" in result_refs


def test_observation_resources_are_valid(lab_0001_bundle):
    """Verify Observation resources comply with FHIR R4 schema and categories."""
    _, _, bundle = lab_0001_bundle
    observations = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"]

    for obs in observations:
        assert isinstance(obs, Observation)
        assert obs.status == "final"
        assert obs.subject.reference == "Patient/patient-lab-0001"
        assert obs.category[0].coding[0].code == "laboratory"
        assert obs.valueQuantity is not None
        assert obs.valueQuantity.value is not None


def test_loinc_codes_preserved(lab_0001_bundle):
    """Verify LOINC codes are preserved with http://loinc.org system URI."""
    report, _, bundle = lab_0001_bundle
    observations = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"]

    for extracted_obs, fhir_obs in zip(report.observations, observations):
        if extracted_obs.loinc_code:
            loinc_coding = next(
                c for c in fhir_obs.code.coding if c.system == LOINC_SYSTEM
            )
            assert loinc_coding.code == extracted_obs.loinc_code
            assert loinc_coding.display == extracted_obs.test_name


def test_ucum_units_preserved(lab_0001_bundle):
    """Verify UCUM units and measured values are preserved accurately."""
    report, _, bundle = lab_0001_bundle
    observations = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"]

    for extracted_obs, fhir_obs in zip(report.observations, observations):
        qty = fhir_obs.valueQuantity
        assert qty.system == UCUM_SYSTEM
        assert qty.code == extracted_obs.normalized_unit
        assert qty.unit == extracted_obs.unit
        assert float(qty.value) == extracted_obs.value


def test_reference_ranges_preserved(lab_0001_bundle):
    """Verify reference intervals (low, high, text) are preserved in Observation resources."""
    report, _, bundle = lab_0001_bundle
    observations = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"]

    for extracted_obs, fhir_obs in zip(report.observations, observations):
        rr = fhir_obs.referenceRange[0]
        assert rr.text == extracted_obs.ref_range_text
        if extracted_obs.ref_range_low is not None:
            assert float(rr.low.value) == extracted_obs.ref_range_low
            assert rr.low.code == extracted_obs.normalized_unit
        if extracted_obs.ref_range_high is not None:
            assert float(rr.high.value) == extracted_obs.ref_range_high
            assert rr.high.code == extracted_obs.normalized_unit


def test_abnormal_interpretation_preserved(lab_0001_bundle):
    """Verify interpretation flag (NORMAL, HIGH, LOW) is preserved with HL7 coding."""
    report, _, bundle = lab_0001_bundle
    observations = [e.resource for e in bundle.entry if e.resource.resource_type == "Observation"]

    flag_map = {
        "NORMAL": "N",
        "HIGH": "H",
        "LOW": "L",
    }

    for extracted_obs, fhir_obs in zip(report.observations, observations):
        interp = fhir_obs.interpretation[0]
        expected_code = flag_map[extracted_obs.abnormal_flag]
        coding = next(c for c in interp.coding if c.system == OBS_INTERPRETATION_SYSTEM)
        assert coding.code == expected_code
        assert interp.text == extracted_obs.abnormal_flag


def test_resource_references_resolve_correctly(lab_0001_bundle):
    """Verify every relative reference within the Bundle resolves to an existing entry."""
    _, _, bundle = lab_0001_bundle
    val = FHIRValidator.validate_bundle(bundle)
    assert val.is_valid is True
    assert not any("not found in bundle" in err for err in val.errors)


def test_verification_state_preserved_verified_vs_discrepancy(lab_0001_bundle):
    """Verify machine-readable preservation of VERIFIED vs DISCREPANCY states."""
    report, ocr, _ = lab_0001_bundle

    # 1. Clean report -> all VERIFIED, Observation status 'final', DiagnosticReport 'final'
    ver_clean = verify_report(report, ocr)
    bundle_clean = lab_report_to_fhir_bundle(report, ver_clean)
    val_clean = FHIRValidator.validate_bundle(bundle_clean)
    assert val_clean.verification_status_counts["VERIFIED"] == len(report.observations)

    # 2. Injected corruption -> DISCREPANCY, Observation status 'preliminary', DiagnosticReport 'preliminary'
    corrupted_rep, err = inject_single_error(report, "VALUE_CORRUPTION", target_idx=0)
    ver_corrupt = verify_report(corrupted_rep, ocr)
    bundle_corrupt = lab_report_to_fhir_bundle(corrupted_rep, ver_corrupt)
    val_corrupt = FHIRValidator.validate_bundle(bundle_corrupt)

    assert val_corrupt.is_valid is True
    assert val_corrupt.verification_status_counts["DISCREPANCY"] == 1
    assert val_corrupt.verification_status_counts["VERIFIED"] == len(report.observations) - 1

    corrupt_obs = next(
        e.resource
        for e in bundle_corrupt.entry
        if e.resource.resource_type == "Observation" and e.resource.id == "obs-lab-0001-1"
    )
    assert corrupt_obs.status == "preliminary"

    # Check discrepancy extension
    disc_ext = next(e for e in corrupt_obs.extension if e.url == EXT_VERIFICATION_DISCREPANCY)
    sub_map = {sub.url: sub.valueString for sub in disc_ext.extension}
    assert sub_map["reason"] == "VALUE_MISMATCH"
    assert sub_map["field"] == "value"
    assert sub_map["extractedValue"] == str(err.corrupted_value)
    assert sub_map["sourceValue"] == str(err.original_value)


def test_verification_state_unchecked_and_missing_source(lab_0001_bundle):
    """Verify machine-readable preservation of UNCHECKED and MISSING_SOURCE_EVIDENCE states."""
    report, _, _ = lab_0001_bundle

    # 1. UNCHECKED when no verification result provided
    bundle_unchecked = lab_report_to_fhir_bundle(report, None)
    val_unchecked = FHIRValidator.validate_bundle(bundle_unchecked)
    assert val_unchecked.is_valid is True
    assert val_unchecked.verification_status_counts["UNCHECKED"] == len(report.observations)

    obs0 = next(e.resource for e in bundle_unchecked.entry if e.resource.resource_type == "Observation")
    assert obs0.status == "registered"
    status_ext = next(e for e in obs0.extension if e.url == EXT_VERIFICATION_STATUS)
    assert status_ext.valueCode == "UNCHECKED"

    # 2. MISSING_SOURCE_EVIDENCE
    custom_ver = ReportVerificationResult(
        doc_id=report.doc_id,
        overall_status=VerificationStatus.MISSING_SOURCE_EVIDENCE,
        total_observations=len(report.observations),
        verified_observations=0,
        discrepancy_count=0,
        observation_results=[
            ObservationVerificationResult(
                test_name=report.observations[0].test_name,
                status=VerificationStatus.MISSING_SOURCE_EVIDENCE,
            )
        ],
    )
    bundle_missing = lab_report_to_fhir_bundle(report, custom_ver)
    val_missing = FHIRValidator.validate_bundle(bundle_missing)
    assert val_missing.is_valid is True
    assert val_missing.verification_status_counts["MISSING_SOURCE_EVIDENCE"] == 1

    obs_missing = next(e.resource for e in bundle_missing.entry if e.resource.resource_type == "Observation")
    assert obs_missing.status == "preliminary"


def test_invalid_or_missing_required_data_handled_explicitly():
    """Verify that invalid FHIR resources or broken references trigger explicit validation errors."""
    # 1. Missing required field in resource
    malformed_bundle = {
        "resourceType": "Bundle",
        "id": "bad-bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": "Observation/bad-obs",
                "resource": {
                    "resourceType": "Observation",
                    "id": "bad-obs",
                    # missing status and code
                },
            }
        ],
    }
    res1 = FHIRValidator.validate_bundle(malformed_bundle)
    assert res1.is_valid is False
    assert any("FHIR R4 Schema parsing failure" in err for err in res1.errors)

    # 2. Broken reference (subject reference not in bundle)
    broken_ref_bundle = Bundle(
        id="broken-bundle",
        type="collection",
        entry=[
            {
                "fullUrl": "Patient/pat-1",
                "resource": Patient(id="pat-1"),
            },
            {
                "fullUrl": "DiagnosticReport/diag-1",
                "resource": DiagnosticReport(
                    id="diag-1",
                    status="final",
                    code={"text": "Panel"},
                    subject={"reference": "Patient/non-existent-patient"},
                ),
            },
        ],
    )
    res2 = FHIRValidator.validate_bundle(broken_ref_bundle)
    assert res2.is_valid is False
    assert any("subject 'Patient/non-existent-patient' not found in bundle" in err for err in res2.errors)
