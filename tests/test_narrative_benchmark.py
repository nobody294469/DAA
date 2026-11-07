"""Dedicated validation test suite for Phase 1 Task 3: Narrative Benchmark Construction.

Tests all 18 validation requirements from task specification:
  1. exactly 20 narrative documents
  2. exactly 15 CV / 5 OOD
  3. all document IDs unique
  4. all narrative documents contain genuine prose
  5. required HPI / Hospital Course narrative content exists
  6. embedded lab/med claims map to ground truth
  7. every evidence span is valid
  8. char_start/char_end bounds are valid
  9. evidence slicing reproduces the expected text
 10. group_id document-boundary safety
 11. contradiction_type validity
 12. no conflicting identical claim/evidence pairs
 13. CV/OOD document disjointness
 14. template split integrity
 15. DR-3 context-window compliance
 16. schema validation
 17. existing Task 2 dataset remains unchanged outside intended narrative integration
 18. final benchmark metadata counts are internally consistent
"""
from collections import defaultdict
import json
from pathlib import Path
import pytest

from clindoc.synth.narrative_schema import NarrativeDocumentGroundTruth
from clindoc.m2.schema import (
    M2Example, M2DatasetMetadata, VerificationLabel,
    OOD_NARRATIVE_TEMPLATES,
)
from clindoc.m2.context import reconstruct_canonical_page_text, extract_context_window
from clindoc.m2.dataset import assert_class_balance


GT_DIR = Path("data/narrative_ground_truth")
OCR_DIR = Path("data/narrative_ocr_cache")
PDF_DIR = Path("data/narrative_rendered")
CANONICAL_DATA_DIR = Path("data/m2_verification")
PRE_TASK3_BACKUP_DIR = Path("data/m2_verification_pre_task3_backup")


@pytest.fixture(scope="module")
def narrative_ground_truth_docs() -> list[NarrativeDocumentGroundTruth]:
    """Load all 20 narrative ground truth documents."""
    gt_files = sorted(GT_DIR.glob("NARRATIVE_*.json"))
    assert len(gt_files) == 20, f"Expected 20 ground-truth files, found {len(gt_files)}"
    docs = []
    for f in gt_files:
        with open(f, "r", encoding="utf-8") as fp:
            docs.append(NarrativeDocumentGroundTruth.model_validate(json.load(fp)))
    return docs


