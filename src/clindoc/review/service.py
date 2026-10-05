"""Claim-level human review service with inline re-verification (PRODUCTIZATION_SPEC §7.3, PD-002, PD-010).

Authority:
  - PRODUCTIZATION_SPEC.md §4.3 (claims, review_actions, verification_results logical models)
  - PRODUCTIZATION_SPEC.md §7.3 (Reviewer Actions, Claim versioning, Inline re-verification)
  - PRODUCTIZATION_DECISIONS.md PD-002 (Mode 0: 100% human review, no autonomous export)
  - PRODUCTIZATION_DECISIONS.md PD-010 (Immutable claim history: edits create new Claim versions)
  - PRODUCTIZATION_DECISIONS.md PD-012 (Append-only hash-chained audit logging, fail-closed atomic)

Core Invariants:
  1. Immutable claim history: Existing claims are never overwritten in-place. Edits create
     a new Claim version linked via supersedes_claim_id, incrementing version, setting
     origin='reviewer' and preserving evidence linkage.
  2. Optimistic locking: Updates validate expected_version against current Claim.version
     to prevent concurrent reviewers from silently overwriting each other.
  3. Inline re-verification: Edited claims immediately execute the existing VerificationCascade
     (Stages A/B only, unless explicit escalation criteria are met), persisting the new
     VerificationResult.
  4. Triage re-evaluation: Edited claims re-run the Mode 0 TriagePolicy engine. The edited
     claim does not become automatically accepted; its verification and triage verdict
     are respected, maintaining review_state=PENDING under Mode 0.
  5. State lifecycle validation: Enforces valid state transitions:
     - PENDING -> ACCEPTED (ACCEPT)
     - PENDING -> EDITED (EDIT; creates new version in PENDING)
     - PENDING -> REJECTED (REJECT)
     - PENDING -> ESCALATED (ESCALATE)
     - ACCEPTED / REJECTED / ESCALATED -> PENDING (REOPEN)
     All other transitions are rejected.
  6. Audit integration: Every action creates a ReviewAction record and appends a PHI-free
     AuditEvent using AuditService within the same atomic transaction. Audit failures
     force a rollback of all review mutations.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Optional, Union
import uuid

from sqlalchemy.orm import Session

from clindoc.api.audit.service import AuditService, AuditServiceError, validate_metadata_phi_free
from clindoc.db.enums import (
    ClaimOrigin,
    ClaimReviewState,
    ContradictionType as DBContradictionType,
    ReviewActionType,
    VerificationLabel as DBVerificationLabel,
    VerificationStage as DBVerificationStage,
)
from clindoc.db.models import (
    Claim,
    Evidence,
    ReviewAction,
    TriagePolicy,
    VerificationResult as DBVerificationResult,
)
from clindoc.extract.contracts import (
    BBoxModel,
    Claim as ContractClaim,
    Evidence as ContractEvidence,
)
from clindoc.review.triage import (
    ClaimTriageInput,
    ClaimTriageResult,
    TriageEngine,
)
from clindoc.verify.cascade import VerificationCascade

logger = logging.getLogger(__name__)


# ─── EXCEPTIONS ───────────────────────────────────────────────────────────────

class ClaimReviewServiceError(Exception):
    """Base exception for claim review service operations."""


class ClaimNotFoundError(ClaimReviewServiceError, KeyError):
    """Raised when a specified claim does not exist in the database."""


class InvalidStateTransitionError(ClaimReviewServiceError, ValueError):
    """Raised when an action is illegal from the claim's current review_state."""


class OptimisticLockError(ClaimReviewServiceError, ValueError):
    """Raised when the expected version does not match the current claim version."""


class MissingReasonCodeError(ClaimReviewServiceError, ValueError):
    """Raised when EDIT or REJECT actions omit the required reason code."""


class AuditIntegrationError(ClaimReviewServiceError):
    """Raised when audit event persistence fails, causing review rollback."""


