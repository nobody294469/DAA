"""Mapper converting extracted & verified clinical claims into FHIR R4B Bundles."""

import base64
import datetime
from decimal import Decimal
import hashlib
import json
import logging
import os
import re
import subprocess
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

from fhir.resources.R4B.annotation import Annotation
from fhir.resources.R4B.attachment import Attachment
from fhir.resources.R4B.bundle import Bundle, BundleEntry
from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.device import Device, DeviceDeviceName, DeviceProperty, DeviceVersion
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference, DocumentReferenceContent
from fhir.resources.R4B.dosage import Dosage, DosageDoseAndRate
from fhir.resources.R4B.duration import Duration
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.extension import Extension
from fhir.resources.R4B.humanname import HumanName
from fhir.resources.R4B.identifier import Identifier
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation, ObservationReferenceRange
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.period import Period
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.provenance import Provenance, ProvenanceAgent, ProvenanceEntity
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference
from fhir.resources.R4B.timing import Timing, TimingRepeat

from clindoc.extract.contracts import Claim, Evidence
from clindoc.fhir.constants import (
    ACT_CODE_SYSTEM,
    ALL_TEMPORAL_STATUS_CODES,
    DIAG_CATEGORY_SYSTEM,
    DIAGNOSTIC_REPORT_STATUSES,
    DOC_CATEGORY_SYSTEM,
    EXT_CLAIM_PROVENANCE,
    EXT_COMPETING_CANDIDATE,
    EXT_SOURCE_EVIDENCE,
    EXT_TEMPORAL_STATUS,
    EXT_VERIFICATION_DISCREPANCY,
    EXT_VERIFICATION_STATUS,
    IDENTIFIER_MRN_SYSTEM,
    INTERPRETATION_HL7_MAP,
    LOINC_SYSTEM,
    MEDICATION_REQUEST_TEMPORAL_STATUSES,
    MEDICATION_STATEMENT_TEMPORAL_STATUSES,
    OBS_CATEGORY_SYSTEM,
    OBS_INTERPRETATION_SYSTEM,
    OBSERVATION_TEMPORAL_STATUSES,
    QUALITATIVE_SNOMED_MAP,
    RXNORM_SYSTEM,
    SNOMED_SYSTEM,
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
    if hasattr(claim, "review_state") and claim.review_state is not None:
        rs_str = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
        sub_exts.append(Extension(url="reviewState", valueCode=rs_str))
    if hasattr(claim, "model_run_id") and claim.model_run_id is not None:
        sub_exts.append(Extension(url="modelRunId", valueString=str(claim.model_run_id)))

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


def _map_document_reference(
    document_id: str,
    patient_id: str,
    doc_metadata: Optional[dict[str, Any]] = None,
) -> DocumentReference:
    """Map source clinical document metadata into a FHIR R4B DocumentReference resource."""
    meta = doc_metadata or {}
    doc_slug = _sanitize_id(document_id.lower())
    docref_id = f"docref-{doc_slug}"

    mime_type = meta.get("mime_type") or "application/pdf"
    filename = meta.get("original_filename") or meta.get("filename") or f"{doc_slug}.pdf"
    storage_ref = meta.get("storage_ref")

    # Ingested byte hash handling (Phase 4.3):
    # Compute SHA-256 directly from exact source bytes if provided;
    # or convert verified pre-screened sha256 hex string to base64Binary.
    # If no source bytes or pre-screened hash exists, do not fabricate!
    source_bytes = meta.get("source_bytes") or meta.get("raw_bytes")
    sha256_hash = meta.get("sha256")

    attachment_hash = None
    if source_bytes and isinstance(source_bytes, bytes):
        raw_digest = hashlib.sha256(source_bytes).digest()
        attachment_hash = base64.b64encode(raw_digest).decode("ascii")
    elif sha256_hash and isinstance(sha256_hash, (str, bytes)):
        if isinstance(sha256_hash, bytes) and len(sha256_hash) in (20, 32):
            attachment_hash = base64.b64encode(sha256_hash).decode("ascii")
        elif isinstance(sha256_hash, str):
            clean_hash = sha256_hash.strip()
            if len(clean_hash) in (40, 64):
                try:
                    raw_digest = bytes.fromhex(clean_hash)
                    attachment_hash = base64.b64encode(raw_digest).decode("ascii")
                except ValueError:
                    attachment_hash = None
            else:
                try:
                    raw_digest = base64.b64decode(clean_hash, validate=True)
                    if len(raw_digest) in (20, 32):
                        attachment_hash = clean_hash
                    else:
                        attachment_hash = None
                except Exception:
                    attachment_hash = None

    # Clinical authoring / document creation date belongs on Attachment.creation, NOT DocumentReference.date!
    doc_creation_raw = meta.get("doc_date") or meta.get("creation_date") or meta.get("authored_at")
    attachment_creation = None
    if doc_creation_raw and isinstance(doc_creation_raw, str):
        try:
            attachment_creation = datetime.datetime.fromisoformat(doc_creation_raw)
            if not attachment_creation.tzinfo:
                attachment_creation = attachment_creation.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            attachment_creation = None
    elif doc_creation_raw and isinstance(doc_creation_raw, datetime.datetime):
        attachment_creation = doc_creation_raw if doc_creation_raw.tzinfo else doc_creation_raw.replace(tzinfo=datetime.timezone.utc)

    attachment = Attachment(
        contentType=mime_type,
        title=filename,
        url=storage_ref,
        hash=attachment_hash,
        creation=attachment_creation,
    )

    content = [DocumentReferenceContent(attachment=attachment)]

    # DocumentReference.date represents when this reference was registered/indexed in the repository.
    # It must NOT be silently conflated with clinical authoring or document creation date.
    ref_date_raw = (
        meta.get("uploaded_at")
        or meta.get("indexed_at")
        or meta.get("reference_date")
        or meta.get("registered_at")
    )
    if ref_date_raw and isinstance(ref_date_raw, str):
        try:
            doc_datetime = datetime.datetime.fromisoformat(ref_date_raw)
            if not doc_datetime.tzinfo:
                doc_datetime = doc_datetime.replace(tzinfo=datetime.timezone.utc)
        except Exception:
            doc_datetime = datetime.datetime.now(datetime.timezone.utc)
    elif ref_date_raw and isinstance(ref_date_raw, datetime.datetime):
        doc_datetime = ref_date_raw if ref_date_raw.tzinfo else ref_date_raw.replace(tzinfo=datetime.timezone.utc)
    else:
        # Dynamic UTC instant when document reference is created; never a hardcoded historical static date!
        doc_datetime = datetime.datetime.now(datetime.timezone.utc)

    return DocumentReference(
        id=docref_id,
        status="current",
        subject=Reference(reference=f"Patient/{patient_id}"),
        date=doc_datetime,
        category=[
            CodeableConcept(
                coding=[
                    Coding(
                        system=DOC_CATEGORY_SYSTEM,
                        code="clinical-note",
                        display="Clinical Note",
                    )
                ],
                text="Clinical Document",
            )
        ],
        content=content,
    )


def _map_encounter(
    document_id: str,
    patient_id: str,
    encounter_info: Optional[dict[str, Any]] = None,
) -> Optional[Encounter]:
    """Map encounter information into a FHIR R4B Encounter resource if metadata is present.

    If encounter metadata is absent or incomplete, returns None (never fabricate!).
    """
    if not encounter_info:
        return None

    enc_id_val = encounter_info.get("encounter_id") or encounter_info.get("id")
    if not enc_id_val:
        return None

    doc_slug = _sanitize_id(document_id.lower())
    enc_slug = _sanitize_id(str(enc_id_val).lower())
    enc_id = f"enc-{enc_slug}" if enc_slug != "unknown" else f"enc-{doc_slug}"

    enc_class_code = encounter_info.get("encounter_class") or encounter_info.get("class") or "AMB"
    class_display_map = {
        "AMB": "ambulatory",
        "IMP": "inpatient encounter",
        "EMER": "emergency",
        "VR": "virtual",
        "HH": "home health",
    }
    class_disp = class_display_map.get(str(enc_class_code).upper(), "ambulatory")

    enc_status = encounter_info.get("status") or "finished"
    if enc_status not in ("planned", "arrived", "triaged", "in-progress", "onleave", "finished", "cancelled"):
        enc_status = "finished"

    return Encounter(
        id=enc_id,
        status=enc_status,
        class_fhir=Coding(
            system=ACT_CODE_SYSTEM,
            code=str(enc_class_code).upper(),
            display=class_disp,
        ),
        subject=Reference(reference=f"Patient/{patient_id}"),
    )


def _get_git_commit_sha() -> Optional[str]:
    """Dynamically obtain git commit SHA if available at runtime, without hardcoding or failing."""
    env_sha = os.environ.get("GIT_COMMIT_SHA")
    if env_sha and len(env_sha.strip()) >= 7:
        return env_sha.strip()
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        )
        if res.returncode == 0:
            raw_sha = res.stdout.strip()
            if len(raw_sha) == 40 and all(c in "0123456789abcdefABCDEF" for c in raw_sha):
                return raw_sha
    except Exception:
        pass
    return None


def _build_pipeline_device(
    doc_slug: str,
    pipeline_info: Optional[dict[str, Any]] = None,
) -> Device:
    """Construct genuine FHIR R4B Device resource representing the ClinicalDoc pipeline."""
    info = pipeline_info or {}
    device_id = info.get("device_id") or f"device-clindoc-{doc_slug}"

    versions = []
    # 1. Pipeline version
    pipe_ver = info.get("pipeline_version") or "1.0.0"
    versions.append(DeviceVersion(value=str(pipe_ver)))

    # 2. Git commit SHA (dynamic, omitted if unavailable)
    commit_sha = info.get("git_commit_sha")
    if commit_sha is None and "git_commit_sha" not in info:
        commit_sha = _get_git_commit_sha()
    if commit_sha and str(commit_sha).strip():
        versions.append(
            DeviceVersion(
                type=CodeableConcept(text="git-commit"),
                value=str(commit_sha).strip(),
            )
        )

    # 3. Model / checkpoint properties
    properties = []
    models = info.get("models")
    if models is None and "models" not in info:
        models = {
            "deterministic": "Regex & Rule Catalog v1.0",
            "gliner": "Ihor/gliner-biomed-base-v1.0",
            "stage_b": "BioClinical-ModernBERT",
            "stage_c": "Qwen2.5-7B-Instruct-AWQ",
        }
    if models and isinstance(models, dict):
        for mod_key, mod_val in models.items():
            if mod_val:
                properties.append(
                    DeviceProperty(
                        type=CodeableConcept(text=f"model.{mod_key}"),
                        valueCode=[CodeableConcept(text=str(mod_val))],
                    )
                )

    return Device(
        id=device_id,
        status="active",
        deviceName=[
            DeviceDeviceName(
                name=info.get("pipeline_name") or "ClinicalDoc Extraction & Verification Pipeline",
                type="user-friendly-name",
            )
        ],
        version=versions if versions else None,
        property=properties if properties else None,
    )


def _map_provenance(
    document_id: str,
    target_references: list[Reference],
    device_id: str,
    doc_ref_id: Optional[str] = None,
    author_practitioner_id: Optional[str] = None,
    pipeline_started_at: Optional[datetime.datetime] = None,
    pipeline_completed_at: Optional[datetime.datetime] = None,
    recorded_at: Optional[datetime.datetime] = None,
) -> Provenance:
    """Construct top-level FHIR R4B extraction Provenance linking targets to Device assembler and source DocumentReference."""
    doc_slug = _sanitize_id(document_id.lower())
    prov_id = f"prov-{doc_slug}"

    agents = [
        ProvenanceAgent(
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        code="assembler",
                        display="Assembler",
                    )
                ]
            ),
            who=Reference(reference=f"Device/{device_id}"),
        )
    ]

    if author_practitioner_id:
        agents.append(
            ProvenanceAgent(
                type=CodeableConcept(
                    coding=[
                        Coding(
                            system="http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                            code="author",
                            display="Author",
                        )
                    ]
                ),
                who=Reference(reference=f"Practitioner/{author_practitioner_id}"),
            )
        )

    entities = []
    if doc_ref_id:
        entities.append(
            ProvenanceEntity(
                role="source",
                what=Reference(reference=f"DocumentReference/{doc_ref_id}"),
            )
        )

    occ_period = None
    occ_dt = None
    if pipeline_started_at and pipeline_completed_at:
        start_tz = pipeline_started_at if pipeline_started_at.tzinfo else pipeline_started_at.replace(tzinfo=datetime.timezone.utc)
        end_tz = pipeline_completed_at if pipeline_completed_at.tzinfo else pipeline_completed_at.replace(tzinfo=datetime.timezone.utc)
        occ_period = Period(start=start_tz, end=end_tz)
    elif pipeline_completed_at:
        occ_dt = pipeline_completed_at if pipeline_completed_at.tzinfo else pipeline_completed_at.replace(tzinfo=datetime.timezone.utc)

    rec_time = recorded_at or datetime.datetime.now(datetime.timezone.utc)
    if not rec_time.tzinfo:
        rec_time = rec_time.replace(tzinfo=datetime.timezone.utc)

    return Provenance(
        id=prov_id,
        target=target_references,
        occurredPeriod=occ_period,
        occurredDateTime=occ_dt,
        recorded=rec_time,
        activity=CodeableConcept(
            coding=[
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                    code="CREATE",
                    display="Created",
                )
            ],
            text="Automated Extraction & Verification",
        ),
        agent=agents,
        entity=entities if entities else None,
    )


