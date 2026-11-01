"""Phase 5.2.3 Sub-Milestone 4.1: Verification Cascade Anchor Propagation Tests.

Authority: Phase 5.2.3 Sub-Milestone 4.1
Tests fail-closed topological integrity check in Stage A verification:
1. If a parent medication anchor ('med.name') fails verification, dependent attributes
   ('med.dose', 'med.route', 'med.frequency', 'med.duration') in the same medication group
   must not remain independently eligible for unreviewed export.
2. A verified parent does not automatically make an independently failed child claim valid.
3. Multiple medication groups must remain isolated.
4. Interleaved medication attributes retain their correct group associations.
5. Orphan attributes and ambiguous associations remain fail-closed.
6. Original verification results are preserved (never overwritten to manufacture a passing state).
7. Diagnostic reason code 'PARENT_ANCHOR_UNVERIFIED' is stably emitted.
8. No bypass through export: Gate 9 in FHIRExportService blocks export of invalidated children.
"""
from __future__ import annotations

import uuid
import pytest

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.schema.verification import VerificationResult
from clindoc.services.fhir_export import FHIRExportService
from clindoc.verify.stage_a import (
    DeterministicVerifier,
    REASON_PARENT_ANCHOR_UNVERIFIED,
    StageAResult,
    apply_medication_group_integrity,
)


def _make_bbox() -> BBoxModel:
    return BBoxModel(x0=10.0, y0=20.0, x1=100.0, y1=40.0)


def _make_evidence(text: str, doc_id: str = "DOC-001", page: int = 1) -> Evidence:
    return Evidence(
        evidence_id=str(uuid.uuid4()),
        document_id=doc_id,
        page_number=page,
        text=text,
        char_start=0,
        char_end=len(text),
        bbox=_make_bbox(),
    )


def _make_claim(
    field_type: str,
    value: str,
    evidence_id: str,
    group_id: str | None = None,
    value_numeric: float | None = None,
    unit_raw: str | None = None,
    doc_id: str = "DOC-001",
) -> Claim:
    return Claim(
        claim_id=str(uuid.uuid4()),
        document_id=doc_id,
        group_id=group_id,
        field_type=field_type,
        value=value,
        value_numeric=value_numeric,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id=evidence_id,
        region_id="reg-01",
    )


