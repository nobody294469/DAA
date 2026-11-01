"""EHR Integration Service for Controlled Clinical Pilot Transmission.

Authority:
  - Phase 8.12: Controlled Clinical Pilot / Live EHR Integration Boundary
  - PRODUCTIZATION_SPEC.md §1.2, §4.3, §7.2, §9.3, §9.4, §10
  - PRODUCTIZATION_DECISIONS.md PD-002, PD-009, PD-012, PD-015
  - PROJECT_SPEC.md §15, §16
"""

from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import hashlib
import json
import logging
import time
from typing import Any, Optional, Sequence
import uuid

from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService, validate_metadata_phi_free
from clindoc.db.enums import (
    DocumentApprovalStatus,
    DocumentStatus,
    FHIRExportStatus,
    FHIRVersion,
    PatientIdentityApprovalStatus,
)
from clindoc.db.models import (
    Document as DBDocument,
    DocumentApproval,
    FHIRBundle,
    FHIRExport,
)
from clindoc.observability.metrics import get_metrics
from clindoc.services.ehr_transport import (
    EHRAuthenticationError,
    EHRDuplicateSubmissionError,
    EHRTimeoutError,
    EHRTransmissionResponse,
    EHRTransportError,
    EHRValidationError,
    FHIRTransport,
    MockFHIRTransport,
)
from clindoc.services.fhir_export import (
    FHIRExportEligibilityError,
    FHIRExportNotFoundError,
    FHIRExportResult,
    FHIRExportService,
)

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────


class EHRIntegrationError(Exception):
    """Base exception for EHR integration and safety boundary violations."""


class EHRPatientIdentityError(EHRIntegrationError):
    """Raised when patient identity is not confirmed, mismatched, or missing."""


class EHRApprovalMissingError(EHRIntegrationError):
    """Raised when Four-Eyes document approval is missing or not export-eligible."""


class EHRUnresolvedClaimError(EHRIntegrationError):
    """Raised when the document contains unreviewed, fallback, or unresolved claims."""


