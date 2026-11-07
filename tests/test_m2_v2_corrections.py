"""Tests for M2 v2 dataset corrections and slice macro-F1 evaluation."""
import pytest
from pathlib import Path

from clindoc.m2.schema import VerificationLabel, M2Example
from clindoc.m2.metrics import macro_f1, slice_macro_f1, per_class_metrics, LABEL_ORDER
from clindoc.m2.generation import (
    generate_clean_lab_examples,
    generate_clean_med_examples,
    CANONICAL_MEDICATIONS,
)
from clindoc.m2.evaluate import _build_eval_result


# ─── A. lab.flag Tests ────────────────────────────────────────────────────────

@pytest.mark.xfail(reason="depends on locally generated data/extracted; counts differ per machine", strict=False)
def test_lab_flag_v2_generation():
    """Verify lab.flag produces both SUPPORTED and DISCREPANCY examples without conflict."""
    extracted_dir = Path("data/extracted")
    gt_dir = Path("data/ground_truth")
    if not extracted_dir.exists() or not gt_dir.exists():
        pytest.skip("Extraction or ground truth directories missing")

    examples = generate_clean_lab_examples(extracted_dir, gt_dir)
    flag_examples = [e for e in examples if e.field_type == "lab.flag"]

    supported = [e for e in flag_examples if e.label == VerificationLabel.SUPPORTED]
    discrepancy = [e for e in flag_examples if e.label == VerificationLabel.DISCREPANCY]

    assert len(supported) > 0, "No SUPPORTED lab.flag examples generated"
    assert len(discrepancy) > 0, "No DISCREPANCY lab.flag examples generated"
    assert len(supported) == len(discrepancy), (
        f"Expected equal SUPPORTED ({len(supported)}) and DISCREPANCY ({len(discrepancy)})"
    )

    # Check for conflicting (claim, evidence) pairs with different labels
    seen_pairs: dict[tuple[str, str], VerificationLabel] = {}
    for ex in flag_examples:
        key = (ex.claim, ex.evidence)
        if key in seen_pairs:
            assert seen_pairs[key] == ex.label, (
                f"Conflicting label for lab.flag pair: '{ex.claim}' vs '{ex.evidence}'"
            )
        seen_pairs[key] = ex.label


# ─── B. med.name Tests ────────────────────────────────────────────────────────

def test_med_name_v2_generation():
    """Verify med.name produces both SUPPORTED and DISCREPANCY examples, with strict evidence safeguard."""
    med_extracted_dir = Path("data/med_extracted")
    med_gt_dir = Path("data/med_ground_truth")
    if not med_extracted_dir.exists() or not med_gt_dir.exists():
        pytest.skip("Med extraction or ground truth directories missing")

    examples, _ = generate_clean_med_examples(med_extracted_dir, med_gt_dir)
    name_examples = [e for e in examples if e.field_type == "med.name"]

    supported = [e for e in name_examples if e.label == VerificationLabel.SUPPORTED]
    discrepancy = [e for e in name_examples if e.label == VerificationLabel.DISCREPANCY]

    assert len(supported) > 0, "No SUPPORTED med.name examples generated"
    assert len(discrepancy) > 0, "No DISCREPANCY med.name examples generated"
    assert len(supported) == len(discrepancy), (
        f"Expected equal SUPPORTED ({len(supported)}) and DISCREPANCY ({len(discrepancy)})"
    )

    # Critical Safeguard: alternative name must NEVER appear in the evidence text
    for ex in discrepancy:
        alt_name = ex.claim.replace("Medication: ", "").strip()
        assert alt_name.lower() not in ex.evidence.lower(), (
            f"Safeguard violation: '{alt_name}' found in evidence '{ex.evidence}'"
        )

    # Check for conflicting (claim, evidence) pairs
    seen_pairs: dict[tuple[str, str], VerificationLabel] = {}
    for ex in name_examples:
        key = (ex.claim, ex.evidence)
        if key in seen_pairs:
            assert seen_pairs[key] == ex.label, (
                f"Conflicting label for med.name pair: '{ex.claim}' vs '{ex.evidence}'"
            )
        seen_pairs[key] = ex.label


# ─── C. Field-Type Metric Tests ───────────────────────────────────────────────