def _map_review_provenance(
    document_id: str,
    target_references: list[Reference],
    reviewer_practitioner_id: str,
    review_decision: str,
    reviewed_at: Optional[datetime.datetime] = None,
    recorded_at: Optional[datetime.datetime] = None,
) -> Provenance:
    """Construct top-level FHIR R4B human review Provenance linking targets to Reviewer Practitioner."""
    doc_slug = _sanitize_id(document_id.lower())
    prov_id = f"prov-review-{doc_slug}"

    agents = [
        ProvenanceAgent(
            type=CodeableConcept(
                coding=[
                    Coding(
                        system="http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        code="verifier",
                        display="Verifier",
                    )
                ]
            ),
            who=Reference(reference=f"Practitioner/{reviewer_practitioner_id}"),
        )
    ]

    occ_dt = None
    if reviewed_at:
        occ_dt = reviewed_at if reviewed_at.tzinfo else reviewed_at.replace(tzinfo=datetime.timezone.utc)

    rec_time = recorded_at or datetime.datetime.now(datetime.timezone.utc)
    if not rec_time.tzinfo:
        rec_time = rec_time.replace(tzinfo=datetime.timezone.utc)

    return Provenance(
        id=prov_id,
        target=target_references,
        occurredDateTime=occ_dt,
        recorded=rec_time,
        activity=CodeableConcept(
            coding=[
                Coding(
                    system="http://terminology.hl7.org/CodeSystem/v3-DocumentCompletion",
                    code="AU",
                    display="Authenticated",
                )
            ],
            text=f"Human Review: {review_decision}",
        ),
        agent=agents,
    )


LIFECYCLE_HEADER_REGEX = re.compile(
    r"(?:^|\n)\s*(?:report\s+status|diagnostic\s+(?:report\s+)?status|result\s+status|lab\s+status|status)\s*[:=-]\s*"
    r"(final|preliminary|partial|amended|corrected|appended|cancelled|registered)\b",
    re.IGNORECASE,
)

EXPLICIT_REPORT_TITLE_REGEX = re.compile(
    r"(?:^|\n)\s*(final|preliminary|partial|amended|corrected|appended|cancelled)\s+(?:laboratory\s+|diagnostic\s+)?report\b",
    re.IGNORECASE,
)

VALID_LIFECYCLE_STATUSES = {"preliminary", "final", "amended", "corrected", "cancelled", "unknown"}

NARRATIVE_DOC_TYPES = {
    "narrative_note",
    "discharge_summary",
    "clinic_note",
    "clinical_note",
    "progress_note",
    "consult_note",
    "history_and_physical",
    "hp_note",
    "soap_note",
    "emergency_department_note",
    "ed_note",
}


def resolve_observation_lifecycle_status(
    document_metadata: Optional[dict[str, Any]] = None,
    document_type: Optional[str] = None,
    evidence_texts: Optional[Sequence[str]] = None,
) -> str:
    """Derive Observation.status strictly from grounded source-document lifecycle information.

    Authority:
      - Phase 4.0.1, Phase 4.0.2, Phase 4.2
      - HL7 FHIR R4B Observation.status value set:
        preliminary | final | amended | corrected | cancelled | unknown

    Rules:
      - Explicit preliminary report -> preliminary
      - Explicit partial report -> preliminary (Observation uses preliminary for interim/partial results)
      - Explicit final/signed completed diagnostic report -> final
      - Explicit amended report -> amended
      - Explicit corrected report -> corrected
      - Explicit cancelled report -> cancelled
      - No reliable lifecycle information -> unknown

    Critical Invariants:
      - NEVER derive Observation.status from verification verdicts (SUPPORTED/DISCREPANCY).
      - NEVER derive from confidence, stage reached, or human review state (ACCEPTED/EDITED/PENDING).
      - Do not assume every signed clinical note is a final laboratory report.
      - Do not infer final status merely because a document contains a result.
      - Bare narrative notes without an explicit diagnostic report status header remain 'unknown'.
    """
    # 1. Observation's own grounded evidence text takes highest precedence
    if evidence_texts:
        for txt in evidence_texts:
            if not txt:
                continue
            m = LIFECYCLE_HEADER_REGEX.search(txt)
            if m:
                status_word = m.group(1).lower()
                if status_word == "partial":
                    return "preliminary"
                if status_word in VALID_LIFECYCLE_STATUSES:
                    return status_word

            m_title = EXPLICIT_REPORT_TITLE_REGEX.search(txt)
            if m_title:
                status_word = m_title.group(1).lower()
                if status_word == "partial":
                    return "preliminary"
                if status_word in VALID_LIFECYCLE_STATUSES:
                    return status_word

    # 2. Observation-specific metadata override
    if document_metadata:
        obs_meta = document_metadata.get("observation_status")
        if isinstance(obs_meta, str):
            norm_val = obs_meta.strip().lower()
            if norm_val == "partial":
                return "preliminary"
            if norm_val in VALID_LIFECYCLE_STATUSES:
                return norm_val

    # 3. Report-level metadata inspection
    report_status = None
    if document_metadata:
        for key in ("report_status", "lifecycle_status", "diagnostic_report_status", "status"):
            val = document_metadata.get(key)
            if isinstance(val, str):
                report_status = val.strip().lower()
                break

    # If report status is 'partial' (or partial document type), child observations
    # MUST NOT be forced to preliminary. Observations lacking explicit lifecycle evidence remain 'unknown'.
    doc_type_clean = (document_type or "").strip().lower()
    if report_status == "partial" or doc_type_clean in ("partial_lab_report", "partial_report", "partial_diagnostic_report"):
        return "unknown"

    # Other non-partial report-level explicit statuses apply if present
    if report_status and report_status in VALID_LIFECYCLE_STATUSES and report_status != "unknown":
        return report_status

    # Signed check on completed diagnostic/lab reports only
    if document_metadata:
        is_signed = bool(document_metadata.get("signed") or document_metadata.get("is_signed"))
        if is_signed and doc_type_clean not in NARRATIVE_DOC_TYPES:
            return "final"

    # Document type explicit report status
    if doc_type_clean in ("preliminary_lab_report", "preliminary_report"):
        return "preliminary"
    elif doc_type_clean in ("final_lab_report", "final_diagnostic_report"):
        return "final"
    elif doc_type_clean in ("amended_lab_report", "amended_report"):
        return "amended"
    elif doc_type_clean in ("corrected_lab_report", "corrected_report"):
        return "corrected"
    elif doc_type_clean in ("cancelled_lab_report", "cancelled_report"):
        return "cancelled"

    # 4. Bare narrative or absence of reliable report-level lifecycle information -> unknown
    return "unknown"


def resolve_diagnostic_report_lifecycle_status(
    document_metadata: Optional[dict[str, Any]] = None,
    document_type: Optional[str] = None,
    evidence_texts: Optional[Sequence[str]] = None,
) -> str:
    """Derive DiagnosticReport.status strictly from grounded source-document lifecycle information.

    Authority: Phase 4.4.1 §1
    FHIR R4B DiagnosticReport.status value set:
      registered | partial | preliminary | final | amended | corrected | appended | cancelled | entered-in-error | unknown

    Critical Invariants:
      - NEVER infer status from extraction progress or verification state.
      - NEVER infer 'partial' merely because extraction is incomplete or partially verified.
      - Map 'partial' ONLY when explicitly grounded in source lifecycle evidence.
    """
    if document_metadata:
        for key in (
            "report_status",
            "lifecycle_status",
            "diagnostic_report_status",
            "status",
        ):
            val = document_metadata.get(key)
            if isinstance(val, str):
                norm_val = val.strip().lower()
                if norm_val in DIAGNOSTIC_REPORT_STATUSES:
                    return norm_val

        is_signed = bool(document_metadata.get("signed") or document_metadata.get("is_signed"))
        doc_type_clean = (document_type or "").strip().lower()
        if is_signed and doc_type_clean not in NARRATIVE_DOC_TYPES:
            return "final"

    doc_type_clean = (document_type or "").strip().lower()
    if doc_type_clean in ("partial_lab_report", "partial_report", "partial_diagnostic_report"):
        return "partial"
    elif doc_type_clean in ("preliminary_lab_report", "preliminary_report"):
        return "preliminary"
    elif doc_type_clean in ("final_lab_report", "final_diagnostic_report"):
        return "final"
    elif doc_type_clean in ("amended_lab_report", "amended_report"):
        return "amended"
    elif doc_type_clean in ("corrected_lab_report", "corrected_report"):
        return "corrected"
    elif doc_type_clean in ("appended_lab_report", "appended_report"):
        return "appended"
    elif doc_type_clean in ("cancelled_lab_report", "cancelled_report"):
        return "cancelled"

    if evidence_texts:
        for txt in evidence_texts:
            if not txt:
                continue
            m = LIFECYCLE_HEADER_REGEX.search(txt)
            if m:
                status_word = m.group(1).lower()
                if status_word in DIAGNOSTIC_REPORT_STATUSES:
                    return status_word

            m_title = EXPLICIT_REPORT_TITLE_REGEX.search(txt)
            if m_title:
                status_word = m_title.group(1).lower()
                if status_word in DIAGNOSTIC_REPORT_STATUSES:
                    return status_word

    return "unknown"


def resolve_observation_effective_time(
    document_metadata: Optional[dict[str, Any]] = None,
    temporal_status: str = "unspecified",
) -> tuple[Optional[Any], Optional[Period]]:
    """Resolve supported effectiveDateTime or effectivePeriod from grounded source timing.

    Rules:
      - Explicit collection/measurement timestamp -> populate supported effectiveDateTime.
      - Date-only source -> preserve date precision (YYYY-MM-DD) without inventing midnight or timezone.
      - Explicit collection period -> use effectivePeriod when both endpoints are grounded.
      - Historical finding without a documented date -> omit effective[x] (None).
      - Encounter/admission date alone must NOT be treated as specimen collection time.
      - Do NOT fabricate issued or effective timestamps.
      - Missing timezone handling: If a time string lacks timezone information and timezone
        cannot be safely determined from metadata, omit rather than guessing.
    """
    if not document_metadata:
        return None, None

    # Collection period: use effectivePeriod when both endpoints are grounded
    period_info = (
        document_metadata.get("effective_period")
        or document_metadata.get("collection_period")
    )
    if isinstance(period_info, dict):
        start_raw = period_info.get("start")
        end_raw = period_info.get("end")
        if start_raw and end_raw:
            try:
                p = Period(start=start_raw, end=end_raw)
                return None, p
            except Exception:
                pass

    dt_raw = (
        document_metadata.get("effective_date_time")
        or document_metadata.get("effective_datetime")
        or document_metadata.get("collection_time")
        or document_metadata.get("collection_date")
        or document_metadata.get("specimen_collection_time")
    )

    if dt_raw is None:
        return None, None

    if isinstance(dt_raw, datetime.datetime):
        if dt_raw.tzinfo is not None:
            return dt_raw, None
        else:
            # Missing timezone on datetime object - omit rather than guessing
            return None, None

    if isinstance(dt_raw, datetime.date):
        return dt_raw, None

    if isinstance(dt_raw, str):
        raw_s = dt_raw.strip()
        # Date-only check: YYYY-MM-DD (preserve date precision without inventing midnight or timezone)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_s):
            try:
                parsed_date = datetime.date.fromisoformat(raw_s)
                return parsed_date, None
            except ValueError:
                return None, None

        # Full ISO timestamp check: must contain timezone offset or 'Z'
        if re.search(r"[Zz]|[+-]\d{2}:\d{2}$", raw_s):
            try:
                parsed_dt = datetime.datetime.fromisoformat(raw_s.replace("Z", "+00:00"))
                return parsed_dt, None
            except ValueError:
                return None, None
        else:
            # String has time component without timezone (e.g. "2026-10-15T08:30:00")
            # Fail safely: omit rather than guessing local or UTC timezone
            return None, None

    return None, None


REPORT_TITLE_REGEX = re.compile(
    r"(?:^|\n)\s*(?:title|report(?:\s+type)?|panel)\s*[:=-]\s*([A-Za-z0-9\s\-/()]{3,60})\b",
    re.IGNORECASE,
)

EXPLICIT_PANEL_HEADER_REGEX = re.compile(
    r"\b(COMPLETE BLOOD COUNT(?:\s+WITH\s+DIFF(?:ERENTIAL)?)?|BASIC METABOLIC PANEL|COMPREHENSIVE METABOLIC PANEL|"
    r"LIPID PANEL|HEPATIC FUNCTION PANEL|RENAL FUNCTION PANEL|URINALYSIS|LABORATORY REPORT|DIAGNOSTIC REPORT|"
    r"BLOOD WORK REPORT|CHEMISTRY PANEL|HEMATOLOGY PANEL|COAGULATION PANEL)\b",
    re.IGNORECASE,
)

PANEL_LOINC_MAP: dict[str, tuple[str, str]] = {
    "LABORATORY REPORT": ("11502-2", "Laboratory report"),
    "LAB REPORT": ("11502-2", "Laboratory report"),
    "COMPLETE BLOOD COUNT": ("58410-2", "Complete blood count (hemogram) panel"),
    "COMPLETE BLOOD COUNT WITH DIFF": ("57021-8", "CBC W Auto Differential panel"),
    "COMPLETE BLOOD COUNT WITH DIFFERENTIAL": ("57021-8", "CBC W Auto Differential panel"),
    "BASIC METABOLIC PANEL": ("24320-4", "Basic metabolic panel"),
    "COMPREHENSIVE METABOLIC PANEL": ("24323-8", "Comprehensive metabolic panel"),
    "LIPID PANEL": ("57698-3", "Lipid panel"),
    "HEPATIC FUNCTION PANEL": ("24325-3", "Hepatic function panel"),
    "RENAL FUNCTION PANEL": ("24362-6", "Renal function panel"),
    "URINALYSIS": ("24356-8", "Urinalysis panel"),
}


