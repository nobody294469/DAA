"""Dedicated Test / Benchmark-Only Synthetic FHIR Export Mechanism (Phase 5.1.1.1).

This module is strictly isolated to test suites and benchmarking harnesses.
It is NOT part of FHIRExportService, cannot be invoked by production routes,
and cannot be activated by production callers.
"""

from typing import Any, Optional, Sequence
import hashlib
import json
import logging

from clindoc.db.enums import FHIRExportStatus
from clindoc.extract.contracts import Claim, Evidence
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.fhir.validator import FHIRValidator
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import (
    FHIRExportError,
    FHIRExportResult,
)

logger = logging.getLogger(__name__)


def export_unreviewed_synthetic_bundle_experimental(
    claims: Sequence[Claim],
    evidence_map: dict[str, Evidence],
    verification_map: dict[str, VerificationResult],
    document_id: Optional[str] = None,
    document_type: Optional[str] = None,
    patient_id: Optional[str] = None,
    patient_name: Optional[str] = None,
    document_metadata: Optional[dict[str, Any]] = None,
    encounter_info: Optional[dict[str, Any]] = None,
    pipeline_info: Optional[dict[str, Any]] = None,
    pipeline_timing: Optional[dict[str, Any]] = None,
    review_info: Optional[dict[str, Any]] = None,
    include_document_reference: bool = True,
    include_provenance_resource: bool = True,
    **kwargs: Any,
) -> FHIRExportResult:
    """EXPLICIT TEST & BENCHMARK HARNESS ONLY.

    Exports synthetic Claim representations into a validated FHIR R4B Bundle
    without requiring database records or human review states.

    This function is strictly isolated in clindoc.testing and is inaccessible
    from any production REST API endpoint, background worker, or EHR dispatch service.
    """
    if not claims:
        raise FHIRExportError("Cannot export empty claims sequence.")

    resolved_doc_id = document_id or claims[0].document_id
    exportable_claims: list[Claim] = []
    exportable_claim_ids: list[str] = []
    non_exportable_claims: list[dict[str, Any]] = []

    for c in claims:
        ver = verification_map.get(c.claim_id)
        claim_is_fb = getattr(c, "is_fallback", False)
        ver_is_fb = getattr(ver, "is_fallback", False) if ver else False
        claim_is_neg = getattr(c, "negated", False)
        if claim_is_fb or ver_is_fb:
            non_exportable_claims.append({"claim_id": c.claim_id, "reason": "Fallback claim"})
        elif claim_is_neg:
            non_exportable_claims.append({"claim_id": c.claim_id, "reason": "Negated claim"})
        else:
            exportable_claims.append(c)
            exportable_claim_ids.append(c.claim_id)

    if not exportable_claims:
        from clindoc.services.fhir_export import FHIRExportEligibilityError
        raise FHIRExportEligibilityError(
            f"No claims from document '{resolved_doc_id}' are eligible for synthetic FHIR export."
        )

    audit_trail: list[dict[str, Any]] = []

    bundle = claims_to_fhir_bundle(
        claims=exportable_claims,
        evidence_map=evidence_map,
        verification_map=verification_map,
        document_id=resolved_doc_id,
        document_type=document_type,
        patient_id=patient_id,
        patient_name=patient_name,
        document_metadata=document_metadata,
        encounter_info=encounter_info,
        pipeline_info=pipeline_info,
        pipeline_timing=pipeline_timing,
        review_info=review_info,
        include_document_reference=include_document_reference,
        include_provenance_resource=include_provenance_resource,
        audit_trail=audit_trail,
        enforce_req44_lifecycle=True,
    )

    val_report = FHIRValidator.validate_bundle(bundle)
    if not val_report.is_valid:
        logger.warning("Synthetic test FHIR bundle %s has validation errors: %s", bundle.id, val_report.errors)

    bundle_dict = json.loads(bundle.json())
    bundle_json = json.dumps(bundle_dict, indent=2, sort_keys=True)
    bundle_sha256 = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()

    return FHIRExportResult(
        bundle=bundle,
        bundle_id=bundle.id or f"bundle-{resolved_doc_id}",
        bundle_sha256=bundle_sha256,
        bundle_json=bundle_json,
        is_valid=val_report.is_valid,
        validation_report=val_report,
        exportable_claim_ids=exportable_claim_ids,
        non_exportable_claims=[],
        audit_trail=audit_trail,
        export_status=FHIRExportStatus.GENERATED,
    )
