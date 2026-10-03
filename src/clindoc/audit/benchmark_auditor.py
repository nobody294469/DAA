"""Benchmark Evidence and Provenance Integrity Auditor.

Performs a read-only, non-destructive audit of the canonical M2 verification dataset
(data/m2_verification/all_examples.jsonl) against authoritative OCR text caches.

Authority:
  - PROJECT_SPEC.md §7.1, §7.8, §8, §9, §10, §20.1
  - DEC-003: Document context window bounding
  - DEC-015: Group ID entity linking
  - DEC-017: Canonical field vocabulary
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Optional, Sequence

from clindoc.m2.context import reconstruct_canonical_page_text
from clindoc.m2.schema import M2Example
from clindoc.verify.stage_a import DeterministicVerifier


class ProvenanceStatus(str, Enum):
    """Integrity status of an example's provenance and character offsets."""
    VALID_ATOMIC = "VALID_ATOMIC"              # Offsets point to atomic mention within evidence
    VALID_VERBATIM = "VALID_VERBATIM"          # Offsets point exactly to evidence text
    CORRUPTED_DEFAULT = "CORRUPTED_DEFAULT"    # char_start=0, char_end=100 pointing to header
    OUT_OF_BOUNDS = "OUT_OF_BOUNDS"            # Offsets exceed document length or negative
    DEGRADED_OFFSET_MISMATCH = "DEGRADED_OFFSET_MISMATCH"  # Degraded OCR offsets do not match clean cache
    MISSING_DOC = "MISSING_DOC"                # Referenced document not found in caches


class SemanticCategory(str, Enum):
    """Semantic relationship between claim and evidence span."""
    SUPPORTS_CLAIM = "SUPPORTS_CLAIM"
    CONTRADICTS_CLAIM = "CONTRADICTS_CLAIM"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WRONG_ENTITY = "WRONG_ENTITY"
    UNRELATED_EVIDENCE = "UNRELATED_EVIDENCE"
    TRUNCATED_OR_INCOMPLETE_EVIDENCE = "TRUNCATED_OR_INCOMPLETE_EVIDENCE"
    INVALID_PROVENANCE = "INVALID_PROVENANCE"
    OTHER = "OTHER"


class IntegrityLevel(str, Enum):
    """Overall classification of an example's benchmark readiness."""
    CLEAN = "CLEAN"
    SUSPICIOUS = "SUSPICIOUS"
    DEFINITELY_INVALID = "DEFINITELY_INVALID"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass
class ExampleAuditRecord:
    """Audit result for a single M2Example."""
    example_id: str
    source_doc_id: str
    field_type: str
    claim: str
    ground_truth_label: str
    severity: str
    generation_method: str
    char_start: Optional[int]
    char_end: Optional[int]
    page_number: int
    provenance_status: ProvenanceStatus
    semantic_category: SemanticCategory
    integrity_level: IntegrityLevel
    is_conflict_with_gt: bool
    problem_reason: Optional[str] = None
    evidence_snippet: str = ""
    remediation_bucket: str = "safe_to_repair"  # safe_to_repair | manual_review | exclude | regenerate