def select_deterministic_slot_candidate(
    candidates: Sequence[Claim],
    verification_map: dict[str, VerificationResult],
    field_slot: str,
    group_id: str,
    audit_trail: Optional[list[dict[str, Any]]] = None,
) -> tuple[Optional[Claim], list[dict[str, Any]]]:
    """Select a single candidate deterministically when multiple supported candidates compete for a slot.

    Authority: Phase 4.4 §2, Phase 5.2.3 Sub-Milestone 4.3
    Rules:
      1. Human review status: ACCEPTED / EDITED > PENDING / None > REJECTED / ESCALATED.
      2. Verification verdict: SUPPORTED / VERIFIED > INSUFFICIENT_EVIDENCE > DISCREPANCY > REFUTED.
      3. Verification confidence (descending).
      4. Extraction confidence (descending).
      5. Deterministic tie-breaker: claim_id lexicographical ascending.

    Never silently discard competing candidates. Non-selected candidates are preserved
    in the audit trail and returned as shadowed records. Supports slot-specific resolution
    within split regimens and tapers (e.g., med.dose.morning, med.dose.bedtime).
    """
    if not candidates:
        return None, []

    if len(candidates) == 1:
        return candidates[0], []

    def candidate_sort_key(c: Claim) -> tuple:
        ver = verification_map.get(c.claim_id)
        rev_state = (
            (c.review_state.value if hasattr(c.review_state, "value") else str(c.review_state)).upper()
            if hasattr(c, "review_state") and c.review_state
            else None
        )
        rev_score = 3 if rev_state in ("ACCEPTED", "EDITED") else (0 if rev_state in ("REJECTED", "ESCALATED") else 2)

        lbl = str(getattr(ver, "label", "")).upper() if ver else ""
        ver_score = 3 if lbl in ("SUPPORTED", "VERIFIED") else (2 if lbl == "INSUFFICIENT_EVIDENCE" else (1 if lbl == "DISCREPANCY" else 0))

        ver_conf = float(getattr(ver, "confidence", 0.0) or 0.0) if ver else 0.0
        ext_conf = float(getattr(c, "extraction_confidence", 0.0) or 0.0)

        # Lower tuple value sorts first in ascending sort
        return (-rev_score, -ver_score, -ver_conf, -ext_conf, c.claim_id)

    sorted_candidates = sorted(candidates, key=candidate_sort_key)
    selected = sorted_candidates[0]
    shadowed_records: list[dict[str, Any]] = []

    for other in sorted_candidates[1:]:
        o_ver = verification_map.get(other.claim_id)
        s_rec = {
            "group_id": group_id,
            "slot": field_slot,
            "selected_claim_id": selected.claim_id,
            "selected_value": selected.value,
            "competing_claim_id": other.claim_id,
            "competing_value": other.value,
            "competing_confidence": float(o_ver.confidence) if o_ver else 0.0,
            "competing_label": str(o_ver.label) if o_ver else "UNVERIFIED",
            "selection_reason": "Selected candidate has higher review/verification priority, confidence, or deterministic tie-break",
        }
        shadowed_records.append(s_rec)
        if audit_trail is not None:
            audit_trail.append(s_rec)

    return selected, shadowed_records


def _match_authentic_panel_header(text: str) -> Optional[re.Match]:
    """Matches an authentic report or panel header, filtering out future order / follow-up plan mentions."""
    if not text:
        return None
    for m in EXPLICIT_PANEL_HEADER_REGEX.finditer(text):
        # Check if preceded on the same sentence/clause by planning words
        start_idx = max(0, m.start() - 60)
        prefix = text[start_idx:m.start()].lower()
        if re.search(
            r"\b(?:repeat|order(?:ed|ing)?|schedule(?:d)?|due\s+for|return(?:\s+to\s+clinic)?\s+for|plan(?:ned)?\s+for|recommend(?:ed)?)\b",
            prefix,
        ):
            continue
        return m
    return None


def _should_create_diagnostic_report(
    document_metadata: Optional[dict[str, Any]],
    document_type: Optional[str],
    document_id: str,
    evidence_texts: Sequence[str],
    has_observations: bool,
) -> bool:
    """Determine whether to create or suppress a DiagnosticReport resource.

    Authority: Phase 4.4 §1, Phase 4.4.1 §4
    """
    if not has_observations:
        return False

    meta = document_metadata or {}
    if meta.get("suppress_diagnostic_report") is True or meta.get("is_diagnostic_report") is False:
        return False

    if meta.get("is_diagnostic_report") is True:
        return True

    # If explicit report status, title, accession number or code is grounded in metadata
    if any(
        meta.get(k)
        for k in (
            "report_status",
            "diagnostic_report_status",
            "report_title",
            "panel_name",
            "diagnostic_report_code",
            "report_id",
            "accession_number",
        )
    ):
        return True

    doc_type_clean = (document_type or "").strip().lower()

    # Explicit non-diagnostic types
    if doc_type_clean in ("clinic_rx_pad", "pharmacy_label", "prescription", "mar_order_sheet", "order"):
        return False

    # Explicit diagnostic types
    if (
        doc_type_clean in ("lab_report", "diagnostic_report", "pathology_report", "radiology_report")
        or "_lab_report" in doc_type_clean
        or "_diagnostic_report" in doc_type_clean
    ):
        return True

    if document_id.startswith("LAB_"):
        return True

    # Check evidence texts for explicit diagnostic report titles or status headers
    for txt in evidence_texts:
        if not txt:
            continue
        if _match_authentic_panel_header(txt) or LIFECYCLE_HEADER_REGEX.search(txt) or EXPLICIT_REPORT_TITLE_REGEX.search(txt):
            return True

    # Narrative note types without diagnostic report evidence -> suppress
    if doc_type_clean in NARRATIVE_DOC_TYPES:
        return False

    return False


def _resolve_diagnostic_report_code(
    document_metadata: Optional[dict[str, Any]],
    document_type: Optional[str],
    document_id: str,
    evidence_texts: Optional[Sequence[str]],
) -> CodeableConcept:
    meta = document_metadata or {}

    # 1. Structured metadata diagnostic report code
    if meta.get("diagnostic_report_code"):
        d_code = meta["diagnostic_report_code"]
        if isinstance(d_code, CodeableConcept):
            return d_code
        if isinstance(d_code, dict):
            try:
                return CodeableConcept.model_validate(d_code)
            except Exception:
                pass

    # 2. Metadata report_title / panel_name
    title = meta.get("report_title") or meta.get("panel_name") or meta.get("report_name")
    report_loinc = meta.get("report_loinc") or meta.get("loinc_code")
    if title:
        codings = None
        if report_loinc:
            codings = [Coding(system=LOINC_SYSTEM, code=str(report_loinc).strip(), display=str(title).strip())]
        return CodeableConcept(coding=codings, text=str(title).strip())

    # 3. Evidence texts: regex search for explicit panel or report titles
    if evidence_texts:
        for txt in evidence_texts:
            if not txt:
                continue

            m_panel = _match_authentic_panel_header(txt)
            if m_panel:
                matched_title = m_panel.group(1).strip()
                words = [w.capitalize() for w in matched_title.split()]
                display_str = " ".join(words)
                matched_upper = matched_title.upper()
                codings = None
                if matched_upper in PANEL_LOINC_MAP:
                    p_code, p_disp = PANEL_LOINC_MAP[matched_upper]
                    codings = [Coding(system=LOINC_SYSTEM, code=p_code, display=p_disp)]
                return CodeableConcept(coding=codings, text=display_str)

            m_title = REPORT_TITLE_REGEX.search(txt)
            if m_title:
                matched_title = m_title.group(1).strip()
                if len(matched_title) >= 3:
                    if matched_title.lower() in ("laboratory report", "lab report"):
                        return CodeableConcept(
                            coding=[Coding(system=LOINC_SYSTEM, code="11502-2", display="Laboratory report")],
                            text="Laboratory Report",
                        )
                    return CodeableConcept(text=matched_title)

    # 4. Document type or Document ID check
    doc_type_clean = (document_type or "").strip().lower()
    if doc_type_clean == "lab_report" or document_id.startswith("LAB_") or doc_type_clean in ("final_lab_report", "preliminary_lab_report"):
        return CodeableConcept(
            coding=[Coding(system=LOINC_SYSTEM, code="11502-2", display="Laboratory report")],
            text="Laboratory Report",
        )

    return CodeableConcept(text="Diagnostic Report")


def _map_canonical_diagnostic_report(
    document_id: str,
    patient_id: str,
    observations: Sequence[Observation],
    organization_id: Optional[str] = None,
    encounter_id: Optional[str] = None,
    doc_ref: Optional[DocumentReference] = None,
    document_metadata: Optional[dict[str, Any]] = None,
    document_type: Optional[str] = None,
    evidence_texts: Optional[Sequence[str]] = None,
    anchor_extension: Optional[Extension] = None,
) -> DiagnosticReport:
    doc_slug = _sanitize_id(document_id.lower())
    meta = document_metadata or {}

    report_status = resolve_diagnostic_report_lifecycle_status(
        document_metadata=meta,
        document_type=document_type,
        evidence_texts=evidence_texts,
    )

    category = [
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
    ]

    rep_code = _resolve_diagnostic_report_code(
        document_metadata=meta,
        document_type=document_type,
        document_id=document_id,
        evidence_texts=evidence_texts,
    )

    subject = Reference(reference=f"Patient/{patient_id}")
    enc_ref = Reference(reference=f"Encounter/{encounter_id}") if encounter_id else None
    performer = [Reference(reference=f"Organization/{organization_id}")] if organization_id else None
    results = [Reference(reference=f"Observation/{obs.id}") for obs in observations]

    presented_form = None
    if doc_ref and doc_ref.content:
        for c in doc_ref.content:
            if c.attachment:
                if presented_form is None:
                    presented_form = []
                presented_form.append(c.attachment)

    identifiers = None
    rep_id_val = meta.get("report_id") or meta.get("accession_number")
    if rep_id_val:
        identifiers = [
            Identifier(
                system="http://clindoc.org/fhir/identifier/report-id",
                value=str(rep_id_val).strip(),
            )
        ]

    eff_dt, eff_period = resolve_observation_effective_time(document_metadata=meta)

    issued_dt = None
    issued_raw = meta.get("issued_at") or meta.get("report_issued")
    if issued_raw:
        if isinstance(issued_raw, datetime.datetime):
            issued_dt = issued_raw if issued_raw.tzinfo else issued_raw.replace(tzinfo=datetime.timezone.utc)
        elif isinstance(issued_raw, str):
            try:
                dt_p = datetime.datetime.fromisoformat(issued_raw)
                issued_dt = dt_p if dt_p.tzinfo else dt_p.replace(tzinfo=datetime.timezone.utc)
            except Exception:
                issued_dt = None

    extensions = [anchor_extension] if anchor_extension else None

    return DiagnosticReport(
        id=f"report-{doc_slug}",
        status=report_status,
        category=category,
        code=rep_code,
        subject=subject,
        encounter=enc_ref,
        performer=performer,
        result=results,
        presentedForm=presented_form,
        identifier=identifiers,
        effectiveDateTime=eff_dt,
        effectivePeriod=eff_period,
        issued=issued_dt,
        extension=extensions,
    )


# ─── MEDICATION REGIMEN & MAPPING HELPERS (Phase 5.2.3 Sub-Milestone 4.3) ─────

TAPER_TRANSITION_REGEX = re.compile(
    r"\b(?:then|followed\s+by|afterwards|taper(?:ing)?\s+to|step(?:ping)?\s+down\s+to|reduc(?:e|ing)\s+to|decrease\s+to|step\s+down|taper(?:\s+regimen|\s+schedule)?)\b",
    re.IGNORECASE,
)
MORNING_REGEX = re.compile(r"\b(?:morning|qam|am|breakfast|q\.a\.m\.)\b", re.IGNORECASE)
BEDTIME_REGEX = re.compile(r"\b(?:bedtime|qhs|pm|night|evening|dinner|hs|q\.h\.s\.)\b", re.IGNORECASE)
SPLIT_REGEX = re.compile(r"\b(?:split\s+dos(?:e|ing)|split)\b", re.IGNORECASE)

# ─── REQ-44 SEMANTIC ROLE & LIFECYCLE REGEXES (Phase 5.2.3 Sub-Milestone 4.4.1) ─