# ─── DATA STRUCTURES ──────────────────────────────────────────────────────────

@dataclass
class EditClaimResult:
    """Encapsulates the output of an EDIT action on a clinical claim."""

    new_claim: Claim
    previous_claim: Claim
    verification_result: DBVerificationResult
    triage_result: ClaimTriageResult
    review_action: ReviewAction


# ─── FIELD NORMALIZATION & ENUM MAPPING HELPERS ──────────────────────────────

def _normalize_field_type(ft: str) -> str:
    """Normalize relational/legacy field type strings to canonical FieldType literal."""
    mapping = {
        "medication.name": "med.name",
        "medication.dose": "med.dose",
        "medication.route": "med.route",
        "medication.frequency": "med.frequency",
        "medication.duration": "med.duration",
        "laboratory.value": "lab.value",
        "laboratory.unit": "lab.unit",
        "laboratory.flag": "lab.flag",
        "laboratory.reference_range": "lab.reference_range",
        "laboratory.test_name": "lab.test_name",
    }
    return mapping.get(ft, ft)


def _map_verification_label(label: str) -> DBVerificationLabel:
    """Map verification label string to relational DBVerificationLabel."""
    if label == "SUPPORTED":
        return DBVerificationLabel.SUPPORTED
    if label == "INSUFFICIENT_EVIDENCE":
        return DBVerificationLabel.INSUFFICIENT_EVIDENCE
    try:
        return DBVerificationLabel(label)
    except ValueError:
        return DBVerificationLabel.REFUTED


def _map_verification_stage(stage_letter: str) -> DBVerificationStage:
    """Map stage letter to relational DBVerificationStage."""
    mapping = {
        "A": DBVerificationStage.STAGE_A,
        "B": DBVerificationStage.STAGE_B,
        "C": DBVerificationStage.STAGE_C,
    }
    return mapping.get(stage_letter, DBVerificationStage.STAGE_A)


def _map_contradiction_type(c_type: Optional[str]) -> Optional[DBContradictionType]:
    """Map cascade contradiction type to relational DBContradictionType."""
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


# ─── REVIEW SERVICE ──────────────────────────────────────────────────────────

