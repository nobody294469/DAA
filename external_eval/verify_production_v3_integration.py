"""Production verification runner for section detection integration (DEC-020).

Authority: Candidate V3 Post-Audit Corrective Pass
Location: external_eval/verify_production_v3_integration.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

# Silence noisy external libraries (PyRuSH loguru logger, spacy/medspacy)
try:
    from loguru import logger as loguru_logger
    loguru_logger.remove()
    loguru_logger.add(sys.stderr, level="WARNING")
except ImportError:
    pass

for log_name in ["medspacy", "spacy", "clindoc"]:
    logging.getLogger(log_name).setLevel(logging.WARNING)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from clindoc.extract.contracts import Claim
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    GroundTruthClaim,
    evaluate_dataset,
)
from clindoc.extract.router import ExtractionRouter
from clindoc.extract.section_detector import get_section_detector
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from external_eval.expanded_benchmark.run_expanded_evaluation import (
    load_expanded_document,
)
from external_eval.runners.synthetic_benchmark_runner import (
    load_synthetic_document_and_gt,
)

logger = logging.getLogger("clindoc.verify_integration")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def load_frozen_12_docs() -> tuple[list[DocumentText], dict[str, list[GroundTruthClaim]]]:
    manifest_path = REPO_ROOT / "external_eval" / "synthetic_benchmark" / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    docs: list[DocumentText] = []
    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}

    for entry in manifest["documents"]:
        doc, gt = load_synthetic_document_and_gt(entry)
        docs.append(doc)
        gt_by_doc[doc.document_id] = gt

    return docs, gt_by_doc


def load_expanded_60_docs() -> tuple[list[DocumentText], dict[str, list[GroundTruthClaim]]]:
    manifest_path = REPO_ROOT / "external_eval" / "expanded_benchmark" / "manifest.json"
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    docs: list[DocumentText] = []
    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}

    for entry in manifest["documents"]:
        doc, gt = load_expanded_document(entry)
        docs.append(doc)
        gt_by_doc[doc.document_id] = gt

    return docs, gt_by_doc


def load_saved_cascade_predictions(
    result_path: Path,
) -> tuple[dict[str, list[Claim]], dict[str, list[Claim]]]:
    """Load validated cascade predictions from benchmark experiment results.

    Explicitly fails with diagnostic error if required cascade keys are missing.
    Never silently substitutes deterministic predictions.
    """
    if not result_path.exists():
        raise FileNotFoundError(f"Experiment results file not found: {result_path}")

    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "predictions" not in data:
        raise RuntimeError(
            f"Corrupt or incompatible benchmark artifact at {result_path}: missing top-level 'predictions' key."
        )

    preds_container = data["predictions"]
    missing_keys = []
    if "baseline_cascade" not in preds_container:
        missing_keys.append("baseline_cascade")
    if "poc_v3_cascade" not in preds_container:
        missing_keys.append("poc_v3_cascade")

    if missing_keys:
        available = list(preds_container.keys())
        raise RuntimeError(
            f"Missing required cascade predictions in {result_path}: expected keys {missing_keys}, "
            f"found only {available}. Never silently substituting deterministic predictions."
        )

    base_raw = preds_container["baseline_cascade"]
    v3_raw = preds_container["poc_v3_cascade"]

    def to_claims(raw_dict: dict[str, list[dict]]) -> dict[str, list[Claim]]:
        return {
            doc_id: [Claim(**item) for item in items]
            for doc_id, items in raw_dict.items()
        }

    return to_claims(base_raw), to_claims(v3_raw)


def run_deterministic_with_options(
    docs: list[DocumentText],
    enable_section_detection: bool,
) -> dict[str, list[Claim]]:
    """Execute the production deterministic extractor with or without section detection."""
    detector = get_section_detector() if enable_section_detection else None
    extractor = DeterministicExtractor(
        section_detector=detector,
        enable_section_detection=enable_section_detection,
    )
    preds: dict[str, list[Claim]] = {}

    for doc in docs:
        claims: list[Claim] = []
        is_lab = doc.document_id.upper().startswith("LAB_")
        is_med = doc.document_id.upper().startswith("MED_")

        for page in doc.pages:
            page_text = page.get_page_text()

            if is_lab:
                evs, clms = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r_lab",
                    is_lab=True,
                )
                claims.extend(clms)
            elif is_med:
                evs, clms = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r_med",
                    is_lab=False,
                )
                claims.extend(clms)
            else:
                # Narrative note: meds unconstrained, labs section-aware
                evs_m, clms_m = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r_med",
                    is_lab=False,
                )
                claims.extend(clms_m)

                evs_l, clms_l = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r_lab",
                    is_lab=True,
                )
                claims.extend(clms_l)

        preds[doc.document_id] = claims

    return preds


def verify_provenance_and_spans(docs: list[DocumentText], preds: dict[str, list[Claim]]) -> int:
    """Verify that all claims have valid evidence IDs, methods, and non-empty values."""
    total_claims = 0
    for doc in docs:
        claims = preds.get(doc.document_id, [])
        for c in claims:
            total_claims += 1
            assert c.evidence_id, f"Claim {c} missing evidence_id"
            assert c.extraction_method == "deterministic"
            assert c.value, f"Claim {c} has empty value"
    return total_claims


def verify_claim_emission_order(preds: dict[str, list[Claim]]) -> None:
    """Verify intra-row claim emission order matches baseline:
    lab.test_name -> lab.value -> lab.unit -> lab.reference_range -> lab.flag.
    """
    canonical_lab_order = [
        "lab.test_name",
        "lab.value",
        "lab.unit",
        "lab.reference_range",
        "lab.flag",
    ]
    order_index = {field: i for i, field in enumerate(canonical_lab_order)}

    for doc_id, claims in preds.items():
        # Group claims by group_id
        groups: dict[str, list[Claim]] = {}
        for c in claims:
            groups.setdefault(c.group_id, []).append(c)

        for gid, grp_claims in groups.items():
            lab_claims = [c for c in grp_claims if c.field_type in order_index]
            if len(lab_claims) > 1:
                indices = [order_index[c.field_type] for c in lab_claims]
                assert indices == sorted(indices), (
                    f"Doc {doc_id} group {gid} emitted lab claims out of order: "
                    f"{[c.field_type for c in lab_claims]}"
                )


def format_metrics_table(eval_base: dict[str, Any], eval_opt: dict[str, Any]) -> str:
    """Format side-by-side 10-field comparison table."""
    lines = [
        f"  {'Field Type':<22} | {'Base Prec':<9} {'Base Rec':<9} {'Base F1':<9} | "
        f"{'Opt Prec':<9} {'Opt Rec':<9} {'Opt F1':<9} | {'F1 Delta':<9}",
        "  " + "-" * 105,
    ]

    for field in sorted(ALL_CANONICAL_FIELDS):
        mb = eval_base["metrics_per_field"].get(field, {"precision": 0.0, "recall": 0.0, "f1": 0.0})
        mo = eval_opt["metrics_per_field"].get(field, {"precision": 0.0, "recall": 0.0, "f1": 0.0})
        delta = mo["f1"] - mb["f1"]
        delta_str = f"{delta:+.4f}" if abs(delta) >= 1e-4 else "0.0000"
        lines.append(
            f"  {field:<22} | {mb['precision']:<9.4f} {mb['recall']:<9.4f} {mb['f1']:<9.4f} | "
            f"{mo['precision']:<9.4f} {mo['recall']:<9.4f} {mo['f1']:<9.4f} | {delta_str:<9}"
        )

    return "\n".join(lines)


def main():
    print("=" * 80)
    print("CLINICALDOC CANDIDATE V3 PRODUCTION INTEGRATION BENCHMARK VERIFICATION")
    print("=" * 80)

    # 1. Load ground truth datasets
    frozen_docs, frozen_gt = load_frozen_12_docs()
    expanded_docs, expanded_gt = load_expanded_60_docs()
    print(f"Loaded {len(frozen_docs)} frozen benchmark documents and {len(expanded_docs)} expanded benchmark documents.")

    exp_res_path = (
        REPO_ROOT
        / "external_eval"
        / "experiments"
        / "poc_medspacy_section_v3"
        / "poc_v3_expanded_results.json"
    )
    froz_res_path = (
        REPO_ROOT
        / "external_eval"
        / "experiments"
        / "poc_medspacy_section_v3"
        / "poc_v3_frozen12_results.json"
    )

    # 2. Load validated cascade predictions (explicit diagnostic check, no silent substitution)
    exp_base_cascade, exp_v3_cascade = load_saved_cascade_predictions(exp_res_path)
    froz_base_cascade, froz_v3_cascade = load_saved_cascade_predictions(froz_res_path)

    # 3. Run production deterministic extractor dynamically
    print("\nRunning production deterministic extractor across benchmarks...")
    frozen_base_det = run_deterministic_with_options(frozen_docs, enable_section_detection=False)
    frozen_opt_det = run_deterministic_with_options(frozen_docs, enable_section_detection=True)
    exp_base_det = run_deterministic_with_options(expanded_docs, enable_section_detection=False)
    exp_opt_det = run_deterministic_with_options(expanded_docs, enable_section_detection=True)

    # Verify provenance and emission order
    verify_provenance_and_spans(frozen_docs, frozen_base_det)
    verify_provenance_and_spans(frozen_docs, frozen_opt_det)
    verify_provenance_and_spans(expanded_docs, exp_base_det)
    verify_provenance_and_spans(expanded_docs, exp_opt_det)

    verify_claim_emission_order(frozen_base_det)
    verify_claim_emission_order(frozen_opt_det)
    verify_claim_emission_order(exp_base_det)
    verify_claim_emission_order(exp_opt_det)

    # =========================================================================
    # PART 1: FULL DEC-004 CASCADE EVALUATION (Deterministic + GLiNER-BioMed)
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 1: FULL DEC-004 CASCADE EVALUATION (Deterministic + GLiNER-BioMed Fallback)")
    print("=" * 80)

    # Expanded 60-Doc Cascade
    eval_exp_cas_base = evaluate_dataset(exp_base_cascade, expanded_gt)
    eval_exp_cas_v3 = evaluate_dataset(exp_v3_cascade, expanded_gt)
    exp_cas_delta = eval_exp_cas_v3["micro_f1"] - eval_exp_cas_base["micro_f1"]

    print("\n--- [1.1] Expanded 60-Document Benchmark (Cascade) ---")
    print(
        f"Micro F1: Baseline = {eval_exp_cas_base['micro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_exp_cas_v3['micro_f1']:.4f}  ({exp_cas_delta:+.4f})"
    )
    print(
        f"Macro F1: Baseline = {eval_exp_cas_base['macro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_exp_cas_v3['macro_f1']:.4f}  "
        f"({eval_exp_cas_v3['macro_f1'] - eval_exp_cas_base['macro_f1']:+.4f})"
    )
    print("\nPer-Field Metrics (Expanded Benchmark - Cascade):")
    print(format_metrics_table(eval_exp_cas_base, eval_exp_cas_v3))

    # Frozen 12-Doc Cascade
    eval_froz_cas_base = evaluate_dataset(froz_base_cascade, frozen_gt)
    eval_froz_cas_v3 = evaluate_dataset(froz_v3_cascade, frozen_gt)
    froz_cas_delta = eval_froz_cas_v3["micro_f1"] - eval_froz_cas_base["micro_f1"]

    print("\n--- [1.2] Frozen 12-Document Benchmark (Cascade) ---")
    print(
        f"Micro F1: Baseline = {eval_froz_cas_base['micro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_froz_cas_v3['micro_f1']:.4f}  ({froz_cas_delta:+.4f})"
    )
    print(
        f"Macro F1: Baseline = {eval_froz_cas_base['macro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_froz_cas_v3['macro_f1']:.4f}  "
        f"({eval_froz_cas_v3['macro_f1'] - eval_froz_cas_base['macro_f1']:+.4f})"
    )
    print("\nPer-Field Metrics (Frozen Benchmark - Cascade):")
    print(format_metrics_table(eval_froz_cas_base, eval_froz_cas_v3))

    # =========================================================================
    # PART 2: PURE DETERMINISTIC EXTRACTION EVALUATION (Rule/Regex Engine Alone)
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 2: PURE DETERMINISTIC EXTRACTION EVALUATION (Rule/Regex Engine Alone)")
    print("=" * 80)

    # Expanded 60-Doc Deterministic
    eval_exp_det_base = evaluate_dataset(exp_base_det, expanded_gt)
    eval_exp_det_v3 = evaluate_dataset(exp_opt_det, expanded_gt)
    exp_det_delta = eval_exp_det_v3["micro_f1"] - eval_exp_det_base["micro_f1"]

    print("\n--- [2.1] Expanded 60-Document Benchmark (Pure Deterministic) ---")
    print(
        f"Micro F1: Baseline = {eval_exp_det_base['micro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_exp_det_v3['micro_f1']:.4f}  ({exp_det_delta:+.4f})"
    )
    print(
        f"Macro F1: Baseline = {eval_exp_det_base['macro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_exp_det_v3['macro_f1']:.4f}  "
        f"({eval_exp_det_v3['macro_f1'] - eval_exp_det_base['macro_f1']:+.4f})"
    )
    print("\nPer-Field Metrics (Expanded Benchmark - Pure Deterministic):")
    print(format_metrics_table(eval_exp_det_base, eval_exp_det_v3))

    # Frozen 12-Doc Deterministic
    eval_froz_det_base = evaluate_dataset(frozen_base_det, frozen_gt)
    eval_froz_det_v3 = evaluate_dataset(frozen_opt_det, frozen_gt)
    froz_det_delta = eval_froz_det_v3["micro_f1"] - eval_froz_det_base["micro_f1"]

    print("\n--- [2.2] Frozen 12-Document Benchmark (Pure Deterministic) ---")
    print(
        f"Micro F1: Baseline = {eval_froz_det_base['micro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_froz_det_v3['micro_f1']:.4f}  ({froz_det_delta:+.4f})"
    )
    print(
        f"Macro F1: Baseline = {eval_froz_det_base['macro_f1']:.4f}  -->  "
        f"V3 Opt-In = {eval_froz_det_v3['macro_f1']:.4f}  "
        f"({eval_froz_det_v3['macro_f1'] - eval_froz_det_base['macro_f1']:+.4f})"
    )
    print("\nPer-Field Metrics (Frozen Benchmark - Pure Deterministic):")
    print(format_metrics_table(eval_froz_det_base, eval_froz_det_v3))

    # =========================================================================
    # PART 3: MEDICATION INVARIANCE VERIFICATION
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 3: MEDICATION INVARIANCE VERIFICATION")
    print("=" * 80)
    med_fields = [f for f in ALL_CANONICAL_FIELDS if f.startswith("med.")]

    print("\nChecking Medication Invariance (Cascade Pipeline):")
    for fld in sorted(med_fields):
        b_f1 = eval_exp_cas_base["metrics_per_field"][fld]["f1"]
        o_f1 = eval_exp_cas_v3["metrics_per_field"][fld]["f1"]
        assert b_f1 == o_f1, f"Cascade medication field {fld} drifted: base={b_f1}, opt={o_f1}"
        print(f"  {fld:<18} Baseline F1: {b_f1:.4f} | Opt-In F1: {o_f1:.4f} (100% invariant)")

    print("\nChecking Medication Invariance (Deterministic Pipeline):")
    for fld in sorted(med_fields):
        b_f1 = eval_exp_det_base["metrics_per_field"][fld]["f1"]
        o_f1 = eval_exp_det_v3["metrics_per_field"][fld]["f1"]
        assert b_f1 == o_f1, f"Deterministic medication field {fld} drifted: base={b_f1}, opt={o_f1}"
        print(f"  {fld:<18} Baseline F1: {b_f1:.4f} | Opt-In F1: {o_f1:.4f} (100% invariant)")

    # =========================================================================
    # PART 4: CONFIGURATION & DEFAULT INVARIANCE VERIFICATION
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 4: CONFIGURATION & DEFAULT INVARIANCE VERIFICATION")
    print("=" * 80)
    router = ExtractionRouter()
    assert router.section_detection_enabled is False, (
        "ExtractionRouter did not default to section_detection_enabled=False!"
    )
    assert router.deterministic.enable_section_detection is False, (
        "DeterministicExtractor did not default to enable_section_detection=False!"
    )
    print("  [OK] Feature flag remains disabled by default (enabled: false)")

    # Verify that default extractor produces identical claims to baseline
    default_extractor = DeterministicExtractor()
    sample_doc = frozen_docs[0]
    sample_page = sample_doc.pages[0]
    sample_text = sample_page.get_page_text()

    _, claims_default = default_extractor.extract_region(
        sample_doc.document_id, sample_page, sample_text, is_lab=True
    )
    _, claims_disabled = DeterministicExtractor(enable_section_detection=False).extract_region(
        sample_doc.document_id, sample_page, sample_text, is_lab=True
    )

    assert len(claims_default) == len(claims_disabled), "Claim count mismatch in default vs disabled!"
    for c_def, c_dis in zip(claims_default, claims_disabled):
        assert c_def.field_type == c_dis.field_type
        assert c_def.value == c_dis.value
        assert c_def.extraction_confidence == c_dis.extraction_confidence
    print("  [OK] Default extraction produces 100% identical outputs to disabled mode")

    # =========================================================================
    # PART 5: ACCEPTANCE THRESHOLDS VERIFICATION
    # =========================================================================
    print("\n" + "=" * 80)
    print("PART 5: ACCEPTANCE THRESHOLDS VERIFICATION")
    print("=" * 80)

    # 5.1 Full DEC-004 Cascade Thresholds
    # Note: Expanded cascade micro F1 increased from 0.5560 -> 0.5632 (baseline) and
    # 0.5970 -> 0.6038 (V3) following the med.duration normalization fix.
    assert abs(eval_exp_cas_base["micro_f1"] - 0.5632) < 0.002, (
        f"Expanded Baseline Cascade F1 {eval_exp_cas_base['micro_f1']} != 0.5632"
    )
    assert abs(eval_exp_cas_v3["micro_f1"] - 0.6038) < 0.002, (
        f"Expanded V3 Cascade F1 {eval_exp_cas_v3['micro_f1']} != 0.6038"
    )
    assert abs(eval_froz_cas_base["micro_f1"] - 0.7673) < 0.002, (
        f"Frozen Baseline Cascade F1 {eval_froz_cas_base['micro_f1']} != 0.7673"
    )
    assert abs(eval_froz_cas_v3["micro_f1"] - 0.7869) < 0.002, (
        f"Frozen V3 Cascade F1 {eval_froz_cas_v3['micro_f1']} != 0.7869"
    )
    print("  [OK] Cascade benchmarks match ground truth: 0.5632 -> 0.6038 (expanded) and 0.7673 -> 0.7869 (frozen)")

    # 5.2 Pure Deterministic Thresholds
    # Note: Expanded deterministic micro F1 increased to 0.6595 (baseline) and
    # 0.7083 (V3) following med.duration and med.dose targeted fixes.
    assert abs(eval_exp_det_base["micro_f1"] - 0.6595) < 0.002, (
        f"Expanded Baseline Deterministic F1 {eval_exp_det_base['micro_f1']} != 0.6595"
    )
    assert abs(eval_exp_det_v3["micro_f1"] - 0.7083) < 0.002, (
        f"Expanded V3 Deterministic F1 {eval_exp_det_v3['micro_f1']} != 0.7083"
    )
    assert abs(eval_froz_det_base["micro_f1"] - 0.8074) < 0.002, (
        f"Frozen Baseline Deterministic F1 {eval_froz_det_base['micro_f1']} != 0.8074"
    )
    assert abs(eval_froz_det_v3["micro_f1"] - 0.8298) < 0.002, (
        f"Frozen V3 Deterministic F1 {eval_froz_det_v3['micro_f1']} != 0.8298"
    )
    print("  [OK] Deterministic benchmarks match ground truth: 0.6595 -> 0.7083 (expanded) and 0.8074 -> 0.8298 (frozen)")

    # 5.3 Key Laboratory Improvements (Cascade)
    assert eval_exp_cas_v3["metrics_per_field"]["lab.value"]["f1"] >= 0.490
    assert eval_exp_cas_v3["metrics_per_field"]["lab.reference_range"]["f1"] >= 0.730
    assert eval_exp_cas_v3["metrics_per_field"]["lab.test_name"]["f1"] >= 0.470
    assert eval_exp_cas_v3["metrics_per_field"]["lab.unit"]["f1"] >= 0.630
    assert eval_exp_cas_v3["metrics_per_field"]["lab.flag"]["f1"] >= 0.480
    print("  [OK] Laboratory field metrics meet all target thresholds")

    print("\n" + "=" * 80)
    print("ALL POST-AUDIT CORRECTIVE PASS VERIFICATION CHECKS PASSED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    main()