ROLE_FOLLOWUP_REGEX = re.compile(
    r"\b(?:review|f/u|follow[- ]?up|see|visit|consult|appointment|recheck|re-evaluate)\s+(?:in|after|within)\s+[0-9]+\s*(?:days?|d|weeks?|wks?|wk|months?|mo|hours?|hrs?|h)\b|"
    r"\b(?:review|f/u|follow[- ]?up|see|visit|consult|appointment|recheck|re-evaluate)\s+after\b",
    re.IGNORECASE,
)
ROLE_MONITORING_REGEX = re.compile(
    r"\b(?:check|repeat|monitor|draw|measure)\s+(?:labs?|cbc|bmp|inr|creatinine|bp|pulse|levels?)\s+(?:in|after|within)\s+[0-9]+\s*(?:days?|d|weeks?|wks?|wk|months?|mo|hours?|hrs?|h)\b|"
    r"\b(?:repeat|check|monitor)\s+(?:labs?|cbc|bmp|inr|creatinine|bp)\s+in\s+[0-9]+\s*(?:days?|d|weeks?|wks?|wk|months?|mo|hours?|hrs?|h)\b",
    re.IGNORECASE,
)
ROLE_NON_MED_HISTORY_REGEX = re.compile(
    r"\b(?:symptoms?|disease|pain|condition)\s+duration\s+[0-9]+\s*(?:days?|d|weeks?|wks?|wk|months?|mo|years?|yrs?|y)\b|"
    r"\b(?:history\s+of|symptoms?\s+for|pain\s+for|past)\s+[0-9]+\s*(?:days?|d|weeks?|wks?|wk|months?|mo|years?|yrs?|y)\b",
    re.IGNORECASE,
)
ORDER_REF_REGEX = re.compile(
    r"\b(?:rx\s*#?|order\s*#?|prescription\s*#?|ref\s*#?)\s*([A-Za-z0-9\-]+)\b",
    re.IGNORECASE,
)
ORDER_CANCEL_REGEX = re.compile(
    r"\b(?:cancel|discontinue|void|revoke)\s+(?:previous\s+)?(?:rx|prescription|order)\b|"
    r"\b(?:cancel|void)\s+[A-Za-z0-9\-]+\b",
    re.IGNORECASE,
)
PATIENT_CESSATION_REGEX = re.compile(
    r"\bpatient\s+(?:stopped(?:\s+taking)?|discontinued|ceased|self-discontinued)\b|"
    r"\b(?:stopped|ceased|discontinued)\s+by\s+(?:the\s+)?patient\b|"
    r"\bpatient\s+(?:has\s+)?(?:stopped|ceased|quit)\b",
    re.IGNORECASE,
)
COMPLETED_COURSE_REGEX = re.compile(
    r"\b(?:completed|finished)\s+(?:(?:a|the|[a-zA-Z0-9\-]+\s+){0,3})?(?:course|treatment|therapy|prescription|regimen)\b|"
    r"\bcompleted\s+[0-9]+\s*(?:days?|d|weeks?|wks?|wk|months?|mo)\s+course\b|"
    r"\b(?:completed|finished)\s+(?:physical\s+therapy|pt|rehab|radiation|psychotherapy|dialysis)\b",
    re.IGNORECASE,
)
DETACHED_COMPLETION_TARGET_REGEX = re.compile(
    r"\b(?:physical\s+therapy|pt|rehab|radiation|psychotherapy|exercise|dialysis|quarantine|isolation|evaluation)\b",
    re.IGNORECASE,
)
FAMILY_HISTORY_REGEX = re.compile(
    r"\b(?:family\s+history|fam\s+hx|fhx)\b|\b(?:mother|father|sister|brother|parent|sibling|maternal|paternal)\s+(?:takes?|took|on|prescribed|used|had)\b",
    re.IGNORECASE,
)
SPECULATIVE_REGEX = re.compile(
    r"\b(?:consider(?:ing)?|possible|questionable|rule\s+out|r/o)\s+(?:starting\s+|prescribing\s+|initiating\s+|giving\s+)?(?:[a-zA-Z0-9\-]+\b)?|\b(?:may\s+benefit\s+from|discuss(?:ed)?\s+starting)\b",
    re.IGNORECASE,
)
UNREFERENCED_CANCEL_DIRECTIVE_REGEX = re.compile(
    r"^\s*(?:discontinue|d/c|stop|cancel|void)\b|\b(?:please\s+)?(?:discontinue|d/c|stop|cancel|void)\s+[A-Za-z0-9\-]+",
    re.IGNORECASE,
)

# ─── REQ-44 CHECKS 1, 2, 3, 5 CLINICAL-SEMANTIC HARDENING HELPERS ─────────────

PATIENT_PLAN_COMMENTARY_REGEX = re.compile(
    r"\bpatient\s+(?:says?|stated?|reports?|mentioned?|wants?|wishes?|plans?|thinking\s+of|might|may)\b|"
    r"\b(?:per\s+patient|patient's\s+wish|patient\s+plans?|patient\s+considering)\b|"
    r"\b(?:may\s+start|might\s+start|thinking\s+about\s+starting|considering\s+starting)\b",
    re.IGNORECASE,
)
CLINICIAN_PLAN_DIRECTIVE_REGEX = re.compile(
    r"\b(?:plan\s*:?\s*)?(?:continue|start|initiate|increase|decrease|taper|prescribe|order|maintain|switch\s+to|titrate|wean|hold|resume)\b",
    re.IGNORECASE,
)
LINKED_CANCEL_ORDER_REGEX = re.compile(
    r"\b(?:cancel(?:led)?|discontinue(?:d)?|void(?:ed)?|revoke(?:d)?)\s+(?:previous\s+|prior\s+)?(?:rx\s*#?|order\s*#?|prescription\s*#?|ref\s*#?)\s*([A-Za-z0-9\-]+)"
    r"|\b(?:rx\s*#?|order\s*#?|prescription\s*#?|ref\s*#?)\s*([A-Za-z0-9\-]+)\s+(?:is\s+)?(?:cancelled|discontinued|voided|revoked)\b",
    re.IGNORECASE,
)
UNCONFIRMED_INTAKE_REGEX = re.compile(
    r"\b(?:current\s+use\s+not\s+confirmed|not\s+confirmed|unconfirmed|adherence\s+unverified|"
    r"unknown\s+if\s+taking|previously\s+prescribed|prior\s+prescription|past\s+use|use\s+unclear|"
    r"history\s+of\s+use|past\s+medical\s+history|pmhx?|prescribed\s+in\s+past)\b",
    re.IGNORECASE,
)
AFFIRMATIVE_INTAKE_REGEX = re.compile(
    r"\b(?:currently\s+taking|currently\s+takes?|current\s+(?:medications?|meds?|regimen|intake|home\s+meds?)|"
    r"takes?|taking|adherent|confirmed\s+active|reports\s+taking|home\s+regimen|active\s+intake)\b",
    re.IGNORECASE,
)
ACTIVE_SIG_REGEX = re.compile(
    r"\b(?:take\s+[0-9]|taking\s+[0-9]|swallow|chew|inject|inhale|apply)\b|"
    r"\b[0-9]+\s*(?:mg|mcg|g|ml|units?)\s+(?:po|oral|sublingual|iv|subq|im|topical|inhaled|by\s+mouth)\s+(?:daily|bid|tid|qid|qhs|qam|q[0-9]+h|twice\s+daily|three\s+times\s+daily|once\s+daily|every\s+day)\b|"
    r"\b(?:daily|bid|tid|qid|qhs|qam|twice\s+daily|three\s+times\s+daily|once\s+daily|every\s+morning|at\s+bedtime)\b",
    re.IGNORECASE,
)


def _is_medication_completed_linked(med_name: str, full_text: str) -> bool:
    """Verify explicit clinical evidence of completion directly linked to the target drug."""
    if not full_text:
        return False

    # 1. Direct completion wording: "<med> completed", "completed course of <med>", "finished <med>"
    if med_name:
        escaped_name = re.escape(med_name)
        direct_pat = re.compile(
            rf"\b{escaped_name}\s+(?:(?:is|was)\s+)?(?:completed|finished)\b|"
            rf"\b(?:completed|finished)\s+(?:(?:a|the|\d+[- ](?:days?|weeks?|months?|years?))\s+)?(?:course|treatment|therapy|prescription)\s+(?:of\s+)?{escaped_name}\b|"
            rf"\b(?:completed|finished)\s+{escaped_name}\b",
            re.IGNORECASE,
        )
        if direct_pat.search(full_text):
            return True

    # 2. General course completion without detached non-med target
    # If a detached non-med target is present anywhere in full_text and direct_pat did not match, it is NOT linked to this medication
    if DETACHED_COMPLETION_TARGET_REGEX.search(full_text):
        return False

    m_course = COMPLETED_COURSE_REGEX.search(full_text)
    if m_course:
        return True

    return False


def _is_order_cancellation_linked_to_med(med_name: str, ev_texts: Sequence[str]) -> bool:
    """Verify that an order cancellation directive explicitly binds the cancellation verb,

    the order reference, and the target medication within the same clause or association.
    """
    for txt in ev_texts:
        if not txt:
            continue
        clauses = re.split(r"[.\n;]", txt)
        for clause in clauses:
            m_cancel = LINKED_CANCEL_ORDER_REGEX.search(clause)
            if m_cancel:
                if med_name:
                    if re.search(rf"\b{re.escape(med_name)}\b", clause, re.IGNORECASE):
                        return True
                else:
                    return True
    return False


def _classify_temporal_role(dur_claim: Claim, full_text: str, dur_pos: int) -> str:
    """Classify the clinical semantic role of an extracted temporal expression.

    Authority: Phase 5.2.3 Sub-Milestone 4.4 REQ-44 (v3.1)
    - ROLE_REGIMEN_DURATION: Governs medication administration limit ('for 2 weeks', 'x 10d')
    - ROLE_FOLLOWUP_REVIEW: Governs clinic/physician encounter ('review after 1 week')
    - ROLE_MONITORING_REASSESSMENT: Governs diagnostic testing ('repeat labs in 5 days')
    - ROLE_NON_MED_CLINICAL_HISTORY: Governs disease/symptom history ('symptom duration 6 months')
    """
    if not full_text or dur_pos < 0:
        return "ROLE_REGIMEN_DURATION"

    val_len = len(dur_claim.value) if dur_claim and dur_claim.value else 0
    dur_end = dur_pos + val_len

    # Identify sentence/clause boundary around dur_pos to avoid cross-sentence leakage
    prev_boundary = max(full_text.rfind(".", 0, dur_pos), full_text.rfind("\n", 0, dur_pos), full_text.rfind(";", 0, dur_pos))
    preceding_start = max(0, prev_boundary + 1, dur_pos - 40)

    candidates = [pos for pos in (full_text.find(".", dur_end), full_text.find("\n", dur_end)) if pos != -1]
    next_boundary = min(candidates) if candidates else len(full_text)
    following_end = min(len(full_text), next_boundary, dur_end + 30)

    local_window = full_text[preceding_start:following_end]

    if ROLE_NON_MED_HISTORY_REGEX.search(local_window):
        return "ROLE_NON_MED_CLINICAL_HISTORY"

    if ROLE_FOLLOWUP_REGEX.search(local_window):
        return "ROLE_FOLLOWUP_REVIEW"

    if ROLE_MONITORING_REGEX.search(local_window):
        return "ROLE_MONITORING_REASSESSMENT"

    return "ROLE_REGIMEN_DURATION"


def _build_dose_and_rate(
    dose_claim: Optional[Claim],
    dose_ext: Optional[Extension],
) -> tuple[Optional[list[DosageDoseAndRate]], Optional[Extension]]:
    """Build DosageDoseAndRate with UCUM quantity and provenance extension.

    Preserves invalid-dose omission and prevents zero-dose fallback.
    """
    if not dose_claim:
        return None, None
    d_val = dose_claim.value_numeric
    if d_val is None:
        m = re.search(r"[-+]?\d*\.?\d+", dose_claim.value)
        if m:
            try:
                parsed_val = float(m.group())
                if parsed_val == 0.0:
                    if re.search(r"\b0(?:\.0+)?\b", dose_claim.value):
                        d_val = 0.0
                    else:
                        d_val = None
                else:
                    d_val = parsed_val
            except ValueError:
                d_val = None
        else:
            d_val = None
    elif d_val == 0.0:
        if not re.search(r"\b0(?:\.0+)?\b", dose_claim.value):
            d_val = None

    if d_val is not None:
        dose_ucum_sys = None
        dose_ucum_code = None
        if dose_claim.unit_raw:
            dose_ucum_sys = UCUM_SYSTEM
            u_clean = dose_claim.unit_raw.strip()
            if u_clean.lower() in ("units", "unit"):
                dose_ucum_code = "[iU]"
            else:
                dose_ucum_code = u_clean

        dose_qty = Quantity(
            value=round(Decimal(str(d_val)), 4),
            unit=dose_claim.unit_raw or None,
            system=dose_ucum_sys,
            code=dose_ucum_code,
            extension=[dose_ext] if dose_ext else None,
        )
        return [DosageDoseAndRate(doseQuantity=dose_qty)], None
    else:
        # Unparseable dose string (e.g. "one puff", "sliding scale")
        # Do NOT emit doseQuantity = 0.0! Instruction is preserved in Dosage.text
        # And dose_ext is preserved on Dosage.extension
        return None, dose_ext


def _build_timing_object(
    freq_claim: Optional[Claim],
    dur_claim: Optional[Claim],
    verification_map: dict[str, VerificationResult],
    evidence_map: dict[str, Evidence],
) -> Optional[Timing]:
    """Construct Timing object with frequency code, repeat.boundsDuration, and element provenance.

    Authority: FHIR R4B Timing semantics. Does not invent unsupported boundsDuration.
    """
    if not freq_claim and not dur_claim:
        return None

    timing_code = None
    t_exts = []
    repeat_obj = None

    if freq_claim:
        f_ver = verification_map.get(freq_claim.claim_id)
        f_ev = evidence_map.get(freq_claim.evidence_id)
        freq_ext = build_element_provenance_extension(freq_claim, f_ver, f_ev) if (f_ver and f_ev) else None
        timing_code = CodeableConcept(
            text=freq_claim.value,
            extension=[freq_ext] if freq_ext else None
        )

    if dur_claim:
        d_ver = verification_map.get(dur_claim.claim_id)
        d_ev = evidence_map.get(dur_claim.evidence_id)
        dur_ext = build_element_provenance_extension(dur_claim, d_ver, d_ev) if (d_ver and d_ev) else None

        dur_val = dur_claim.value_numeric
        if dur_val is None:
            m_dur = re.search(r"[-+]?\d*\.?\d+", dur_claim.value)
            if m_dur:
                try:
                    dur_val = float(m_dur.group())
                except ValueError:
                    dur_val = None

        dur_unit = (dur_claim.unit_raw or "").strip().lower()
        if not dur_unit:
            m_unit = re.search(r"\b(days?|d|weeks?|wks?|wk|months?|mo|hours?|hrs?|h)\b", dur_claim.value, re.I)
            if m_unit:
                dur_unit = m_unit.group(1).lower()

        ucum_code = None
        disp_unit = dur_unit or "days"
        if dur_unit in ("day", "days", "d"):
            ucum_code = "d"
            disp_unit = "days"
        elif dur_unit in ("week", "weeks", "wk", "wks"):
            ucum_code = "wk"
            disp_unit = "weeks"
        elif dur_unit in ("month", "months", "mo"):
            ucum_code = "mo"
            disp_unit = "months"
        elif dur_unit in ("hour", "hours", "hr", "hrs", "h"):
            ucum_code = "h"
            disp_unit = "hours"

        if dur_val is not None and ucum_code:
            bounds_dur = Duration(
                value=round(Decimal(str(dur_val)), 4),
                unit=disp_unit,
                system=UCUM_SYSTEM,
                code=ucum_code,
                extension=[dur_ext] if dur_ext else None,
            )
            repeat_obj = TimingRepeat(boundsDuration=bounds_dur)
        elif dur_ext:
            t_exts.append(dur_ext)

    return Timing(
        code=timing_code,
        repeat=repeat_obj,
        extension=t_exts if t_exts else None,
    )


