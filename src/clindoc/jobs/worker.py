"""Document processing background worker and pipeline orchestration skeleton.

Authority:
  - PRODUCTIZATION_SPEC.md §2 (W-03 to W-07), §4.1 (R-3), §4.2, §4.3, §9.2, §13 (F-17), §16
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-007, §7 Item 3

Implements Phase 8.3 Task 3B Step 1:
  - Claims ProcessingJobs atomically from JobQueue.
  - Loads Document and manages state transitions (INGESTED -> PROCESSING -> PENDING_REVIEW / FAILED).
  - Provides sequential orchestration boundaries:
      1. Ingestion / OCR
      2. Extraction
      3. Normalization
      4. Verification
      5. Persistence
  - Platform-independent temporary working file management and guaranteed cleanup.
  - Top-level exception handling delegating retry and terminal-failure behavior to JobQueue.
"""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Optional, Sequence
import uuid

from sqlalchemy.orm import Session

from clindoc.config import ProductionProfileError, RuntimeProfile, get_active_profile
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    ContradictionType as DBContradictionType,
    DocumentStatus,
    JobStatus,
    VerificationLabel as DBVerificationLabel,
    VerificationStage as DBVerificationStage,
)
from clindoc.db.models import (
    Claim,
    Document,
    Evidence,
    JobStageEvent,
    ModelRun,
    ProcessingJob,
    RoutingDecision,
    VerificationResult,
)
from clindoc.extract.contracts import (
    Claim as ContractClaim,
    DocumentExtractionOutput,
    Evidence as ContractEvidence,
)
from clindoc.extract.router import ExtractionRouter
from clindoc.normalize.normalizer import TerminologyNormalizer
from clindoc.verify.cascade import VerificationCascade
from clindoc.jobs.queue import (
    InvalidStateTransitionError,
    JobNotFoundError,
    JobQueue,
)
from clindoc.manifest import (
    CANONICAL_CALIBRATION_TEMPERATURE,
    CANONICAL_MODERNBERT_CHECKPOINT_SHA256,
    CANONICAL_MODERNBERT_MODEL_ID,
    CANONICAL_QWEN_MAX_TOKENS,
    CANONICAL_QWEN_MODEL_ID,
    CANONICAL_QWEN_QUANTIZATION,
    CANONICAL_QWEN_REVISION_HASH,
    CANONICAL_QWEN_SEED,
    CANONICAL_QWEN_SERVING_ENGINE,
    CANONICAL_QWEN_TEMPERATURE,
    CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD,
    CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
    CANONICAL_STAGE_C_PROMPT_TEXT_SHA256,
    CANONICAL_STAGE_C_PROMPT_VERSION,
)
from clindoc.ocr.document_text import DocumentText
from clindoc.ocr.ingest import (
    IngestionError,
    UnreadableDocumentError,
    ingest_document,
)
from clindoc.storage.blob import BlobNotFoundError, BlobStore

logger = logging.getLogger(__name__)


def _map_verification_label(label: str) -> DBVerificationLabel:
    """Map verification label string/enum to DBVerificationLabel."""
    if label in ("DISCREPANCY", "REFUTED"):
        return DBVerificationLabel.REFUTED
    if label == "SUPPORTED":
        return DBVerificationLabel.SUPPORTED
    if label == "INSUFFICIENT_EVIDENCE":
        return DBVerificationLabel.INSUFFICIENT_EVIDENCE
    try:
        return DBVerificationLabel(label)
    except ValueError:
        return DBVerificationLabel.REFUTED


def _map_contradiction_type(c_type: Optional[str]) -> Optional[DBContradictionType]:
    """Map verification contradiction type to DBContradictionType."""
    if not c_type:
        return None
    mapping = {
        "value_mismatch": DBContradictionType.NUMERICAL_MISMATCH,
        "dose_mismatch": DBContradictionType.NUMERICAL_MISMATCH,
        "unit_mismatch": DBContradictionType.NUMERICAL_MISMATCH,
        "negation_mismatch": DBContradictionType.NEGATION,
        "wrong_entity": DBContradictionType.ENTITY_MISMATCH,
        "code_mismatch": DBContradictionType.ENTITY_MISMATCH,
        "temporal_mismatch": DBContradictionType.TEMPORAL_MISMATCH,
    }
    return mapping.get(c_type, DBContradictionType.OTHER)


