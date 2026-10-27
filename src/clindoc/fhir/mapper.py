"""Mapper converting extracted & verified clinical claims into FHIR R4B Bundles."""

from decimal import Decimal
import json
import re
from typing import Any, Optional, Sequence

from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.timing import Timing

from clindoc.extract.contracts import Claim, Evidence
from clindoc.fhir.constants import (
    DIAG_CATEGORY_SYSTEM,
    EXT_CLAIM_PROVENANCE,
    EXT_SOURCE_EVIDENCE,
    EXT_VERIFICATION_DISCREPANCY,
    EXT_VERIFICATION_STATUS,
    IDENTIFIER_MRN_SYSTEM,
    LOINC_SYSTEM,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    RXNORM_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation
from clindoc.schema.verification import VerificationResult
from clindoc.verify.schema import (
    FieldDiscrepancy,
    ObservationVerificationResult,
    ReportVerificationResult,
    VerificationStatus,
)


VITAL_SIGN_NAMES = frozenset({"Systolic blood pressure", "Diastolic blood pressure", "Heart rate", "Respiratory rate",
                               "Body temperature", "Oxygen saturation", "SpO2"})  # text_provider._VITALS

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


# ─── CANONICAL CLAIM-PROVENANCE & FHIR MAPPING (DEC-008, DEC-016) ─────────────

def build_resource_provenance_extension(
    claim: Claim,
    verification: VerificationResult,
    evidence: Evidence,
) -> Extension:
    """Construct top-level resource provenance extension conforming to DEC-016 & §16.1.

    URL: http://clindoc.org/fhir/StructureDefinition/claim-provenance
    Carries: claimId, verificationId, verificationLabel, confidence, stageReached,
             evidenceText, sourcePage, sourceBoundingBox, ocrConfidence (if present).
    """
    bbox = evidence.bbox
    bbox_str = f"{bbox.x0},{bbox.y0},{bbox.x1},{bbox.y1}"
    conf_val = round(Decimal(str(verification.confidence)), 4)

    sub_exts = [
        Extension(url="claimId", valueString=claim.claim_id),
        Extension(url="verificationId", valueString=verification.verification_id),
        Extension(url="verificationLabel", valueCode=str(verification.label)),
        Extension(url="confidence", valueDecimal=conf_val),
        Extension(url="stageReached", valueCode=str(verification.stage_reached)),
        Extension(url="evidenceText", valueString=evidence.text),
        Extension(url="sourcePage", valueInteger=int(evidence.page_number)),
        Extension(url="sourceBoundingBox", valueString=bbox_str),
    ]
    if evidence.ocr_confidence is not None:
        sub_exts.append(
            Extension(
                url="ocrConfidence",
                valueDecimal=round(Decimal(str(evidence.ocr_confidence)), 4),
            )
        )
    return Extension(url=EXT_CLAIM_PROVENANCE, extension=sub_exts)


def build_element_provenance_extension(
    claim: Claim,
    verification: VerificationResult,
    evidence: Evidence,
) -> Extension:
    """Construct constituent element-level provenance sub-extension conforming to DEC-016 & §16.2.

    URL: http://clindoc.org/fhir/StructureDefinition/claim-provenance
    Carries: claimId, verificationId, verificationLabel, confidence, evidenceText.
    """
    conf_val = round(Decimal(str(verification.confidence)), 4)
    sub_exts = [
        Extension(url="claimId", valueString=claim.claim_id),
        Extension(url="verificationId", valueString=verification.verification_id),
        Extension(url="verificationLabel", valueCode=str(verification.label)),
        Extension(url="confidence", valueDecimal=conf_val),
        Extension(url="evidenceText", valueString=evidence.text),
    ]
    return Extension(url=EXT_CLAIM_PROVENANCE, extension=sub_exts)


def claims_to_fhir_bundle(
    claims: Sequence[Claim],
    evidence_map: dict[str, Evidence],
    verification_map: dict[str, VerificationResult],
    document_id: Optional[str] = None,
    document_type: Optional[str] = None,
    patient_id: Optional[str] = None,
    patient_name: Optional[str] = None,
) -> Bundle:
    """Map canonical clinical claims and verifications into a compliant FHIR R4B Bundle.

    Authority:
      - PROJECT_SPEC §5 #10, §7.7, §15, §16
      - DEC-008: Hybrid MedicationRequest vs MedicationStatement routing
      - DEC-016: Two-tier provenance hierarchy (resource-level & element-level)

    Args:
        claims: Sequence of extracted and normalized Claim objects.
        evidence_map: Mapping from evidence_id -> Evidence.
        verification_map: Mapping from claim_id -> VerificationResult.
        document_id: Identifier of source document (defaults to claims[0].document_id).
        document_type: Source document layout type (e.g. clinic_rx_pad, mar_order_sheet, etc.).
        patient_id: Optional patient MRN/ID override.
        patient_name: Optional patient name string override.

    Returns:
        FHIR R4B Bundle of type 'collection' carrying resources and provenance extensions.
    """
    if not claims:
        raise ValueError("claims sequence must not be empty")

    resolved_doc_id = document_id or claims[0].document_id
    doc_slug = _sanitize_id(resolved_doc_id.lower())

    # 1. Validation of inputs (§0.3, §5 Component 10)
    for c in claims:
        if c.claim_id not in verification_map:
            raise ValueError(
                f"Claim '{c.claim_id}' has no corresponding VerificationResult in verification_map "
                f"(PROJECT_SPEC §5 Component 10)"
            )
        if c.evidence_id not in evidence_map:
            raise ValueError(
                f"Claim '{c.claim_id}' references missing Evidence '{c.evidence_id}' in evidence_map"
            )

    # 2. Partition claims into Lab and Medication groups
    lab_groups: dict[str, list[Claim]] = {}
    med_groups: dict[str, list[Claim]] = {}

    for c in claims:
        grp_key = c.group_id if c.group_id else f"auto-{c.field_type}-{c.claim_id}"
        if c.field_type.startswith("lab."):
            lab_groups.setdefault(grp_key, []).append(c)
        elif c.field_type.startswith("med."):
            med_groups.setdefault(grp_key, []).append(c)
        else:
            raise ValueError(f"Unsupported claim field_type: {c.field_type}")

    # 3. Patient, Organization, Practitioner
    pat_id_str = _sanitize_id(patient_id or f"patient-{doc_slug}")
    patient = Patient(
        id=pat_id_str,
        identifier=[
            Identifier(
                system=IDENTIFIER_MRN_SYSTEM,
                value=patient_id or f"SYNTHETIC-PATIENT-{doc_slug.upper()[:12]}",
            )
        ],
        name=[HumanName(text=patient_name or "Synthetic Patient")],
    )

    org_id_str = f"org-{doc_slug}"
    organization = Organization(
        id=org_id_str,
        name="Clinical Healthcare Organization",
    )

    pract_id_str = f"pract-{doc_slug}"
    practitioner = Practitioner(
        id=pract_id_str,
        name=[HumanName(text="Attending Physician")],
    )

    # 4. Map Lab Observation resources
    observations: list[Observation] = []
    for grp_idx, (grp_key, grp) in enumerate(lab_groups.items()):
        val_claim = next((c for c in grp if c.field_type == "lab.value"), None)
        name_claim = next((c for c in grp if c.field_type == "lab.test_name"), None)
        unit_claim = next((c for c in grp if c.field_type == "lab.unit"), None)
        flag_claim = next((c for c in grp if c.field_type == "lab.flag"), None)
        ref_claim = next((c for c in grp if c.field_type == "lab.reference_range"), None)

        anchor_claim = val_claim or name_claim or grp[0]
        anchor_ver = verification_map[anchor_claim.claim_id]
        anchor_ev = evidence_map[anchor_claim.evidence_id]

        res_provenance = build_resource_provenance_extension(anchor_claim, anchor_ver, anchor_ev)
        obs_status = "final" if anchor_ver.label == "SUPPORTED" else "preliminary"

        # Code & element-level provenance for test_name
        loinc_claim = (
            name_claim
            or next((c for c in grp if c.normalized_code and c.code_system == "LOINC"), None)
            or (anchor_claim if anchor_claim.code_system == "LOINC" else None)
        )
        test_display = (
            name_claim.value
            if name_claim
            else (
                anchor_claim.value
                if anchor_claim.field_type == "lab.test_name"
                else (
                    loinc_claim.value.split(":")[0].replace(" flag", "").strip()
                    if loinc_claim and loinc_claim.value
                    else "Laboratory Observation"
                )
            )
        )
        codings = []
        if loinc_claim and loinc_claim.normalized_code and loinc_claim.code_system == "LOINC":
            codings.append(
                Coding(system=LOINC_SYSTEM, code=loinc_claim.normalized_code, display=test_display)
            )

        name_exts = []
        if name_claim:
            name_exts.append(
                build_element_provenance_extension(
                    name_claim, verification_map[name_claim.claim_id], evidence_map[name_claim.evidence_id]
                )
            )
        obs_code = CodeableConcept(
            coding=codings if codings else None,
            text=test_display,
            extension=name_exts if name_exts else None,
        )

        # Value Quantity & element-level provenance for value/unit
        val_quantity = None
        if val_claim:
            num_val = val_claim.value_numeric
            if num_val is None:
                m = re.search(r"[-+]?\d*\.?\d+", val_claim.value)
                num_val = float(m.group()) if m else 0.0

            unit_str = unit_claim.value if unit_claim else (val_claim.unit_raw or None)
            ucum_sys = None
            ucum_code = None
            if unit_claim and unit_claim.normalized_code and unit_claim.code_system == "UCUM":
                ucum_sys = UCUM_SYSTEM
                ucum_code = unit_claim.normalized_code
            elif unit_str:
                ucum_sys = UCUM_SYSTEM
                ucum_code = unit_str.strip()

            vq_exts = [
                build_element_provenance_extension(
                    val_claim, verification_map[val_claim.claim_id], evidence_map[val_claim.evidence_id]
                )
            ]
            if unit_claim:
                vq_exts.append(
                    build_element_provenance_extension(
                        unit_claim, verification_map[unit_claim.claim_id], evidence_map[unit_claim.evidence_id]
                    )
                )

            val_quantity = Quantity(
                value=round(Decimal(str(num_val)), 4),
                unit=unit_str,
                system=ucum_sys,
                code=ucum_code,
                extension=vq_exts,
            )

        # Interpretation & element-level provenance for flag
        interpretations = None
        if flag_claim:
            flag_upper = flag_claim.value.strip().upper()
            if flag_upper in ("H", "HIGH"):
                f_code, f_disp = "H", "High"
            elif flag_upper in ("L", "LOW"):
                f_code, f_disp = "L", "Low"
            elif flag_upper in ("A", "CRITICAL", "ABNORMAL", "PANIC"):
                f_code, f_disp = "A", "Abnormal"
            elif flag_upper in ("N", "NORMAL", "NORM"):
                f_code, f_disp = "N", "Normal"
            else:
                f_code, f_disp = flag_upper, flag_claim.value

            flag_ext = build_element_provenance_extension(
                flag_claim, verification_map[flag_claim.claim_id], evidence_map[flag_claim.evidence_id]
            )
            interpretations = [
                CodeableConcept(
                    coding=[
                        Coding(
                            system=OBS_INTERPRETATION_SYSTEM,
                            code=f_code,
                            display=f_disp,
                        )
                    ],
                    text=flag_claim.value,
                    extension=[flag_ext],
                )
            ]

        # Reference range & element-level provenance
        ref_ranges = None
        if ref_claim:
            ref_ext = build_element_provenance_extension(
                ref_claim, verification_map[ref_claim.claim_id], evidence_map[ref_claim.evidence_id]
            )
            obs_ref = ObservationReferenceRange(
                text=ref_claim.value,
                extension=[ref_ext],
            )
            parts = re.split(r"\s*-\s*", ref_claim.value.strip())
            if len(parts) == 2:
                try:
                    low_val = float(re.search(r"[-+]?\d*\.?\d+", parts[0]).group())
                    high_val = float(re.search(r"[-+]?\d*\.?\d+", parts[1]).group())
                    u = unit_claim.value if unit_claim else None
                    obs_ref.low = Quantity(value=round(Decimal(str(low_val)), 4), unit=u)
                    obs_ref.high = Quantity(value=round(Decimal(str(high_val)), 4), unit=u)
                except Exception:
                    pass
            ref_ranges = [obs_ref]

        obs_id = f"obs-{doc_slug}-{grp_idx + 1}"
        obs_res = Observation(
            id=obs_id,
            status=obs_status,
            category=[
                CodeableConcept(
                    coding=[
                        Coding(
                            system=OBS_CATEGORY_SYSTEM,
                            code="vital-signs" if test_display in VITAL_SIGN_NAMES else "laboratory",
                            display="Vital Signs" if test_display in VITAL_SIGN_NAMES else "Laboratory",
                        )
                    ]
                )
            ],
            code=obs_code,
            subject=Reference(reference=f"Patient/{patient.id}"),
            valueQuantity=val_quantity,
            interpretation=interpretations,
            referenceRange=ref_ranges,
            extension=[res_provenance],
        )
        observations.append(obs_res)

    # 5. Map Medication resources (DEC-008)
    med_resources: list[MedicationRequest | MedicationStatement] = []
    rx_doc_types = {"clinic_rx_pad", "mar_order_sheet", "pharmacy_label", "prescription", "order"}
    is_rx_doc = bool(document_type and document_type.strip().lower() in rx_doc_types)

    for grp_idx, (grp_key, grp) in enumerate(med_groups.items()):
        name_claim = next((c for c in grp if c.field_type == "med.name"), None)
        dose_claim = next((c for c in grp if c.field_type == "med.dose"), None)
        route_claim = next((c for c in grp if c.field_type == "med.route"), None)
        freq_claim = next((c for c in grp if c.field_type == "med.frequency"), None)
        dur_claim = next((c for c in grp if c.field_type == "med.duration"), None)

        anchor_claim = name_claim or dose_claim or grp[0]
        anchor_ver = verification_map[anchor_claim.claim_id]
        anchor_ev = evidence_map[anchor_claim.evidence_id]

        res_provenance = build_resource_provenance_extension(anchor_claim, anchor_ver, anchor_ev)

        # Hybrid routing rule per DEC-008
        has_planned = any(c.temporal_status == "planned" for c in grp)
        is_request = is_rx_doc or has_planned

        temp_status = next(
            (c.temporal_status for c in grp if c.temporal_status != "unspecified"),
            anchor_claim.temporal_status,
        )

        # Medication code & element-level provenance
        rx_claim = (
            name_claim
            or next((c for c in grp if c.normalized_code and c.code_system == "RxNorm"), None)
            or (anchor_claim if anchor_claim.code_system == "RxNorm" else None)
        )
        if name_claim:
            med_display = name_claim.value
        elif rx_claim and rx_claim.value:
            prefix = rx_claim.value.split(":")[0].replace(" dose", "").replace(" route", "").replace(" frequency", "").replace("Medication: ", "").strip()
            med_display = prefix if prefix else "Unspecified Medication"
        else:
            med_display = "Unspecified Medication"

        codings = []
        if rx_claim and rx_claim.normalized_code and rx_claim.code_system == "RxNorm":
            codings.append(
                Coding(system=RXNORM_SYSTEM, code=rx_claim.normalized_code, display=med_display)
            )

        name_exts = []
        if name_claim:
            name_exts.append(
                build_element_provenance_extension(
                    name_claim, verification_map[name_claim.claim_id], evidence_map[name_claim.evidence_id]
                )
            )
        med_code = CodeableConcept(
            coding=codings if codings else None,
            text=med_display,
            extension=name_exts if name_exts else None,
        )

        # Dosage construction with element-level provenance
        dose_and_rate = None
        if dose_claim:
            d_val = dose_claim.value_numeric
            if d_val is None:
                m = re.search(r"[-+]?\d*\.?\d+", dose_claim.value)
                d_val = float(m.group()) if m else 0.0

            dose_ext = build_element_provenance_extension(
                dose_claim, verification_map[dose_claim.claim_id], evidence_map[dose_claim.evidence_id]
            )
            dose_ucum_sys = None
            dose_ucum_code = None
            if dose_claim.unit_raw:
                dose_ucum_sys = UCUM_SYSTEM
                u_clean = dose_claim.unit_raw.strip()
                if u_clean == "units":
                    dose_ucum_code = "[iU]"
                else:
                    dose_ucum_code = u_clean

            dose_qty = Quantity(
                value=round(Decimal(str(d_val)), 4),
                unit=dose_claim.unit_raw or None,
                system=dose_ucum_sys,
                code=dose_ucum_code,
                extension=[dose_ext],
            )
            dose_and_rate = [DosageDoseAndRate(doseQuantity=dose_qty)]

        route_cc = None
        if route_claim:
            route_ext = build_element_provenance_extension(
                route_claim, verification_map[route_claim.claim_id], evidence_map[route_claim.evidence_id]
            )
            route_cc = CodeableConcept(text=route_claim.value, extension=[route_ext])

        timing_obj = None
        if freq_claim or dur_claim:
            timing_code = None
            t_exts = []
            if freq_claim:
                freq_ext = build_element_provenance_extension(
                    freq_claim, verification_map[freq_claim.claim_id], evidence_map[freq_claim.evidence_id]
                )
                timing_code = CodeableConcept(text=freq_claim.value, extension=[freq_ext])
            if dur_claim:
                dur_ext = build_element_provenance_extension(
                    dur_claim, verification_map[dur_claim.claim_id], evidence_map[dur_claim.evidence_id]
                )
                t_exts.append(dur_ext)
            timing_obj = Timing(code=timing_code, extension=t_exts if t_exts else None)

        dosage_parts = []
        if name_claim:
            dosage_parts.append(name_claim.value)
        if dose_claim:
            dosage_parts.append(f"{dose_claim.value} {dose_claim.unit_raw or ''}".strip())
        if route_claim:
            dosage_parts.append(route_claim.value)
        if freq_claim:
            dosage_parts.append(freq_claim.value)
        if dur_claim:
            dosage_parts.append(f"for {dur_claim.value}")
        dosage_text = " ".join(dosage_parts).strip() or "Standard dosage instructions"

        dosage = Dosage(
            text=dosage_text,
            route=route_cc,
            doseAndRate=dose_and_rate,
            timing=timing_obj,
        )

        if is_request:
            if temp_status == "discontinued":
                req_status = "stopped"
            elif temp_status == "historical":
                req_status = "completed"
            elif temp_status == "planned":
                req_status = "active"
            else:
                req_status = "active"
            req_intent = "plan" if temp_status == "planned" else "order"

            med_res = MedicationRequest(
                id=f"medreq-{doc_slug}-{grp_idx + 1}",
                status=req_status,
                intent=req_intent,
                subject=Reference(reference=f"Patient/{patient.id}"),
                medicationCodeableConcept=med_code,
                dosageInstruction=[dosage],
                extension=[res_provenance],
            )
        else:
            if temp_status == "discontinued":
                stat_status = "stopped"
            elif temp_status == "historical":
                stat_status = "completed"
            elif temp_status == "planned":
                stat_status = "intended"
            else:
                stat_status = "active"

            med_res = MedicationStatement(
                id=f"medstat-{doc_slug}-{grp_idx + 1}",
                status=stat_status,
                subject=Reference(reference=f"Patient/{patient.id}"),
                medicationCodeableConcept=med_code,
                dosage=[dosage],
                extension=[res_provenance],
            )
        med_resources.append(med_res)

    # 6. DiagnosticReport (if lab observations present)
    diag_report = None
    if observations:
        diag_report = DiagnosticReport(
            id=f"report-{doc_slug}",
            status="final",
            category=[
                CodeableConcept(
                    coding=[
                        Coding(
                            system=DIAG_CATEGORY_SYSTEM,
                            code="LAB",
                            display="Laboratory",
                        )
                    ],
                    text="Laboratory",
                )
            ],
            code=CodeableConcept(text="Clinical Laboratory Report"),
            subject=Reference(reference=f"Patient/{patient.id}"),
            performer=[Reference(reference=f"Organization/{organization.id}")],
            result=[Reference(reference=f"Observation/{obs.id}") for obs in observations],
            extension=[observations[0].extension[0]] if observations[0].extension else None,
        )

    # 7. Assemble Bundle
    entries = [
        BundleEntry(fullUrl=f"Patient/{patient.id}", resource=patient),
        BundleEntry(fullUrl=f"Organization/{organization.id}", resource=organization),
        BundleEntry(fullUrl=f"Practitioner/{practitioner.id}", resource=practitioner),
    ]

    if diag_report:
        entries.append(BundleEntry(fullUrl=f"DiagnosticReport/{diag_report.id}", resource=diag_report))

    for obs in observations:
        entries.append(BundleEntry(fullUrl=f"Observation/{obs.id}", resource=obs))

    for med in med_resources:
        entries.append(BundleEntry(fullUrl=f"{med.resource_type}/{med.id}", resource=med))

    return Bundle(
        id=f"bundle-{doc_slug}",
        type="collection",
        entry=entries,
    )