class BenchmarkAuditor:
    """Read-only integrity auditor for clinical extraction verification datasets."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir or Path(".")
        self.verifier = DeterministicVerifier()
        self._doc_cache: dict[tuple[str, int], Optional[str]] = {}
        self._degraded_cache: dict[tuple[str, str], Optional[str]] = {}

    def get_clean_page_text(self, doc_id: str, page_number: int = 1) -> Optional[str]:
        """Load clean page text from primary OCR caches."""
        key = (doc_id, page_number)
        if key in self._doc_cache:
            return self._doc_cache[key]

        candidates = [
            (self.base_dir / "data" / "ocr_cache" / f"{doc_id}.json", True),
            (self.base_dir / "data" / "med_ocr_cache" / f"{doc_id}.json", False),
            (self.base_dir / "data" / "narrative_ocr_cache" / f"{doc_id}.json", False),
        ]

        for p, is_tab in candidates:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        ocr_data = json.load(f)
                    txt, _ = reconstruct_canonical_page_text(
                        ocr_data.get("lines", []),
                        is_tabular=is_tab,
                        page_number=page_number,
                    )
                    self._doc_cache[key] = txt
                    return txt
                except Exception:
                    pass

        self._doc_cache[key] = None
        return None

    def get_degraded_page_text(self, doc_id: str, severity: str) -> Optional[str]:
        """Load degraded page text from corrupt OCR caches if available."""
        if severity == "clean":
            return self.get_clean_page_text(doc_id)

        key = (doc_id, severity)
        if key in self._degraded_cache:
            return self._degraded_cache[key]

        candidates = [
            self.base_dir / "data" / "degraded" / "ocr_cache" / "corrupt_lab" / f"{doc_id}_ocr_corrupt_{severity}.json",
            self.base_dir / "data" / "degraded" / "ocr_cache" / "corrupt_med" / f"{doc_id}_ocr_corrupt_{severity}.json",
        ]

        for p in candidates:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        d = json.load(f)
                    lines = d.get("lines", [])
                    txt = "\n".join(l.get("text", "") for l in lines)
                    self._degraded_cache[key] = txt
                    return txt
                except Exception:
                    pass

        self._degraded_cache[key] = None
        return None

    def audit_example(self, ex: M2Example) -> ExampleAuditRecord:
        """Execute comprehensive read-only integrity audit on a single example."""
        clean_pt = self.get_clean_page_text(ex.source_doc_id, ex.page_number or 1)
        degraded_pt = self.get_degraded_page_text(ex.source_doc_id, ex.severity)
        
        # Reference page text: use clean document as authoritative canonical reference
        ref_pt = clean_pt or degraded_pt
        ev_text = ex.evidence or ""
        cs = ex.char_start
        ce = ex.char_end

        # 1. Provenance Validation
        if ref_pt is None:
            prov_status = ProvenanceStatus.MISSING_DOC
            problem = f"Document {ex.source_doc_id} not found in any OCR cache"
        elif cs is None and ce is None:
            # Document-level absence / unobserved negative control
            prov_status = ProvenanceStatus.VALID_VERBATIM
            problem = None
        elif cs is None or ce is None or cs < 0 or ce > len(ref_pt) or cs >= ce:
            prov_status = ProvenanceStatus.OUT_OF_BOUNDS
            problem = f"Offsets [{cs}, {ce}] exceed document length {len(ref_pt) if ref_pt else 0}"
        elif cs == 0 and ce == 100:
            prov_status = ProvenanceStatus.CORRUPTED_DEFAULT
            problem = "Corrupted default fallback offsets [0, 100] pointing to document header"
        else:
            pt_slice = ref_pt[cs:ce]
            if pt_slice == ev_text:
                prov_status = ProvenanceStatus.VALID_VERBATIM
                problem = None
            elif pt_slice in ev_text:
                prov_status = ProvenanceStatus.VALID_ATOMIC
                problem = None
            else:
                # Check if it matches degraded text
                if degraded_pt and 0 <= cs < ce <= len(degraded_pt) and degraded_pt[cs:ce] in ev_text:
                    prov_status = ProvenanceStatus.DEGRADED_OFFSET_MISMATCH
                    problem = "Offsets valid only against corrupt OCR file, mismatch clean canonical OCR"
                else:
                    prov_status = ProvenanceStatus.DEGRADED_OFFSET_MISMATCH
                    problem = f"Slice ref_pt[{cs}:{ce}] does not match evidence text"

        # 2. Semantic Audit
        claim_stub = type("ClaimStub", (), {
            "value": ex.claim,
            "field_type": ex.field_type,
            "value_numeric": getattr(ex, "value_numeric", None),
            "unit_raw": getattr(ex, "unit_raw", None),
            "negated": False,
            "normalized_code": None,
            "code_system": None,
            "evidence_id": "aud-ev-1",
        })()
        ev_stub = type("EvStub", (), {"text": ev_text, "evidence_id": "aud-ev-1"})()

        target_entity = self.verifier._extract_claim_entity(claim_stub)
        entity_in_ev = self.verifier._is_entity_present_in_text(target_entity, ev_text) if target_entity else True
        entity_in_doc = self.verifier._is_entity_present_in_text(target_entity, clean_pt) if (target_entity and clean_pt) else False

        # Classify Semantic Category
        if prov_status == ProvenanceStatus.OUT_OF_BOUNDS:
            sem_cat = SemanticCategory.INVALID_PROVENANCE
        elif prov_status == ProvenanceStatus.CORRUPTED_DEFAULT:
            if not entity_in_ev:
                sem_cat = SemanticCategory.INVALID_PROVENANCE
            else:
                sem_cat = SemanticCategory.TRUNCATED_OR_INCOMPLETE_EVIDENCE
        elif not entity_in_ev:
            if entity_in_doc:
                # Target entity exists in document but evidence span failed to capture it
                sem_cat = SemanticCategory.UNRELATED_EVIDENCE if prov_status == ProvenanceStatus.CORRUPTED_DEFAULT else SemanticCategory.WRONG_ENTITY
            else:
                # Target entity does not exist anywhere in document
                if ex.label.value == "INSUFFICIENT_EVIDENCE":
                    sem_cat = SemanticCategory.INSUFFICIENT_EVIDENCE
                else:
                    sem_cat = SemanticCategory.WRONG_ENTITY
        else:
            # Target entity is present in evidence span: verify value
            res = self.verifier.verify(claim_stub, ev_stub)
            if res.label == "SUPPORTED":
                sem_cat = SemanticCategory.SUPPORTS_CLAIM
            elif res.label == "DISCREPANCY":
                sem_cat = SemanticCategory.CONTRADICTS_CLAIM
            elif res.label == "INSUFFICIENT_EVIDENCE":
                sem_cat = SemanticCategory.TRUNCATED_OR_INCOMPLETE_EVIDENCE
            else:
                sem_cat = SemanticCategory.SUPPORTS_CLAIM if ex.label.value == "SUPPORTED" else SemanticCategory.CONTRADICTS_CLAIM

        # 3. Ground-Truth Conflict Determination
        gt = ex.label.value
        is_conflict = False
        if gt == "SUPPORTED":
            if sem_cat != SemanticCategory.SUPPORTS_CLAIM:
                is_conflict = True
                if not problem:
                    problem = f"Ground truth is SUPPORTED but evidence evaluates to {sem_cat.value}"
        elif gt == "DISCREPANCY":
            if sem_cat not in (SemanticCategory.CONTRADICTS_CLAIM, SemanticCategory.WRONG_ENTITY):
                is_conflict = True
                if not problem:
                    problem = f"Ground truth is DISCREPANCY but evidence evaluates to {sem_cat.value}"
        elif gt == "INSUFFICIENT_EVIDENCE":
            if sem_cat not in (SemanticCategory.INSUFFICIENT_EVIDENCE, SemanticCategory.INVALID_PROVENANCE, SemanticCategory.UNRELATED_EVIDENCE):
                is_conflict = True
                if not problem:
                    problem = f"Ground truth is INSUFFICIENT_EVIDENCE but evidence evaluates to {sem_cat.value}"

        # 4. Overall Integrity Level Classification
        if prov_status in (ProvenanceStatus.OUT_OF_BOUNDS, ProvenanceStatus.CORRUPTED_DEFAULT) or (gt == "SUPPORTED" and sem_cat != SemanticCategory.SUPPORTS_CLAIM):
            integrity = IntegrityLevel.DEFINITELY_INVALID
            remediation = "regenerate" if prov_status == ProvenanceStatus.CORRUPTED_DEFAULT else "exclude"
        elif is_conflict or sem_cat in (SemanticCategory.TRUNCATED_OR_INCOMPLETE_EVIDENCE, SemanticCategory.WRONG_ENTITY, SemanticCategory.UNRELATED_EVIDENCE):
            integrity = IntegrityLevel.SUSPICIOUS
            remediation = "manual_review"
        elif prov_status == ProvenanceStatus.DEGRADED_OFFSET_MISMATCH:
            integrity = IntegrityLevel.SUSPICIOUS
            remediation = "safe_to_repair"
        else:
            integrity = IntegrityLevel.CLEAN
            remediation = "none"

        return ExampleAuditRecord(
            example_id=ex.example_id,
            source_doc_id=ex.source_doc_id,
            field_type=ex.field_type,
            claim=ex.claim,
            ground_truth_label=gt,
            severity=ex.severity,
            generation_method=ex.generation_method,
            char_start=cs,
            char_end=ce,
            page_number=ex.page_number or 1,
            provenance_status=prov_status,
            semantic_category=sem_cat,
            integrity_level=integrity,
            is_conflict_with_gt=is_conflict,
            problem_reason=problem,
            evidence_snippet=ev_text[:100],
            remediation_bucket=remediation,
        )

    def audit_dataset(self, examples: Sequence[M2Example]) -> list[ExampleAuditRecord]:
        """Audit an entire collection of M2Examples."""
        return [self.audit_example(ex) for ex in examples]
