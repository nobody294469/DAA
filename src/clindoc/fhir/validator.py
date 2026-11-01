"""Validation module for generated FHIR R4 resources and Bundles."""

import json
from typing import Any
from pydantic import BaseModel, Field

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.device import Device
from fhir.resources.R4B.diagnosticreport import DiagnosticReport
from fhir.resources.R4B.documentreference import DocumentReference
from fhir.resources.R4B.encounter import Encounter
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.provenance import Provenance


from clindoc.fhir.constants import (
    DIAGNOSTIC_REPORT_STATUSES,
    EXT_CLAIM_PROVENANCE,
    EXT_COMPETING_CANDIDATE,
    EXT_SOURCE_EVIDENCE,
    EXT_TEMPORAL_STATUS,
    EXT_VERIFICATION_STATUS,
    LOINC_SYSTEM,
    MEDICATION_REQUEST_TEMPORAL_STATUSES,
    MEDICATION_STATEMENT_TEMPORAL_STATUSES,
    OBSERVATION_TEMPORAL_STATUSES,
    UCUM_SYSTEM,
)
from clindoc.verify.schema import VerificationStatus


VALID_MED_REQUEST_STATUSES = {
    "active",
    "on-hold",
    "cancelled",
    "completed",
    "entered-in-error",
    "stopped",
    "draft",
    "unknown",
}

VALID_MED_STATEMENT_STATUSES = {
    "active",
    "completed",
    "entered-in-error",
    "intended",
    "stopped",
    "on-hold",
    "unknown",
    "not-taken",
}


VALID_VERIFICATION_STATUSES = {
    # Canonical labels (PROJECT_SPEC §10)
    "SUPPORTED",
    "DISCREPANCY",
    "INSUFFICIENT_EVIDENCE",
    # Legacy statuses (Phase 0)
    VerificationStatus.VERIFIED.value,
    VerificationStatus.DISCREPANCY.value,
    VerificationStatus.MISSING_SOURCE_EVIDENCE.value,
    VerificationStatus.UNCHECKED.value,
}


class BundleValidationResult(BaseModel):
    """Structured report of FHIR R4 Bundle validation."""

    is_valid: bool
    bundle_id: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    resource_counts: dict[str, int] = Field(default_factory=dict)
    verification_status_counts: dict[str, int] = Field(default_factory=dict)


