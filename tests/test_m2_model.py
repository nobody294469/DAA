"""Unit tests for M2 auxiliary features and model architecture."""
import torch
import torch.nn as nn
from clindoc.m2.schema import (
    AUX_DIM, AUX_PROJ_DIM, HIDDEN_DIM, FUSION_DIM, NUM_LABELS,
    FIELD_TYPE_ORDER, DOC_TEMPLATE_ORDER
)
from clindoc.m2.features import encode_aux_features, encode_aux_batch
from clindoc.m2.model import format_input


def test_aux_feature_encoding():
    feat = encode_aux_features(
        ocr_confidence=0.95,
        field_type="lab.value",
        doc_template="template_a",
    )
    assert len(feat) == AUX_DIM
    assert feat[0] == 0.95
    # lab.value is at index 1
    assert feat[1] == 1.0
    # template_a is at index 1 + 13 = 14
    assert feat[14] == 1.0
    assert sum(feat[1:14]) == 1.0  # Exactly one field type active
    assert sum(feat[14:21]) == 1.0  # Exactly one template active


def test_aux_batch_tensor():
    batch = encode_aux_batch(
        [0.9, 0.4],
        ["lab.value", "med.dose"],
        ["template_b", "pharmacy_label"],
    )
    assert batch.shape == (2, AUX_DIM)
    assert batch.dtype == torch.float32


def test_format_input():
    text = format_input("lab.value", "Glucose: 90 mg/dL", "Glucose: 90 mg/dL")
    assert "[FIELD] lab.value" in text
    assert "[CLAIM] Glucose: 90 mg/dL" in text
    assert "[EVIDENCE] Glucose: 90 mg/dL" in text


def test_model_projection_and_fusion_logic():
    # Test fusion mathematics independently of remote HF weights
    aux_proj = nn.Sequential(
        nn.Linear(AUX_DIM, AUX_PROJ_DIM),
        nn.GELU(),
    )
    classifier_fused = nn.Linear(FUSION_DIM, NUM_LABELS)
    classifier_text = nn.Linear(HIDDEN_DIM, NUM_LABELS)

    batch_size = 4
    pooled = torch.randn(batch_size, HIDDEN_DIM)
    aux = torch.randn(batch_size, AUX_DIM)

    # With aux
    fused = torch.cat([pooled, aux_proj(aux)], dim=-1)
    assert fused.shape == (batch_size, FUSION_DIM)
    logits_fused = classifier_fused(fused)
    assert logits_fused.shape == (batch_size, NUM_LABELS)

    # Without aux
    logits_text = classifier_text(pooled)
    assert logits_text.shape == (batch_size, NUM_LABELS)