def test_slice_macro_f1_two_class():
    """A 2-class slice gets the average over its represented classes (divided by 2, not 3)."""
    # 0 = SUPPORTED, 1 = DISCREPANCY. Perfect classification on both.
    y_true = [0, 1, 0, 1]
    y_pred = [0, 1, 0, 1]
    # Standard macro_f1 divides by 3 -> (1.0 + 1.0 + 0.0) / 3 = 0.6667
    assert round(macro_f1(y_true, y_pred), 4) == 0.6667
    # slice_macro_f1 divides by 2 -> (1.0 + 1.0) / 2 = 1.0
    assert slice_macro_f1(y_true, y_pred) == 1.0


def test_slice_macro_f1_one_class():
    """A 1-class slice gets that class's F1 rather than being divided by 3."""
    y_true = [0, 0, 0]
    y_pred = [0, 0, 0]
    # Standard macro_f1 divides by 3 -> (1.0 + 0.0 + 0.0) / 3 = 0.3333
    assert round(macro_f1(y_true, y_pred), 4) == 0.3333
    # slice_macro_f1 divides by 1 -> 1.0
    assert slice_macro_f1(y_true, y_pred) == 1.0


def test_slice_macro_f1_three_class():
    """A 3-class slice matches normal 3-class macro-F1."""
    y_true = [0, 1, 2, 0, 1, 2]
    y_pred = [0, 1, 2, 0, 1, 2]
    assert slice_macro_f1(y_true, y_pred) == macro_f1(y_true, y_pred) == 1.0

    # Non-perfect 3-class case
    y_true_imbalanced = [0, 0, 1, 1, 2, 2]
    y_pred_imbalanced = [0, 1, 1, 1, 2, 0]
    assert slice_macro_f1(y_true_imbalanced, y_pred_imbalanced) == pytest.approx(
        macro_f1(y_true_imbalanced, y_pred_imbalanced)
    )


def test_slice_macro_f1_absent_class_in_pred():
    """Predictions containing a previously absent class are included in the slice metric."""
    # y_true only has class 0, but model erroneously predicts class 1
    y_true = [0, 0]
    y_pred = [0, 1]
    # present_classes should be {0, 1}
    # class 0: TP=1, FP=0, FN=1 -> F1 = 2*1/(2+1) = 2/3
    # class 1: TP=0, FP=1, FN=0 -> F1 = 0.0
    # Average over represented classes = (2/3 + 0.0) / 2 = 1/3
    expected_f1 = (2.0 / 3.0 + 0.0) / 2.0
    assert slice_macro_f1(y_true, y_pred) == pytest.approx(expected_f1)


def test_slice_macro_f1_empty():
    assert slice_macro_f1([], []) == 0.0


# ─── D. Regression & Scope Tests ─────────────────────────────────────────────

def test_evaluate_build_eval_result_preserves_overall_metrics():
    """Confirm _build_eval_result only alters by_field_type while overall metrics remain standard."""
    # 2-class slice scenario in field_type "lab.flag"
    y_true = [0, 1, 0, 1]
    y_pred = [0, 1, 0, 1]

    dummy_examples = [
        M2Example(
            example_id=f"id_{i}",
            claim=f"claim_{i}",
            evidence="ev",
            field_type="lab.flag",
            doc_template="template_a",
            ocr_confidence=1.0,
            severity="clean",
            label=LABEL_ORDER[y_true[i]],
            source_doc_id=f"doc_{i}",
            generation_method="test",
            claim_type="lab",
        )
        for i in range(4)
    ]

    res = _build_eval_result(
        model_name="test_model",
        split="cv",
        fold=0,
        seed=42,
        y_true=y_true,
        y_pred=y_pred,
        y_probs=[[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        examples=dummy_examples,
    )

    # Overall macro_f1 remains standard 3-class (0.6667)
    assert res.macro_f1 == 0.6667
    # by_severity remains standard 3-class (0.6667)
    assert res.by_severity["clean"]["macro_f1"] == 0.6667
    # by_claim_type remains standard 3-class (0.6667)
    assert res.by_claim_type["lab"]["macro_f1"] == 0.6667

    # ONLY by_field_type uses slice macro-F1 (1.0 for 2-class perfect)
    assert res.by_field_type["lab.flag"]["macro_f1"] == 1.0
