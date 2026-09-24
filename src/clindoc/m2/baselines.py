"""All 7 M2 baselines for clinical claim/evidence verification.

1. Deterministic verifier (M0 wrapper)
2. Levenshtein/lexical threshold (threshold selected from training data)
3. TF-IDF + LogisticRegression
4. Frozen ModernBERT + linear head
5. Fine-tuned ModernBERT text-only
6. Fine-tuned + OCR confidence
7. Full model (text + OCR confidence + field_type + doc_template)
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable

from clindoc.m2.schema import M2Example, VerificationLabel, LABEL_ORDER


LABEL_TO_INT = {l: i for i, l in enumerate(LABEL_ORDER)}
INT_TO_LABEL = {i: l for l, i in LABEL_TO_INT.items()}


# ─── Baseline 1: Deterministic Verifier Wrapper ───────────────────────────────

def _map_verify_status(status: str) -> str:
    """Map M0 VerificationStatus → M2 VerificationLabel."""
    mapping = {
        "VERIFIED": "SUPPORTED",
        "DISCREPANCY": "DISCREPANCY",
        "MISSING_SOURCE_EVIDENCE": "INSUFFICIENT_EVIDENCE",
        "UNCHECKED": "INSUFFICIENT_EVIDENCE",
    }
    return mapping.get(status, "INSUFFICIENT_EVIDENCE")


def predict_deterministic(examples: list[M2Example]) -> list[int]:
    """Run deterministic verifier on each example.

    - If OCR confidence < 0.5 or evidence is too short (< 3 chars) -> INSUFFICIENT_EVIDENCE
    - Check token/value matching between claim and evidence:
      Extract significant tokens (numbers and words) from claim. If any critical token
      (especially numbers) in claim is absent from evidence -> DISCREPANCY
    - If evidence matches claim with high token overlap -> SUPPORTED, else DISCREPANCY
    """
    import re
    preds = []
    for ex in examples:
        ev = ex.evidence.strip()
        claim = ex.claim.strip()
        if ex.ocr_confidence < 0.5 or len(ev) < 3:
            preds.append(LABEL_TO_INT["INSUFFICIENT_EVIDENCE"])
            continue

        ev_lower = ev.lower()
        claim_lower = claim.lower()

        # Check numeric tokens in claim: if a number is in claim but not in evidence -> DISCREPANCY
        claim_nums = re.findall(r"\b\d+(?:\.\d+)?\b", claim_lower)
        ev_nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", ev_lower))
        num_mismatch = any(num not in ev_nums for num in claim_nums)
        if num_mismatch:
            preds.append(LABEL_TO_INT["DISCREPANCY"])
            continue

        # Extract words (> 2 chars) from claim
        claim_words = [w for w in re.findall(r"\b[a-z]{3,}\b", claim_lower) if w not in {"the", "and", "for"}]
        if not claim_words:
            preds.append(LABEL_TO_INT["SUPPORTED"] if claim_lower in ev_lower or ev_lower in claim_lower else LABEL_TO_INT["DISCREPANCY"])
            continue

        missing_words = [w for w in claim_words if w not in ev_lower]
        if len(missing_words) / len(claim_words) > 0.2:
            preds.append(LABEL_TO_INT["DISCREPANCY"])
        else:
            preds.append(LABEL_TO_INT["SUPPORTED"])
    return preds


# ─── Baseline 2: Levenshtein Threshold ────────────────────────────────────────

def train_levenshtein(train_examples: list[M2Example]) -> dict:
    """Select optimal Levenshtein threshold from training data."""
    from Levenshtein import ratio
    y_true = [LABEL_TO_INT[ex.label.value] for ex in train_examples]
    # Try thresholds
    best_f1, best_thr = 0.0, 0.5
    for thr in [i / 20.0 for i in range(1, 20)]:
        preds = []
        for ex in train_examples:
            ev = ex.evidence.strip()
            sim = ratio(ex.claim.lower(), ev.lower())
            if ex.ocr_confidence < 0.5 or len(ev) < 3:
                preds.append(LABEL_TO_INT["INSUFFICIENT_EVIDENCE"])
            elif sim >= thr:
                preds.append(LABEL_TO_INT["SUPPORTED"])
            else:
                preds.append(LABEL_TO_INT["DISCREPANCY"])
        from clindoc.m2.metrics import macro_f1
        f1 = macro_f1(y_true, preds)
        if f1 > best_f1:
            best_f1, best_thr = f1, thr
    return {"threshold": best_thr, "train_macro_f1": best_f1}


def predict_levenshtein(examples: list[M2Example], params: dict) -> list[int]:
    from Levenshtein import ratio
    thr = params["threshold"]
    preds = []
    for ex in examples:
        ev = ex.evidence.strip()
        sim = ratio(ex.claim.lower(), ev.lower())
        if ex.ocr_confidence < 0.5 or len(ev) < 3:
            preds.append(LABEL_TO_INT["INSUFFICIENT_EVIDENCE"])
        elif sim >= thr:
            preds.append(LABEL_TO_INT["SUPPORTED"])
        else:
            preds.append(LABEL_TO_INT["DISCREPANCY"])
    return preds


# ─── Baseline 3: TF-IDF + LogisticRegression ──────────────────────────────────

def train_tfidf_lr(train_examples: list[M2Example], seed: int = 42) -> object:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    X = [ex.claim + " [SEP] " + ex.evidence for ex in train_examples]
    y = [LABEL_TO_INT[ex.label.value] for ex in train_examples]
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), max_features=10000)),
        ("lr", LogisticRegression(max_iter=1000, C=1.0, random_state=seed, solver="lbfgs")),
    ])
    pipe.fit(X, y)
    return pipe


def predict_tfidf_lr(model: object, examples: list[M2Example]) -> list[int]:
    X = [ex.claim + " [SEP] " + ex.evidence for ex in examples]
    return list(model.predict(X))


def predict_tfidf_lr_proba(model: object, examples: list[M2Example]) -> list[list[float]]:
    X = [ex.claim + " [SEP] " + ex.evidence for ex in examples]
    proba = model.predict_proba(X)
    return [list(row) for row in proba]


# ─── Baseline 4: Frozen BioClinical ModernBERT Embeddings + Linear Probe ───────

def extract_frozen_embeddings(
    examples: list[M2Example],
    model_id: str = "thomas-sounack/BioClinical-ModernBERT-base",
    batch_size: int = 32,
    device: str = "cuda",
) -> list[list[float]]:
    import torch
    import numpy as np
    from transformers import AutoModel, AutoTokenizer
    from clindoc.m2.model import format_input

    dev = device if torch.cuda.is_available() and device == "cuda" else "cpu"
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    encoder = AutoModel.from_pretrained(model_id).to(dev)
    encoder.eval()

    all_embs = []
    with torch.no_grad():
        for i in range(0, len(examples), batch_size):
            batch = examples[i: i + batch_size]
            texts = [format_input(ex.field_type, ex.claim, ex.evidence) for ex in batch]
            enc = tokenizer(texts, padding=True, truncation=True, max_length=256, return_tensors="pt")
            input_ids = enc["input_ids"].to(dev)
            mask = enc["attention_mask"].to(dev)
            if dev == "cuda":
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    out = encoder(input_ids=input_ids, attention_mask=mask)
            else:
                out = encoder(input_ids=input_ids, attention_mask=mask)
            m = mask.unsqueeze(-1).float()
            pooled = (out.last_hidden_state * m).sum(dim=1) / m.sum(dim=1).clamp(min=1e-9)
            all_embs.extend(pooled.cpu().float().tolist())
    return all_embs


def train_frozen_modernbert_probe(
    X_train_embs: list[list[float]],
    y_train: list[int],
    seed: int = 42,
) -> object:
    from sklearn.linear_model import LogisticRegression
    clf = LogisticRegression(max_iter=1000, C=1.0, random_state=seed, solver="lbfgs")
    clf.fit(X_train_embs, y_train)
    return clf


def predict_frozen_modernbert_probe(clf: object, X_embs: list[list[float]]) -> list[int]:
    return list(clf.predict(X_embs))


def predict_frozen_modernbert_probe_proba(clf: object, X_embs: list[list[float]]) -> list[list[float]]:
    proba = clf.predict_proba(X_embs)
    return [list(row) for row in proba]


LABEL_ORDER = ["SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"]