@pytest.fixture(scope="module")
def canonical_examples() -> list[M2Example]:
    """Load all 9,168 integrated canonical verification examples."""
    all_ex_path = CANONICAL_DATA_DIR / "all_examples.jsonl"
    assert all_ex_path.exists()
    examples = []
    with open(all_ex_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                examples.append(M2Example.model_validate(json.loads(line)))
    return examples


# ─── 1. Exactly 20 Narrative Documents ────────────────────────────────────────
def test_1_narrative_document_count(narrative_ground_truth_docs):
    assert len(narrative_ground_truth_docs) == 20
    # Also check PDF files and OCR cache files
    pdf_files = list(PDF_DIR.glob("NARRATIVE_*.pdf"))
    assert len(pdf_files) == 20, f"Expected 20 PDF files, found {len(pdf_files)}"
    ocr_files = list(OCR_DIR.glob("NARRATIVE_*.json"))
    assert len(ocr_files) == 20, f"Expected 20 OCR cache files, found {len(ocr_files)}"


# ─── 2. Exactly 15 CV / 5 OOD ─────────────────────────────────────────────────
def test_2_cv_ood_split_counts(narrative_ground_truth_docs):
    cv_docs = [d for d in narrative_ground_truth_docs if d.split == "CV"]
    ood_docs = [d for d in narrative_ground_truth_docs if d.split == "OOD"]
    assert len(cv_docs) == 15, f"Expected 15 CV documents, got {len(cv_docs)}"
    assert len(ood_docs) == 5, f"Expected 5 OOD documents, got {len(ood_docs)}"


# ─── 3. All Document IDs Unique & Correct Namespace ───────────────────────────
def test_3_unique_document_ids(narrative_ground_truth_docs):
    doc_ids = [d.doc_id for d in narrative_ground_truth_docs]
    assert len(doc_ids) == len(set(doc_ids))
    for i, doc_id in enumerate(sorted(doc_ids), start=1):
        expected_id = f"NARRATIVE_{i:04d}"
        assert doc_id == expected_id, f"Document ID mismatch: {doc_id} != {expected_id}"


# ─── 4. Genuine Prose (Not Tabular) ───────────────────────────────────────────
def test_4_genuine_narrative_prose(narrative_ground_truth_docs):
    for doc in narrative_ground_truth_docs:
        assert len(doc.sections) >= 3, f"Document {doc.doc_id} must have multiple narrative sections"
        total_prose_chars = sum(len(s.text) for s in doc.sections)
        assert total_prose_chars >= 800, f"Document {doc.doc_id} prose too brief ({total_prose_chars} chars)"
        for s in doc.sections:
            # Must contain complete sentences with periods
            assert "." in s.text, f"Section {s.title} in {doc.doc_id} has no periods (not prose)"
            assert len(s.text.split()) >= 10, f"Section {s.title} in {doc.doc_id} is too short to be prose"


# ─── 5. Required HPI / Hospital Course Content Exists ─────────────────────────
def test_5_required_hpi_and_hospital_course_sections(narrative_ground_truth_docs):
    for doc in narrative_ground_truth_docs:
        section_titles = [s.title.upper() for s in doc.sections]
        has_hpi = any("HISTORY" in t or "PRESENT ILLNESS" in t or "CHRONOLOGICAL" in t or "INTERVAL" in t for t in section_titles)
        has_course = any("COURSE" in t or "PROGRESSION" in t for t in section_titles)
        assert has_hpi, f"Document {doc.doc_id} missing HPI section (titles: {section_titles})"
        assert has_course, f"Document {doc.doc_id} missing Hospital Course section (titles: {section_titles})"


# ─── 6. Embedded Lab/Med Claims Map to Ground Truth ───────────────────────────
def test_6_embedded_claims_map_to_entities(narrative_ground_truth_docs):
    for doc in narrative_ground_truth_docs:
        assert len(doc.lab_entities) >= 3, f"Document {doc.doc_id} has < 3 lab entities"
        assert len(doc.med_entities) >= 2, f"Document {doc.doc_id} has < 2 med entities"
        assert len(doc.claims) >= 15, f"Document {doc.doc_id} has < 15 atomic claims"

        lab_group_ids = {l.group_id for l in doc.lab_entities}
        med_group_ids = {m.group_id for m in doc.med_entities}
        for c in doc.claims:
            assert c.group_id in (lab_group_ids | med_group_ids), (
                f"Claim {c.claim_id} group_id {c.group_id} not found in doc entities"
            )


# ─── 7. Every Evidence Span is Valid (Non-Empty) ──────────────────────────────
def test_7_valid_evidence_spans(narrative_ground_truth_docs):
    for doc in narrative_ground_truth_docs:
        for c in doc.claims:
            assert c.evidence_text and c.evidence_text.strip(), (
                f"Empty evidence text in claim {c.claim_id} of {doc.doc_id}"
            )
            assert c.evidence_id, f"Missing evidence_id in claim {c.claim_id}"


# ─── 8. char_start / char_end Bounds are Valid ────────────────────────────────
def test_8_offset_bounds_valid(narrative_ground_truth_docs):
    for doc in narrative_ground_truth_docs:
        ocr_path = OCR_DIR / f"{doc.doc_id}.json"
        with open(ocr_path, "r", encoding="utf-8") as f:
            ocr_data = json.load(f)
        page_text, _ = reconstruct_canonical_page_text(ocr_data.get("lines", []), is_tabular=False, page_number=1)

        for c in doc.claims:
            assert 0 <= c.char_start < c.char_end <= len(page_text), (
                f"Invalid bounds [{c.char_start}, {c.char_end}] for text len {len(page_text)} in {doc.doc_id}"
            )


# ─── 9. Evidence Slicing Reproduces Expected Text ─────────────────────────────
def test_9_evidence_slicing_exact_match(narrative_ground_truth_docs):
    for doc in narrative_ground_truth_docs:
        ocr_path = OCR_DIR / f"{doc.doc_id}.json"
        with open(ocr_path, "r", encoding="utf-8") as f:
            ocr_data = json.load(f)
        page_text, _ = reconstruct_canonical_page_text(ocr_data.get("lines", []), is_tabular=False, page_number=1)

        for c in doc.claims:
            sliced = page_text[c.char_start:c.char_end]
            assert sliced == c.evidence_text, (
                f"Evidence slicing mismatch in {doc.doc_id}:\n"
                f"  Expected: {c.evidence_text!r}\n"
                f"  Got:      {sliced!r}"
            )


# ─── 10. group_id Document-Boundary Safety ────────────────────────────────────
def test_10_group_id_document_boundary_safety(narrative_ground_truth_docs, canonical_examples):
    doc_to_groups = defaultdict(set)
    group_to_docs = defaultdict(set)

    for doc in narrative_ground_truth_docs:
        for c in doc.claims:
            doc_to_groups[doc.doc_id].add(c.group_id)
            group_to_docs[c.group_id].add(doc.doc_id)

    for ex in canonical_examples:
        if ex.group_id:
            group_to_docs[ex.group_id].add(ex.source_doc_id)

    # Assert no group crosses documents
    for gid, docs in group_to_docs.items():
        assert len(docs) == 1, f"LEAKAGE: group_id {gid} spans multiple documents: {docs}"


# ─── 11. Contradiction Type Validity ──────────────────────────────────────────
def test_11_contradiction_type_validity(canonical_examples):
    valid_types = {"value_mismatch", "dose_mismatch", "wrong_entity", "temporal_mismatch", None}
    narrative_examples = [e for e in canonical_examples if e.source_doc_id.startswith("NARRATIVE_")]
    assert len(narrative_examples) == 780

    for ex in narrative_examples:
        assert ex.contradiction_type in valid_types, (
            f"Invalid contradiction_type {ex.contradiction_type!r} in {ex.example_id}"
        )
        if ex.label == VerificationLabel.DISCREPANCY:
            assert ex.contradiction_type is not None, (
                f"DISCREPANCY example {ex.example_id} has null contradiction_type"
            )
        else:
            assert ex.contradiction_type is None, (
                f"Non-discrepancy example {ex.example_id} has non-null contradiction_type {ex.contradiction_type}"
            )


# ─── 12. No Conflicting Identical (claim, evidence) Pairs ─────────────────────
def test_12_no_conflicting_claim_evidence_pairs(canonical_examples):
    pair_labels: dict[tuple[str, str], VerificationLabel] = {}
    narrative_examples = [e for e in canonical_examples if e.source_doc_id.startswith("NARRATIVE_")]

    for ex in narrative_examples:
        key = (ex.claim.strip(), ex.evidence.strip())
        if key in pair_labels:
            assert pair_labels[key] == ex.label, (
                f"CONFLICT: Identical (claim, evidence) pair has conflicting labels:\n"
                f"  Claim:    {ex.claim}\n"
                f"  Existing: {pair_labels[key]}\n"
                f"  New:      {ex.label}"
            )
        pair_labels[key] = ex.label


# ─── 13. CV / OOD Document Disjointness ───────────────────────────────────────
def test_13_cv_ood_disjointness(canonical_examples):
    with open(CANONICAL_DATA_DIR / "split_manifest.json", "r", encoding="utf-8") as f:
        manifest = json.load(f)

    cv_docs = set(manifest["cv_doc_ids"])
    ood_docs = set(manifest["ood_doc_ids"])
    assert cv_docs.isdisjoint(ood_docs), "CV and OOD document sets are not disjoint!"
    assert len(cv_docs) == 86, f"Expected 86 CV documents, got {len(cv_docs)}"
    assert len(ood_docs) == 34, f"Expected 34 OOD documents, got {len(ood_docs)}"


# ─── 14. Template Split Integrity ─────────────────────────────────────────────
def test_14_template_split_integrity(narrative_ground_truth_docs):
    cv_templates = set()
    ood_templates = set()

    for doc in narrative_ground_truth_docs:
        if doc.split == "CV":
            cv_templates.add(doc.template_id)
        else:
            ood_templates.add(doc.template_id)

    assert cv_templates.isdisjoint(ood_templates), (
        f"Template leakage across CV/OOD: {cv_templates & ood_templates}"
    )
    for t in ood_templates:
        assert t in OOD_NARRATIVE_TEMPLATES, f"OOD template {t} not in OOD_NARRATIVE_TEMPLATES"


# ─── 15. DR-3 Context Window Compliance ───────────────────────────────────────
def test_15_context_window_compliance(canonical_examples):
    narrative_examples = [e for e in canonical_examples if e.source_doc_id.startswith("NARRATIVE_")]
    for ex in narrative_examples:
        assert 0 < len(ex.evidence) <= 500, (
            f"Context window length {len(ex.evidence)} exceeds DR-3 hard cap of 500 chars in {ex.example_id}"
        )


# ─── 16. Schema Validation ────────────────────────────────────────────────────
def test_16_schema_validation(narrative_ground_truth_docs, canonical_examples):
    narrative_examples = [e for e in canonical_examples if e.source_doc_id.startswith("NARRATIVE_")]
    tier_1_fields = {"lab.value", "lab.flag", "med.name", "med.dose", "med.route", "med.frequency"}

    for ex in narrative_examples:
        # Pydantic validation
        M2Example.model_validate(ex.model_dump())
        # Check field type is strictly Tier 1
        assert ex.field_type in tier_1_fields, f"Learned example for non-Tier-1 field {ex.field_type}"
        assert ex.ocr_confidence == 1.0, f"Born digital OCR confidence must be 1.0, got {ex.ocr_confidence}"
        assert ex.severity == "clean"

    for doc in narrative_ground_truth_docs:
        NarrativeDocumentGroundTruth.model_validate(doc.model_dump())


# ─── 17. Existing Task 2 Dataset Remained Byte-for-Byte Unchanged ─────────────
@pytest.mark.skipif(not Path("data/m2_verification_pre_task3_backup/all_examples.jsonl").exists(), reason="needs the local pre-Task-3 backup (not in the repo)")
def test_17_task2_dataset_preserved_byte_for_byte():
    backup_file = PRE_TASK3_BACKUP_DIR / "all_examples.jsonl"
    current_file = CANONICAL_DATA_DIR / "all_examples.jsonl"
    assert backup_file.exists(), "Pre-Task 3 backup file missing"
    assert current_file.exists(), "Canonical all_examples.jsonl missing"

    with open(backup_file, "r", encoding="utf-8") as f_old, open(current_file, "r", encoding="utf-8") as f_new:
        old_lines = f_old.readlines()
        new_lines = f_new.readlines()

    assert len(old_lines) == 8388
    assert len(new_lines) == 9168

    for idx, (l_old, l_new) in enumerate(zip(old_lines, new_lines)):
        assert l_old == l_new, f"Byte-for-byte mismatch at line {idx} between Task 2 and integrated dataset!"


# ─── 18. Final Benchmark Metadata Counts Internally Consistent ────────────────
def test_18_metadata_counts_internally_consistent():
    with open(CANONICAL_DATA_DIR / "metadata.json", "r", encoding="utf-8") as f:
        meta_dict = json.load(f)
    with open(CANONICAL_DATA_DIR / "generation_manifest.json", "r", encoding="utf-8") as f:
        gen_manifest = json.load(f)

    meta = M2DatasetMetadata.model_validate(meta_dict)
    assert meta.total_examples == 9168
    assert len(meta.cv_doc_ids) == 86
    assert len(meta.ood_doc_ids) == 34
    assert gen_manifest["total_documents"] == 120
    assert gen_manifest["cv_documents"] == 86
    assert gen_manifest["ood_documents"] == 34
    assert gen_manifest["total_examples"] == 9168

    # Check sum of label counts matches total_examples
    assert sum(meta.label_counts.values()) == meta.total_examples

    # Check class balance assertion passes on metadata
    assert_class_balance(meta, Path("configs/class_balance_floor.yaml"))
