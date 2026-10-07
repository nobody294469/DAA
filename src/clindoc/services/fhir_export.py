"""FHIR R4B Export Service converting verified intermediate representations into compliant FHIR Bundles.

Authority:
  - PROJECT_SPEC §5 #10, §7.7, §15, §16
  - DEC-008: Hybrid MedicationRequest vs MedicationStatement routing
  - DEC-016: Two-tier provenance hierarchy (resource-level & element-level)
  - PRODUCTIZATION_SPEC §1.2, §4.3, §7.2, §9.3, §9.4
  - PRODUCTIZATION_DECISIONS.md PD-002, PD-009, PD-011, PD-012, PD-015
  - Phase 8.8: FHIR R4B Export & Provenance Grounding
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import hashlib
import json
import logging
from typing import Any, Optional, Sequence
import time
import uuid

from fhir.resources.R4B.bundle import Bundle
from sqlalchemy.orm import Session

from clindoc.observability.metrics import get_metrics

from clindoc.api.audit.service import AuditService, validate_metadata_phi_free
from clindoc.db.enums import (
    ClaimReviewState,
    DocumentApprovalStatus,
    DocumentStatus,
    FHIRExportStatus,
    FHIRVersion,
    PatientIdentityApprovalStatus,
)
from clindoc.db.models import (
    Claim as DBClaim,
    Document as DBDocument,
    DocumentApproval,
    Evidence as DBEvidence,
    FHIRBundle,
    FHIRExport,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.fhir.mapper import claims_to_fhir_bundle
from clindoc.fhir.validator import BundleValidationResult, FHIRValidator
from clindoc.schema.verification import VerificationResult
from clindoc.storage.blob import BlobStore

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────


class FHIRExportError(Exception):
    """Base exception for FHIR export operations."""


class FHIRExportEligibilityError(FHIRExportError):
    """Raised when a document or claim fails export eligibility safety rules."""


class FHIRExportValidationError(FHIRExportError):
    """Raised when generated FHIR bundle fails structural schema validation."""


class FHIRExportNotFoundError(FHIRExportError):
    """Raised when a referenced document or resource is not found."""


# ─── DATA STRUCTURES ─────────────────────────────────────────────────────────


@dataclass
class ClaimEligibilityEvaluation:
    """Detailed evaluation of an individual claim's eligibility for FHIR export."""

    claim_id: str
    is_eligible: bool
    reason: Optional[str] = None
    review_state: Optional[str] = None
    verification_label: Optional[str] = None
    is_fallback: bool = False


@dataclass
class FHIRExportResult:
    """Outcome of a FHIR R4B export operation."""

    bundle: Bundle
    bundle_id: str
    bundle_sha256: str
    bundle_json: str
    is_valid: bool
    validation_report: BundleValidationResult
    exportable_claim_ids: list[str] = field(default_factory=list)
    non_exportable_claims: list[dict[str, Any]] = field(default_factory=list)
    db_bundle_id: Optional[uuid.UUID] = None
    db_export_id: Optional[uuid.UUID] = None
    storage_ref: Optional[str] = None
    export_status: FHIRExportStatus = FHIRExportStatus.GENERATED

    def to_dict(self) -> dict[str, Any]:
        """Convert export summary to JSON-serializable dictionary."""
        return {
            "bundle_id": self.bundle_id,
            "bundle_sha256": self.bundle_sha256,
            "is_valid": self.is_valid,
            "export_status": self.export_status.value if hasattr(self.export_status, "value") else str(self.export_status),
            "exportable_claim_count": len(self.exportable_claim_ids),
            "non_exportable_claim_count": len(self.non_exportable_claims),
            "exportable_claim_ids": self.exportable_claim_ids,
            "non_exportable_claims": self.non_exportable_claims,
            "validation_report": {
                "is_valid": self.validation_report.is_valid,
                "errors": self.validation_report.errors,
                "warnings": self.validation_report.warnings,
                "resource_counts": self.validation_report.resource_counts,
                "verification_status_counts": self.validation_report.verification_status_counts,
            },
            "db_bundle_id": str(self.db_bundle_id) if self.db_bundle_id else None,
            "db_export_id": str(self.db_export_id) if self.db_export_id else None,
            "storage_ref": self.storage_ref,
        }