class FHIRValidator:
    """Validator ensuring FHIR R4 compliance, referential integrity, and clinical metadata completeness."""

    @classmethod
    def validate_bundle(cls, bundle_input: Bundle | str | dict[str, Any]) -> BundleValidationResult:
        """Validate a FHIR Bundle instance, raw JSON string, or dictionary.

        Args:
            bundle_input: Bundle object, JSON string, or parsed dict.

        Returns:
            BundleValidationResult containing boolean validity, error messages, and metrics.
        """
        errors: list[str] = []
        warnings: list[str] = []
        resource_counts: dict[str, int] = {}
        ver_counts: dict[str, int] = {k: 0 for k in VALID_VERIFICATION_STATUSES}

        # 1. Parse / re-parse via fhir.resources model
        bundle: Bundle
        try:
            if isinstance(bundle_input, str):
                bundle = Bundle.parse_raw(bundle_input)
            elif isinstance(bundle_input, dict):
                bundle = Bundle.parse_obj(bundle_input)
            elif isinstance(bundle_input, Bundle):
                # Ensure it can serialize and re-parse cleanly
                bundle = Bundle.parse_raw(bundle_input.json())
            else:
                return BundleValidationResult(
                    is_valid=False,
                    bundle_id="unknown",
                    errors=[f"Unsupported bundle input type: {type(bundle_input)}"],
                )
        except Exception as exc:
            return BundleValidationResult(
                is_valid=False,
                bundle_id="unknown",
                errors=[f"FHIR R4 Schema parsing failure: {str(exc)}"],
            )

        bundle_id = bundle.id or "unknown"

        if bundle.type != "collection":
            warnings.append(f"Bundle type is '{bundle.type}', expected 'collection'")

        if not bundle.entry:
            errors.append("Bundle has no entries")
            return BundleValidationResult(
                is_valid=False,
                bundle_id=bundle_id,
                errors=errors,
            )

        # 2. Collect available resource references & detect duplicate IDs
        available_refs: set[str] = set()
        seen_keys: set[str] = set()
        for idx, entry in enumerate(bundle.entry):
            res = entry.resource
            if res is None:
                errors.append(f"Entry {idx} missing resource object")
                continue
            r_type = res.resource_type
            r_id = res.id
            resource_counts[r_type] = resource_counts.get(r_type, 0) + 1
            if r_type and r_id:
                r_key = f"{r_type}/{r_id}"
                if r_key in seen_keys:
                    errors.append(f"Duplicate resource identified in bundle: '{r_key}'")
                seen_keys.add(r_key)
                available_refs.add(r_key)

        # 3. Validate referential integrity & resource-specific invariants
        has_patient = False
        has_diagnostic_report = False

        for idx, entry in enumerate(bundle.entry):
            res = entry.resource
            if res is None:
                continue

            r_type = res.resource_type

            # DEC-020 Base FHIR R4B validation: Reject fabricated US Core profile claims
            if hasattr(res, "meta") and res.meta and res.meta.profile:
                for p_url in res.meta.profile:
                    if "hl7.org/fhir/us/core" in str(p_url).lower():
                        errors.append(
                            f"{r_type}/{res.id} declares US Core profile '{p_url}' which violates DEC-020 (target is FHIR R4B Base)"
                        )

            if r_type == "Patient":
                has_patient = True

            elif r_type == "DiagnosticReport":
                has_diagnostic_report = True
                diag: DiagnosticReport = res

                # Check status
                if diag.status not in DIAGNOSTIC_REPORT_STATUSES:
                    errors.append(f"DiagnosticReport/{diag.id} invalid status '{diag.status}' (must be valid FHIR R4B status)")

                # Check code
                if not diag.code or (not diag.code.coding and not diag.code.text):
                    errors.append(f"DiagnosticReport/{diag.id} missing code")

                # Check subject reference
                if not diag.subject or not diag.subject.reference:
                    errors.append(f"DiagnosticReport/{diag.id} missing subject reference")
                elif diag.subject.reference not in available_refs:
                    errors.append(f"DiagnosticReport/{diag.id} subject '{diag.subject.reference}' not found in bundle")

                # Check encounter reference
                if diag.encounter and diag.encounter.reference not in available_refs:
                    errors.append(f"DiagnosticReport/{diag.id} encounter '{diag.encounter.reference}' not found in bundle")

                # Check result references
                if not diag.result:
                    errors.append(f"DiagnosticReport/{diag.id} missing result references (empty DiagnosticReport)")
                else:
                    for r_ref in diag.result:
                        if not r_ref.reference or r_ref.reference not in available_refs:
                            errors.append(f"DiagnosticReport/{diag.id} result reference '{r_ref.reference}' not found in bundle")
                        elif not r_ref.reference.startswith("Observation/"):
                            errors.append(f"DiagnosticReport/{diag.id} result reference '{r_ref.reference}' must target an Observation")

                # Check presentedForm attachments
                if diag.presentedForm:
                    for pf in diag.presentedForm:
                        if not pf.contentType:
                            errors.append(f"DiagnosticReport/{diag.id} presentedForm attachment missing contentType")

            elif r_type == "Observation":
                obs: Observation = res

                # Check subject reference
                if not obs.subject or not obs.subject.reference:
                    errors.append(f"Observation/{obs.id} missing subject reference")
                elif obs.subject.reference not in available_refs:
                    errors.append(f"Observation/{obs.id} subject '{obs.subject.reference}' not found in bundle")

                # Check encounter reference
                if obs.encounter and obs.encounter.reference not in available_refs:
                    errors.append(f"Observation/{obs.id} encounter '{obs.encounter.reference}' not found in bundle")

                # Check derivedFrom references
                if obs.derivedFrom:
                    for df in obs.derivedFrom:
                        if df.reference and df.reference not in available_refs:
                            errors.append(f"Observation/{obs.id} derivedFrom '{df.reference}' not found in bundle")

                # Check status
                if obs.status not in ("registered", "preliminary", "final", "amended", "corrected", "cancelled", "entered-in-error", "unknown"):
                    errors.append(f"Observation/{obs.id} invalid status '{obs.status}'")

                # Check code
                if not obs.code:
                    errors.append(f"Observation/{obs.id} missing code")
                else:
                    has_loinc = False
                    if obs.code.coding:
                        for c in obs.code.coding:
                            if c.system == LOINC_SYSTEM and c.code:
                                has_loinc = True
                    if not has_loinc:
                        warnings.append(f"Observation/{obs.id} has no LOINC coding")

                # Check value[x] exclusivity and presence (FHIR R4B Base + ClinicalDoc application constraint)
                val_choices = [
                    obs.valueQuantity is not None,
                    obs.valueCodeableConcept is not None,
                    obs.valueString is not None,
                    obs.valueBoolean is not None,
                    obs.valueInteger is not None,
                    obs.valueRange is not None,
                    obs.valueRatio is not None,
                    obs.valueSampledData is not None,
                    obs.valueTime is not None,
                    obs.valueDateTime is not None,
                    obs.valuePeriod is not None,
                ]
                num_populated = sum(1 for c in val_choices if c)
                has_data_absent = obs.dataAbsentReason is not None and (
                    bool(obs.dataAbsentReason.coding) or bool(obs.dataAbsentReason.text)
                )
                has_components = bool(obs.component)

                if num_populated == 0:
                    if not has_data_absent and not has_components:
                        errors.append(
                            f"Observation/{obs.id} missing value[x] without dataAbsentReason or component "
                            "(ClinicalDoc application requirement: atomic observations must provide either value[x] or dataAbsentReason)"
                        )
                elif num_populated > 1:
                    errors.append(
                        f"Observation/{obs.id} has multiple value[x] choices populated "
                        "(FHIR R4B choice elements allow at most one value[x])"
                    )
                elif has_data_absent:
                    errors.append(
                        f"Observation/{obs.id} has both value[x] and dataAbsentReason populated "
                        "(FHIR R4B invariant: dataAbsentReason SHALL only be present if value[x] is absent)"
                    )
                elif obs.valueQuantity is not None:
                    if obs.valueQuantity.value is None:
                        errors.append(f"Observation/{obs.id} valueQuantity.value is None")
                    if not obs.valueQuantity.unit:
                        warnings.append(f"Observation/{obs.id} valueQuantity.unit is missing")
                elif obs.valueString is not None:
                    if not obs.valueString.strip():
                        errors.append(f"Observation/{obs.id} valueString is empty")
                elif obs.valueCodeableConcept is not None:
                    if not obs.valueCodeableConcept.coding and not obs.valueCodeableConcept.text:
                        errors.append(f"Observation/{obs.id} valueCodeableConcept is empty")

                # Check verification / provenance extensions
                ver_status_found = False
                source_ev_found = False
                if obs.extension:
                    for ext in obs.extension:
                        if ext.url == EXT_CLAIM_PROVENANCE:
                            ver_status_found = True
                            source_ev_found = True
                            sub_map = {s.url: s for s in (ext.extension or []) if s.url}
                            ver_label = sub_map.get("verificationLabel")
                            lbl_val = ver_label.valueCode if ver_label else None
                            if lbl_val and lbl_val in VALID_VERIFICATION_STATUSES:
                                ver_counts[lbl_val] = ver_counts.get(lbl_val, 0) + 1
                            else:
                                errors.append(f"Observation/{obs.id} invalid or missing verificationLabel in provenance extension")
                            for req_f in ("claimId", "verificationId", "confidence", "stageReached", "evidenceText", "sourcePage", "sourceBoundingBox"):
                                if req_f not in sub_map:
                                    errors.append(f"Observation/{obs.id} provenance missing '{req_f}' sub-extension")
                        elif ext.url == EXT_VERIFICATION_STATUS:
                            ver_status_found = True
                            if ext.valueCode in VALID_VERIFICATION_STATUSES:
                                ver_counts[ext.valueCode] = ver_counts.get(ext.valueCode, 0) + 1
                            else:
                                errors.append(f"Observation/{obs.id} unknown verification status '{ext.valueCode}'")
                        elif ext.url == EXT_TEMPORAL_STATUS:
                            if ext.valueCode not in OBSERVATION_TEMPORAL_STATUSES:
                                errors.append(f"Observation/{obs.id} invalid temporal status '{ext.valueCode}' (allowed: {sorted(OBSERVATION_TEMPORAL_STATUSES)})")
                        elif ext.url == EXT_SOURCE_EVIDENCE:
                            source_ev_found = True

                if not ver_status_found:
                    errors.append(f"Observation/{obs.id} missing verification status or claim-provenance extension")
                if not source_ev_found:
                    errors.append(f"Observation/{obs.id} missing source evidence or claim-provenance extension")

            elif r_type in ("MedicationRequest", "MedicationStatement"):
                # Subject reference
                if not res.subject or not res.subject.reference:
                    errors.append(f"{r_type}/{res.id} missing subject reference")
                elif res.subject.reference not in available_refs:
                    errors.append(f"{r_type}/{res.id} subject '{res.subject.reference}' not found in bundle")

                # Encounter / context reference
                if getattr(res, "encounter", None) and res.encounter.reference not in available_refs:
                    errors.append(f"{r_type}/{res.id} encounter '{res.encounter.reference}' not found in bundle")
                if getattr(res, "context", None) and res.context.reference not in available_refs:
                    errors.append(f"{r_type}/{res.id} context '{res.context.reference}' not found in bundle")

                # Supporting information / derivedFrom references
                if getattr(res, "supportingInformation", None):
                    for si in res.supportingInformation:
                        if si.reference and si.reference not in available_refs:
                            errors.append(f"{r_type}/{res.id} supportingInformation '{si.reference}' not found in bundle")
                if getattr(res, "derivedFrom", None):
                    for df in res.derivedFrom:
                        if df.reference and df.reference not in available_refs:
                            errors.append(f"{r_type}/{res.id} derivedFrom '{df.reference}' not found in bundle")

                # Medication code
                if not getattr(res, "medicationCodeableConcept", None):
                    errors.append(f"{r_type}/{res.id} missing medicationCodeableConcept")

                # Status & Intent Semantics
                if not res.status:
                    errors.append(f"{r_type}/{res.id} missing status")
                elif r_type == "MedicationRequest":
                    if res.status not in VALID_MED_REQUEST_STATUSES:
                        errors.append(f"MedicationRequest/{res.id} invalid status '{res.status}'")
                    if not getattr(res, "intent", None):
                        errors.append(f"MedicationRequest/{res.id} missing intent")
                elif r_type == "MedicationStatement":
                    if res.status not in VALID_MED_STATEMENT_STATUSES:
                        errors.append(f"MedicationStatement/{res.id} invalid status '{res.status}'")

                # Dosage structure validation
                dosages = getattr(res, "dosageInstruction", None) or getattr(res, "dosage", None)
                if dosages:
                    for d_idx, d in enumerate(dosages):
                        if not d.text and not d.doseAndRate:
                            warnings.append(f"{r_type}/{res.id} dosage[{d_idx}] lacks both text and doseAndRate")

                # Provenance extension check
                prov_found = False
                if res.extension:
                    for ext in res.extension:
                        if ext.url == EXT_CLAIM_PROVENANCE:
                            prov_found = True
                            sub_map = {s.url: s for s in (ext.extension or []) if s.url}
                            ver_label = sub_map.get("verificationLabel")
                            lbl_val = ver_label.valueCode if ver_label else None
                            if lbl_val and lbl_val in VALID_VERIFICATION_STATUSES:
                                ver_counts[lbl_val] = ver_counts.get(lbl_val, 0) + 1
                            else:
                                errors.append(f"{r_type}/{res.id} invalid or missing verificationLabel in provenance extension")
                            for req_f in ("claimId", "verificationId", "confidence", "stageReached", "evidenceText", "sourcePage", "sourceBoundingBox"):
                                if req_f not in sub_map:
                                    errors.append(f"{r_type}/{res.id} provenance missing '{req_f}' sub-extension")
                        elif ext.url == EXT_TEMPORAL_STATUS:
                            allowed_temp = (
                                MEDICATION_REQUEST_TEMPORAL_STATUSES
                                if r_type == "MedicationRequest"
                                else MEDICATION_STATEMENT_TEMPORAL_STATUSES
                            )
                            if ext.valueCode not in allowed_temp:
                                errors.append(f"{r_type}/{res.id} invalid temporal status '{ext.valueCode}' (allowed: {sorted(allowed_temp)})")

                if not prov_found:
                    errors.append(f"{r_type}/{res.id} missing claim-provenance extension ({EXT_CLAIM_PROVENANCE})")

            elif r_type == "DocumentReference":
                doc_ref: DocumentReference = res
                if not doc_ref.status:
                    errors.append(f"DocumentReference/{doc_ref.id} missing status")
                if not doc_ref.subject or not doc_ref.subject.reference:
                    errors.append(f"DocumentReference/{doc_ref.id} missing subject reference")
                elif doc_ref.subject.reference not in available_refs:
                    errors.append(f"DocumentReference/{doc_ref.id} subject '{doc_ref.subject.reference}' not found in bundle")
                if not doc_ref.content:
                    errors.append(f"DocumentReference/{doc_ref.id} missing content")
                else:
                    for cnt in doc_ref.content:
                        if cnt.attachment and cnt.attachment.hash:
                            import base64
                            try:
                                h_bytes = base64.b64decode(cnt.attachment.hash)
                                if len(h_bytes) not in (20, 32):
                                    errors.append(f"DocumentReference/{doc_ref.id} attachment.hash decoded byte length {len(h_bytes)} is not valid SHA-256 (32) or SHA-1 (20)")
                            except Exception:
                                errors.append(f"DocumentReference/{doc_ref.id} attachment.hash is not valid base64")

            elif r_type == "Device":
                dev: Device = res
                if not dev.status:
                    errors.append(f"Device/{dev.id} missing status")
                if not dev.deviceName:
                    errors.append(f"Device/{dev.id} missing deviceName")

            elif r_type == "Encounter":
                enc: Encounter = res
                if not enc.status:
                    errors.append(f"Encounter/{enc.id} missing status")
                if not enc.class_fhir:
                    errors.append(f"Encounter/{enc.id} missing class")
                if not enc.subject or not enc.subject.reference:
                    errors.append(f"Encounter/{enc.id} missing subject reference")
                elif enc.subject.reference not in available_refs:
                    errors.append(f"Encounter/{enc.id} subject '{enc.subject.reference}' not found in bundle")

            elif r_type == "Provenance":
                prov: Provenance = res
                if not prov.recorded:
                    errors.append(f"Provenance/{prov.id} missing recorded timestamp")
                elif not getattr(prov.recorded, "tzinfo", None):
                    errors.append(f"Provenance/{prov.id} recorded timestamp must be timezone-aware UTC")

                if not prov.target:
                    errors.append(f"Provenance/{prov.id} missing target references")
                else:
                    for t_ref in prov.target:
                        if not t_ref.reference or t_ref.reference not in available_refs:
                            errors.append(f"Provenance/{prov.id} target '{t_ref.reference}' not found in bundle")
                if not prov.agent:
                    errors.append(f"Provenance/{prov.id} missing agent")
                else:
                    for ag in prov.agent:
                        if ag.who and ag.who.reference and ag.who.reference not in available_refs:
                            errors.append(f"Provenance/{prov.id} agent '{ag.who.reference}' not found in bundle")
                if prov.entity:
                    for ent in prov.entity:
                        if ent.what and ent.what.reference and ent.what.reference not in available_refs:
                            errors.append(f"Provenance/{prov.id} entity '{ent.what.reference}' not found in bundle")

        # Bundle-level DEC-020 profile check
        if hasattr(bundle, "meta") and bundle.meta and bundle.meta.profile:
            for p_url in bundle.meta.profile:
                if "hl7.org/fhir/us/core" in str(p_url).lower():
                    errors.append(
                        f"Bundle/{bundle.id} declares US Core profile '{p_url}' which violates DEC-020 (target is FHIR R4B Base)"
                    )

        if not has_patient:
            errors.append("Bundle missing Patient resource")

        is_valid = len(errors) == 0

        return BundleValidationResult(
            is_valid=is_valid,
            bundle_id=bundle_id,
            errors=errors,
            warnings=warnings,
            resource_counts=resource_counts,
            verification_status_counts=ver_counts,
        )