def _get_claim_pos(c: Claim, full_text: str, evidence_map: dict[str, Evidence], occurrence_idx: int = 0) -> int:
    """Resolve character offset for a claim within document text.

    Supports occurrence_idx to prevent token collision on duplicate strings
    (e.g. multi-phase tapers with repeated duration phrases).
    """
    ev = evidence_map.get(c.evidence_id)
    if ev and ev.char_start > 0:
        return ev.char_start
    # If unit is available, search value + unit first to avoid digit collisions (e.g. '2 mg' vs '2 weeks')
    if c.unit_raw:
        pattern = r"\b" + re.escape(c.value) + r"\s*" + re.escape(c.unit_raw) + r"\b"
        matches = list(re.finditer(pattern, full_text, re.I))
        if matches:
            m = matches[occurrence_idx] if occurrence_idx < len(matches) else matches[-1]
            return m.start()
    pattern = r"\b" + re.escape(c.value) + r"\b"
    matches = list(re.finditer(pattern, full_text, re.I))
    if matches:
        m = matches[occurrence_idx] if occurrence_idx < len(matches) else matches[-1]
        return m.start()
    idx = full_text.find(c.value)
    return idx if idx >= 0 else 0


def _build_regimen_dosages(
    grp: Sequence[Claim],
    name_claim: Optional[Claim],
    route_claim: Optional[Claim],
    verification_map: dict[str, VerificationResult],
    evidence_map: dict[str, Evidence],
    grp_key: str,
    audit_trail: Optional[list[dict[str, Any]]] = None,
) -> tuple[list[Dosage], list[dict[str, Any]]]:
    """Construct FHIR R4B Dosage entries supporting split dosing and sequential tapers.

    Authority: Phase 5.2.3 Sub-Milestone 4.3
    - Split dosing: multiple concurrent Dosage entries (sharing sequence=1).
    - Sequential taper: chronological phases (distinct sequence=1, sequence=2, ...) with
      boundsDuration on bounded phases and no ungrounded start dates or durations.
    - Slot competition fallback: deterministic candidate selection for competing unslotted doses.
    """
    dose_claims = [c for c in grp if c.field_type == "med.dose"]
    freq_claims = [c for c in grp if c.field_type == "med.frequency"]
    dur_claims = [c for c in grp if c.field_type == "med.duration"]

    grp_texts = []
    for c in grp:
        ev = evidence_map.get(c.evidence_id)
        if ev and ev.text and ev.text not in grp_texts:
            grp_texts.append(ev.text)
    full_grp_text = " ".join(grp_texts)

    route_cc = None
    if route_claim:
        r_ver = verification_map.get(route_claim.claim_id)
        r_ev = evidence_map.get(route_claim.evidence_id)
        route_ext = build_element_provenance_extension(route_claim, r_ver, r_ev) if (r_ver and r_ev) else None
        route_cc = CodeableConcept(text=route_claim.value, extension=[route_ext] if route_ext else None)

    # Base case: 0 or 1 dose claim
    if len(dose_claims) <= 1:
        dose_claim = dose_claims[0] if dose_claims else None
        freq_claim, freq_shadowed = select_deterministic_slot_candidate(
            freq_claims, verification_map, field_slot="med.frequency", group_id=grp_key, audit_trail=audit_trail
        )

        # Filter duration claims by semantic role (REQ-44)
        valid_regimen_durs = []
        for dur in dur_claims:
            dur_p = _get_claim_pos(dur, full_grp_text, evidence_map)
            role = _classify_temporal_role(dur, full_grp_text, dur_p)
            if role == "ROLE_REGIMEN_DURATION":
                valid_regimen_durs.append(dur)
            elif audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "duration": dur.value,
                    "status": "EXCLUDED",
                    "reason": f"EXCLUDED_NON_REGIMEN_DURATION: Classified as {role}",
                })

        if len(valid_regimen_durs) == 1:
            dur_claim = valid_regimen_durs[0]
            dur_shadowed = []
        elif len(valid_regimen_durs) > 1:
            # Case B: Multiple competing regimen durations -> Fail closed!
            dur_claim = None
            dur_shadowed = []
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "FAIL_CLOSED",
                    "reason": "AMBIGUOUS_MULTI_DURATION_CONFLICT: Multiple competing regimen durations in single dose instruction",
                })
        else:
            dur_claim = None
            dur_shadowed = []
        shadowed = freq_shadowed + dur_shadowed

        dose_ext = None
        if dose_claim:
            d_ver = verification_map.get(dose_claim.claim_id)
            d_ev = evidence_map.get(dose_claim.evidence_id)
            dose_ext = build_element_provenance_extension(dose_claim, d_ver, d_ev) if (d_ver and d_ev) else None

        dose_and_rate, unparsed_dose_ext = _build_dose_and_rate(dose_claim, dose_ext)
        timing_obj = _build_timing_object(freq_claim, dur_claim, verification_map, evidence_map)

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
            sequence=1,
            text=dosage_text,
            route=route_cc,
            doseAndRate=dose_and_rate,
            timing=timing_obj,
            extension=[unparsed_dose_ext] if unparsed_dose_ext else None,
        )
        return [dosage], shadowed

    # Multiple dose claims: check for split dosing, sequential taper, or slot competition
    sorted_doses = sorted(dose_claims, key=lambda c: _get_claim_pos(c, full_grp_text, evidence_map))
    sorted_freqs = sorted(freq_claims, key=lambda c: _get_claim_pos(c, full_grp_text, evidence_map))
    sorted_durs = sorted(
        dur_claims,
        key=lambda c: _get_claim_pos(c, full_grp_text, evidence_map, occurrence_idx=dur_claims.index(c)),
    )

    # Safety check: if one dose is discontinued/historical and another is current (W-1), do NOT treat as split/taper
    temp_statuses = {c.temporal_status for c in sorted_doses}
    has_mixed_inactive = ("discontinued" in temp_statuses or "historical" in temp_statuses) and ("current" in temp_statuses or "planned" in temp_statuses)

    has_taper_cues = bool(TAPER_TRANSITION_REGEX.search(full_grp_text)) and not has_mixed_inactive
    has_morning = bool(MORNING_REGEX.search(full_grp_text))
    has_bedtime = bool(BEDTIME_REGEX.search(full_grp_text))
    has_split_cues = (SPLIT_REGEX.search(full_grp_text) or (has_morning and has_bedtime)) and not has_mixed_inactive

    # ── CASE A: SEQUENTIAL TAPER (Phase 5.2.3 Sub-Milestone 4.4 REQ-44 v3.1) ─
    if has_taper_cues:
        dosages: list[Dosage] = []
        dose_positions = [_get_claim_pos(d, full_grp_text, evidence_map) for d in sorted_doses]
        num_phases = len(sorted_doses)

        # 1. Identify transition boundaries between adjacent dose claims
        boundaries: list[Optional[int]] = []
        has_paragraph_break_without_transition = False

        for k in range(num_phases - 1):
            p_k = dose_positions[k]
            p_next = dose_positions[k + 1]
            inter_text = full_grp_text[p_k:p_next]

            # Rule 2: Paragraph boundary non-sufficiency
            is_par_break = bool(re.search(r"(?:\r?\n\s*){2,}", inter_text))
            m_trans = TAPER_TRANSITION_REGEX.search(inter_text)

            if m_trans:
                b_k = p_k + m_trans.start()
                boundaries.append(b_k)
            else:
                boundaries.append(None)
                if is_par_break:
                    has_paragraph_break_without_transition = True

        all_transitions_grounded = all(b is not None for b in boundaries)

        # 2. Build partition spans [L_k, R_k)
        partitions: list[tuple[int, int]] = []
        if all_transitions_grounded:
            for k in range(num_phases):
                l_k = 0 if k == 0 else boundaries[k - 1]
                r_k = boundaries[k] if k < num_phases - 1 else len(full_grp_text)
                partitions.append((l_k, r_k))
        else:
            for k in range(num_phases):
                l_k = dose_positions[k]
                r_k = dose_positions[k + 1] if k + 1 < num_phases else len(full_grp_text)
                partitions.append((l_k, r_k))

        for idx, d_claim in enumerate(sorted_doses):
            phase_num = idx + 1
            d_ver = verification_map.get(d_claim.claim_id)
            d_ev = evidence_map.get(d_claim.evidence_id)
            dose_ext = build_element_provenance_extension(d_claim, d_ver, d_ev) if (d_ver and d_ev) else None
            dose_and_rate, unparsed_ext = _build_dose_and_rate(d_claim, dose_ext)

            l_k, r_k = partitions[idx]

            # Pair with frequency in this phase window
            phase_freq = None
            if sorted_freqs:
                cand_freqs = [f for f in sorted_freqs if l_k <= _get_claim_pos(f, full_grp_text, evidence_map) < r_k]
                if cand_freqs:
                    phase_freq = cand_freqs[0]
                elif len(sorted_freqs) == len(sorted_doses):
                    phase_freq = sorted_freqs[idx]
                else:
                    d_pos = dose_positions[idx]
                    phase_freq = min(sorted_freqs, key=lambda f: abs(_get_claim_pos(f, full_grp_text, evidence_map) - d_pos))

            # Pair with duration in this phase partition (REQ-44-03)
            phase_dur = None
            if sorted_durs and all_transitions_grounded:
                # Find all duration claims in [l_k, r_k) with ROLE_REGIMEN_DURATION
                cand_regimen_durs = []
                for dur_i, dur in enumerate(sorted_durs):
                    dur_p = _get_claim_pos(dur, full_grp_text, evidence_map, occurrence_idx=dur_i)
                    if l_k <= dur_p < r_k:
                        role = _classify_temporal_role(dur, full_grp_text, dur_p)
                        if role == "ROLE_REGIMEN_DURATION":
                            cand_regimen_durs.append(dur)
                        elif audit_trail is not None:
                            audit_trail.append({
                                "group_id": grp_key,
                                "phase": phase_num,
                                "duration": dur.value,
                                "status": "EXCLUDED",
                                "reason": f"EXCLUDED_NON_REGIMEN_DURATION: Classified as {role}",
                            })

                if len(cand_regimen_durs) == 1:
                    phase_dur = cand_regimen_durs[0]
                elif len(cand_regimen_durs) > 1:
                    # Case B: Multiple competing regimen durations -> Fail closed!
                    phase_dur = None
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "phase": phase_num,
                            "status": "FAIL_CLOSED",
                            "reason": "AMBIGUOUS_MULTI_DURATION_CONFLICT: Multiple regimen durations compete in single phase clause",
                        })
                else:
                    phase_dur = None
            elif not all_transitions_grounded:
                if audit_trail is not None:
                    reason = (
                        "PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED"
                        if has_paragraph_break_without_transition
                        else "MISSING_EXPLICIT_TRANSITION_FAIL_CLOSED"
                    )
                    audit_trail.append({
                        "group_id": grp_key,
                        "phase": phase_num,
                        "status": "FAIL_CLOSED",
                        "reason": f"{reason}: Sequential transitions not fully grounded",
                    })

            timing_obj = _build_timing_object(phase_freq, phase_dur, verification_map, evidence_map)

            # Build narrative text using source span preservation
            clause_raw = full_grp_text[l_k:r_k].strip()
            # Strip non-medication follow-up / monitoring / history sentences from dosage text
            m_non_med = re.search(
                r"(?:\.\s+|\n\s*|\s+also\s+note\s+|\s+note\s+)(?:"
                r"(?:review|f/u|follow[- ]?up|see|visit|consult|appointment|recheck)\s+(?:in|after|within)|"
                r"(?:check|repeat|monitor|draw|measure)\s+(?:labs?|cbc|bmp|inr|creatinine|bp|pulse|levels?)|"
                r"(?:symptoms?|disease|pain|condition)\s+duration|"
                r"(?:history\s+of|symptoms?\s+for|pain\s+for)\s+[0-9]+"
                r")",
                clause_raw,
                re.IGNORECASE,
            )
            if m_non_med:
                clause_raw = clause_raw[:m_non_med.start()].strip()

            clause_clean = clause_raw.rstrip(".,;:- ")
            # Normalize drug name colon prefix (e.g. "Prednisone: 20 mg" -> "Prednisone 20 mg")
            clause_clean = re.sub(r"^([A-Za-z0-9\-]+):\s*", r"\1 ", clause_clean)

            if idx == 0:
                if name_claim and name_claim.value.lower() not in clause_clean.lower():
                    p_text = f"{name_claim.value} {clause_clean}".strip()
                else:
                    p_text = clause_clean
            else:
                if not re.match(r"^(?:then|followed\s+by)\b", clause_clean, re.I):
                    p_text = f"Then {clause_clean}".strip()
                else:
                    p_text = clause_clean[0].upper() + clause_clean[1:] if clause_clean else "Then"

            # Fallback if clause reconstruction is degenerate
            if not p_text:
                parts = []
                if idx == 0 and name_claim:
                    parts.append(name_claim.value)
                elif idx > 0:
                    parts.append("Then")
                parts.append(f"{d_claim.value} {d_claim.unit_raw or ''}".strip())
                if route_claim:
                    parts.append(route_claim.value)
                if phase_freq:
                    parts.append(phase_freq.value)
                if phase_dur:
                    parts.append(f"for {phase_dur.value}")
                p_text = " ".join(parts).strip()

            dosages.append(
                Dosage(
                    sequence=phase_num,
                    text=p_text,
                    route=route_cc,
                    doseAndRate=dose_and_rate,
                    timing=timing_obj,
                    extension=[unparsed_ext] if unparsed_ext else None,
                )
            )
        return dosages, []

    # ── CASE B: SPLIT DOSING ──────────────────────────────────────────────────
    if has_split_cues:
        morning_doses = []
        bedtime_doses = []
        other_doses = []

        dose_spans = []
        for d in sorted_doses:
            m = re.search(re.escape(d.value), full_grp_text)
            start_pos = m.start() if m else 0
            dose_spans.append((start_pos, d))
        dose_spans.sort(key=lambda x: x[0])

        for idx, (pos, d) in enumerate(dose_spans):
            next_pos = dose_spans[idx + 1][0] if idx + 1 < len(dose_spans) else len(full_grp_text)
            window_txt = full_grp_text[pos:next_pos]

            closest_freq = None
            if sorted_freqs:
                if len(sorted_freqs) == len(sorted_doses):
                    closest_freq = sorted_freqs[idx]
                else:
                    d_off = _get_claim_pos(d, full_grp_text, evidence_map)
                    closest_freq = min(sorted_freqs, key=lambda f: abs(_get_claim_pos(f, full_grp_text, evidence_map) - d_off))

            freq_val = closest_freq.value if closest_freq else ""
            combined_window = f"{window_txt} {freq_val}"

            if MORNING_REGEX.search(combined_window) and not BEDTIME_REGEX.search(combined_window):
                morning_doses.append(d)
            elif BEDTIME_REGEX.search(combined_window) and not MORNING_REGEX.search(combined_window):
                bedtime_doses.append(d)
            elif MORNING_REGEX.search(combined_window) and BEDTIME_REGEX.search(combined_window):
                m_am = MORNING_REGEX.search(combined_window)
                m_pm = BEDTIME_REGEX.search(combined_window)
                if m_am and m_pm:
                    if m_am.start() < m_pm.start():
                        morning_doses.append(d)
                    else:
                        bedtime_doses.append(d)
                else:
                    other_doses.append(d)
            else:
                other_doses.append(d)

        if not morning_doses and not bedtime_doses and len(sorted_doses) == 2:
            morning_doses = [sorted_doses[0]]
            bedtime_doses = [sorted_doses[1]]

        shadowed: list[dict[str, Any]] = []
        selected_split_doses = []

        if morning_doses:
            m_win, m_shad = select_deterministic_slot_candidate(
                morning_doses, verification_map, field_slot="med.dose.morning", group_id=grp_key, audit_trail=audit_trail
            )
            if m_win:
                selected_split_doses.append((m_win, "morning"))
            shadowed.extend(m_shad)

        if bedtime_doses:
            b_win, b_shad = select_deterministic_slot_candidate(
                bedtime_doses, verification_map, field_slot="med.dose.bedtime", group_id=grp_key, audit_trail=audit_trail
            )
            if b_win:
                selected_split_doses.append((b_win, "bedtime"))
            shadowed.extend(b_shad)

        for od in other_doses:
            selected_split_doses.append((od, "other"))

        if len(selected_split_doses) >= 2:
            dosages = []
            for d_claim, slot_type in selected_split_doses:
                d_ver = verification_map.get(d_claim.claim_id)
                d_ev = evidence_map.get(d_claim.evidence_id)
                dose_ext = build_element_provenance_extension(d_claim, d_ver, d_ev) if (d_ver and d_ev) else None
                dose_and_rate, unparsed_ext = _build_dose_and_rate(d_claim, dose_ext)

                d_freq = None
                for f in sorted_freqs:
                    f_txt = f.value
                    if slot_type == "morning" and MORNING_REGEX.search(f_txt):
                        d_freq = f
                        break
                    elif slot_type == "bedtime" and BEDTIME_REGEX.search(f_txt):
                        d_freq = f
                        break
                if not d_freq and sorted_freqs:
                    d_pos = _get_claim_pos(d_claim, full_grp_text, evidence_map)
                    d_freq = min(sorted_freqs, key=lambda f: abs(_get_claim_pos(f, full_grp_text, evidence_map) - d_pos))

                timing_obj = _build_timing_object(d_freq, None, verification_map, evidence_map)

                parts = []
                if name_claim:
                    parts.append(name_claim.value)
                parts.append(f"{d_claim.value} {d_claim.unit_raw or ''}".strip())
                if route_claim:
                    parts.append(route_claim.value)
                if d_freq:
                    parts.append(d_freq.value)
                d_text = " ".join(parts).strip()

                dosages.append(
                    Dosage(
                        sequence=1,  # Concurrent instructions share sequence=1
                        text=d_text,
                        route=route_cc,
                        doseAndRate=dose_and_rate,
                        timing=timing_obj,
                        extension=[unparsed_ext] if unparsed_ext else None,
                    )
                )
            return dosages, shadowed

    # ── CASE C: FALLBACK - TRUE COMPETING CANDIDATES ─────────────────────────
    dose_win, dose_shad = select_deterministic_slot_candidate(
        sorted_doses, verification_map, field_slot="med.dose", group_id=grp_key, audit_trail=audit_trail
    )
    freq_win, freq_shad = select_deterministic_slot_candidate(
        sorted_freqs, verification_map, field_slot="med.frequency", group_id=grp_key, audit_trail=audit_trail
    )
    dur_win, dur_shad = select_deterministic_slot_candidate(
        sorted_durs, verification_map, field_slot="med.duration", group_id=grp_key, audit_trail=audit_trail
    )
    shadowed = dose_shad + freq_shad + dur_shad

    dose_ext = None
    if dose_win:
        d_ver = verification_map.get(dose_win.claim_id)
        d_ev = evidence_map.get(dose_win.evidence_id)
        dose_ext = build_element_provenance_extension(dose_win, d_ver, d_ev) if (d_ver and d_ev) else None

    dose_and_rate, unparsed_ext = _build_dose_and_rate(dose_win, dose_ext)
    timing_obj = _build_timing_object(freq_win, dur_win, verification_map, evidence_map)

    parts = []
    if name_claim:
        parts.append(name_claim.value)
    if dose_win:
        parts.append(f"{dose_win.value} {dose_win.unit_raw or ''}".strip())
    if route_claim:
        parts.append(route_claim.value)
    if freq_win:
        parts.append(freq_win.value)
    if dur_win:
        parts.append(f"for {dur_win.value}")
    d_text = " ".join(parts).strip() or "Standard dosage instructions"

    dosage = Dosage(
        sequence=1,
        text=d_text,
        route=route_cc,
        doseAndRate=dose_and_rate,
        timing=timing_obj,
        extension=[unparsed_ext] if unparsed_ext else None,
    )
    return [dosage], shadowed


