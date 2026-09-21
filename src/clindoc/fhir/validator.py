"""Validation module for generated FHIR R4 resources and Bundles."""

import json
from typing import Any
from pydantic import BaseModel, Field

from fhir.resources.R4B.bundle import Bundle
from fhir.resources.R4B.patient import Patient
from fhir.resources.R4B.organization import Organization
from fhir.resources.R4B.practitioner import Practitioner
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.diagnosticreport import DiagnosticReport

from clindoc.fhir.constants import (
    EXT_SOURCE_EVIDENCE,
    EXT_VERIFICATION_STATUS,
    LOINC_SYSTEM,
    UCUM_SYSTEM,
)
from clindoc.verify.schema import VerificationStatus


VALID_VERIFICATION_STATUSES = {
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

        # 2. Collect available resource references
        available_refs: set[str] = set()
        for idx, entry in enumerate(bundle.entry):
            res = entry.resource
            if res is None:
                errors.append(f"Entry {idx} missing resource object")
                continue
            r_type = res.resource_type
            r_id = res.id
            resource_counts[r_type] = resource_counts.get(r_type, 0) + 1
            if r_type and r_id:
                available_refs.add(f"{r_type}/{r_id}")

        # 3. Validate referential integrity & resource-specific invariants
        has_patient = False
        has_diagnostic_report = False

        for idx, entry in enumerate(bundle.entry):
            res = entry.resource
            if res is None:
                continue

            r_type = res.resource_type

            if r_type == "Patient":
                has_patient = True

            elif r_type == "DiagnosticReport":
                has_diagnostic_report = True
                diag: DiagnosticReport = res
                # Check subject reference
                if not diag.subject or not diag.subject.reference:
                    errors.append(f"DiagnosticReport/{diag.id} missing subject reference")
                elif diag.subject.reference not in available_refs:
                    errors.append(f"DiagnosticReport/{diag.id} subject '{diag.subject.reference}' not found in bundle")

                # Check result references
                if not diag.result:
                    warnings.append(f"DiagnosticReport/{diag.id} contains no observation results")
                else:
                    for r_ref in diag.result:
                        if not r_ref.reference or r_ref.reference not in available_refs:
                            errors.append(f"DiagnosticReport/{diag.id} result reference '{r_ref.reference}' not found in bundle")

            elif r_type == "Observation":
                obs: Observation = res

                # Check subject reference
                if not obs.subject or not obs.subject.reference:
                    errors.append(f"Observation/{obs.id} missing subject reference")
                elif obs.subject.reference not in available_refs:
                    errors.append(f"Observation/{obs.id} subject '{obs.subject.reference}' not found in bundle")

                # Check status
                if obs.status not in ("registered", "preliminary", "final", "amended", "corrected"):
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

                # Check valueQuantity
                if obs.valueQuantity is None:
                    errors.append(f"Observation/{obs.id} missing valueQuantity")
                else:
                    if obs.valueQuantity.value is None:
                        errors.append(f"Observation/{obs.id} valueQuantity.value is None")
                    if not obs.valueQuantity.unit:
                        warnings.append(f"Observation/{obs.id} valueQuantity.unit is missing")

                # Check verification status extension
                ver_status_found = False
                source_ev_found = False
                if obs.extension:
                    for ext in obs.extension:
                        if ext.url == EXT_VERIFICATION_STATUS:
                            ver_status_found = True
                            if ext.valueCode in VALID_VERIFICATION_STATUSES:
                                ver_counts[ext.valueCode] = ver_counts.get(ext.valueCode, 0) + 1
                            else:
                                errors.append(f"Observation/{obs.id} unknown verification status '{ext.valueCode}'")
                        elif ext.url == EXT_SOURCE_EVIDENCE:
                            source_ev_found = True

                if not ver_status_found:
                    errors.append(f"Observation/{obs.id} missing verification status extension ({EXT_VERIFICATION_STATUS})")
                if not source_ev_found:
                    errors.append(f"Observation/{obs.id} missing source evidence extension ({EXT_SOURCE_EVIDENCE})")

        if not has_patient:
            errors.append("Bundle missing Patient resource")
        if not has_diagnostic_report:
            errors.append("Bundle missing DiagnosticReport resource")

        is_valid = len(errors) == 0

        return BundleValidationResult(
            is_valid=is_valid,
            bundle_id=bundle_id,
            errors=errors,
            warnings=warnings,
            resource_counts=resource_counts,
            verification_status_counts=ver_counts,
        )