# ─── EXPORT SERVICE ──────────────────────────────────────────────────────────


class FHIRExportService:
    """Deterministic, production-oriented FHIR R4B export service.

    Enforces:
      - Transformation/mapping ONLY; performs NO clinical inference and invokes NO LLM.
      - Verification Safety Boundary: Fallback results (is_fallback=True) are NEVER exported.
      - Mode 0 Human-Review Boundary: Claims in PENDING, REJECTED, or ESCALATED states are
        strictly rejected from export.
      - Autonomous pipeline mode: Unverified claims, discrepancies, or missing evidence are excluded.
      - Referential integrity: Grounding provenance preserved via two-tier extensions,
        DocumentReference derivedFrom links, and Provenance resources.
      - Four-Eyes Document Approval gate: In database workflows, requires is_export_eligible=True
        and patient identity confirmation prior to export.
    """

    def __init__(self, audit_service: Optional[AuditService] = None) -> None:
        self.audit_service = audit_service or AuditService()

    @staticmethod
    def evaluate_claim_eligibility(
        claim: Claim | DBClaim | dict[str, Any],
        ver: Optional[VerificationResult | DBVerificationResult | dict[str, Any]] = None,
        ev: Optional[Evidence | DBEvidence | dict[str, Any]] = None,
        require_human_acceptance: bool = False,
    ) -> ClaimEligibilityEvaluation:
        """Evaluate whether a single claim satisfies export safety and verification criteria.

        Rules:
          1. Fallback Gate: Fallback outputs (is_fallback=True) are NOT verified facts.
          2. Human Review Gate: If a human review state exists:
             - ACCEPTED or EDITED: Eligible.
             - PENDING: Ineligible (unresolved human review).
             - REJECTED: Ineligible (reviewer rejected finding).
             - ESCALATED: Ineligible (unresolved discrepancy).
          3. Verification Verdict Gate: If no human decision is attached:
             - Must be SUPPORTED.
             - DISCREPANCY, REFUTED, or INSUFFICIENT_EVIDENCE are strictly ineligible.
          4. Evidence Grounding Gate: Must have valid evidence reference.
          5. Value Completeness: Must have non-empty value.
        """
        claim_id = (
            getattr(claim, "claim_id", None)
            or (claim.get("claim_id") if isinstance(claim, dict) else None)
            or str(getattr(claim, "id", "unknown"))
        )
        claim_id_str = str(claim_id)

        # 1. Fallback Check (F-05 / F-06 / F-07)
        claim_is_fb = getattr(claim, "is_fallback", False) or (claim.get("is_fallback", False) if isinstance(claim, dict) else False)
        ver_is_fb = False
        if ver is not None:
            ver_is_fb = getattr(ver, "is_fallback", False) or (ver.get("is_fallback", False) if isinstance(ver, dict) else False)

        if claim_is_fb or ver_is_fb:
            return ClaimEligibilityEvaluation(
                claim_id=claim_id_str,
                is_eligible=False,
                reason="Claim verification verdict was produced via fallback mechanism; cannot be exported as verified clinical fact",
                is_fallback=True,
            )

        # 2. Extract review state if present
        rev_state = None
        if hasattr(claim, "review_state") and claim.review_state is not None:
            rev_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state).upper()
        elif isinstance(claim, dict) and "review_state" in claim and claim["review_state"] is not None:
            rev_state = str(claim["review_state"]).upper()

        # 3. Extract verification label
        ver_label = None
        if ver is not None:
            lbl = getattr(ver, "label", None) or (ver.get("label") if isinstance(ver, dict) else None)
            if lbl is not None:
                ver_label = lbl.value if hasattr(lbl, "value") else str(lbl).upper()

        # 4. Review State Evaluation
        if rev_state is not None:
            if rev_state == ClaimReviewState.PENDING.value:
                return ClaimEligibilityEvaluation(
                    claim_id=claim_id_str,
                    is_eligible=False,
                    reason="Claim review state is PENDING; requires human review decision before export",
                    review_state=rev_state,
                    verification_label=ver_label,
                )
            elif rev_state == ClaimReviewState.REJECTED.value:
                return ClaimEligibilityEvaluation(
                    claim_id=claim_id_str,
                    is_eligible=False,
                    reason="Claim was REJECTED during human review",
                    review_state=rev_state,
                    verification_label=ver_label,
                )
            elif rev_state == ClaimReviewState.ESCALATED.value:
                return ClaimEligibilityEvaluation(
                    claim_id=claim_id_str,
                    is_eligible=False,
                    reason="Claim is ESCALATED and awaiting senior clinical resolution",
                    review_state=rev_state,
                    verification_label=ver_label,
                )
            elif rev_state in (ClaimReviewState.ACCEPTED.value, ClaimReviewState.EDITED.value):
                pass  # Human confirmed
            else:
                return ClaimEligibilityEvaluation(
                    claim_id=claim_id_str,
                    is_eligible=False,
                    reason=f"Claim review state '{rev_state}' is unrecognized for export",
                    review_state=rev_state,
                    verification_label=ver_label,
                )
        elif require_human_acceptance:
            return ClaimEligibilityEvaluation(
                claim_id=claim_id_str,
                is_eligible=False,
                reason="Policy requires human review acceptance, but claim has no review decision",
                verification_label=ver_label,
            )

        # 5. Verification Verdict Evaluation (when no human override accepted)
        if rev_state not in (ClaimReviewState.ACCEPTED.value, ClaimReviewState.EDITED.value):
            if ver is None:
                return ClaimEligibilityEvaluation(
                    claim_id=claim_id_str,
                    is_eligible=False,
                    reason="Claim has no associated VerificationResult",
                )
            if ver_label not in ("SUPPORTED", "VERIFIED"):
                return ClaimEligibilityEvaluation(
                    claim_id=claim_id_str,
                    is_eligible=False,
                    reason=f"Verification label is '{ver_label}' (must be SUPPORTED)",
                    verification_label=ver_label,
                )

        # 6. Evidence Grounding Check
        if ev is None:
            return ClaimEligibilityEvaluation(
                claim_id=claim_id_str,
                is_eligible=False,
                reason="Claim is missing grounding evidence reference",
                review_state=rev_state,
                verification_label=ver_label,
            )

        # 7. Value Non-Empty Check
        c_val = getattr(claim, "value", None) or (claim.get("value") if isinstance(claim, dict) else None)
        if not c_val or not str(c_val).strip():
            return ClaimEligibilityEvaluation(
                claim_id=claim_id_str,
                is_eligible=False,
                reason="Claim raw value is empty or whitespace",
                review_state=rev_state,
                verification_label=ver_label,
            )

        return ClaimEligibilityEvaluation(
            claim_id=claim_id_str,
            is_eligible=True,
            review_state=rev_state,
            verification_label=ver_label,
        )

    def export_intermediate_representation(
        self,
        claims: Sequence[Claim],
        evidence_map: dict[str, Evidence],
        verification_map: dict[str, VerificationResult],
        document_id: Optional[str] = None,
        document_type: Optional[str] = None,
        patient_id: Optional[str] = None,
        patient_name: Optional[str] = None,
        document_metadata: Optional[dict[str, Any]] = None,
        encounter_info: Optional[dict[str, Any]] = None,
        include_document_reference: bool = True,
        include_provenance_resource: bool = True,
        enforce_eligibility: bool = True,
        require_human_acceptance: bool = False,
    ) -> FHIRExportResult:
        """Export in-memory intermediate representation into a validated FHIR R4B Bundle.

        Args:
            claims: Sequence of Claim models.
            evidence_map: Lookup of evidence_id -> Evidence.
            verification_map: Lookup of claim_id -> VerificationResult.
            document_id: Document ID override.
            document_type: Document layout classification.
            patient_id: Patient MRN/ID override.
            patient_name: Patient display name override.
            document_metadata: Optional metadata (mime_type, filename, storage_ref, sha256).
            encounter_info: Optional encounter metadata (encounter_id, encounter_class, status).
            include_document_reference: Whether to include DocumentReference resource.
            include_provenance_resource: Whether to include top-level Provenance resource.
            enforce_eligibility: Filter claims using the export eligibility safety boundary.
            require_human_acceptance: If True, only claims explicitly ACCEPTED or EDITED pass.

        Returns:
            FHIRExportResult containing Bundle, validation report, and eligibility breakdown.
        """
        if not claims:
            raise FHIRExportError("Cannot export empty claims sequence.")

        resolved_doc_id = document_id or claims[0].document_id

        exportable_claims: list[Claim] = []
        exportable_claim_ids: list[str] = []
        non_exportable_claims: list[dict[str, Any]] = []

        for c in claims:
            ver = verification_map.get(c.claim_id)
            ev = evidence_map.get(c.evidence_id)

            if enforce_eligibility:
                eval_res = self.evaluate_claim_eligibility(
                    claim=c,
                    ver=ver,
                    ev=ev,
                    require_human_acceptance=require_human_acceptance,
                )
                if eval_res.is_eligible:
                    exportable_claims.append(c)
                    exportable_claim_ids.append(c.claim_id)
                else:
                    non_exportable_claims.append({
                        "claim_id": c.claim_id,
                        "field_type": c.field_type,
                        "value": c.value,
                        "reason": eval_res.reason,
                        "review_state": eval_res.review_state,
                        "verification_label": eval_res.verification_label,
                        "is_fallback": eval_res.is_fallback,
                    })
            else:
                exportable_claims.append(c)
                exportable_claim_ids.append(c.claim_id)

        if not exportable_claims:
            raise FHIRExportEligibilityError(
                f"No claims from document '{resolved_doc_id}' are eligible for FHIR export. "
                f"Non-exportable count: {len(non_exportable_claims)}."
            )

        # Generate Bundle via canonical mapper
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
            include_document_reference=include_document_reference,
            include_provenance_resource=include_provenance_resource,
        )

        # Validate bundle structurally
        val_report = FHIRValidator.validate_bundle(bundle)
        if not val_report.is_valid:
            logger.warning("Generated FHIR bundle %s has validation errors: %s", bundle.id, val_report.errors)

        # Compute deterministic JSON and SHA-256
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
            non_exportable_claims=non_exportable_claims,
            export_status=FHIRExportStatus.GENERATED,
        )

    def export_document(
        self,
        session: Session,
        document_id: uuid.UUID | str,
        exported_by: uuid.UUID | str,
        blob_store: Optional[BlobStore] = None,
        target_type: str = "FILE_DOWNLOAD",
        destination_path: Optional[str] = None,
        source_ip: Optional[str] = None,
        correlation_id: Optional[str] = None,
        include_provenance_resource: bool = True,
        include_document_reference: bool = True,
        provenance_minimized: bool = False,
    ) -> FHIRExportResult:
        """Export a processed database Document into a persisted FHIR R4B Bundle and FHIRExport record.

        Enforces:
          1. Document existence & status.
          2. Four-Eyes Approval gate (PD-002, PD-009, PD-015): Document must have a confirmed
             DocumentApproval record where is_export_eligible=True and patient identity confirmed.
          3. Claim review states: Active claims in ACCEPTED or EDITED states only (Mode 0).
          4. Persistence in fhir_bundles and fhir_exports tables.
          5. Transition Document status to EXPORTED.
          6. Append-only, hash-chained AuditEvent recording (PD-012, strictly PHI-free).
          7. Fail-closed: Rolls back transaction if audit recording or persistence fails.
        """
        doc_uuid = uuid.UUID(str(document_id)) if not isinstance(document_id, uuid.UUID) else document_id
        user_uuid = uuid.UUID(str(exported_by)) if not isinstance(exported_by, uuid.UUID) else exported_by
        export_start_time = time.perf_counter()

        # 1. Fetch document
        doc = session.query(DBDocument).filter(DBDocument.id == doc_uuid).first()
        if not doc:
            get_metrics().record_fhir_export_failure("eligibility_failure", "document_not_found")
            raise FHIRExportNotFoundError(f"Document with ID '{doc_uuid}' not found.")

        # 2. Check Four-Eyes Document Approval Gate
        latest_approval = (
            session.query(DocumentApproval)
            .filter(DocumentApproval.document_id == doc_uuid)
            .order_by(DocumentApproval.created_at.desc())
            .first()
        )
        if not latest_approval:
            get_metrics().record_fhir_export_failure("eligibility_failure", "approval_missing")
            raise FHIRExportEligibilityError(
                f"Document '{doc_uuid}' has not undergone Four-Eyes approval review. "
                "FHIR export is strictly prohibited prior to formal operator approval (PD-002, PD-009)."
            )

        if not latest_approval.is_export_eligible:
            reason = "patient_identity_not_confirmed" if latest_approval.patient_identity_status != PatientIdentityApprovalStatus.CONFIRMED else "not_export_eligible"
            get_metrics().record_fhir_export_failure("eligibility_failure", reason)
            raise FHIRExportEligibilityError(
                f"Document '{doc_uuid}' approval is not export-eligible. "
                f"Status: {latest_approval.approval_status}, "
                f"Patient identity: {latest_approval.patient_identity_status} (PD-015)."
            )

        # 3. Load active claims for this document
        # Active claims = not superseded by a newer version
        all_claims = (
            session.query(DBClaim)
            .filter(DBClaim.document_id == doc_uuid)
            .all()
        )
        if not all_claims:
            get_metrics().record_fhir_export_failure("eligibility_failure", "unresolved_claims")
            raise FHIRExportEligibilityError(f"Document '{doc_uuid}' contains no extracted claims.")

        superseded_ids = {c.supersedes_claim_id for c in all_claims if c.supersedes_claim_id is not None}
        active_claims = [c for c in all_claims if c.id not in superseded_ids]

        # 4. Prepare intermediate representation mappings
        claims_contracts: list[Claim] = []
        evidence_map: dict[str, Evidence] = []
        evidence_dict: dict[str, Evidence] = {}
        verification_dict: dict[str, VerificationResult] = {}

        claim_evaluations: dict[str, ClaimEligibilityEvaluation] = {}
        exportable_claims: list[Claim] = []
        exportable_claim_ids: list[str] = []
        non_exportable_claims: list[dict[str, Any]] = []

        for c in active_claims:
            c_id_str = str(c.claim_id)

            # Reconstruct Claim Pydantic contract
            c_payload = dict(c.payload)
            c_payload["claim_id"] = c_id_str
            c_payload["document_id"] = str(c.document_id)
            c_contract = Claim.model_validate(c_payload)

            # Resolve latest VerificationResult
            db_ver = (
                session.query(DBVerificationResult)
                .filter(DBVerificationResult.claim_id == c.id)
                .order_by(DBVerificationResult.created_at.desc())
                .first()
            )
            ver_contract = None
            if db_ver and db_ver.payload:
                v_payload = dict(db_ver.payload)
                v_payload["claim_id"] = c_id_str
                v_payload["verification_id"] = str(db_ver.id)
                v_payload["evidence_id"] = c_contract.evidence_id
                ver_contract = VerificationResult.model_validate(v_payload)
                verification_dict[c_id_str] = ver_contract

            # Resolve Evidence
            db_ev = (
                session.query(DBEvidence)
                .filter(DBEvidence.id == c.evidence_id)
                .first()
                if c.evidence_id
                else None
            )
            ev_contract = None
            if db_ev:
                ev_payload = dict(db_ev.payload) if db_ev.payload else {}
                bbox_val = ev_payload.get("bbox") or db_ev.bounding_box or {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
                if isinstance(bbox_val, list) and len(bbox_val) == 4:
                    bbox_obj = BBoxModel.from_list(bbox_val)
                elif isinstance(bbox_val, dict):
                    bbox_obj = BBoxModel(**bbox_val)
                else:
                    bbox_obj = BBoxModel(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

                text_val = ev_payload.get("text") or db_ev.evidence_text or "evidence"
                char_start = ev_payload.get("char_start", db_ev.char_start)
                char_end = ev_payload.get("char_end", db_ev.char_end)
                page_num = ev_payload.get("page_number", db_ev.page_number)

                ev_contract = Evidence(
                    evidence_id=c_contract.evidence_id,
                    document_id=str(doc_uuid),
                    page_number=page_num,
                    char_start=char_start,
                    char_end=char_end,
                    text=text_val,
                    bbox=bbox_obj,
                    ocr_confidence=ev_payload.get("ocr_confidence", db_ev.ocr_confidence),
                )
                evidence_dict[c_contract.evidence_id] = ev_contract

            # Evaluate eligibility
            eval_res = self.evaluate_claim_eligibility(
                claim=c,
                ver=db_ver,
                ev=db_ev,
                require_human_acceptance=True,  # Document approval requires human accepted/edited claims
            )
            claim_evaluations[c_id_str] = eval_res

            if eval_res.is_eligible:
                exportable_claims.append(c_contract)
                exportable_claim_ids.append(c_id_str)
            else:
                non_exportable_claims.append({
                    "claim_id": c_id_str,
                    "field_type": c.field_type,
                    "value": c.value,
                    "reason": eval_res.reason,
                    "review_state": eval_res.review_state,
                    "verification_label": eval_res.verification_label,
                    "is_fallback": eval_res.is_fallback,
                })

        if not exportable_claims:
            raise FHIRExportEligibilityError(
                f"Document '{doc_uuid}' contains zero export-eligible claims after review filtering. "
                f"Total active claims: {len(active_claims)}, non-exportable: {len(non_exportable_claims)}."
            )

        # 5. Build FHIR Bundle
        doc_metadata = {
            "filename": doc.original_filename,
            "mime_type": doc.mime_type,
            "storage_ref": doc.storage_ref,
            "sha256": doc.sha256,
        }
        doc_layout_type = getattr(doc, "document_type", getattr(doc, "source_type", None))
        bundle = claims_to_fhir_bundle(
            claims=exportable_claims,
            evidence_map=evidence_dict,
            verification_map=verification_dict,
            document_id=str(doc_uuid),
            document_type=doc_layout_type,
            patient_id=str(doc.patient_context_id) if doc.patient_context_id else None,
            document_metadata=doc_metadata,
            include_document_reference=include_document_reference,
            include_provenance_resource=include_provenance_resource,
        )

        # 6. Validate Bundle
        val_report = FHIRValidator.validate_bundle(bundle)
        if not val_report.is_valid:
            get_metrics().record_fhir_export_failure("validation_failure", "fhir_validation_failure")
            raise FHIRExportValidationError(
                f"Generated FHIR bundle failed structural validation: {val_report.errors}"
            )

        # 7. Compute deterministic serialization and SHA-256
        bundle_dict = json.loads(bundle.json())
        bundle_json = json.dumps(bundle_dict, indent=2, sort_keys=True)
        bundle_sha256 = hashlib.sha256(bundle_json.encode("utf-8")).hexdigest()

        # 8. Store bundle JSON in BlobStore if available
        storage_ref = f"fhir-bundles/{doc_uuid}/{bundle.id}.json"
        if blob_store is not None:
            try:
                storage_ref = blob_store.put(
                    data=bundle_json.encode("utf-8"),
                    document_id=str(doc_uuid),
                    mime_type="application/fhir+json",
                )
            except Exception as exc:
                logger.warning("BlobStore persistence failed for FHIR bundle: %s; using virtual ref", exc)

        # 9. Persist FHIRBundle in Database
        fhir_bundle_row = FHIRBundle(
            document_id=doc_uuid,
            approval_id=latest_approval.id,
            fhir_version=FHIRVersion.R4B,
            provenance_minimized=provenance_minimized,
            bundle_sha256=bundle_sha256,
            storage_ref=storage_ref,
            bundle_payload=bundle_dict,
            validation_report={
                "is_valid": val_report.is_valid,
                "errors": val_report.errors,
                "warnings": val_report.warnings,
                "resource_counts": val_report.resource_counts,
                "verification_status_counts": val_report.verification_status_counts,
            },
        )
        session.add(fhir_bundle_row)
        session.flush()

        # 10. Persist FHIRExport record
        idempotency_key = f"export-{doc_uuid}-{fhir_bundle_row.id}-{uuid.uuid4().hex[:8]}"
        export_row = FHIRExport(
            bundle_id=fhir_bundle_row.id,
            target_type=target_type,
            export_status=FHIRExportStatus.EXPORTED_FILE if destination_path else FHIRExportStatus.GENERATED,
            idempotency_key=idempotency_key,
            destination_path=destination_path,
            exported_by=user_uuid,
            exported_at=datetime.datetime.now(datetime.timezone.utc),
        )
        session.add(export_row)
        session.flush()

        # 11. Update Document status to EXPORTED
        doc.status = DocumentStatus.EXPORTED
        session.flush()

        # 12. Audit Logging (PD-012: Fail-closed atomic hash-chained audit event)
        audit_metadata: dict[str, Any] = {
            "document_id": str(doc_uuid),
            "bundle_id": bundle.id,
            "bundle_sha256": bundle_sha256,
            "export_id": str(export_row.id),
            "fhir_bundle_id": str(fhir_bundle_row.id),
            "resource_counts": val_report.resource_counts,
            "exportable_claim_count": len(exportable_claims),
            "non_exportable_claim_count": len(non_exportable_claims),
            "target_type": target_type,
            "is_valid": val_report.is_valid,
        }
        validate_metadata_phi_free(audit_metadata)

        try:
            self.audit_service.record_event(
                session=session,
                actor_id=str(user_uuid),
                event_type="FHIR_BUNDLE_EXPORTED",
                entity_type="fhir_bundle",
                entity_id=str(fhir_bundle_row.id),
                action="EXPORT",
                source_ip=source_ip,
                correlation_id=correlation_id,
                metadata_json=audit_metadata,
            )
        except Exception as exc:
            session.rollback()
            get_metrics().record_fhir_export_failure("internal_error", "audit_failure")
            logger.error("AuditService recording failed for FHIR export of document %s: %s", doc_uuid, exc)
            raise FHIRExportError(f"FHIR export failed closed: Audit event could not be secured: {exc}") from exc

        export_duration = time.perf_counter() - export_start_time
        get_metrics().record_fhir_export_success(export_duration)
        get_metrics().record_document_status("exported")

        return FHIRExportResult(
            bundle=bundle,
            bundle_id=bundle.id or f"bundle-{doc_uuid}",
            bundle_sha256=bundle_sha256,
            bundle_json=bundle_json,
            is_valid=val_report.is_valid,
            validation_report=val_report,
            exportable_claim_ids=exportable_claim_ids,
            non_exportable_claims=non_exportable_claims,
            db_bundle_id=fhir_bundle_row.id,
            db_export_id=export_row.id,
            storage_ref=storage_ref,
            export_status=export_row.export_status,
        )