class ClaimReviewService:
    """Service orchestrating claim review lifecycle actions, inline re-verification, and audit."""

    def __init__(
        self,
        audit_service: Optional[AuditService] = None,
        cascade: Optional[VerificationCascade] = None,
        triage_engine: Optional[TriageEngine] = None,
    ) -> None:
        self.audit_service = audit_service or AuditService()
        self.cascade = cascade or VerificationCascade()
        self.triage_engine = triage_engine

    def _resolve_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        expected_version: Optional[int] = None,
    ) -> Claim:
        """Resolve a Claim by primary key row ID or stable clinical entity UUID."""
        claim_uuid = uuid.UUID(str(claim_id)) if not isinstance(claim_id, uuid.UUID) else claim_id

        # 1. Direct row lookup by primary key
        claim = session.get(Claim, claim_uuid)
        if claim is None:
            # 2. Lookup by stable claim_id and version
            q = session.query(Claim).filter(Claim.claim_id == claim_uuid)
            if expected_version is not None:
                claim = q.filter(Claim.version == expected_version).first()
            else:
                claim = q.order_by(Claim.version.desc()).first()

        if claim is None:
            raise ClaimNotFoundError(f"Claim '{claim_uuid}' not found.")
        return claim

    def _check_optimistic_lock(self, claim: Claim, expected_version: int) -> None:
        """Validate optimistic locking against concurrent edits."""
        if claim.version != expected_version:
            raise OptimisticLockError(
                f"Optimistic lock conflict for claim '{claim.claim_id}': "
                f"expected version {expected_version}, but found version {claim.version}."
            )

    def _record_audit_event(
        self,
        session: Session,
        *,
        actor_id: uuid.UUID,
        target_claim: Claim,
        action: str,
        previous_state: str,
        new_state: str,
        reason_code: Optional[str] = None,
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append an audit event via AuditService ensuring strictly PHI-free metadata."""
        audit_metadata: dict[str, Any] = {
            "action": action,
            "previous_state": str(previous_state),
            "new_state": str(new_state),
            "reason_code": reason_code,
            "claim_version": target_claim.version,
            "stable_claim_id": str(target_claim.claim_id),
            "document_id": str(target_claim.document_id),
            "field_type": target_claim.field_type,
            "origin": target_claim.origin.value if hasattr(target_claim.origin, "value") else str(target_claim.origin),
        }
        if target_claim.supersedes_claim_id:
            audit_metadata["supersedes_claim_id"] = str(target_claim.supersedes_claim_id)

        if extra_metadata:
            for k, v in extra_metadata.items():
                if k not in audit_metadata:
                    audit_metadata[k] = v

        # Validate strictly PHI-free before recording
        validate_metadata_phi_free(audit_metadata)

        try:
            self.audit_service.record_event(
                session=session,
                actor_id=str(actor_id),
                event_type="CLAIM_REVIEW_ACTION",
                entity_type="claim",
                entity_id=str(target_claim.id),
                action=action,
                metadata_json=audit_metadata,
            )
        except Exception as exc:
            logger.error("AuditService recording failed for claim %s: %s", target_claim.id, exc)
            raise AuditIntegrationError(f"Audit event recording failed: {exc}") from exc

    def accept_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        expected_version: int,
        reason_code: Optional[str] = None,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> Claim:
        """Transition a claim from PENDING to ACCEPTED."""
        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            if current_state != ClaimReviewState.PENDING.value:
                raise InvalidStateTransitionError(
                    f"Cannot ACCEPT claim from '{current_state}'. Allowed origin state is PENDING."
                )

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.ACCEPTED
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.ACCEPTED.value
            after_payload["accepted_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.ACCEPT,
                previous_state=current_state,
                new_state=ClaimReviewState.ACCEPTED.value,
                reason_code=reason_code,
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.ACCEPT.value,
                previous_state=current_state,
                new_state=ClaimReviewState.ACCEPTED.value,
                reason_code=reason_code,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def reject_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        reason_code: str,
        expected_version: int,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> Claim:
        """Transition a claim from PENDING to REJECTED. Requires reason_code."""
        if not reason_code or not str(reason_code).strip():
            raise MissingReasonCodeError("Reason code is required for REJECT action.")

        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            if current_state != ClaimReviewState.PENDING.value:
                raise InvalidStateTransitionError(
                    f"Cannot REJECT claim from '{current_state}'. Allowed origin state is PENDING."
                )

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.REJECTED
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.REJECTED.value
            after_payload["rejection_reason"] = reason_code.strip()
            after_payload["rejected_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.REJECT,
                previous_state=current_state,
                new_state=ClaimReviewState.REJECTED.value,
                reason_code=reason_code.strip(),
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.REJECT.value,
                previous_state=current_state,
                new_state=ClaimReviewState.REJECTED.value,
                reason_code=reason_code.strip(),
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def escalate_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        expected_version: int,
        reason_code: Optional[str] = None,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> Claim:
        """Transition a claim from PENDING to ESCALATED."""
        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            if current_state != ClaimReviewState.PENDING.value:
                raise InvalidStateTransitionError(
                    f"Cannot ESCALATE claim from '{current_state}'. Allowed origin state is PENDING."
                )

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.ESCALATED
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.ESCALATED.value
            after_payload["escalated_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.ESCALATE,
                previous_state=current_state,
                new_state=ClaimReviewState.ESCALATED.value,
                reason_code=reason_code,
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.ESCALATE.value,
                previous_state=current_state,
                new_state=ClaimReviewState.ESCALATED.value,
                reason_code=reason_code,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def reopen_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        expected_version: int,
        reason_code: Optional[str] = None,
        notes: Optional[str] = None,
        commit: bool = True,
    ) -> Claim:
        """Reopen a reviewed claim back to PENDING.
        
        Allowed origin states: ACCEPTED, REJECTED, ESCALATED.
        Rejected origin states: PENDING (already open), EDITED (superseded).
        """
        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(claim, expected_version)

            current_state = claim.review_state.value if hasattr(claim.review_state, "value") else str(claim.review_state)
            reopenable_states = {
                ClaimReviewState.ACCEPTED.value,
                ClaimReviewState.REJECTED.value,
                ClaimReviewState.ESCALATED.value,
            }
            if current_state not in reopenable_states:
                raise InvalidStateTransitionError(
                    f"Cannot REOPEN claim from '{current_state}'. Allowed states are ACCEPTED, REJECTED, ESCALATED."
                )

            before_payload = copy.deepcopy(claim.payload) if claim.payload else {}
            claim.review_state = ClaimReviewState.PENDING
            after_payload = dict(before_payload)
            after_payload["review_state"] = ClaimReviewState.PENDING.value
            after_payload["reopened_by"] = str(actor_uuid)
            claim.payload = after_payload

            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.REOPEN,
                previous_state=current_state,
                new_state=ClaimReviewState.PENDING.value,
                reason_code=reason_code,
                notes=notes,
                before_payload=before_payload,
                after_payload=after_payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=claim,
                action=ReviewActionType.REOPEN.value,
                previous_state=current_state,
                new_state=ClaimReviewState.PENDING.value,
                reason_code=reason_code,
            )

            if commit:
                session.commit()
            return claim

        except Exception:
            session.rollback()
            raise

    def edit_claim(
        self,
        session: Session,
        claim_id: Union[uuid.UUID, str],
        actor_id: Union[uuid.UUID, str],
        new_value: str,
        reason_code: str,
        expected_version: int,
        new_normalized_code: Optional[str] = None,
        new_code_system: Optional[str] = None,
        notes: Optional[str] = None,
        page_text: Optional[str] = None,
        commit: bool = True,
    ) -> EditClaimResult:
        """Edit a clinical claim creating a new immutable version with immediate re-verification.

        Guarantees:
          1. Immutable claim history (PD-010): Original claim values/provenance remain untouched.
          2. Optimistic locking: Validates expected_version.
          3. Mandatory reason code: Raises MissingReasonCodeError if omitted.
          4. Origin tracking: new_claim.origin = ClaimOrigin.REVIEWER, created_by = actor_id.
          5. Predecessor linkage: new_claim.supersedes_claim_id = previous_claim.id.
          6. Inline re-verification: Executes cascade against linked evidence without creating new architecture.
          7. Triage re-evaluation: Re-runs Mode 0 triage; new claim remains PENDING.
          8. Fail-closed atomic audit: Rolls back everything if audit persistence fails.
        """
        if not new_value or not str(new_value).strip():
            raise ValueError("Claim value must not be empty.")
        if not reason_code or not str(reason_code).strip():
            raise MissingReasonCodeError("Reason code is required for EDIT action.")

        actor_uuid = uuid.UUID(str(actor_id)) if not isinstance(actor_id, uuid.UUID) else actor_id

        try:
            previous_claim = self._resolve_claim(session, claim_id, expected_version)
            self._check_optimistic_lock(previous_claim, expected_version)

            current_state = (
                previous_claim.review_state.value
                if hasattr(previous_claim.review_state, "value")
                else str(previous_claim.review_state)
            )
            if current_state != ClaimReviewState.PENDING.value:
                raise InvalidStateTransitionError(
                    f"Cannot EDIT claim from '{current_state}'. Allowed origin state is PENDING."
                )

            # 1. Update previous claim's review_state to EDITED (preserving its clinical data immutably)
            previous_claim.review_state = ClaimReviewState.EDITED

            # 2. Assemble new claim version
            new_version = previous_claim.version + 1
            canonical_field_type = _normalize_field_type(previous_claim.field_type)
            norm_code = new_normalized_code if new_normalized_code is not None else previous_claim.normalized_code
            code_sys = new_code_system if new_code_system is not None else previous_claim.code_system

            new_payload = copy.deepcopy(previous_claim.payload) if previous_claim.payload else {}
            new_payload.update({
                "claim_id": str(previous_claim.claim_id),
                "document_id": str(previous_claim.document_id),
                "group_id": previous_claim.group_id,
                "field_type": canonical_field_type,
                "value": new_value.strip(),
                "normalized_code": norm_code,
                "code_system": code_sys,
                "version": new_version,
                "origin": ClaimOrigin.REVIEWER.value,
                "review_state": ClaimReviewState.PENDING.value,
                "supersedes_claim_id": str(previous_claim.id),
                "edited_by": str(actor_uuid),
                "edit_reason_code": reason_code.strip(),
            })

            new_claim = Claim(
                id=uuid.uuid4(),
                claim_id=previous_claim.claim_id,
                document_id=previous_claim.document_id,
                group_id=previous_claim.group_id,
                field_type=canonical_field_type,
                value=new_value.strip(),
                normalized_code=norm_code,
                code_system=code_sys,
                version=new_version,
                supersedes_claim_id=previous_claim.id,
                origin=ClaimOrigin.REVIEWER,
                review_state=ClaimReviewState.PENDING,
                created_by=actor_uuid,
                created_at=datetime.now(timezone.utc),
                evidence_id=previous_claim.evidence_id,
                model_run_id=None,
                payload=new_payload,
            )
            session.add(new_claim)
            session.flush()

            # 3. Resolve Grounding Evidence for Inline Re-Verification
            db_evidence: Optional[Evidence] = None
            contract_evidence: Optional[ContractEvidence] = None
            if new_claim.evidence_id:
                db_evidence = session.get(Evidence, new_claim.evidence_id)
                if db_evidence and db_evidence.evidence_text and db_evidence.evidence_text.strip():
                    bbox_data = db_evidence.bounding_box or [0.0, 0.0, 1.0, 1.0]
                    if isinstance(bbox_data, list) and len(bbox_data) == 4:
                        bbox_model = BBoxModel.from_list(bbox_data)
                    elif isinstance(bbox_data, dict) and "x0" in bbox_data:
                        bbox_model = BBoxModel(**bbox_data)
                    else:
                        bbox_model = BBoxModel(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

                    contract_evidence = ContractEvidence(
                        evidence_id=str(db_evidence.id),
                        document_id=str(db_evidence.document_id),
                        page_number=max(1, db_evidence.page_number),
                        text=db_evidence.evidence_text.strip(),
                        char_start=max(0, db_evidence.char_start),
                        char_end=max(max(0, db_evidence.char_start), db_evidence.char_end),
                        bbox=bbox_model,
                        ocr_confidence=db_evidence.ocr_confidence,
                    )

            # 4. Construct ContractClaim for Cascade
            extraction_method = (
                new_payload.get("extraction_method")
                if new_payload.get("extraction_method") in ("deterministic", "gliner", "llm_narrative")
                else "deterministic"
            )
            contract_claim = ContractClaim(
                claim_id=str(new_claim.claim_id),
                document_id=str(new_claim.document_id),
                group_id=new_claim.group_id or "default",
                field_type=canonical_field_type,  # type: ignore[arg-type]
                value=new_claim.value,
                evidence_id=str(new_claim.evidence_id) if new_claim.evidence_id else "missing",
                extraction_method=extraction_method,
                extraction_confidence=float(new_payload.get("extraction_confidence", 1.0)),
                region_id=str(new_payload.get("region_id", new_claim.group_id or "default")),
                normalized_code=new_claim.normalized_code,
                code_system=new_claim.code_system,  # type: ignore[arg-type]
            )

            # 5. Inline Re-Verification via VerificationCascade (Stages A/B)
            cascade_res = self.cascade.verify_claim(
                claim=contract_claim,
                evidence=contract_evidence,
                page_text=page_text,
            )

            db_vr = DBVerificationResult(
                id=uuid.uuid4(),
                claim_id=new_claim.id,
                label=_map_verification_label(cascade_res.label),
                calibrated_confidence=float(cascade_res.confidence),
                verification_stage=_map_verification_stage(cascade_res.stage_reached),
                escalated=bool(cascade_res.escalated),
                is_fallback=False,
                provider_kind="cascade_reverification",
                model_run_id=None,
                contradiction_type=_map_contradiction_type(cascade_res.contradiction_type),
                rationale=cascade_res.rationale,
                stage_a_details={"label": cascade_res.stage_a_label} if cascade_res.stage_a_label else None,
                stage_b_details={"label": cascade_res.stage_b_label, "raw_confidence": cascade_res.stage_b_raw_confidence} if cascade_res.stage_b_label else None,
                stage_c_details={"rationale": cascade_res.rationale} if cascade_res.rationale else None,
                payload=cascade_res.model_dump(mode="json"),
            )
            session.add(db_vr)
            session.flush()

            # 6. Re-evaluate Triage Policy (Phase 8.5 Step 1 Engine)
            triage_input = ClaimTriageInput.from_orm(
                claim=new_claim,
                verification_result=db_vr,
                evidence=db_evidence,
                flags={"has_injection_flag": False, "has_identity_mismatch": False},
            )

            if self.triage_engine is not None:
                engine = self.triage_engine
            else:
                active_policy = session.query(TriagePolicy).filter(TriagePolicy.is_active.is_(True)).first()
                engine = TriageEngine(policy=active_policy)

            triage_result = engine.classify_claim(triage_input)

            # 7. Persist ReviewAction (linking to the newly created version)
            action = ReviewAction(
                id=uuid.uuid4(),
                claim_id=new_claim.id,
                actor_id=actor_uuid,
                action_type=ReviewActionType.EDIT,
                previous_state=current_state,
                new_state=ClaimReviewState.EDITED.value,
                reason_code=reason_code.strip(),
                notes=notes,
                before_payload=previous_claim.payload,
                after_payload=new_claim.payload,
                created_at=datetime.now(timezone.utc),
            )
            session.add(action)
            session.flush()

            # 8. Cryptographic Hash-Chained Audit Logging (Atomic)
            audit_metadata = {
                "previous_claim_id": str(previous_claim.id),
                "new_claim_id": str(new_claim.id),
                "verification_verdict": db_vr.label.value if hasattr(db_vr.label, "value") else str(db_vr.label),
                "verification_stage": db_vr.verification_stage.value if hasattr(db_vr.verification_stage, "value") else str(db_vr.verification_stage),
                "triage_category": triage_result.category.value if hasattr(triage_result.category, "value") else str(triage_result.category),
                "triage_risk_tier": triage_result.risk_tier.value if hasattr(triage_result.risk_tier, "value") else str(triage_result.risk_tier),
                "triage_priority_rank": triage_result.priority_rank,
            }
            self._record_audit_event(
                session,
                actor_id=actor_uuid,
                target_claim=new_claim,
                action=ReviewActionType.EDIT.value,
                previous_state=current_state,
                new_state=ClaimReviewState.EDITED.value,
                reason_code=reason_code.strip(),
                extra_metadata=audit_metadata,
            )

            if commit:
                session.commit()

            return EditClaimResult(
                new_claim=new_claim,
                previous_claim=previous_claim,
                verification_result=db_vr,
                triage_result=triage_result,
                review_action=action,
            )

        except Exception:
            session.rollback()
            raise