def map_medication_request(
    resource_id: str,
    patient_id: str,
    medication_code: CodeableConcept,
    dosage_instructions: list[Dosage],
    temporal_status: str = "current",
    extensions: Optional[list[Extension]] = None,
    shadowed_records: Optional[list[dict[str, Any]]] = None,
    encounter_id: Optional[str] = None,
    doc_ref_id: Optional[str] = None,
    status_override: Optional[str] = None,
) -> MedicationRequest:
    """Map validated claims and dosage instructions to a canonical FHIR R4B MedicationRequest.

    Authority: DEC-008, Phase 4.4, Phase 5.2.3 Sub-Milestone 4.3
    """
    req_extensions = list(extensions or [])
    if temporal_status in MEDICATION_REQUEST_TEMPORAL_STATUSES:
        req_extensions.append(
            Extension(url=EXT_TEMPORAL_STATUS, valueCode=temporal_status)
        )
    if shadowed_records:
        for s_rec in shadowed_records:
            req_extensions.append(
                Extension(
                    url=EXT_COMPETING_CANDIDATE,
                    extension=[
                        Extension(url="slot", valueString=s_rec["slot"]),
                        Extension(url="selectedClaimId", valueString=s_rec["selected_claim_id"]),
                        Extension(url="competingClaimId", valueString=s_rec["competing_claim_id"]),
                        Extension(url="competingValue", valueString=str(s_rec["competing_value"])),
                        Extension(url="selectionReason", valueString=s_rec["selection_reason"]),
                    ],
                )
            )

    if status_override:
        req_status = status_override
    elif temporal_status == "discontinued":
        req_status = "stopped"
    elif temporal_status == "historical":
        req_status = "completed"
    elif temporal_status == "planned":
        req_status = "active"
    else:
        req_status = "active"
    req_intent = "plan" if temporal_status == "planned" else "order"

    return MedicationRequest(
        id=resource_id,
        status=req_status,
        intent=req_intent,
        subject=Reference(reference=f"Patient/{patient_id}"),
        encounter=Reference(reference=f"Encounter/{encounter_id}") if encounter_id else None,
        supportingInformation=[Reference(reference=f"DocumentReference/{doc_ref_id}")] if doc_ref_id else None,
        medicationCodeableConcept=medication_code,
        dosageInstruction=dosage_instructions,
        extension=req_extensions if req_extensions else None,
    )


def map_medication_statement(
    resource_id: str,
    patient_id: str,
    medication_code: CodeableConcept,
    dosages: list[Dosage],
    temporal_status: str = "current",
    extensions: Optional[list[Extension]] = None,
    shadowed_records: Optional[list[dict[str, Any]]] = None,
    encounter_id: Optional[str] = None,
    doc_ref_id: Optional[str] = None,
    status_override: Optional[str] = None,
) -> MedicationStatement:
    """Map validated claims and dosage instructions to a canonical FHIR R4B MedicationStatement.

    Authority: DEC-008, Phase 4.4, Phase 5.2.3 Sub-Milestone 4.3, REQ-44 v3.1
    """
    stmt_extensions = list(extensions or [])
    if temporal_status in MEDICATION_STATEMENT_TEMPORAL_STATUSES:
        stmt_extensions.append(
            Extension(url=EXT_TEMPORAL_STATUS, valueCode=temporal_status)
        )
    if shadowed_records:
        for s_rec in shadowed_records:
            stmt_extensions.append(
                Extension(
                    url=EXT_COMPETING_CANDIDATE,
                    extension=[
                        Extension(url="slot", valueString=s_rec["slot"]),
                        Extension(url="selectedClaimId", valueString=s_rec["selected_claim_id"]),
                        Extension(url="competingClaimId", valueString=s_rec["competing_claim_id"]),
                        Extension(url="competingValue", valueString=str(s_rec["competing_value"])),
                        Extension(url="selectionReason", valueString=s_rec["selection_reason"]),
                    ],
                )
            )

    if status_override:
        stat_status = status_override
    elif temporal_status == "discontinued":
        stat_status = "stopped"
    elif temporal_status == "historical":
        stat_status = "unknown"
    elif temporal_status == "completed":
        stat_status = "completed"
    elif temporal_status == "planned":
        stat_status = "intended"
    else:
        stat_status = "active"

    return MedicationStatement(
        id=resource_id,
        status=stat_status,
        subject=Reference(reference=f"Patient/{patient_id}"),
        context=Reference(reference=f"Encounter/{encounter_id}") if encounter_id else None,
        derivedFrom=[Reference(reference=f"DocumentReference/{doc_ref_id}")] if doc_ref_id else None,
        medicationCodeableConcept=medication_code,
        dosage=dosages,
        extension=stmt_extensions if stmt_extensions else None,
    )


