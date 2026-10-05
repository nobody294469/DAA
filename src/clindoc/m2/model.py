"""M2 ClinicalVerifier: BioClinical ModernBERT-base with aux feature fusion.

Architecture:
  - BioClinical ModernBERT encoder (22L, hidden=768)
  - Mean pooling over non-padding tokens
  - Optional aux projection:
      "full"     -> Linear(21->64) + GELU (fused dim 832)
      "ocr_only" -> Linear(1->16) + GELU (fused dim 784)
      "none"     -> no aux features (dim 768)
  - Classification head: Linear(head_in, 3)

Variants & Ablations:
  - B4: freeze_all_encoder=True (Frozen ModernBERT + linear probe)
  - V5: aux_mode="none", freeze=0 (Fine-tuned ModernBERT, text only)
  - V6: aux_mode="ocr_only", freeze=0 (Fine-tuned ModernBERT + OCR confidence)
  - V7: aux_mode="full", freeze=0 (Full model)
  - V8: aux_mode="full", freeze_lower_n_layers=8 (Frozen lower 8 layers)
"""
from __future__ import annotations

import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer

from clindoc.m2.schema import (
    AUX_DIM, AUX_PROJ_DIM, HIDDEN_DIM, FUSION_DIM, NUM_LABELS, LABEL_ORDER,
)

MODEL_CHECKPOINT = "thomas-sounack/BioClinical-ModernBERT-base"
MAX_SEQ_LENGTH = 256
LABEL_TO_INT = {lbl: i for i, lbl in enumerate(LABEL_ORDER)}
INT_TO_LABEL = {i: lbl for lbl, i in LABEL_TO_INT.items()}


def format_input(field_type: str, claim: str, evidence: str) -> str:
    """Format claim + evidence as a single sequence-pair string for the encoder."""
    return "[FIELD] " + field_type + " [CLAIM] " + claim + " [EVIDENCE] " + evidence


class ClinicalVerifier(nn.Module):
    """3-way clinical claim/evidence verification classifier."""

    def __init__(
        self,
        model_id: str = MODEL_CHECKPOINT,
        use_aux: bool = True,
        aux_mode: str = "full",
        freeze_lower_n_layers: int = 0,
        freeze_all_encoder: bool = False,
        local_files_only: bool = False,
    ) -> None:
        super().__init__()
        self.model_id = model_id
        if not use_aux:
            aux_mode = "none"
        self.aux_mode = aux_mode
        self.use_aux = (aux_mode != "none")

        self.encoder = AutoModel.from_pretrained(model_id, local_files_only=local_files_only)

        # Freeze encoder options
        if freeze_all_encoder:
            for param in self.encoder.parameters():
                param.requires_grad_(False)
        elif freeze_lower_n_layers > 0:
            for name, param in self.encoder.named_parameters():
                should_freeze = "embeddings" in name
                for layer_idx in range(freeze_lower_n_layers):
                    if f"layers.{layer_idx}." in name or f"layer.{layer_idx}." in name:
                        should_freeze = True
                        break
                param.requires_grad_(not should_freeze)

        # Auxiliary projection
        if self.aux_mode == "full":
            self.aux_proj = nn.Sequential(
                nn.Linear(AUX_DIM, AUX_PROJ_DIM),
                nn.GELU(),
            )
            head_in = HIDDEN_DIM + AUX_PROJ_DIM  # 768 + 64 = 832
        elif self.aux_mode == "ocr_only":
            self.aux_proj = nn.Sequential(
                nn.Linear(1, 16),
                nn.GELU(),
            )
            head_in = HIDDEN_DIM + 16  # 768 + 16 = 784
        else:
            self.aux_proj = None
            head_in = HIDDEN_DIM  # 768

        self.classifier = nn.Linear(head_in, NUM_LABELS)
        self.dropout = nn.Dropout(p=0.1)

    def forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        aux_features: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Return (B, NUM_LABELS) logits."""
        out = self.encoder(input_ids=input_ids, attention_mask=attention_mask)
        hidden = out.last_hidden_state  # (B, T, 768)
        mask = attention_mask.unsqueeze(-1).float()  # (B, T, 1)
        # Mean pool over non-padding tokens
        pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
        pooled = self.dropout(pooled)

        if self.use_aux and self.aux_proj is not None and aux_features is not None:
            fused = torch.cat([pooled, self.aux_proj(aux_features)], dim=-1)
        else:
            fused = pooled

        return self.classifier(fused)

    @property
    def n_trainable_params(self) -> int:
        return sum(p.numel() for p in self.parameters() if p.requires_grad)


def load_tokenizer(model_id: str = MODEL_CHECKPOINT) -> AutoTokenizer:
    return AutoTokenizer.from_pretrained(model_id)


def tokenize_examples(
    examples: list,
    tokenizer: AutoTokenizer,
    max_length: int = MAX_SEQ_LENGTH,
) -> dict:
    """Tokenize a list of M2Example into model-ready tensors."""
    texts = [
        format_input(ex.field_type, ex.claim, ex.evidence)
        for ex in examples
    ]
    return tokenizer(
        texts,
        padding=True,
        truncation=True,
        max_length=max_length,
        return_tensors="pt",
    )
