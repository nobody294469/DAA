"""Mapper converting extracted & verified laboratory reports into FHIR R4 Bundles."""

from decimal import Decimal
import json
import re
from typing import Any

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference

from clindoc.fhir.constants import (
    DIAG_CATEGORY_SYSTEM,
    EXT_SOURCE_EVIDENCE,
    EXT_VERIFICATION_DISCREPANCY,
    EXT_VERIFICATION_STATUS,
    IDENTIFIER_MRN_SYSTEM,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation
from clindoc.verify.schema import (
    FieldDiscrepancy,
    ObservationVerificationResult,
    ReportVerificationResult,
    VerificationStatus,
)


def _sanitize_id(val: str) -> str:
    """Sanitize strings into valid FHIR resource IDs (letters, digits, dots, dashes; <= 64 chars)."""
    clean = re.sub(r"[^A-Za-z0-9\-\.]", "-", val.strip())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean[:64] or "unknown"


def _map_patient(report: ExtractedLabReport) -> Patient:
    """Map extracted patient information into a FHIR R4 Patient resource."""
    doc_slug = _sanitize_id(report.doc_id.lower())
    pat_id = f"patient-{doc_slug}"

    patient_info = report.patient
    identifiers = []
    names = []
    gender = "unknown"
    birth_date = None

    if patient_info:
        if patient_info.patient_id:
            identifiers.append(
                Identifier(
                    system=IDENTIFIER_MRN_SYSTEM,
                    value=str(patient_info.patient_id),
                )
            )

        if patient_info.name:
            full_name = patient_info.name.strip()
            parts = full_name.split()
            if len(parts) > 1:
                family = parts[-1]
                given = parts[:-1]
            elif parts:
                family = parts[0]
                given = []
            else:
                family = full_name
                given = []
            names.append(
                HumanName(
                    text=full_name,
                    family=family,
                    given=given if given else None,
                )
            )

        if patient_info.gender:
            if patient_info.gender.upper() == "M":
                gender = "male"
            elif patient_info.gender.upper() == "F":
                gender = "female"
            else:
                gender = "unknown"

        if patient_info.dob:
            birth_date = patient_info.dob

    return Patient(
        id=pat_id,
        identifier=identifiers if identifiers else None,
        name=names if names else None,
        gender=gender,
        birthDate=birth_date,
    )


def _map_organization(report: ExtractedLabReport) -> Organization:
    """Map laboratory / facility into a FHIR R4 Organization resource."""
    doc_slug = _sanitize_id(report.doc_id.lower())
    org_id = f"org-{doc_slug}"
    org_name = report.facility_name.strip() if report.facility_name else "Clinical Laboratory"

    return Organization(
        id=org_id,
        name=org_name,
    )


def _map_practitioner(report: ExtractedLabReport) -> Practitioner | None:
    """Map ordering provider into a FHIR R4 Practitioner resource if present."""
    if not report.ordering_provider or not report.ordering_provider.strip():
        return None

    doc_slug = _sanitize_id(report.doc_id.lower())
    pract_id = f"pract-{doc_slug}"
    provider_name = report.ordering_provider.strip()

    return Practitioner(
        id=pract_id,
        name=[HumanName(text=provider_name)],
    )


def _map_interpretation(flag: str) -> list[CodeableConcept]:
    """Map abnormal flag to standard HL7 ObservationInterpretation CodeableConcept."""
    code_map = {
        "NORMAL": ("N", "Normal"),
        "HIGH": ("H", "High"),
        "LOW": ("L", "Low"),
    }
    flag_upper = flag.strip().upper() if flag else "NORMAL"
    code, display = code_map.get(flag_upper, ("N", "Normal"))

    return [
        CodeableConcept(
            coding=[
                Coding(
                    system=OBS_INTERPRETATION_SYSTEM,
                    code=code,
                    display=display,
                )
            ],
            text=flag_upper,
        )
    ]


def _build_source_evidence_extension(obs: ExtractedObservation) -> Extension:
    """Construct FHIR Extension representing source grounding evidence / provenance."""
    ev = obs.source_evidence
    return Extension(
        url=EXT_SOURCE_EVIDENCE,
        extension=[
            Extension(url="text", valueString=ev.text),
            Extension(url="page", valueInteger=ev.page),
            Extension(url="bbox", valueString=json.dumps(ev.bbox)),
            Extension(url="confidence", valueDecimal=round(Decimal(str(ev.ocr_confidence)), 4)),
        ],
    )


def _build_discrepancy_extension(discrepancies: list[FieldDiscrepancy]) -> list[Extension]:
    """Construct FHIR Extension detailing verification discrepancies."""
    exts = []
    for disc in discrepancies:
        exts.append(
            Extension(
                url=EXT_VERIFICATION_DISCREPANCY,
                extension=[
                    Extension(url="reason", valueString=disc.reason),
                    Extension(url="field", valueString=disc.field),
                    Extension(url="extractedValue", valueString=str(disc.extracted_value)),
                    Extension(url="sourceValue", valueString=str(disc.source_value)),
                ],
            )
        )
    return exts


def _map_observation(
    obs: ExtractedObservation,
    idx: int,
    report: ExtractedLabReport,
    patient_id: str,
    org_id: str,
    obs_ver_result: ObservationVerificationResult | None,
) -> Observation:
    """Map an ExtractedObservation into a standard FHIR R4 Observation resource."""
    doc_slug = _sanitize_id(report.doc_id.lower())
    obs_id = f"obs-{doc_slug}-{idx + 1}"

    # Determine verification status and discrepancy list
    if obs_ver_result is None:
        status_code = VerificationStatus.UNCHECKED.value
        fhir_status = "registered"
        discrepancies = []
    else:
        status_code = obs_ver_result.status.value
        if obs_ver_result.status == VerificationStatus.VERIFIED:
            fhir_status = "final"
        elif obs_ver_result.status == VerificationStatus.DISCREPANCY:
            fhir_status = "preliminary"
        elif obs_ver_result.status == VerificationStatus.MISSING_SOURCE_EVIDENCE:
            fhir_status = "preliminary"
        else:
            fhir_status = "registered"
        discrepancies = obs_ver_result.discrepancies

    # Observation category
    category = [
        CodeableConcept(
            coding=[
                Coding(
                    system=OBS_CATEGORY_SYSTEM,
                    code="laboratory",
                    display="Laboratory",
                )
            ]
        )
    ]

    # Observation code (LOINC + verbatim test name)
    codings = []
    if obs.loinc_code:
        codings.append(
            Coding(
                system=LOINC_SYSTEM,
                code=obs.loinc_code,
                display=obs.test_name,
            )
        )
    obs_code = CodeableConcept(
        coding=codings if codings else None,
        text=obs.test_name,
    )

    # Measured valueQuantity (numeric + UCUM)
    val_quantity = Quantity(
        value=Decimal(str(obs.value)),
        unit=obs.unit,
        system=UCUM_SYSTEM,
        code=obs.normalized_unit,
    )

    # Reference interval
    ref_ranges = []
    low_qty = (
        Quantity(
            value=Decimal(str(obs.ref_range_low)),
            unit=obs.unit,
            system=UCUM_SYSTEM,
            code=obs.normalized_unit,
        )
        if obs.ref_range_low is not None
        else None
    )
    high_qty = (
        Quantity(
            value=Decimal(str(obs.ref_range_high)),
            unit=obs.unit,
            system=UCUM_SYSTEM,
            code=obs.normalized_unit,
        )
        if obs.ref_range_high is not None
        else None
    )
    ref_ranges.append(
        ObservationReferenceRange(
            low=low_qty,
            high=high_qty,
            text=obs.ref_range_text,
        )
    )

    # Extensions: verification status + source evidence + optional discrepancies
    extensions = [
        Extension(
            url=EXT_VERIFICATION_STATUS,
            valueCode=status_code,
        ),
        _build_source_evidence_extension(obs),
    ]
    if discrepancies:
        extensions.extend(_build_discrepancy_extension(discrepancies))

    return Observation(
        id=obs_id,
        status=fhir_status,
        category=category,
        code=obs_code,
        subject=Reference(reference=f"Patient/{patient_id}"),
        effectiveDateTime=report.report_date,
        performer=[Reference(reference=f"Organization/{org_id}")],
        valueQuantity=val_quantity,
        referenceRange=ref_ranges,
        interpretation=_map_interpretation(obs.abnormal_flag),
        extension=extensions,
    )


def _map_diagnostic_report(
    report: ExtractedLabReport,
    patient_id: str,
    org_id: str,
    pract_id: str | None,
    observation_ids: list[str],
    ver_result: ReportVerificationResult | None,
) -> DiagnosticReport:
    """Map laboratory report metadata into a FHIR R4 DiagnosticReport resource."""
    doc_slug = _sanitize_id(report.doc_id.lower())
    diag_id = f"report-{doc_slug}"

    if ver_result is None:
        report_status = "registered"
    elif ver_result.overall_status == VerificationStatus.VERIFIED:
        report_status = "final"
    elif ver_result.overall_status in (
        VerificationStatus.DISCREPANCY,
        VerificationStatus.MISSING_SOURCE_EVIDENCE,
    ):
        report_status = "preliminary"
    else:
        report_status = "registered"

    category = [
        CodeableConcept(
            coding=[
                Coding(
                    system=DIAG_CATEGORY_SYSTEM,
                    code="LAB",
                    display="Laboratory",
                )
            ]
        )
    ]

    panel_title = (
        report.observations[0].panel_name
        if report.observations and report.observations[0].panel_name
        else "Clinical Laboratory Report"
    )

    results_interpreter = [Reference(reference=f"Practitioner/{pract_id}")] if pract_id else None

    return DiagnosticReport(
        id=diag_id,
        status=report_status,
        category=category,
        code=CodeableConcept(text=panel_title),
        subject=Reference(reference=f"Patient/{patient_id}"),
        effectiveDateTime=report.report_date,
        performer=[Reference(reference=f"Organization/{org_id}")],
        resultsInterpreter=results_interpreter,
        result=[Reference(reference=f"Observation/{obs_id}") for obs_id in observation_ids],
    )


def lab_report_to_fhir_bundle(
    report: ExtractedLabReport,
    verification_result: ReportVerificationResult | None = None,
) -> Bundle:
    """Convert an ExtractedLabReport and its verification outcome into a FHIR R4 Bundle.

    Args:
        report: The extracted structured laboratory document.
        verification_result: Optional verification outcome. If omitted, observations
            will be mapped with VerificationStatus.UNCHECKED and status='registered'.

    Returns:
        fhir.resources.bundle.Bundle of type 'collection' containing Patient, Organization,
        Practitioner (if present), DiagnosticReport, and all Observation resources.
    """
    doc_slug = _sanitize_id(report.doc_id.lower())
    bundle_id = f"bundle-{doc_slug}"

    # 1. Patient
    patient = _map_patient(report)

    # 2. Organization
    organization = _map_organization(report)

    # 3. Practitioner (optional)
    practitioner = _map_practitioner(report)

    # 4. Observations
    obs_ver_map: dict[str, ObservationVerificationResult] = {}
    if verification_result:
        for ores in verification_result.observation_results:
            obs_ver_map[ores.test_name] = ores

    observations: list[Observation] = []
    for idx, obs in enumerate(report.observations):
        ores = obs_ver_map.get(obs.test_name)
        obs_res = _map_observation(
            obs=obs,
            idx=idx,
            report=report,
            patient_id=patient.id,
            org_id=organization.id,
            obs_ver_result=ores,
        )
        observations.append(obs_res)

    # 5. DiagnosticReport
    diag_report = _map_diagnostic_report(
        report=report,
        patient_id=patient.id,
        org_id=organization.id,
        pract_id=practitioner.id if practitioner else None,
        observation_ids=[o.id for o in observations],
        ver_result=verification_result,
    )

    # 6. Bundle Entries
    entries = [
        BundleEntry(fullUrl=f"Patient/{patient.id}", resource=patient),
        BundleEntry(fullUrl=f"Organization/{organization.id}", resource=organization),
    ]

    if practitioner:
        entries.append(BundleEntry(fullUrl=f"Practitioner/{practitioner.id}", resource=practitioner))

    entries.append(BundleEntry(fullUrl=f"DiagnosticReport/{diag_report.id}", resource=diag_report))

    for obs in observations:
        entries.append(BundleEntry(fullUrl=f"Observation/{obs.id}", resource=obs))

    return Bundle(
        id=bundle_id,
        type="collection",
        entry=entries,
    )
