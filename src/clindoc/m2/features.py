"""Auxiliary feature encoding for M2 verification model.

Modes:
  - "full": 21-dim vector [ocr_conf, 13 one-hot field_type, 7 one-hot doc_template]
  - "ocr_only": 1-dim vector [ocr_conf]
  - "none": no auxiliary features
"""
from __future__ import annotations

import torch
from clindoc.m2.schema import FIELD_TYPE_ORDER, DOC_TEMPLATE_ORDER, AUX_DIM

OCR_ONLY_DIM = 1
FULL_AUX_DIM = AUX_DIM  # 21


def encode_aux_features(
    ocr_confidence: float,
    field_type: str,
    doc_template: str,
    mode: str = "full",
) -> list[float]:
    """Encode auxiliary features."""
    if mode == "none":
        return []
    elif mode == "ocr_only":
        return [float(ocr_confidence)]

    feat = [float(ocr_confidence)]
    # Field type one-hot (13 dims)
    for ft in FIELD_TYPE_ORDER:
        feat.append(1.0 if ft == field_type else 0.0)

    # Doc template one-hot (7 dims)
    for dt in DOC_TEMPLATE_ORDER:
        feat.append(1.0 if dt == doc_template else 0.0)

    assert len(feat) == FULL_AUX_DIM, f"AUX_DIM mismatch: expected {FULL_AUX_DIM}, got {len(feat)}"
    return feat


def encode_aux_batch(
    ocr_confidences: list[float],
    field_types: list[str],
    doc_templates: list[str],
    mode: str = "full",
) -> torch.Tensor | None:
    """Encode a batch of auxiliary features."""
    if mode == "none":
        return None
    rows = [
        encode_aux_features(c, f, d, mode=mode)
        for c, f, d in zip(ocr_confidences, field_types, doc_templates)
    ]
    return torch.tensor(rows, dtype=torch.float32)