class TestVerificationCascadeAnchorPropagation:
    """Sub-Milestone 4.1 verification test suite."""

    @pytest.fixture
    def verifier(self) -> DeterministicVerifier:
        return DeterministicVerifier()

    def test_01_parent_fails_child_attributes_blocked(self, verifier: DeterministicVerifier):
        """When parent med.name fails verification, dependent attributes in that group are blocked."""
        ev_mismatch = _make_evidence("Patient prescribed Atenolol 25 mg daily.")
        ev_dose = _make_evidence("25 mg")
        ev_freq = _make_evidence("daily")

        gid = "group-metoprolol"
        # Parent claim is Metoprolol, but evidence states Atenolol -> med.name fails verification
        claim_name = _make_claim("med.name", "Metoprolol", ev_mismatch.evidence_id, group_id=gid)
        # Dose and frequency match evidence text perfectly in isolation
        claim_dose = _make_claim("med.dose", "25", ev_dose.evidence_id, group_id=gid, value_numeric=25.0, unit_raw="mg")
        claim_freq = _make_claim("med.frequency", "daily", ev_freq.evidence_id, group_id=gid)

        claims = [claim_name, claim_dose, claim_freq]
        ev_map = {
            ev_mismatch.evidence_id: ev_mismatch,
            ev_dose.evidence_id: ev_dose,
            ev_freq.evidence_id: ev_freq,
        }

        results = verifier.verify_claims(claims, ev_map, enforce_topological_integrity=True)

        # Parent anchor was unverified (not found verbatim in Atenolol evidence)
        parent_res = results[claim_name.claim_id]
        assert parent_res.label in (None, "DISCREPANCY", "INSUFFICIENT_EVIDENCE")

        # Child claims MUST be blocked with INSUFFICIENT_EVIDENCE and reason PARENT_ANCHOR_UNVERIFIED
        dose_res = results[claim_dose.claim_id]
        assert dose_res.label == "INSUFFICIENT_EVIDENCE"
        assert dose_res.details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED
        assert dose_res.details.get("original_label") == "SUPPORTED"
        assert dose_res.details.get("parent_anchor_claim_id") == claim_name.claim_id

        freq_res = results[claim_freq.claim_id]
        assert freq_res.label == "INSUFFICIENT_EVIDENCE"
        assert freq_res.details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED

    def test_02_parent_passes_child_passes(self, verifier: DeterministicVerifier):
        """When parent med.name passes, valid dependent attributes remain SUPPORTED."""
        ev = _make_evidence("Lisinopril 10 mg PO daily")
        gid = "group-lisinopril"

        claim_name = _make_claim("med.name", "Lisinopril", ev.evidence_id, group_id=gid)
        claim_dose = _make_claim("med.dose", "10", ev.evidence_id, group_id=gid, value_numeric=10.0, unit_raw="mg")
        claim_route = _make_claim("med.route", "oral", ev.evidence_id, group_id=gid)
        claim_freq = _make_claim("med.frequency", "daily", ev.evidence_id, group_id=gid)

        claims = [claim_name, claim_dose, claim_route, claim_freq]
        ev_map = {ev.evidence_id: ev}

        results = verifier.verify_claims(claims, ev_map, enforce_topological_integrity=True)

        assert results[claim_name.claim_id].label == "SUPPORTED"
        assert results[claim_dose.claim_id].label == "SUPPORTED"
        assert results[claim_route.claim_id].label == "SUPPORTED"
        assert results[claim_freq.claim_id].label == "SUPPORTED"

    def test_03_parent_passes_one_child_independently_fails(self, verifier: DeterministicVerifier):
        """A verified parent does NOT make an independently failing child valid."""
        ev = _make_evidence("Amoxicillin 250 mg PO TID")
        gid = "group-amoxicillin"

        claim_name = _make_claim("med.name", "Amoxicillin", ev.evidence_id, group_id=gid)
        # Discrepant dose: claim asserts 500 mg, evidence states 250 mg
        claim_dose = _make_claim("med.dose", "500", ev.evidence_id, group_id=gid, value_numeric=500.0, unit_raw="mg")
        claim_freq = _make_claim("med.frequency", "three times daily", ev.evidence_id, group_id=gid)

        claims = [claim_name, claim_dose, claim_freq]
        ev_map = {ev.evidence_id: ev}

        results = verifier.verify_claims(claims, ev_map, enforce_topological_integrity=True)

        # Parent passes
        assert results[claim_name.claim_id].label == "SUPPORTED"
        # Child dose remains DISCREPANCY with dose_mismatch
        dose_res = results[claim_dose.claim_id]
        assert dose_res.label == "DISCREPANCY"
        assert dose_res.contradiction_type == "dose_mismatch"
        # Valid child frequency remains SUPPORTED
        assert results[claim_freq.claim_id].label == "SUPPORTED"

    def test_04_interleaved_medications_isolated(self, verifier: DeterministicVerifier):
        """Two interleaved medications: failure in group 1 does NOT affect group 2."""
        ev1 = _make_evidence("Metoprolol 25 mg daily; Lisinopril 10 mg daily")
        gid1 = "group-1-metoprolol"
        gid2 = "group-2-lisinopril"

        # Group 1 has wrong drug name claim (Carvedilol instead of Metoprolol)
        claim1_name = _make_claim("med.name", "Carvedilol", ev1.evidence_id, group_id=gid1)
        claim1_dose = _make_claim("med.dose", "25", ev1.evidence_id, group_id=gid1, value_numeric=25.0, unit_raw="mg")
        claim1_freq = _make_claim("med.frequency", "daily", ev1.evidence_id, group_id=gid1)

        # Group 2 has matching drug name claim (Lisinopril)
        claim2_name = _make_claim("med.name", "Lisinopril", ev1.evidence_id, group_id=gid2)
        claim2_dose = _make_claim("med.dose", "10", ev1.evidence_id, group_id=gid2, value_numeric=10.0, unit_raw="mg")
        claim2_freq = _make_claim("med.frequency", "daily", ev1.evidence_id, group_id=gid2)

        claims = [claim1_name, claim1_dose, claim1_freq, claim2_name, claim2_dose, claim2_freq]
        ev_map = {ev1.evidence_id: ev1}

        results = verifier.verify_claims(claims, ev_map, enforce_topological_integrity=True)

        # Group 1 is invalidated by failed anchor
        assert results[claim1_name.claim_id].label in (None, "DISCREPANCY", "INSUFFICIENT_EVIDENCE")
        assert results[claim1_dose.claim_id].label == "INSUFFICIENT_EVIDENCE"
        assert results[claim1_dose.claim_id].details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED
        assert results[claim1_freq.claim_id].label == "INSUFFICIENT_EVIDENCE"
        assert results[claim1_freq.claim_id].details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED

        # Group 2 remains fully verified and unaffected
        assert results[claim2_name.claim_id].label == "SUPPORTED"
        assert results[claim2_dose.claim_id].label == "SUPPORTED"
        assert results[claim2_freq.claim_id].label == "SUPPORTED"

    def test_05_orphan_and_ambiguous_attributes_fail_closed(self, verifier: DeterministicVerifier):
        """Attributes without a valid parent anchor fail closed with PARENT_ANCHOR_UNVERIFIED."""
        ev = _make_evidence("Take 50 mg daily")
        # Orphan attribute: no group_id
        orphan_dose = _make_claim("med.dose", "50", ev.evidence_id, group_id=None, value_numeric=50.0, unit_raw="mg")
        # Orphan attribute: explicit sentinel __no_anchor__
        sentinel_dose = _make_claim("med.dose", "50", ev.evidence_id, group_id="__no_anchor__", value_numeric=50.0, unit_raw="mg")

        # Ambiguous multi-anchor group: group has two med.name claims
        ambig_gid = "group-ambig"
        ambig_name1 = _make_claim("med.name", "Aspirin", ev.evidence_id, group_id=ambig_gid)
        ambig_name2 = _make_claim("med.name", "Warfarin", ev.evidence_id, group_id=ambig_gid)
        ambig_dose = _make_claim("med.dose", "50", ev.evidence_id, group_id=ambig_gid, value_numeric=50.0, unit_raw="mg")

        claims = [orphan_dose, sentinel_dose, ambig_name1, ambig_name2, ambig_dose]
        ev_map = {ev.evidence_id: ev}

        results = verifier.verify_claims(claims, ev_map, enforce_topological_integrity=True)

        assert results[orphan_dose.claim_id].label == "INSUFFICIENT_EVIDENCE"
        assert results[orphan_dose.claim_id].details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED
        assert results[orphan_dose.claim_id].details.get("anchor_failure_detail") == "MISSING_ANCHOR"

        assert results[sentinel_dose.claim_id].label == "INSUFFICIENT_EVIDENCE"
        assert results[sentinel_dose.claim_id].details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED

        assert results[ambig_dose.claim_id].label == "INSUFFICIENT_EVIDENCE"
        assert results[ambig_dose.claim_id].details.get("reason") == REASON_PARENT_ANCHOR_UNVERIFIED
        assert results[ambig_dose.claim_id].details.get("anchor_failure_detail") == "AMBIGUOUS_MULTI_ANCHOR"

    def test_06_verification_result_pydantic_model_compatibility(self, verifier: DeterministicVerifier):
        """Topological integrity works with VerificationResult Pydantic models."""
        gid = "group-pydantic"
        claim_name = _make_claim("med.name", "DrugX", "ev-1", group_id=gid)
        claim_dose = _make_claim("med.dose", "100", "ev-2", group_id=gid, value_numeric=100.0, unit_raw="mg")

        # Create VerificationResult objects: parent is DISCREPANCY, child was SUPPORTED
        parent_ver = VerificationResult(
            claim_id=claim_name.claim_id,
            label="DISCREPANCY",
            confidence=1.0,
            stage_reached="A",
            stage_a_label="DISCREPANCY",
            contradiction_type="wrong_entity",
            evidence_id="ev-1",
        )
        child_ver = VerificationResult(
            claim_id=claim_dose.claim_id,
            label="SUPPORTED",
            confidence=1.0,
            stage_reached="A",
            stage_a_label="SUPPORTED",
            evidence_id="ev-2",
        )

        in_results = {
            claim_name.claim_id: parent_ver,
            claim_dose.claim_id: child_ver,
        }

        updated = verifier.verify_medication_group_integrity([claim_name, claim_dose], in_results)

        # Parent is unchanged
        assert updated[claim_name.claim_id].label == "DISCREPANCY"
        # Child is now INSUFFICIENT_EVIDENCE with fallback_reason = PARENT_ANCHOR_UNVERIFIED
        updated_child = updated[claim_dose.claim_id]
        assert updated_child.label == "INSUFFICIENT_EVIDENCE"
        assert updated_child.contradiction_type is None
        assert updated_child.fallback_reason == REASON_PARENT_ANCHOR_UNVERIFIED

    def test_07_no_bypass_through_export_gate9(self, verifier: DeterministicVerifier):
        """FHIRExportService Gate 9 rejects child claims invalidated by anchor failure."""
        ev = _make_evidence("Metoprolol 25 mg daily")
        gid = "group-export-test"

        # In this scenario, med.name fails verification
        claim_name = _make_claim("med.name", "Atenolol", ev.evidence_id, group_id=gid)
        claim_dose = _make_claim("med.dose", "25", ev.evidence_id, group_id=gid, value_numeric=25.0, unit_raw="mg")

        claims = [claim_name, claim_dose]
        ev_map = {ev.evidence_id: ev}

        # Run verification with topological integrity
        verified_results = verifier.verify_claims(claims, ev_map, enforce_topological_integrity=True)

        # Confirm child is INSUFFICIENT_EVIDENCE
        assert verified_results[claim_dose.claim_id].label == "INSUFFICIENT_EVIDENCE"

        # Convert to VerificationResult mapping for FHIRExportService
        ver_map = {}
        for c in claims:
            res = verified_results[c.claim_id]
            ver_map[c.claim_id] = VerificationResult(
                claim_id=c.claim_id,
                label=res.label or "INSUFFICIENT_EVIDENCE",
                confidence=res.confidence,
                stage_reached="A",
                stage_a_label=res.label,
                contradiction_type=res.contradiction_type,
                evidence_id=c.evidence_id,
                fallback_reason=res.details.get("reason"),
            )

        export_service = FHIRExportService()
        # Evaluate dose claim eligibility for export
        dose_eval = export_service.evaluate_claim_eligibility(
            claim_dose,
            ev_map[claim_dose.evidence_id],
            ver_map[claim_dose.claim_id],
            enforce_mode_0=True,
        )

        # Export must be strictly rejected
        assert dose_eval.is_eligible is False
        assert "must be SUPPORTED" in dose_eval.reason or "INSUFFICIENT_EVIDENCE" in dose_eval.reason