def _map_verification_stage(stage_letter: str) -> DBVerificationStage:
    """Map stage letter to DBVerificationStage."""
    mapping = {
        "A": DBVerificationStage.STAGE_A,
        "B": DBVerificationStage.STAGE_B,
        "C": DBVerificationStage.STAGE_C,
    }
    return mapping.get(stage_letter, DBVerificationStage.STAGE_A)



class WorkerError(Exception):
    """Base exception for worker orchestration failures."""
    pass


class DocumentNotFoundError(WorkerError):
    """Raised when the document referenced by a job does not exist."""
    pass


class InvalidDocumentStateError(WorkerError):
    """Raised when the document is not in a valid state for processing."""
    pass


class DocumentProcessingWorker:
    """Background worker executing the clinical document verification cascade.

    Orchestrates ingestion, extraction, normalization, verification, and persistence
    for claimed jobs.
    """

    def __init__(
        self,
        queue: Optional[JobQueue] = None,
        blob_store: Optional[BlobStore] = None,
        worker_id: Optional[str] = None,
        lease_seconds: Optional[int] = None,
        ingest_fn: Optional[Callable[..., DocumentText]] = None,
        router: Optional[ExtractionRouter] = None,
        normalizer: Optional[TerminologyNormalizer] = None,
        cascade: Optional[VerificationCascade] = None,
        stop_after_stage: Optional[str] = None,
    ) -> None:
        self.queue = queue or JobQueue()
        self.blob_store = blob_store
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = lease_seconds
        self.ingest_fn = ingest_fn or ingest_document
        self.router = router or ExtractionRouter()
        self.normalizer = normalizer or TerminologyNormalizer()
        self.cascade = cascade or VerificationCascade()
        self.stop_after_stage = stop_after_stage
        self.last_ocr_output: Optional[DocumentText] = None
        self.last_extraction_output: Optional[DocumentExtractionOutput] = None
        self.last_normalized_claims: Optional[list[Any]] = None
        self.last_verification_output: Optional[list[Any]] = None

    def _get_or_create_model_run(self, session: Session, job: ProcessingJob) -> ModelRun:
        """Resolve existing or create canonical ModelRun record for provenance (§3.7)."""
        if job.model_run_id:
            existing = session.get(ModelRun, job.model_run_id)
            if existing:
                return existing

        profile = get_active_profile()
        is_prod = (profile == RuntimeProfile.PRODUCTION)
        model_run = ModelRun(
            is_qualified=is_prod,
            runtime_profile=profile.value if hasattr(profile, "value") else str(profile),
            component="pipeline_cascade",
            provider_kind="vllm" if is_prod else "mock",
            stage_b_model_id=CANONICAL_MODERNBERT_MODEL_ID,
            stage_b_checkpoint_hash=CANONICAL_MODERNBERT_CHECKPOINT_SHA256,
            stage_b_temperature=CANONICAL_CALIBRATION_TEMPERATURE,
            stage_b_escalation_threshold=CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
            stage_b_autonomous_threshold=CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD,
            stage_c_model_id=CANONICAL_QWEN_MODEL_ID,
            model_revision_hash=CANONICAL_QWEN_REVISION_HASH,
            quantization=CANONICAL_QWEN_QUANTIZATION,
            serving_engine_version="0.6.3",
            prompt_id=CANONICAL_STAGE_C_PROMPT_VERSION,
            stage_c_prompt_version=CANONICAL_STAGE_C_PROMPT_VERSION,
            stage_c_prompt_hash=CANONICAL_STAGE_C_PROMPT_TEXT_SHA256,
            stage_c_serving_engine=CANONICAL_QWEN_SERVING_ENGINE,
            stage_c_parameters={
                "temperature": CANONICAL_QWEN_TEMPERATURE,
                "seed": CANONICAL_QWEN_SEED,
                "max_tokens": CANONICAL_QWEN_MAX_TOKENS,
            },
            calibrator_version="v3_fold0_seed42",
            schema_version="1.0.0",
            extraction_model_id=CANONICAL_QWEN_MODEL_ID,
            extraction_prompt_version="V1_NARRATIVE_EXTRACTION_PROMPT",
            extraction_prompt_hash="11223344" * 8,
            extraction_serving_engine=CANONICAL_QWEN_SERVING_ENGINE,
            extraction_parameters={"temperature": 0.0, "seed": 42, "max_tokens": 512},
        )
        session.add(model_run)
        session.flush()
        job.model_run_id = model_run.id
        session.flush()
        return model_run

    @staticmethod
    def _sanitize_error(
        exc: Exception,
        sensitive_strings: Optional[Sequence[Optional[str]]] = None,
    ) -> str:
        """Sanitize error messages to prevent leaking PHI, paths, or secrets into database records."""
        msg = str(exc).strip()
        if not msg:
            return type(exc).__name__
        if sensitive_strings:
            for s in sensitive_strings:
                if s and s in msg:
                    msg = msg.replace(s, "[REDACTED]")
        if len(msg) > 500:
            msg = msg[:497] + "..."
        exc_type = type(exc).__name__
        if msg.startswith(exc_type):
            return msg
        return f"{exc_type}: {msg}"

    def claim_job(self, session: Session) -> Optional[ProcessingJob]:
        """Claim the next available pending job from the queue."""
        return self.queue.claim_next(
            session=session,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )

    def _prepare_scratch_file(self, document: Document) -> tuple[Optional[Path], bool]:
        """Prepare a platform-appropriate temporary working file from blob store or disk.

        Returns (temp_path, is_temporary).
        If is_temporary is True, caller is responsible for unlinking temp_path.
        """
        # Determine appropriate file extension
        suffix = ".pdf"
        if document.original_filename:
            p = Path(document.original_filename)
            if p.suffix:
                suffix = p.suffix.lower()
        elif document.mime_type:
            if "pdf" in document.mime_type:
                suffix = ".pdf"
            elif "png" in document.mime_type:
                suffix = ".png"
            elif "jpeg" in document.mime_type or "jpg" in document.mime_type:
                suffix = ".jpg"
            elif "tiff" in document.mime_type:
                suffix = ".tiff"

        if self.blob_store and document.storage_ref:
            # Decrypt blob to temporary file
            data = self.blob_store.get(document.storage_ref)
            with tempfile.NamedTemporaryFile(
                prefix=f"clindoc_job_{document.id.hex[:8]}_",
                suffix=suffix,
                delete=False,
            ) as tf:
                tf.write(data)
                temp_path = Path(tf.name)
            return temp_path, True
        elif document.storage_ref and document.storage_ref.startswith("file://"):
            local_path = Path(document.storage_ref.removeprefix("file://"))
            if local_path.exists():
                return local_path, False

        return None, False

    def process_job(self, session: Session, job: ProcessingJob) -> ProcessingJob:
        """Execute the sequential pipeline lifecycle for a claimed job."""
        if job.status != JobStatus.RUNNING:
            raise InvalidStateTransitionError(
                f"Worker cannot process job {job.id} with status '{job.status}'. Expected 'RUNNING'."
            )

        document = session.get(Document, job.document_id)
        if document is None:
            err_msg = f"Document {job.document_id} referenced by job {job.id} does not exist."
            logger.error(err_msg)
            return self.queue.mark_failed(
                session=session,
                job_id=job.id,
                error_code="DOCUMENT_NOT_FOUND",
                error_message=err_msg,
                retryable=False,
            )

        # Validate document state
        if document.status in (DocumentStatus.QUARANTINED, DocumentStatus.FAILED_INGEST):
            err_msg = f"Cannot process document {document.id} in state '{document.status}'."
            logger.error(err_msg)
            return self.queue.mark_failed(
                session=session,
                job_id=job.id,
                error_code="INVALID_DOCUMENT_STATE",
                error_message=err_msg,
                retryable=False,
            )

        # Transition document state INGESTED -> PROCESSING
        document.status = DocumentStatus.PROCESSING
        job.current_stage = "PROCESSING"
        session.flush()

        temp_path: Optional[Path] = None
        is_temp = False

        try:
            temp_path, is_temp = self._prepare_scratch_file(document)

            # ─── SEQUENTIAL ORCHESTRATION SKELETON ───────────────────────────
            # Stage 1: Ingestion / OCR
            ocr_output = self._orchestrate_ocr_stage(session, job, document, temp_path)
            self.last_ocr_output = ocr_output

            # Stage 2: Extraction
            extraction_output = self._orchestrate_extraction_stage(session, job, document, ocr_output)
            self.last_extraction_output = extraction_output

            # Step 3 Stop boundary: Do NOT proceed to normalization or verification yet
            if self.stop_after_stage == "EXTRACTION":
                session.flush()
                return job

            # Stage 3: Normalization
            normalized_claims = self._orchestrate_normalization_stage(
                session=session,
                job=job,
                document=document,
                extraction_output=extraction_output,
            )
            self.last_normalized_claims = normalized_claims

            # Step 4 Stop boundary: Do NOT proceed to verification yet
            if self.stop_after_stage == "NORMALIZATION":
                session.flush()
                return job

            # Stage 4: Verification Cascade
            verification_output = self._orchestrate_verification_stage(
                session=session,
                job=job,
                document=document,
                normalized_claims=normalized_claims,
                extraction_output=extraction_output,
                ocr_output=ocr_output,
            )
            self.last_verification_output = verification_output

            if self.stop_after_stage == "VERIFICATION":
                session.flush()
                return job

            # Stage 5: Persistence
            self._orchestrate_persistence_stage(
                session, job, document, extraction_output, normalized_claims, verification_output
            )

            # On successful completion:
            document.status = DocumentStatus.PENDING_REVIEW
            job.current_stage = "COMPLETED"
            session.flush()
            return self.queue.mark_completed(session=session, job_id=job.id)

        except (DocumentNotFoundError, BlobNotFoundError, IngestionError, ProductionProfileError) as terminal_exc:
            logger.error("Terminal failure during job %s: %s", job.id, terminal_exc)
            document.status = DocumentStatus.FAILED
            session.flush()
            sanitized_err = self._sanitize_error(
                terminal_exc,
                sensitive_strings=[
                    str(temp_path) if temp_path else None,
                    document.original_filename,
                    document.patient_context_id,
                ],
            )
            return self.queue.mark_failed(
                session=session,
                job_id=job.id,
                error_code=type(terminal_exc).__name__,
                error_message=sanitized_err,
                retryable=False,
            )
        except Exception as exc:
            logger.exception("Unexpected error during job %s: %s", job.id, exc)
            sanitized_err = self._sanitize_error(
                exc,
                sensitive_strings=[
                    str(temp_path) if temp_path else None,
                    document.original_filename,
                    document.patient_context_id,
                ],
            )
            # Transient / unclassified error -> delegate retry evaluation to JobQueue
            res = self.queue.mark_failed(
                session=session,
                job_id=job.id,
                error_code=type(exc).__name__,
                error_message=sanitized_err,
                retryable=True,
            )
            if res.status == JobStatus.FAILED:
                document.status = DocumentStatus.FAILED
                session.flush()
            return res
        finally:
            if is_temp and temp_path and temp_path.exists():
                import gc
                gc.collect()
                for attempt in range(5):
                    try:
                        temp_path.unlink()
                        break
                    except PermissionError:
                        time.sleep(0.05)
                        gc.collect()
                    except OSError as unlink_err:
                        logger.warning("Failed to unlink scratch file %s: %s", temp_path, unlink_err)
                        break
                else:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except OSError as unlink_err:
                            logger.warning("Failed to unlink scratch file %s: %s", temp_path, unlink_err)

    def run_once(self, session: Session) -> Optional[ProcessingJob]:
        """Claim and process a single job from the queue if one is available."""
        job = self.claim_job(session=session)
        if job is None:
            return None
        return self.process_job(session=session, job=job)

    # ─── STAGE BOUNDARIES (SKELETON) ─────────────────────────────────────────

    def _orchestrate_ocr_stage(
        self,
        session: Session,
        job: ProcessingJob,
        document: Document,
        file_path: Optional[Path],
    ) -> DocumentText:
        """Stage 1: Ingestion & OCR boundary (PROJECT_SPEC §7.1, PRODUCTIZATION_SPEC §2 W-03).

        Executes canonical ingest_document on the decrypted document file, records
        granular telemetry to job_stage_events (STARTED, COMPLETED, FAILED), computes
        PHI-free operational metrics, and retains DocumentText for downstream extraction.
        """
        job.current_stage = "OCR"
        session.flush()

        # Telemetry: Record stage STARTED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="OCR",
            status="STARTED",
        )

        start_time = time.monotonic()
        sensitive_elements = [
            str(file_path) if file_path else None,
            document.original_filename,
            document.patient_context_id,
        ]

        if file_path is None or not file_path.exists():
            err = UnreadableDocumentError(
                f"No readable document file available for processing document {document.id}"
            )
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_err = self._sanitize_error(err, sensitive_elements)
            self.queue.record_stage_event(
                session=session,
                job_id=job.id,
                stage_name="OCR",
                status="FAILED",
                duration_ms=duration_ms,
                error_message=sanitized_err,
            )
            raise err

        try:
            # Map source_type from document if known
            doc_source_type = "auto"
            if document.source_type in ("born_digital", "scanned_image"):
                doc_source_type = document.source_type

            # Provide tabular layout hint if recognizable from original_filename
            is_tabular_hint: Optional[bool] = None
            if document.original_filename and document.original_filename.upper().startswith("LAB_"):
                is_tabular_hint = True

            doc_text = self.ingest_fn(
                path=file_path,
                document_id=str(document.id),
                source_type=doc_source_type,
                is_tabular=is_tabular_hint,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_err = self._sanitize_error(exc, sensitive_elements)
            self.queue.record_stage_event(
                session=session,
                job_id=job.id,
                stage_name="OCR",
                status="FAILED",
                duration_ms=duration_ms,
                error_message=sanitized_err,
            )
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Operational PHI-free metrics (§11)
        confidences = [
            t.confidence
            for p in doc_text.pages
            for t in p.tokens
            if t.confidence is not None
        ]
        ocr_confidence_mean = (
            round(sum(confidences) / len(confidences), 4) if confidences else None
        )

        metrics: dict[str, Any] = {
            "page_count": doc_text.num_pages,
            "modality": doc_text.source_type,
            "total_tokens": doc_text.total_tokens,
        }
        if ocr_confidence_mean is not None:
            metrics["ocr_confidence_mean"] = ocr_confidence_mean

        # Telemetry: Record stage COMPLETED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="OCR",
            status="COMPLETED",
            duration_ms=duration_ms,
            metrics=metrics,
        )

        return doc_text

    def _orchestrate_extraction_stage(
        self,
        session: Session,
        job: ProcessingJob,
        document: Document,
        ocr_output: Any,
    ) -> DocumentExtractionOutput:
        """Stage 2: Extraction & relational persistence boundary (PROJECT_SPEC §7.2, §8, §9).

        Executes ExtractionRouter on DocumentText, records granular telemetry to
        job_stage_events (STARTED, COMPLETED, FAILED), computes PHI-free operational
        metrics, persists Evidence (reusing matching spans), RoutingDecisions, and Claims
        linked to the appropriate ModelRun, ensuring strict retry idempotency.
        """
        job.current_stage = "EXTRACTION"
        session.flush()

        # Telemetry: Record stage STARTED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="EXTRACTION",
            status="STARTED",
        )

        start_time = time.monotonic()
        sensitive_elements = [
            document.original_filename,
            document.patient_context_id,
        ]

        try:
            if ocr_output is None or not isinstance(ocr_output, DocumentText):
                raise ValueError(
                    f"Invalid or missing DocumentText for document {document.id} in extraction stage."
                )

            # 1. Execute extraction router (is_lab_default=None: use native content auto-detection)
            extraction_output = self.router.extract_document(doc=ocr_output, is_lab_default=None)

            # 2. Resolve or create ModelRun provenance record (§3.7)
            model_run = self._get_or_create_model_run(session, job)

            # 3. Persist Evidence (reusing existing matching spans per uq_evidence_document_span)
            existing_evidence = (
                session.query(Evidence)
                .filter(Evidence.document_id == document.id)
                .all()
            )
            evidence_by_span: dict[tuple[int, int, int], Evidence] = {
                (ev.page_number, ev.char_start, ev.char_end): ev
                for ev in existing_evidence
            }

            contract_to_db_evidence: dict[str, uuid.UUID] = {}

            for contract_ev in extraction_output.all_evidence:
                span_key = (contract_ev.page_number, contract_ev.char_start, contract_ev.char_end)
                if span_key in evidence_by_span:
                    db_ev = evidence_by_span[span_key]
                else:
                    db_ev = Evidence(
                        document_id=document.id,
                        page_number=contract_ev.page_number,
                        char_start=contract_ev.char_start,
                        char_end=contract_ev.char_end,
                        bounding_box=contract_ev.bbox.model_dump(),
                        ocr_confidence=contract_ev.ocr_confidence,
                        evidence_type="ocr_text",
                        evidence_text=contract_ev.text,
                        payload=contract_ev.model_dump(mode="json"),
                    )
                    session.add(db_ev)
                    session.flush()
                    evidence_by_span[span_key] = db_ev

                contract_to_db_evidence[contract_ev.evidence_id] = db_ev.id

            # 4. Persist Routing Decisions (idempotent: replace previous decisions for this document)
            session.query(RoutingDecision).filter(
                RoutingDecision.document_id == document.id
            ).delete(synchronize_session=False)

            for rd in extraction_output.all_routing_decisions:
                db_rd = RoutingDecision(
                    document_id=document.id,
                    region_id=rd.region_id,
                    route=rd.route,
                    reason=rd.reason,
                    confidence=rd.confidence,
                )
                session.add(db_rd)
            session.flush()

            # 5. Persist Claims (idempotent: replace previous pipeline claims for this document)
            session.query(Claim).filter(
                Claim.document_id == document.id,
                Claim.origin == ClaimOrigin.PIPELINE,
            ).delete(synchronize_session=False)

            for contract_claim in extraction_output.all_claims:
                try:
                    c_uuid = uuid.UUID(contract_claim.claim_id)
                except (ValueError, TypeError):
                    c_uuid = uuid.uuid4()
                    contract_claim.claim_id = str(c_uuid)

                db_claim = Claim(
                    claim_id=c_uuid,
                    document_id=document.id,
                    group_id=contract_claim.group_id or contract_claim.region_id or "default",
                    field_type=contract_claim.field_type,
                    value=contract_claim.value,
                    normalized_code=contract_claim.normalized_code,
                    code_system=contract_claim.code_system,
                    version=1,
                    origin=ClaimOrigin.PIPELINE,
                    review_state=ClaimReviewState.PENDING,
                    evidence_id=contract_to_db_evidence.get(contract_claim.evidence_id),
                    model_run_id=model_run.id if model_run else None,
                    payload=contract_claim.model_dump(mode="json"),
                )
                session.add(db_claim)
            session.flush()

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_err = self._sanitize_error(exc, sensitive_elements)
            self.queue.record_stage_event(
                session=session,
                job_id=job.id,
                stage_name="EXTRACTION",
                status="FAILED",
                duration_ms=duration_ms,
                error_message=sanitized_err,
            )
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Operational PHI-free telemetry metrics
        route_counts: dict[str, int] = {}
        for rd in extraction_output.all_routing_decisions:
            route_counts[rd.route] = route_counts.get(rd.route, 0) + 1

        metrics: dict[str, Any] = {
            "num_regions": len(extraction_output.regions),
            "num_claims": len(extraction_output.all_claims),
            "num_evidence_spans": len(extraction_output.all_evidence),
            "route_counts": route_counts,
        }

        # Telemetry: Record stage COMPLETED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="EXTRACTION",
            status="COMPLETED",
            duration_ms=duration_ms,
            metrics=metrics,
        )

        return extraction_output

    def _orchestrate_normalization_stage(
        self,
        session: Session,
        job: ProcessingJob,
        document: Document,
        extraction_output: Any,
    ) -> list[Any]:
        """Stage 3: Terminology normalization boundary (PROJECT_SPEC §7.5, §8.3, §14).

        Executes TerminologyNormalizer on extracted claims, records granular telemetry
        to job_stage_events (STARTED, COMPLETED, FAILED), computes PHI-free operational
        metrics, and updates existing persisted pipeline Claim records with standardized
        normalized codes, code systems, units, and audit metadata in-place.
        """
        job.current_stage = "NORMALIZATION"
        session.flush()

        # Telemetry: Record stage STARTED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="NORMALIZATION",
            status="STARTED",
        )

        start_time = time.monotonic()
        sensitive_elements = [
            document.original_filename,
            document.patient_context_id,
        ]

        try:
            # 1. Fetch existing pipeline claims for this document
            db_claims = (
                session.query(Claim)
                .filter(
                    Claim.document_id == document.id,
                    Claim.origin == ClaimOrigin.PIPELINE,
                )
                .all()
            )

            # 2. Collect contract Claim objects to normalize
            claims_to_normalize: list[Any] = []
            if extraction_output is not None and hasattr(extraction_output, "all_claims") and extraction_output.all_claims:
                claims_to_normalize = list(extraction_output.all_claims)
            elif extraction_output is not None and isinstance(extraction_output, list) and extraction_output:
                claims_to_normalize = list(extraction_output)
            else:
                valid_claim_keys = ContractClaim.model_fields.keys()
                for dc in db_claims:
                    if dc.payload:
                        try:
                            claim_data = {k: v for k, v in dc.payload.items() if k in valid_claim_keys}
                            claims_to_normalize.append(ContractClaim.model_validate(claim_data))
                        except Exception:
                            pass

            # 3. Execute terminology normalizer
            normalized_claims = self.normalizer.normalize_claims(claims_to_normalize)

            # 4. Update existing persisted Claim records in-place
            db_claims_by_id = {str(c.claim_id): c for c in db_claims}

            for idx, norm_claim in enumerate(normalized_claims):
                db_claim = db_claims_by_id.get(norm_claim.claim_id)
                if db_claim is None and idx < len(db_claims):
                    db_claim = db_claims[idx]

                if db_claim is not None:
                    db_claim.normalized_code = norm_claim.normalized_code
                    db_claim.code_system = norm_claim.code_system

                    # Enrich full JSON payload with normalization metadata
                    payload = dict(db_claim.payload or {})
                    payload.update({
                        "normalized_code": norm_claim.normalized_code,
                        "code_system": norm_claim.code_system,
                        "code_confidence": getattr(norm_claim, "code_confidence", 0.0),
                        "value_numeric": getattr(norm_claim, "value_numeric", None),
                    })

                    norm_unit = self.normalizer.get_normalized_unit(norm_claim.claim_id)
                    if norm_unit is not None:
                        payload["normalized_unit"] = norm_unit

                    candidates = self.normalizer.get_audit_candidates(norm_claim.claim_id)
                    if candidates:
                        payload["candidates"] = candidates

                    db_claim.payload = payload

            session.flush()

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_err = self._sanitize_error(exc, sensitive_elements)
            self.queue.record_stage_event(
                session=session,
                job_id=job.id,
                stage_name="NORMALIZATION",
                status="FAILED",
                duration_ms=duration_ms,
                error_message=sanitized_err,
            )
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Operational PHI-free telemetry metrics
        num_coded = sum(
            1
            for c in normalized_claims
            if getattr(c, "normalized_code", None) and getattr(c, "code_system", None)
        )
        num_uncoded = len(normalized_claims) - num_coded
        system_counts: dict[str, int] = {}
        for c in normalized_claims:
            cs = getattr(c, "code_system", None)
            if cs:
                s_name = str(cs)
                system_counts[s_name] = system_counts.get(s_name, 0) + 1

        metrics: dict[str, Any] = {
            "total_claims": len(normalized_claims),
            "num_coded": num_coded,
            "num_uncoded": num_uncoded,
            "system_counts": system_counts,
        }

        # Telemetry: Record stage COMPLETED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="NORMALIZATION",
            status="COMPLETED",
            duration_ms=duration_ms,
            metrics=metrics,
        )

        return normalized_claims




    def _orchestrate_verification_stage(
        self,
        session: Session,
        job: ProcessingJob,
        document: Document,
        normalized_claims: Any,
        extraction_output: Any,
        ocr_output: Any,
    ) -> list[Any]:
        """Stage 4: Verification cascade boundary (PROJECT_SPEC §7.6, §10, §12).

        Executes VerificationCascade.verify_claims with claims, evidence_map, and
        page_text_map. Records granular telemetry (STARTED, COMPLETED, FAILED),
        computes PHI-free operational metrics, and persists VerificationResult records
        linked to Claim.id and ModelRun.id with idempotent cleanup.
        """
        job.current_stage = "VERIFICATION"
        session.flush()

        # Telemetry: Record stage STARTED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="VERIFICATION",
            status="STARTED",
        )

        start_time = time.monotonic()
        sensitive_elements = [
            document.original_filename,
            document.patient_context_id,
        ]

        try:
            # 1. Fetch persisted pipeline claims for this document
            db_claims = (
                session.query(Claim)
                .filter(
                    Claim.document_id == document.id,
                    Claim.origin == ClaimOrigin.PIPELINE,
                )
                .all()
            )

            # 2. Reconstruct / collect contract Claim objects
            claims_to_verify: list[Any] = []
            if normalized_claims is not None and isinstance(normalized_claims, list) and normalized_claims:
                claims_to_verify = list(normalized_claims)
            else:
                valid_claim_keys = ContractClaim.model_fields.keys()
                for dc in db_claims:
                    if dc.payload:
                        try:
                            claim_data = {k: v for k, v in dc.payload.items() if k in valid_claim_keys}
                            claims_to_verify.append(ContractClaim.model_validate(claim_data))
                        except Exception:
                            pass

            # 3. Construct evidence_map: dict[str, ContractEvidence]
            evidence_map: dict[str, ContractEvidence] = {}
            if extraction_output is not None and hasattr(extraction_output, "all_evidence") and extraction_output.all_evidence:
                evidence_map = {ev.evidence_id: ev for ev in extraction_output.all_evidence}
            else:
                valid_ev_keys = ContractEvidence.model_fields.keys()
                db_evidences = (
                    session.query(Evidence)
                    .filter(Evidence.document_id == document.id)
                    .all()
                )
                for dev in db_evidences:
                    if dev.payload and "evidence_id" in dev.payload:
                        try:
                            ev_data = {k: v for k, v in dev.payload.items() if k in valid_ev_keys}
                            evidence_map[dev.payload["evidence_id"]] = ContractEvidence.model_validate(ev_data)
                        except Exception:
                            pass

            # 4. Construct page_text_map: dict[str, str]
            page_text_map: Optional[dict[str, str]] = None
            source_doc_text = ocr_output or self.last_ocr_output
            if source_doc_text and hasattr(source_doc_text, "pages"):
                page_text_map = {
                    str(p.page_number): p.get_page_text()
                    for p in source_doc_text.pages
                }

            # 5. Invoke verification cascade
            verification_results = self.cascade.verify_claims(
                claims=claims_to_verify,
                evidence_map=evidence_map,
                page_text_map=page_text_map,
            )

            # 6. Idempotent cleanup of prior VerificationResults for these pipeline claims
            claim_primary_ids = [c.id for c in db_claims]
            if claim_primary_ids:
                session.query(VerificationResult).filter(
                    VerificationResult.claim_id.in_(claim_primary_ids)
                ).delete(synchronize_session=False)

            # 7. Persist VerificationResult records
            db_claims_by_claim_id = {str(c.claim_id): c for c in db_claims}
            profile = get_active_profile()
            is_prod = (profile == RuntimeProfile.PRODUCTION)
            provider_kind = "modernbert_vllm" if is_prod else "mock_dev"

            for idx, res in enumerate(verification_results):
                db_claim = db_claims_by_claim_id.get(res.claim_id)
                if db_claim is None and idx < len(db_claims):
                    db_claim = db_claims[idx]

                if db_claim is None:
                    continue

                try:
                    vr_id = uuid.UUID(res.verification_id)
                except (ValueError, TypeError):
                    vr_id = uuid.uuid4()

                db_vr = VerificationResult(
                    id=vr_id,
                    claim_id=db_claim.id,
                    label=_map_verification_label(res.label),
                    calibrated_confidence=float(res.confidence),
                    verification_stage=_map_verification_stage(res.stage_reached),
                    escalated=bool(res.escalated),
                    is_fallback=False,
                    provider_kind=provider_kind,
                    model_run_id=job.model_run_id,
                    contradiction_type=_map_contradiction_type(res.contradiction_type),
                    rationale=res.rationale,
                    stage_a_details={"label": res.stage_a_label} if res.stage_a_label else None,
                    stage_b_details={"label": res.stage_b_label, "raw_confidence": res.stage_b_raw_confidence} if res.stage_b_label else None,
                    stage_c_details={"rationale": res.rationale} if res.rationale else None,
                    payload=res.model_dump(mode="json"),
                )
                session.add(db_vr)

            session.flush()

        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            sanitized_err = self._sanitize_error(exc, sensitive_elements)
            self.queue.record_stage_event(
                session=session,
                job_id=job.id,
                stage_name="VERIFICATION",
                status="FAILED",
                duration_ms=duration_ms,
                error_message=sanitized_err,
            )
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)

        # Operational PHI-free telemetry metrics
        num_supported = sum(1 for r in verification_results if r.label == "SUPPORTED")
        num_discrepancy = sum(1 for r in verification_results if r.label in ("DISCREPANCY", "REFUTED"))
        num_insufficient = sum(1 for r in verification_results if r.label == "INSUFFICIENT_EVIDENCE")
        num_escalated = sum(1 for r in verification_results if r.escalated)
        stage_counts = {"A": 0, "B": 0, "C": 0}
        for r in verification_results:
            if r.stage_reached in stage_counts:
                stage_counts[r.stage_reached] += 1

        metrics: dict[str, Any] = {
            "total_claims_verified": len(verification_results),
            "num_supported": num_supported,
            "num_discrepancy": num_discrepancy,
            "num_insufficient_evidence": num_insufficient,
            "num_escalated": num_escalated,
            "stage_counts": stage_counts,
        }

        # Telemetry: Record stage COMPLETED
        self.queue.record_stage_event(
            session=session,
            job_id=job.id,
            stage_name="VERIFICATION",
            status="COMPLETED",
            duration_ms=duration_ms,
            metrics=metrics,
        )

        return verification_results

    def _orchestrate_persistence_stage(
        self,
        session: Session,
        job: ProcessingJob,
        document: Document,
        extraction_output: Any,
        normalized_claims: Any,
        verification_output: Any,
    ) -> None:
        """Stage 5: Relational persistence boundary."""
        job.current_stage = "PERSISTENCE"
        session.flush()
        # Full implementation wired in subsequent step