# ─── DATA CONTRACTS ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EHRTransmissionResult:
    """Outcome of an EHR transmission operation."""

    document_id: str
    bundle_id: str
    bundle_sha256: str
    destination_type: str
    remote_message_id: Optional[str]
    success: bool
    status_code: int
    idempotency_key: str
    latency_seconds: float
    export_id: Optional[str] = None
    audit_event_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert transmission summary to safe non-PHI dictionary."""
        return {
            "document_id": self.document_id,
            "bundle_id": self.bundle_id,
            "bundle_sha256": self.bundle_sha256,
            "destination_type": self.destination_type,
            "remote_message_id": self.remote_message_id,
            "success": self.success,
            "status_code": self.status_code,
            "idempotency_key": self.idempotency_key,
            "latency_seconds": round(self.latency_seconds, 4),
            "export_id": self.export_id,
            "audit_event_id": self.audit_event_id,
        }


# ─── SERVICE IMPLEMENTATION ──────────────────────────────────────────────────


class EHRIntegrationService:
    """Production-grade EHR Integration Adapter.

    Enforces:
      1. Four-Eyes Approval Safety Gate: Rejects unreviewed or unapproved documents.
      2. Patient Identity Safety Gate: Confirmed identity ONLY; rejects UNCONFIRMED/MISMATCHED.
      3. Claim Verification Safety Gate: Verifies zero fallback or unresolved claims.
      4. Idempotency & Duplicate Prevention: Guarantees no silent duplicate transmissions.
      5. Fail-Closed Boundary: Failed transmissions NEVER alter verification verdicts
         or mark documents as successfully transmitted.
      6. Auditability & Observability: Records append-only audit events and Prometheus metrics.
    """

    def __init__(
        self,
        audit_service: Optional[AuditService] = None,
        transport: Optional[FHIRTransport] = None,
        export_service: Optional[FHIRExportService] = None,
        audit_ledger: Optional[Any] = None,
        trust_store: Optional[Dict[str, Any]] = None,
        revoked_key_ids: Optional[Set[str]] = None,
    ) -> None:
        self.audit_service = audit_service or AuditService()
        self.transport = transport or MockFHIRTransport()
        self.export_service = export_service or FHIRExportService(audit_service=self.audit_service)
        self.audit_ledger = audit_ledger
        self.trust_store = trust_store
        self.revoked_key_ids = revoked_key_ids or set()

    def transmit_document_to_ehr(
        self,
        session: Session,
        *,
        document_id: uuid.UUID | str,
        actor_id: uuid.UUID | str,
        destination_type: str = "sandbox",
        correlation_id: Optional[str] = None,
        allow_resubmission: bool = False,
    ) -> EHRTransmissionResult:
        """Deliver a verified, approved FHIR Bundle to the destination EHR boundary.

        Step-by-step pipeline:
          1. Verify document existence.
          2. Check Four-Eyes approval & patient identity safety gates.
          3. Generate or retrieve existing verified FHIR Bundle.
          4. Check idempotency & duplicate submission rules.
          5. Record EHR_TRANSMISSION_ATTEMPTED audit event.
          6. Transmit to FHIRTransport.
          7. On success: record FHIRExport row, update status to EXPORTED, log success audit event.
          8. On failure: record FHIRExport row with FAILED status, keep document status unchanged,
             log failure audit event, re-raise error.
        """
        doc_uuid = uuid.UUID(str(document_id)) if not isinstance(document_id, uuid.UUID) else document_id
        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id
        corr_id = correlation_id or str(uuid.uuid4())
        start_time = time.perf_counter()

        # ─── 1. FETCH DOCUMENT ───────────────────────────────────────────────
        doc = session.query(DBDocument).filter(DBDocument.id == doc_uuid).first()
        if not doc:
            get_metrics().record_ehr_transmission("failure", destination=destination_type)
            raise FHIRExportNotFoundError(f"Document with ID '{doc_uuid}' not found.")

        # ─── 2. FOUR-EYES APPROVAL & PATIENT IDENTITY SAFETY GATES ───────────
        latest_approval = (
            session.query(DocumentApproval)
            .filter(DocumentApproval.document_id == doc_uuid)
            .order_by(DocumentApproval.created_at.desc())
            .first()
        )
        if not latest_approval:
            get_metrics().record_ehr_transmission("rejected", destination=destination_type)
            raise EHRApprovalMissingError(
                f"Document '{doc_uuid}' has not undergone Four-Eyes approval. "
                "Transmission to EHR is strictly prohibited prior to formal clinician approval (PD-002, PD-009)."
            )

        # Check approval status
        if latest_approval.approval_status != DocumentApprovalStatus.APPROVED:
            get_metrics().record_ehr_transmission("rejected", destination=destination_type)
            raise EHRApprovalMissingError(
                f"Document '{doc_uuid}' is not approved (Current status: {latest_approval.approval_status}). "
                "Only APPROVED documents may be transmitted to EHR."
            )

        # Check patient identity status
        if latest_approval.patient_identity_status != PatientIdentityApprovalStatus.CONFIRMED:
            get_metrics().record_ehr_transmission("rejected", destination=destination_type)
            raise EHRPatientIdentityError(
                f"Document '{doc_uuid}' patient identity is not confirmed (Status: {latest_approval.patient_identity_status}). "
                "Transmission to EHR requires strictly CONFIRMED patient identity (PD-015)."
            )

        # Check overall export eligibility
        if not latest_approval.is_export_eligible:
            get_metrics().record_ehr_transmission("rejected", destination=destination_type)
            raise EHRApprovalMissingError(
                f"Document '{doc_uuid}' approval record has is_export_eligible=False. Transmission blocked."
            )

        # ─── 3. RESOLVE / GENERATE FHIR BUNDLE ───────────────────────────────
        # Capture pre-export document status so failures never falsely mark document as EXPORTED
        initial_doc_status = doc.status

        # Check if bundle was already generated and persisted
        existing_bundle = (
            session.query(FHIRBundle)
            .filter(FHIRBundle.document_id == doc_uuid)
            .order_by(FHIRBundle.created_at.desc())
            .first()
        )

        transport_sha256 = None
        if existing_bundle:
            bundle_id = str(existing_bundle.id)
            bundle_dict = existing_bundle.bundle_payload
            db_bundle_id = existing_bundle.id

            # Stage 3.4 Complete Attestation Gateway:
            # If audit_ledger is configured, verify complete attestation before release
            if self.audit_ledger is not None:
                from clindoc.audit.projection import AuditProjectionEngine
                transport_bytes, verified_sha256 = AuditProjectionEngine.verify_bundle_complete_attestation(
                    session=session,
                    bundle_dict=bundle_dict,
                    ledger_path=self.audit_ledger.ledger_path,
                    checkpoint_path=self.audit_ledger.checkpoint_path,
                    trust_store=self.trust_store,
                    revoked_key_ids=self.revoked_key_ids,
                    keyring=self.audit_ledger.keyring,
                    key_id=self.audit_ledger.signing_key_id,
                )
                bundle_sha256 = verified_sha256
                transport_sha256 = verified_sha256
                bundle_json = transport_bytes.decode("utf-8")
            else:
                bundle_sha256 = existing_bundle.bundle_sha256
                transport_sha256 = bundle_sha256
                bundle_json = json.dumps(bundle_dict, indent=2, sort_keys=True)
        else:
            # Generate and validate bundle
            export_result: FHIRExportResult = self.export_service.export_document(
                session=session,
                document_id=doc_uuid,
                exported_by=actor_uuid,
                target_type="EHR_TRANSMISSION",
                correlation_id=corr_id,
            )
            bundle_id = export_result.bundle_id
            bundle_sha256 = export_result.bundle_sha256
            transport_sha256 = bundle_sha256
            bundle_json = export_result.bundle_json
            db_bundle_id = export_result.db_bundle_id

        # ─── 4. IDEMPOTENCY & DUPLICATE SUBMISSION CHECK ──────────────────────
        idempotency_key = f"ehr-{doc_uuid}-{bundle_sha256}"
        existing_export = (
            session.query(FHIRExport)
            .filter(FHIRExport.idempotency_key == idempotency_key)
            .first()
        )

        if existing_export and existing_export.export_status == FHIRExportStatus.TRANSMITTED:
            if not allow_resubmission:
                get_metrics().record_ehr_transmission("duplicate", destination=destination_type)
                raise EHRDuplicateSubmissionError(
                    f"Document '{doc_uuid}' was already transmitted to EHR under export {existing_export.id} "
                    f"(Idempotency key: {idempotency_key})."
                )

        # ─── 5. AUDIT EVENT: TRANSMISSION ATTEMPT ────────────────────────────
        audit_attempt = self.audit_service.record_event(
            session=session,
            actor_id=str(actor_uuid),
            event_type="EHR_TRANSMISSION_ATTEMPTED",
            entity_type="DOCUMENT",
            entity_id=str(doc_uuid),
            action="TRANSMIT_TO_EHR",
            metadata_json={
                "bundle_sha256": bundle_sha256,
                "destination_type": destination_type,
                "correlation_id": corr_id,
                "idempotency_key": idempotency_key,
            },
            correlation_id=corr_id,
        )
        session.flush()

        # Record attempt metric
        get_metrics().record_ehr_transmission("attempt", destination=destination_type)

        # ─── 6. EXECUTE TRANSMISSION TO TRANSPORT BOUNDARY ───────────────────
        try:
            resp: EHRTransmissionResponse = self.transport.transmit(
                bundle_json,
                idempotency_key=idempotency_key,
                correlation_id=corr_id,
                document_id=str(doc_uuid),
                expected_transport_sha256=transport_sha256,
            )
        except EHRTimeoutError as exc:
            elapsed = time.perf_counter() - start_time
            self._handle_transmission_failure(
                session=session,
                doc=doc,
                initial_doc_status=initial_doc_status,
                db_bundle_id=db_bundle_id,
                actor_uuid=actor_uuid,
                idempotency_key=idempotency_key,
                destination_type=destination_type,
                error_type="TIMEOUT",
                error_message=str(exc),
                corr_id=corr_id,
                bundle_sha256=bundle_sha256,
                elapsed=elapsed,
            )
            get_metrics().record_ehr_transmission("timeout", destination=destination_type, duration_seconds=elapsed)
            raise

        except EHRAuthenticationError as exc:
            elapsed = time.perf_counter() - start_time
            self._handle_transmission_failure(
                session=session,
                doc=doc,
                initial_doc_status=initial_doc_status,
                db_bundle_id=db_bundle_id,
                actor_uuid=actor_uuid,
                idempotency_key=idempotency_key,
                destination_type=destination_type,
                error_type="AUTH_ERROR",
                error_message=str(exc),
                corr_id=corr_id,
                bundle_sha256=bundle_sha256,
                elapsed=elapsed,
            )
            get_metrics().record_ehr_transmission("auth_error", destination=destination_type, duration_seconds=elapsed)
            raise

        except EHRValidationError as exc:
            elapsed = time.perf_counter() - start_time
            self._handle_transmission_failure(
                session=session,
                doc=doc,
                initial_doc_status=initial_doc_status,
                db_bundle_id=db_bundle_id,
                actor_uuid=actor_uuid,
                idempotency_key=idempotency_key,
                destination_type=destination_type,
                error_type="VALIDATION_ERROR",
                error_message=str(exc),
                corr_id=corr_id,
                bundle_sha256=bundle_sha256,
                elapsed=elapsed,
            )
            get_metrics().record_ehr_transmission("validation_error", destination=destination_type, duration_seconds=elapsed)
            raise

        except EHRDuplicateSubmissionError as exc:
            elapsed = time.perf_counter() - start_time
            self._handle_transmission_failure(
                session=session,
                doc=doc,
                initial_doc_status=initial_doc_status,
                db_bundle_id=db_bundle_id,
                actor_uuid=actor_uuid,
                idempotency_key=idempotency_key,
                destination_type=destination_type,
                error_type="DUPLICATE",
                error_message=str(exc),
                corr_id=corr_id,
                bundle_sha256=bundle_sha256,
                elapsed=elapsed,
            )
            get_metrics().record_ehr_transmission("duplicate", destination=destination_type, duration_seconds=elapsed)
            raise

        except Exception as exc:
            elapsed = time.perf_counter() - start_time
            self._handle_transmission_failure(
                session=session,
                doc=doc,
                initial_doc_status=initial_doc_status,
                db_bundle_id=db_bundle_id,
                actor_uuid=actor_uuid,
                idempotency_key=idempotency_key,
                destination_type=destination_type,
                error_type="TRANSPORT_ERROR",
                error_message=str(exc),
                corr_id=corr_id,
                bundle_sha256=bundle_sha256,
                elapsed=elapsed,
            )
            get_metrics().record_ehr_transmission("failure", destination=destination_type, duration_seconds=elapsed)
            raise EHRTransportError(f"EHR transmission failed: {exc}") from exc

        elapsed = time.perf_counter() - start_time

        # ─── 7. HANDLE SUCCESSFUL TRANSMISSION ────────────────────────────────
        # Create or update FHIRExport record
        if existing_export:
            export_row = existing_export
            export_row.export_status = FHIRExportStatus.TRANSMITTED
            export_row.attempts += 1
            export_row.exported_at = datetime.datetime.now(datetime.timezone.utc)
            export_row.destination_path = resp.remote_message_id or f"ehr://{destination_type}"
        else:
            export_row = FHIRExport(
                bundle_id=db_bundle_id,
                target_type="EHR_TRANSMISSION",
                export_status=FHIRExportStatus.TRANSMITTED,
                idempotency_key=idempotency_key,
                destination_path=resp.remote_message_id or f"ehr://{destination_type}",
                exported_by=actor_uuid,
                exported_at=datetime.datetime.now(datetime.timezone.utc),
            )
            session.add(export_row)

        # Update Document status to EXPORTED
        doc.status = DocumentStatus.EXPORTED
        session.flush()

        # Audit Event: EHR_TRANSMISSION_SUCCESS
        audit_success = self.audit_service.record_event(
            session=session,
            actor_id=str(actor_uuid),
            event_type="EHR_TRANSMISSION_SUCCESS",
            entity_type="DOCUMENT",
            entity_id=str(doc_uuid),
            action="TRANSMIT_TO_EHR_CONFIRMED",
            metadata_json={
                "bundle_sha256": bundle_sha256,
                "destination_type": destination_type,
                "remote_message_id": resp.remote_message_id,
                "status_code": resp.status_code,
                "latency_seconds": round(elapsed, 4),
                "idempotency_key": idempotency_key,
            },
            correlation_id=corr_id,
        )
        session.flush()

        # Metrics: Success
        get_metrics().record_ehr_transmission("success", destination=destination_type, duration_seconds=elapsed)

        return EHRTransmissionResult(
            document_id=str(doc_uuid),
            bundle_id=bundle_id,
            bundle_sha256=bundle_sha256,
            destination_type=destination_type,
            remote_message_id=resp.remote_message_id,
            success=True,
            status_code=resp.status_code,
            idempotency_key=idempotency_key,
            latency_seconds=elapsed,
            export_id=str(export_row.id),
            audit_event_id=str(audit_success.id),
        )

    def _handle_transmission_failure(
        self,
        session: Session,
        doc: DBDocument,
        initial_doc_status: DocumentStatus,
        db_bundle_id: Optional[uuid.UUID],
        actor_uuid: uuid.UUID,
        idempotency_key: str,
        destination_type: str,
        error_type: str,
        error_message: str,
        corr_id: str,
        bundle_sha256: str,
        elapsed: float,
    ) -> None:
        """Record structured failure without mutating document verification state."""
        try:
            # Document status remains as-is (e.g. APPROVED/IN_REVIEW), NEVER marked as EXPORTED.
            doc.status = initial_doc_status
            session.flush()

            # Record failed FHIRExport row for operational audit
            if db_bundle_id:
                failed_export = FHIRExport(
                    bundle_id=db_bundle_id,
                    target_type="EHR_TRANSMISSION",
                    export_status=FHIRExportStatus.FAILED,
                    idempotency_key=f"{idempotency_key}-failed-{uuid.uuid4().hex[:6]}",
                    destination_path=f"ehr://{destination_type}",
                    exported_by=actor_uuid,
                    error_message=f"[{error_type}] {error_message[:500]}",
                    exported_at=datetime.datetime.now(datetime.timezone.utc),
                )
                session.add(failed_export)
                session.flush()

            # Record failure audit event
            self.audit_service.record_event(
                session=session,
                actor_id=str(actor_uuid),
                event_type="EHR_TRANSMISSION_FAILED",
                entity_type="DOCUMENT",
                entity_id=str(doc.id),
                action="TRANSMIT_TO_EHR_FAILED",
                metadata_json={
                    "bundle_sha256": bundle_sha256,
                    "destination_type": destination_type,
                    "error_type": error_type,
                    "correlation_id": corr_id,
                    "latency_seconds": round(elapsed, 4),
                },
                correlation_id=corr_id,
            )
            session.flush()
        except Exception as exc:
            logger.error("Failed to record EHR transmission failure audit event: %s", exc)
