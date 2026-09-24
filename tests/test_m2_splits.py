"""Unit tests for M2 split generation and OOD isolation."""
import pytest
from clindoc.m2.schema import M2Example, VerificationLabel, OOD_MED_TEMPLATE, OOD_LAB_TEMPLATE
from clindoc.m2.splits import build_splits, validate_no_leakage, get_fold_examples, get_ood_examples


def test_build_splits_and_leakage():
    examples = []
    # 10 CV med docs, 3 OOD med docs
    for i in range(10):
        examples.append(M2Example(
            example_id=f"med_{i}",
            claim="Amoxicillin 500 mg",
            evidence="Amoxicillin 500 mg",
            field_type="med.name",
            doc_template="clinic_rx_pad",
            ocr_confidence=1.0,
            severity="clean",
            label=VerificationLabel.SUPPORTED,
            source_doc_id=f"MED_DOC_{i}",
            generation_method="clean",
            claim_type="med",
        ))
    for i in range(3):
        examples.append(M2Example(
            example_id=f"med_ood_{i}",
            claim="Lisinopril 10 mg",
            evidence="Lisinopril 10 mg",
            field_type="med.name",
            doc_template=OOD_MED_TEMPLATE,
            ocr_confidence=1.0,
            severity="clean",
            label=VerificationLabel.SUPPORTED,
            source_doc_id=f"MED_OOD_DOC_{i}",
            generation_method="clean",
            claim_type="med",
        ))

    manifest = build_splits(examples, n_splits=5, seed=42)
    assert len(manifest["ood_doc_ids"]) == 3
    assert len(manifest["cv_folds"]) == 5

    # Validate no leakage
    validate_no_leakage(manifest)

    # Check get_fold_examples
    train_ex, val_ex = get_fold_examples(examples, manifest, 0)
    train_doc_ids = {e.source_doc_id for e in train_ex}
    val_doc_ids = {e.source_doc_id for e in val_ex}
    assert train_doc_ids.isdisjoint(val_doc_ids)

    # Check OOD docs not in any CV fold
    ood_ex = get_ood_examples(examples, manifest)
    assert len(ood_ex) == 3
    ood_doc_ids = {e.source_doc_id for e in ood_ex}
    for fold in manifest["cv_folds"]:
        assert ood_doc_ids.isdisjoint(set(fold["train_doc_ids"]))
        assert ood_doc_ids.isdisjoint(set(fold["val_doc_ids"]))


def test_leakage_detector_raises():
    manifest = {
        "ood_doc_ids": ["DOC_OOD_1"],
        "cv_folds": [
            {"train_doc_ids": ["DOC_OOD_1", "DOC_CV_1"], "val_doc_ids": ["DOC_CV_2"]}
        ],
        "n_splits": 1,
    }
    with pytest.raises(AssertionError):
        validate_no_leakage(manifest)