def claims_to_fhir_bundle(
    claims: Sequence[Claim],
    evidence_map: dict[str, Evidence],
    verification_map: dict[str, VerificationResult],
    document_id: Optional[str] = None,
    document_type: Optional[str] = None,
    patient_id: Optional[str] = None,
    patient_name: Optional[str] = None,
    document_metadata: Optional[dict[str, Any]] = None,
    encounter_info: Optional[dict[str, Any]] = None,
    include_document_reference: bool = False,
    include_provenance_resource: bool = False,
    pipeline_info: Optional[dict[str, Any]] = None,
    pipeline_timing: Optional[dict[str, Any]] = None,
    review_info: Optional[dict[str, Any]] = None,
    audit_trail: Optional[list[dict[str, Any]]] = None,
    enforce_req44_lifecycle: bool = True,
) -> Bundle:
    """Map canonical clinical claims and verifications into a compliant FHIR R4B Bundle.

    Authority:
      - PROJECT_SPEC §5 #10, §7.7, §15, §16
      - DEC-008: Hybrid MedicationRequest vs MedicationStatement routing
      - DEC-016: Two-tier provenance hierarchy (resource-level & element-level)
      - Phase 4.3: Provenance Timestamp Integrity & Document Traceability
      - Phase 8.8: FHIR R4B Export & Provenance Grounding

    Args:
        claims: Sequence of extracted and normalized Claim objects.
        evidence_map: Mapping from evidence_id -> Evidence.
        verification_map: Mapping from claim_id -> VerificationResult.
        document_id: Identifier of source document (defaults to claims[0].document_id).
        document_type: Source document layout type (e.g. clinic_rx_pad, mar_order_sheet, etc.).
        patient_id: Optional patient MRN/ID override.
        patient_name: Optional patient name string override.
        document_metadata: Optional source document metadata (mime_type, filename, storage_ref, sha256).
        encounter_info: Optional encounter metadata (encounter_id, encounter_class, status).
        include_document_reference: Whether to include DocumentReference entry in bundle.
        include_provenance_resource: Whether to include top-level Provenance entry in bundle.
        pipeline_info: Optional execution metadata for the software Device agent.
        pipeline_timing: Optional execution timestamps (started_at, completed_at).
        review_info: Optional human review metadata (reviewer_id, review_decision, reviewed_at).

    Returns:
        FHIR R4B Bundle of type 'collection' carrying resources and provenance extensions.
    """
    if not claims:
        raise ValueError("claims sequence must not be empty")

    resolved_doc_id = document_id or claims[0].document_id
    doc_slug = _sanitize_id(resolved_doc_id.lower())

    # Metadata & Timing extraction (Phase 4.3)
    pip_info = pipeline_info or (document_metadata.get("pipeline_info") if document_metadata else None)
    pip_timing = pipeline_timing or (document_metadata.get("pipeline_timing") if document_metadata else None)
    rev_info = review_info or (document_metadata.get("review_info") if document_metadata else None)

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

    # 3. Patient, Organization, Practitioner (Grounded Author / Reviewer only)
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
    org_name = None
    if document_metadata:
        for k in ("organization", "organization_name", "facility_name", "hospital_name"):
            if document_metadata.get(k):
                org_name = str(document_metadata[k]).strip()
                break
    organization = Organization(
        id=org_id_str,
        name=org_name or "Clinical Healthcare Organization",
    )

    # Source author: populate only when explicitly identified (Phase 4.3 Requirement 3)
    author_name = None
    if document_metadata:
        for k in ("author", "author_name", "attending_physician", "ordering_provider", "doctor"):
            if document_metadata.get(k):
                author_name = str(document_metadata[k]).strip()
                break
    author_pract = None
    if author_name:
        author_pract = Practitioner(
            id=f"pract-author-{doc_slug}",
            name=[HumanName(text=author_name)],
        )

    # Human reviewer: populate only when authenticated review identity and decision exist
    reviewer_pract = None
    if rev_info and (rev_info.get("reviewer_id") or rev_info.get("reviewer_name")):
        rev_id = rev_info.get("reviewer_id") or "1"
        rev_slug = _sanitize_id(str(rev_id).lower())
        rev_name = rev_info.get("reviewer_name") or f"Reviewer {rev_id}"
        reviewer_pract = Practitioner(
            id=f"pract-reviewer-{rev_slug}",
            name=[HumanName(text=rev_name)],
        )

    # 3b. DocumentReference & Encounter (Phase 8.8)
    doc_ref = None
    if include_document_reference or document_metadata is not None:
        doc_ref = _map_document_reference(resolved_doc_id, patient.id, document_metadata)

    encounter = _map_encounter(resolved_doc_id, patient.id, encounter_info)

    # 4. Map Lab Observation resources
    observations: list[Observation] = []
    all_evidence_texts = [ev.text for ev in evidence_map.values() if ev.text]

    for grp_idx, (grp_key, grp) in enumerate(lab_groups.items()):
        # Phase 4.1 Negation Safety Gate:
        # A claim with negated == True must NEVER generate an Observation!
        if any(getattr(c, "negated", False) for c in grp):
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "SUPPRESSED",
                    "reason": "Negation safety gate: group contains clinically negated claim",
                })
            continue

        # Phase 4.2 Planned Diagnostic Test Suppression:
        # Planned tests without completed result must never generate an Observation
        if any(getattr(c, "temporal_status", "unspecified") == "planned" for c in grp):
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "SUPPRESSED",
                    "reason": "Planned diagnostic test suppression: planned lab without result",
                })
            continue

        # Discontinued is a medication state; laboratory findings cannot be discontinued
        if any(getattr(c, "temporal_status", "unspecified") == "discontinued" for c in grp):
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "SUPPRESSED",
                    "reason": "Invalid lab temporal status: laboratory findings cannot be discontinued",
                })
            continue

        # Phase 4.4 Cross-analyte contamination check:
        # If a single group contains claims for multiple distinct test names, fail closed!
        test_name_claims = [c for c in grp if c.field_type == "lab.test_name"]
        if len(test_name_claims) > 1:
            distinct_norm_codes = {c.normalized_code for c in test_name_claims if c.normalized_code}
            distinct_names = {c.value.strip().lower() for c in test_name_claims if c.value}
            if len(distinct_norm_codes) > 1 or (len(distinct_norm_codes) == 0 and len(distinct_names) > 1):
                logger.warning(
                    "Lab group '%s' contains conflicting test names: %s. Suppressing group to prevent cross-analyte contamination.",
                    grp_key,
                    distinct_names,
                )
                if audit_trail is not None:
                    audit_trail.append({
                        "group_id": grp_key,
                        "status": "SUPPRESSED",
                        "reason": f"Cross-analyte contamination: conflicting test names {sorted(distinct_names)} in same group",
                    })
                continue

        # Phase 4.4 Deterministic Candidate Selection for Lab Slots
        val_claim, val_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "lab.value"],
            verification_map,
            field_slot="lab.value",
            group_id=grp_key,
            audit_trail=audit_trail,
        )
        name_claim, name_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "lab.test_name"],
            verification_map,
            field_slot="lab.test_name",
            group_id=grp_key,
            audit_trail=audit_trail,
        )
        unit_claim, unit_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "lab.unit"],
            verification_map,
            field_slot="lab.unit",
            group_id=grp_key,
            audit_trail=audit_trail,
        )
        flag_claim, flag_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "lab.flag"],
            verification_map,
            field_slot="lab.flag",
            group_id=grp_key,
            audit_trail=audit_trail,
        )
        ref_claim, ref_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "lab.reference_range"],
            verification_map,
            field_slot="lab.reference_range",
            group_id=grp_key,
            audit_trail=audit_trail,
        )

        # Malformed group check: must have at least test name or value
        if not val_claim and not name_claim:
            logger.warning("Lab group '%s' has neither test name nor value claim; skipping malformed group.", grp_key)
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "SUPPRESSED",
                    "reason": "Malformed group: lacks both test name and value claims",
                })
            continue

        grp_shadowed = val_shadowed + name_shadowed + unit_shadowed + flag_shadowed + ref_shadowed

        anchor_claim = val_claim or name_claim or grp[0]
        anchor_ver = verification_map[anchor_claim.claim_id]
        anchor_ev = evidence_map[anchor_claim.evidence_id]

        res_provenance = build_resource_provenance_extension(anchor_claim, anchor_ver, anchor_ev)

        # Observation lifecycle status strictly derived from grounded report lifecycle (Phase 4.2)
        # NEVER derived from anchor_ver.label (SUPPORTED/DISCREPANCY) or human review state!
        grp_evidence_texts = [
            evidence_map[c.evidence_id].text
            for c in grp
            if c.evidence_id in evidence_map and evidence_map[c.evidence_id].text
        ]
        obs_status = resolve_observation_lifecycle_status(
            document_metadata=document_metadata,
            document_type=document_type,
            evidence_texts=grp_evidence_texts or all_evidence_texts,
        )

        # Temporal status determination (Phase 4.2)
        grp_temp_status = next(
            (c.temporal_status for c in grp if getattr(c, "temporal_status", "unspecified") not in ("unspecified", None)),
            getattr(anchor_claim, "temporal_status", "unspecified"),
        )
        obs_extensions = [res_provenance]
        if grp_temp_status in OBSERVATION_TEMPORAL_STATUSES:
            obs_extensions.append(
                Extension(url=EXT_TEMPORAL_STATUS, valueCode=grp_temp_status)
            )

        # Attach competing candidate provenance extensions (Phase 4.4)
        for s_rec in grp_shadowed:
            obs_extensions.append(
                Extension(
                    url=EXT_COMPETING_CANDIDATE,
                    extension=[
                        Extension(url="slot", valueString=s_rec["slot"]),
                        Extension(url="selectedClaimId", valueString=s_rec["selected_claim_id"]),
                        Extension(url="competingClaimId", valueString=s_rec["competing_claim_id"]),
                        Extension(url="competingValue", valueString=str(s_rec["competing_value"])),
                        Extension(url="selectionReason", valueString=s_rec["selection_reason"]),
                    ],
                )
            )
        # unspecified or non-observation states -> omit extension entirely per Phase 4.0.2 / 4.2.1

        # Explanatory note for historical findings
        obs_notes = None
        if grp_temp_status == "historical":
            obs_notes = [Annotation(text="Historical laboratory finding documented in clinical narrative.")]

        # Effective time: only source-grounded clinical timing, never fabricated
        eff_dt, eff_period = resolve_observation_effective_time(
            document_metadata=document_metadata,
            temporal_status=grp_temp_status,
        )

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

        # Value Quantity / Value CodeableConcept / Value String & element-level provenance for value/unit
        val_quantity = None
        val_codeable = None
        val_string = None
        if val_claim:
            num_val = val_claim.value_numeric
            if num_val is None:
                # Qualitative claims must NEVER be parsed as numbers (e.g. HIV-1 -> 1, Type 2 -> 2)
                from clindoc.normalize.normalizer import normalize_qualitative_value
                raw_clean = val_claim.value.strip().lower()
                is_qual = (
                    (val_claim.normalized_code and val_claim.normalized_code.upper() in QUALITATIVE_SNOMED_MAP)
                    or normalize_qualitative_value(val_claim.value) is not None
                    or raw_clean in ("no growth", "no-growth", "trace", "trace amount")
                    or raw_clean.upper() in QUALITATIVE_SNOMED_MAP
                )
                if not is_qual:
                    m = re.search(r"[-+]?\d*\.?\d+", val_claim.value)
                    if m:
                        try:
                            num_val = float(m.group())
                        except ValueError:
                            num_val = None

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

            if num_val is not None:
                unit_str = unit_claim.value if unit_claim else (val_claim.unit_raw or None)
                ucum_sys = None
                ucum_code = None
                if unit_claim and unit_claim.normalized_code and unit_claim.code_system == "UCUM":
                    ucum_sys = UCUM_SYSTEM
                    ucum_code = unit_claim.normalized_code
                elif unit_str:
                    ucum_sys = UCUM_SYSTEM
                    ucum_code = unit_str.strip()

                val_quantity = Quantity(
                    value=round(Decimal(str(num_val)), 4),
                    unit=unit_str,
                    system=ucum_sys,
                    code=ucum_code,
                    extension=vq_exts,
                )
            else:
                # Qualitative determination (Phase 4.1):
                # Map verified qualitative values to Observation.valueCodeableConcept using approved SNOMED CT mappings
                from clindoc.normalize.normalizer import normalize_qualitative_value
                snomed_key = None
                if val_claim.normalized_code and val_claim.normalized_code.upper() in QUALITATIVE_SNOMED_MAP:
                    snomed_key = val_claim.normalized_code.upper()
                else:
                    norm_k = normalize_qualitative_value(val_claim.value)
                    if norm_k and norm_k in QUALITATIVE_SNOMED_MAP:
                        snomed_key = norm_k
                    else:
                        raw_c = val_claim.value.strip().lower()
                        if "no growth" in raw_c:
                            snomed_key = "NO_GROWTH"
                        elif "trace" in raw_c:
                            snomed_key = "TRACE"
                        elif raw_c.upper() in QUALITATIVE_SNOMED_MAP:
                            snomed_key = raw_c.upper()

                if snomed_key and snomed_key in QUALITATIVE_SNOMED_MAP:
                    s_code, s_disp = QUALITATIVE_SNOMED_MAP[snomed_key]
                    val_codeable = CodeableConcept(
                        coding=[
                            Coding(
                                system=SNOMED_SYSTEM,
                                code=s_code,
                                display=s_disp,
                            )
                        ],
                        text=val_claim.value,
                        extension=vq_exts,
                    )
                else:
                    # Unmapped qualitative determination (safe text-only fallback)
                    # Never fabricate a code or emit valueQuantity = 0.0
                    val_string = val_claim.value

        # Interpretation & element-level provenance for flag (Phase 4.1)
        interpretations = None
        if flag_claim:
            flag_upper = flag_claim.value.strip().upper()
            f_code = None
            f_disp = None
            if flag_upper in ("HH", "CRITICAL HIGH", "PANIC HIGH"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["HH"]
            elif flag_upper in ("H", "HIGH"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["H"]
            elif flag_upper in ("LL", "CRITICAL LOW", "PANIC LOW"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["LL"]
            elif flag_upper in ("L", "LOW"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["L"]
            elif flag_upper in ("AA", "CRITICAL ABNORMAL"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["AA"]
            elif flag_upper in ("A", "CRITICAL", "ABNORMAL", "PANIC"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["A"]
            elif flag_upper in ("N", "NORMAL", "NORM", "WNL"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["N"]
            elif flag_upper in ("IND", "INDETERMINATE"):
                f_code, f_disp = INTERPRETATION_HL7_MAP["IND"]

            flag_ext = build_element_provenance_extension(
                flag_claim, verification_map[flag_claim.claim_id], evidence_map[flag_claim.evidence_id]
            )
            # Unrecognized flags must use text-only CodeableConcept with NO fabricated coding
            hl7_coding = (
                [
                    Coding(
                        system=OBS_INTERPRETATION_SYSTEM,
                        code=f_code,
                        display=f_disp,
                    )
                ]
                if f_code is not None
                else None
            )

            interpretations = [
                CodeableConcept(
                    coding=hl7_coding,
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
                            code="laboratory",
                            display="Laboratory",
                        )
                    ]
                )
            ],
            code=obs_code,
            subject=Reference(reference=f"Patient/{patient.id}"),
            encounter=Reference(reference=f"Encounter/{encounter.id}") if encounter else None,
            derivedFrom=[Reference(reference=f"DocumentReference/{doc_ref.id}")] if doc_ref else None,
            effectiveDateTime=eff_dt,
            effectivePeriod=eff_period,
            valueQuantity=val_quantity,
            valueCodeableConcept=val_codeable,
            valueString=val_string,
            interpretation=interpretations,
            note=obs_notes,
            referenceRange=ref_ranges,
            extension=obs_extensions,
        )
        observations.append(obs_res)

    # 5. Map Medication resources (DEC-008)
    med_resources: list[MedicationRequest | MedicationStatement] = []
    rx_doc_types = {"clinic_rx_pad", "mar_order_sheet", "pharmacy_label", "prescription", "order"}
    is_rx_doc = bool(document_type and document_type.strip().lower() in rx_doc_types)

    for grp_idx, (grp_key, grp) in enumerate(med_groups.items()):
        # Cross-medication contamination check:
        med_name_claims = [c for c in grp if c.field_type == "med.name"]
        if len(med_name_claims) > 1:
            distinct_norm_codes = {c.normalized_code for c in med_name_claims if c.normalized_code}
            distinct_names = {c.value.strip().lower() for c in med_name_claims if c.value}
            if len(distinct_norm_codes) > 1 or (len(distinct_norm_codes) == 0 and len(distinct_names) > 1):
                logger.warning(
                    "Medication group '%s' contains conflicting medication names: %s. Suppressing group to prevent cross-medication contamination.",
                    grp_key,
                    distinct_names,
                )
                if audit_trail is not None:
                    audit_trail.append({
                        "group_id": grp_key,
                        "status": "SUPPRESSED",
                        "reason": f"Cross-medication contamination: conflicting drug names {sorted(distinct_names)} in same group",
                    })
                continue

        name_claim, name_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "med.name"],
            verification_map,
            field_slot="med.name",
            group_id=grp_key,
            audit_trail=audit_trail,
        )
        route_claim, route_shadowed = select_deterministic_slot_candidate(
            [c for c in grp if c.field_type == "med.route"],
            verification_map,
            field_slot="med.route",
            group_id=grp_key,
            audit_trail=audit_trail,
        )

        if grp_key == "__no_anchor__" or grp_key.startswith("__no_anchor"):
            logger.warning("Medication group '%s' is an unanchored orphan group; skipping.", grp_key)
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "SUPPRESSED",
                    "reason": "Unanchored orphan medication attributes without drug name anchor",
                })
            continue

        has_any_dose = any(c.field_type == "med.dose" for c in grp)
        if not name_claim and not has_any_dose:
            logger.warning("Medication group '%s' has neither medication name nor dose claim; skipping malformed group.", grp_key)
            if audit_trail is not None:
                audit_trail.append({
                    "group_id": grp_key,
                    "status": "SUPPRESSED",
                    "reason": "Malformed group: lacks both medication name and dose claims",
                })
            continue

        dosages, dosage_shadowed = _build_regimen_dosages(
            grp=grp,
            name_claim=name_claim,
            route_claim=route_claim,
            verification_map=verification_map,
            evidence_map=evidence_map,
            grp_key=grp_key,
            audit_trail=audit_trail,
        )

        grp_med_shadowed = name_shadowed + route_shadowed + dosage_shadowed

        anchor_claim = name_claim or next((c for c in grp if c.field_type == "med.dose"), grp[0])
        anchor_ver = verification_map[anchor_claim.claim_id]
        anchor_ev = evidence_map[anchor_claim.evidence_id]

        res_provenance = build_resource_provenance_extension(anchor_claim, anchor_ver, anchor_ev)

        # Hybrid routing rule per DEC-008 & REQ-44
        has_planned = any(c.temporal_status == "planned" for c in grp)
        is_request = is_rx_doc or has_planned

        temp_status = next(
            (c.temporal_status for c in grp if c.temporal_status != "unspecified"),
            anchor_claim.temporal_status,
        )

        req_status_override = None
        stat_status_override = None

        if enforce_req44_lifecycle:
            med_ev_texts = [
                evidence_map[c.evidence_id].text
                for c in grp
                if c.evidence_id in evidence_map and evidence_map[c.evidence_id].text
            ]
            med_grp_full_text = " ".join(med_ev_texts)
            med_name_str = (name_claim.value.strip() if name_claim and name_claim.value else "").strip()

            # Category 3: Ambiguous, speculative, family-history, or unanchored mentions
            if (
                temp_status in ("ambiguous", "speculative")
                or FAMILY_HISTORY_REGEX.search(med_grp_full_text)
                or SPECULATIVE_REGEX.search(med_grp_full_text)
            ):
                logger.warning(
                    "Medication group '%s' is speculative or family history; suppressing under REQ-44.",
                    grp_key,
                )
                if audit_trail is not None:
                    audit_trail.append({
                        "group_id": grp_key,
                        "status": "SUPPRESSED",
                        "reason": "REQ-44: Ambiguous, speculative, or family-history mention suppressed without direct patient intake/order anchor",
                    })
                continue

            # Category 2: Historical consumption with unknown completion
            if temp_status == "historical" or re.search(r"\b(?:past\s+medical\s+history|history\s+of\s+use|took\s+in\s+\d{4}|prior\s+trial)\b", med_grp_full_text, re.IGNORECASE):
                is_request = False
                stat_status_override = "unknown"

            # Category 1: Completed Course (requires explicit completion linked to medication)
            elif _is_medication_completed_linked(med_name_str, med_grp_full_text):
                is_request = False
                stat_status_override = "completed"

            # Check for unlinked / detached completion (e.g. physical therapy completed, but not this med)
            elif COMPLETED_COURSE_REGEX.search(med_grp_full_text) or temp_status == "completed":
                # Detached non-med completion or ungrounded completion:
                # NEVER fall back to active! If active intake not verified, map to unknown.
                is_request = False
                if AFFIRMATIVE_INTAKE_REGEX.search(med_grp_full_text) and not UNCONFIRMED_INTAKE_REGEX.search(med_grp_full_text):
                    stat_status_override = "active"
                else:
                    stat_status_override = "unknown"
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "status": "UNKNOWN",
                            "reason": "REQ-44: Detached or ungrounded course completion does not prove medication completion or active use; mapped to status='unknown'",
                        })

            # Category 7, 8, 9: Discontinuation / Cancellation
            elif temp_status == "discontinued" or UNREFERENCED_CANCEL_DIRECTIVE_REGEX.search(med_grp_full_text):
                # Category 7: Patient Cessation -> MedicationStatement(status='stopped')
                if PATIENT_CESSATION_REGEX.search(med_grp_full_text):
                    is_request = False
                    stat_status_override = "stopped"
                # Category 8: Prescriber Cancellation linked to order AND target medication -> MedicationRequest(status='cancelled')
                elif _is_order_cancellation_linked_to_med(med_name_str, med_ev_texts):
                    is_request = True
                    req_status_override = "cancelled"
                # Category 9: Unreferenced / Unlinked Cancellation -> FAIL CLOSED: SUPPRESSED
                else:
                    logger.warning(
                        "Medication group '%s' has discontinued status without verifiable linked order reference or patient cessation; failing closed under REQ-44.",
                        grp_key,
                    )
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "status": "SUPPRESSED",
                            "reason": "REQ-44: Unreferenced cancellation lacks verifiable prior order reference or patient cessation evidence",
                        })
                    continue

            # Category 4: Clinician Directive / Plan (Check 2: Authorship verification)
            elif has_planned:
                if PATIENT_PLAN_COMMENTARY_REGEX.search(med_grp_full_text):
                    logger.warning(
                        "Medication group '%s' in plan context contains patient speculation or commentary; failing closed under REQ-44.",
                        grp_key,
                    )
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "status": "SUPPRESSED",
                            "reason": "REQ-44: Ambiguous authorship or patient speculation in plan context fails closed; suppressed",
                        })
                    continue
                elif CLINICIAN_PLAN_DIRECTIVE_REGEX.search(med_grp_full_text):
                    is_request = True
                    temp_status = "planned"
                else:
                    # Heading alone without clinician directive verb or patient context -> ambiguous authorship fails closed
                    logger.warning(
                        "Medication group '%s' in plan context lacks verifiable clinician directive; failing closed under REQ-44.",
                        grp_key,
                    )
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "status": "SUPPRESSED",
                            "reason": "REQ-44: Ambiguous authorship in plan context without explicit clinician directive fails closed; suppressed",
                        })
                    continue

            # Category 5: New Prescription Order
            elif is_rx_doc:
                is_request = True

            # Category 6: Verified Patient Intake (Check 5: Evidence check, remove catch-all active)
            else:
                is_request = False
                if UNCONFIRMED_INTAKE_REGEX.search(med_grp_full_text):
                    stat_status_override = "unknown"
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "status": "UNKNOWN",
                            "reason": "REQ-44: Unconfirmed patient intake mapped to status='unknown'",
                        })
                elif AFFIRMATIVE_INTAKE_REGEX.search(med_grp_full_text):
                    stat_status_override = "active"
                elif ACTIVE_SIG_REGEX.search(med_grp_full_text) and temp_status == "current":
                    stat_status_override = "active"
                else:
                    # Document type alone without affirmative intake evidence maps to unknown
                    stat_status_override = "unknown"
                    if audit_trail is not None:
                        audit_trail.append({
                            "group_id": grp_key,
                            "status": "UNKNOWN",
                            "reason": "REQ-44: Unverified patient intake lacks affirmative evidence of current consumption; mapped to status='unknown'",
                        })

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

        if is_request:
            med_res = map_medication_request(
                resource_id=f"medreq-{doc_slug}-{grp_idx + 1}",
                patient_id=patient.id,
                medication_code=med_code,
                dosage_instructions=dosages,
                temporal_status=temp_status,
                extensions=[res_provenance],
                shadowed_records=grp_med_shadowed,
                encounter_id=encounter.id if encounter else None,
                doc_ref_id=doc_ref.id if doc_ref else None,
                status_override=req_status_override,
            )
        else:
            med_res = map_medication_statement(
                resource_id=f"medstat-{doc_slug}-{grp_idx + 1}",
                patient_id=patient.id,
                medication_code=med_code,
                dosages=dosages,
                temporal_status=temp_status,
                extensions=[res_provenance],
                shadowed_records=grp_med_shadowed,
                encounter_id=encounter.id if encounter else None,
                doc_ref_id=doc_ref.id if doc_ref else None,
                status_override=stat_status_override,
            )
        med_resources.append(med_res)

    # 6. DiagnosticReport creation & suppression (Phase 4.4)
    diag_report = None
    if _should_create_diagnostic_report(
        document_metadata=document_metadata,
        document_type=document_type,
        document_id=resolved_doc_id,
        evidence_texts=all_evidence_texts,
        has_observations=bool(observations),
    ):
        anchor_ext = observations[0].extension[0] if observations and observations[0].extension else None
        diag_report = _map_canonical_diagnostic_report(
            document_id=resolved_doc_id,
            patient_id=patient.id,
            observations=observations,
            organization_id=organization.id,
            encounter_id=encounter.id if encounter else None,
            doc_ref=doc_ref,
            document_metadata=document_metadata,
            document_type=document_type,
            evidence_texts=all_evidence_texts,
            anchor_extension=anchor_ext,
        )

    # 7. Assemble Bundle
    entries = [
        BundleEntry(fullUrl=f"Patient/{patient.id}", resource=patient),
        BundleEntry(fullUrl=f"Organization/{organization.id}", resource=organization),
    ]

    device = None
    if include_provenance_resource:
        device = _build_pipeline_device(doc_slug, pip_info)
        entries.append(BundleEntry(fullUrl=f"Device/{device.id}", resource=device))

    if author_pract:
        entries.append(BundleEntry(fullUrl=f"Practitioner/{author_pract.id}", resource=author_pract))

    if reviewer_pract:
        entries.append(BundleEntry(fullUrl=f"Practitioner/{reviewer_pract.id}", resource=reviewer_pract))

    if doc_ref:
        entries.append(BundleEntry(fullUrl=f"DocumentReference/{doc_ref.id}", resource=doc_ref))

    if encounter:
        entries.append(BundleEntry(fullUrl=f"Encounter/{encounter.id}", resource=encounter))

    if diag_report:
        entries.append(BundleEntry(fullUrl=f"DiagnosticReport/{diag_report.id}", resource=diag_report))

    for obs in observations:
        entries.append(BundleEntry(fullUrl=f"Observation/{obs.id}", resource=obs))

    for med in med_resources:
        entries.append(BundleEntry(fullUrl=f"{med.resource_type}/{med.id}", resource=med))

    if include_provenance_resource and device:
        target_refs = [Reference(reference=f"Observation/{obs.id}") for obs in observations]
        target_refs.extend(Reference(reference=f"{med.resource_type}/{med.id}") for med in med_resources)
        if diag_report:
            target_refs.append(Reference(reference=f"DiagnosticReport/{diag_report.id}"))

        pip_start = None
        pip_end = None
        pip_rec = None
        if pip_timing:
            s_raw = pip_timing.get("started_at") or pip_timing.get("pipeline_started_at")
            e_raw = pip_timing.get("completed_at") or pip_timing.get("pipeline_completed_at")
            r_raw = pip_timing.get("recorded_at")
            if isinstance(s_raw, str):
                try:
                    pip_start = datetime.datetime.fromisoformat(s_raw)
                except Exception:
                    pip_start = None
            elif isinstance(s_raw, datetime.datetime):
                pip_start = s_raw

            if isinstance(e_raw, str):
                try:
                    pip_end = datetime.datetime.fromisoformat(e_raw)
                except Exception:
                    pip_end = None
            elif isinstance(e_raw, datetime.datetime):
                pip_end = e_raw

            if isinstance(r_raw, str):
                try:
                    pip_rec = datetime.datetime.fromisoformat(r_raw)
                except Exception:
                    pip_rec = None
            elif isinstance(r_raw, datetime.datetime):
                pip_rec = r_raw

        prov_extract = _map_provenance(
            resolved_doc_id,
            target_references=target_refs,
            device_id=device.id,
            doc_ref_id=doc_ref.id if doc_ref else None,
            author_practitioner_id=author_pract.id if author_pract else None,
            pipeline_started_at=pip_start,
            pipeline_completed_at=pip_end,
            recorded_at=pip_rec,
        )
        entries.append(BundleEntry(fullUrl=f"Provenance/{prov_extract.id}", resource=prov_extract))

        if reviewer_pract:
            rev_dec = rev_info.get("review_decision") or rev_info.get("review_state") or "ACCEPTED"
            rev_time = rev_info.get("reviewed_at")
            rev_rec = rev_info.get("recorded_at")
            if isinstance(rev_time, str):
                try:
                    rev_time = datetime.datetime.fromisoformat(rev_time)
                except Exception:
                    rev_time = None
            elif not isinstance(rev_time, datetime.datetime):
                rev_time = None

            rev_rec_dt = None
            if isinstance(rev_rec, str):
                try:
                    rev_rec_dt = datetime.datetime.fromisoformat(rev_rec)
                except Exception:
                    rev_rec_dt = None
            elif isinstance(rev_rec, datetime.datetime):
                rev_rec_dt = rev_rec

            prov_review = _map_review_provenance(
                resolved_doc_id,
                target_references=target_refs,
                reviewer_practitioner_id=reviewer_pract.id,
                review_decision=str(rev_dec),
                reviewed_at=rev_time,
                recorded_at=rev_rec_dt,
            )
            entries.append(BundleEntry(fullUrl=f"Provenance/{prov_review.id}", resource=prov_review))

    return Bundle(
        id=f"bundle-{doc_slug}",
        type="collection",
        entry=entries,
    )
