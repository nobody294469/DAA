"""All M2 baselines for clinical claim/evidence verification.

1. Deterministic verifier (M0 wrapper) - V1
2. Levenshtein/lexical threshold (threshold selected from training data) - V2
3. TF-IDF + LogisticRegression - V3
4. Embedding-similarity baseline (SapBERT/BioLORD cosine, zero training) - V4
5. Off-the-shelf NLI baseline (PubMedBERT-MNLI-MedNLI) - V5
6. Frozen ModernBERT + linear probe (auxiliary baseline)
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Callable, Optional

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


# ─── Baseline 4 (V4): Embedding Similarity Baseline (SapBERT/BioLORD) ────────

def _compute_pair_similarity(embedder: Any, s1: str, s2: str) -> float:
    """Compute similarity between two strings using a SemanticEmbedder."""
    if hasattr(embedder, "similarity"):
        return float(embedder.similarity(s1, s2))
    scores = embedder.similarity_scores(s1, [s2])
    return float(scores[0]) if scores else 0.0


def get_default_embedder(embedder: Optional[Any] = None) -> Any:
    """Return the specified embedder, or SapBERT if available, falling back to NGramProxyEmbedder."""
    if embedder is not None:
        return embedder
    from clindoc.normalize.hybrid import SapBERTEmbedder, NGramProxyEmbedder
    sapbert = SapBERTEmbedder()
    if sapbert.is_available():
        return sapbert
    return NGramProxyEmbedder()


def train_embedding_similarity(
    train_examples: list[M2Example],
    embedder: Optional[Any] = None,
    threshold: Optional[float] = None,
) -> dict:
    """Fit/select optimal similarity threshold from training data or lock specified threshold.

    Authority: PROJECT_SPEC §22.2 (V4), DEC-007.
    'Zero training' means no embedding model parameters are tuned.
    Like Baseline 2 (Levenshtein), the decision threshold is selected on the
    training fold to maximize macro-F1, or fixed to a preset value if provided.
    Never tuned on OOD or test splits.
    """
    if threshold is not None:
        return {"threshold": float(threshold), "train_macro_f1": None}

    embedder = get_default_embedder(embedder)
    y_true = [LABEL_TO_INT[ex.label.value] for ex in train_examples]

    # Precompute similarities to avoid re-embedding per threshold step
    sims = []
    guarded = []
    for ex in train_examples:
        ev = ex.evidence.strip()
        if ex.ocr_confidence < 0.5 or len(ev) < 3:
            guarded.append(True)
            sims.append(0.0)
        else:
            guarded.append(False)
            sims.append(_compute_pair_similarity(embedder, ex.claim.strip(), ev))

    best_f1, best_thr = 0.0, 0.75
    from clindoc.m2.metrics import macro_f1
    for thr in [i / 20.0 for i in range(1, 20)]:
        preds = []
        for is_g, s in zip(guarded, sims):
            if is_g:
                preds.append(LABEL_TO_INT["INSUFFICIENT_EVIDENCE"])
            elif s >= thr:
                preds.append(LABEL_TO_INT["SUPPORTED"])
            else:
                preds.append(LABEL_TO_INT["DISCREPANCY"])
        f1 = macro_f1(y_true, preds)
        if f1 > best_f1:
            best_f1, best_thr = f1, thr

    return {"threshold": best_thr, "train_macro_f1": best_f1}


def predict_embedding_similarity(
    examples: list[M2Example],
    params: dict | float = 0.75,
    embedder: Optional[Any] = None,
) -> list[int]:
    """Predict verification labels using embedding cosine similarity.

    Authority: PROJECT_SPEC §22.2 (V4), DEC-007.
    - If OCR confidence < 0.5 or evidence length < 3 -> INSUFFICIENT_EVIDENCE
    - Else compute cosine similarity between claim and evidence:
        similarity >= threshold -> SUPPORTED
        otherwise -> DISCREPANCY

    Args:
        examples: List of M2Examples.
        params: Dict containing "threshold" key, or float threshold.
        embedder: SemanticEmbedder instance (default: SapBERT with NGram fallback).

    Returns:
        List of integer label predictions adhering to LABEL_ORDER.
    """
    if isinstance(params, dict):
        thr = float(params.get("threshold", 0.75))
    else:
        thr = float(params)

    embedder = get_default_embedder(embedder)
    preds = []
    for ex in examples:
        ev = ex.evidence.strip()
        if ex.ocr_confidence < 0.5 or len(ev) < 3:
            preds.append(LABEL_TO_INT["INSUFFICIENT_EVIDENCE"])
        else:
            sim = _compute_pair_similarity(embedder, ex.claim.strip(), ev)
            if sim >= thr:
                preds.append(LABEL_TO_INT["SUPPORTED"])
            else:
                preds.append(LABEL_TO_INT["DISCREPANCY"])
    return preds


# ─── Baseline 5 (V5): Off-the-Shelf NLI Baseline (PubMedBERT-MNLI-MedNLI) ─────

# Native NLI label indices for pritamdeka/PubMedBERT-MNLI-MedNLI
NATIVE_NLI_CONTRADICTION = 0
NATIVE_NLI_ENTAILMENT = 1
NATIVE_NLI_NEUTRAL = 2

# Mapping from native NLI integer label to project integer label in LABEL_ORDER
# LABEL_ORDER = ["SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"]
NATIVE_TO_PROJECT_INT = {
    NATIVE_NLI_ENTAILMENT: LABEL_TO_INT["SUPPORTED"],           # 1 -> 0
    NATIVE_NLI_CONTRADICTION: LABEL_TO_INT["DISCREPANCY"],      # 0 -> 1
    NATIVE_NLI_NEUTRAL: LABEL_TO_INT["INSUFFICIENT_EVIDENCE"],  # 2 -> 2
}


class MockNLIPredictor:
    """Deterministic mock NLI predictor for unit testing and offline execution.

    Returns 3-dimensional native NLI logits: [contradiction, entailment, neutral].
    """

    def __init__(self, default_native_label: int = NATIVE_NLI_ENTAILMENT, confidence: float = 0.95) -> None:
        self.default_native_label = default_native_label
        self.confidence = confidence

    def predict_pair_logits(self, premise: str, hypothesis: str) -> list[float]:
        """Generate synthetic native logits [contradiction, entailment, neutral]."""
        import math
        p_other = max((1.0 - self.confidence) / 2.0, 1e-6)
        probs = [p_other, p_other, p_other]
        probs[self.default_native_label] = self.confidence
        return [float(math.log(p)) for p in probs]

    def predict_batch_logits(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        return [self.predict_pair_logits(premise, hypothesis) for premise, hypothesis in pairs]


class PubMedBERTNLIPredictor:
    """Off-the-shelf NLI predictor using pritamdeka/PubMedBERT-MNLI-MedNLI.

    Authority: PROJECT_SPEC §22.2 (V5), DEC-locked model selection.
    Frozen sequence classification model fine-tuned on MNLI + MedNLI.
    Outputs native logits for [contradiction, entailment, neutral].
    """

    def __init__(
        self,
        model_name: str = "pritamdeka/PubMedBERT-MNLI-MedNLI",
        device: Optional[str] = None,
        local_files_only: bool = True,
        batch_size: int = 32,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.local_files_only = local_files_only
        self.batch_size = batch_size
        self._tokenizer = None
        self._model = None
        self._initialized = False

    def _lazy_init(self) -> bool:
        if self._initialized:
            return self._model is not None
        self._initialized = True
        try:
            import os
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            allow_download = os.environ.get("ALLOW_NLI_DOWNLOAD", "0").lower() in ("1", "true")
            local_only = self.local_files_only and not allow_download

            if self.device is None:
                self.device = "cuda" if torch.cuda.is_available() else "cpu"

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                local_files_only=local_only,
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.model_name,
                local_files_only=local_only,
            ).to(self.device)
            self._model.eval()
            return True
        except Exception:
            self._model = None
            self._tokenizer = None
            return False

    def is_available(self) -> bool:
        return self._lazy_init()

    def predict_batch_logits(self, pairs: list[tuple[str, str]]) -> list[list[float]]:
        """Compute native NLI logits [contradiction, entailment, neutral] for (premise, hypothesis) pairs."""
        if not pairs:
            return []
        if not self._lazy_init():
            raise RuntimeError(
                f"PubMedBERT NLI model '{self.model_name}' could not be initialized. "
                "Ensure transformers and torch are installed and weights are accessible, "
                "or pass an explicit MockNLIPredictor for offline/testing environments."
            )
        import torch

        all_logits = []
        with torch.no_grad():
            for i in range(0, len(pairs), self.batch_size):
                batch_pairs = pairs[i:i + self.batch_size]
                premises = [p[0] for p in batch_pairs]
                hypotheses = [p[1] for p in batch_pairs]

                inputs = self._tokenizer(
                    text=premises,
                    text_pair=hypotheses,
                    padding=True,
                    truncation=True,
                    max_length=256,
                    return_tensors="pt",
                ).to(self.device)

                outputs = self._model(**inputs)
                batch_logits = outputs.logits.cpu().float().tolist()
                all_logits.extend(batch_logits)

        return all_logits

    def predict_pair_logits(self, premise: str, hypothesis: str) -> list[float]:
        scores = self.predict_batch_logits([(premise, hypothesis)])
        return scores[0] if scores else [0.0, 0.0, 0.0]


def get_default_nli_predictor(predictor: Optional[Any] = None) -> Any:
    """Return provided NLI predictor, or PubMedBERT if available, falling back to MockNLIPredictor."""
    if predictor is not None:
        return predictor
    pubmed_nli = PubMedBERTNLIPredictor()
    if pubmed_nli.is_available():
        return pubmed_nli
    return MockNLIPredictor()


def format_nli_hypothesis(claim: str, field_type: str = "") -> str:
    """Format claim as hypothesis for NLI cross-encoder."""
    claim_str = claim.strip()
    if field_type:
        return f"{field_type}: {claim_str}"
    return claim_str


def _reorder_native_nli_to_project(native_probs_or_logits: list[float]) -> list[float]:
    """Reorder native [contradiction(0), entailment(1), neutral(2)]
    to project LABEL_ORDER [SUPPORTED(0), DISCREPANCY(1), INSUFFICIENT_EVIDENCE(2)].

    project[0] (SUPPORTED) = native[1] (entailment)
    project[1] (DISCREPANCY) = native[0] (contradiction)
    project[2] (INSUFFICIENT_EVIDENCE) = native[2] (neutral)
    """
    contra = native_probs_or_logits[0]
    entail = native_probs_or_logits[1]
    neutral = native_probs_or_logits[2]
    return [entail, contra, neutral]


def predict_zeroshot_nli(
    examples: list[M2Example],
    predictor: Optional[Any] = None,
) -> list[int]:
    """Predict verification labels using off-the-shelf NLI baseline (V5).

    Authority: PROJECT_SPEC §22.2 (V5).
    Locked Model: pritamdeka/PubMedBERT-MNLI-MedNLI.

    Evidence Guards:
    - If OCR confidence < 0.5 or evidence length < 3 -> INSUFFICIENT_EVIDENCE

    Mapping (native softmax argmax):
    - entailment (native 1) -> SUPPORTED (project 0)
    - contradiction (native 0) -> DISCREPANCY (project 1)
    - neutral (native 2) -> INSUFFICIENT_EVIDENCE (project 2)
    """
    predictor = get_default_nli_predictor(predictor)
    preds: list[int] = []

    active_indices: list[int] = []
    active_pairs: list[tuple[str, str]] = []

    for i, ex in enumerate(examples):
        ev = ex.evidence.strip()
        if ex.ocr_confidence < 0.5 or len(ev) < 3:
            preds.append(LABEL_TO_INT["INSUFFICIENT_EVIDENCE"])
        else:
            preds.append(-1)  # Placeholder
            active_indices.append(i)
            hyp = format_nli_hypothesis(ex.claim, ex.field_type)
            active_pairs.append((ev, hyp))

    if active_pairs:
        if hasattr(predictor, "predict_batch_logits"):
            logits_list = predictor.predict_batch_logits(active_pairs)
        else:
            logits_list = [predictor.predict_pair_logits(p[0], p[1]) for p in active_pairs]

        for orig_idx, native_logits in zip(active_indices, logits_list):
            proj_logits = _reorder_native_nli_to_project(native_logits)
            best_idx = int(proj_logits.index(max(proj_logits)))
            preds[orig_idx] = best_idx

    return preds


def predict_zeroshot_nli_proba(
    examples: list[M2Example],
    predictor: Optional[Any] = None,
    scaler: Optional[Any] = None,
) -> list[list[float]]:
    """Compute aligned 3-class probabilities [SUPPORTED, DISCREPANCY, INSUFFICIENT_EVIDENCE] for V5.

    If a fitted TemperatureScaler (DEC-009) is provided, scales project logits before softmax.
    """
    import numpy as np
    predictor = get_default_nli_predictor(predictor)
    probs_list: list[list[float]] = []

    active_indices: list[int] = []
    active_pairs: list[tuple[str, str]] = []

    for i, ex in enumerate(examples):
        ev = ex.evidence.strip()
        if ex.ocr_confidence < 0.5 or len(ev) < 3:
            # Guarded: 100% confidence in INSUFFICIENT_EVIDENCE
            probs_list.append([0.0, 0.0, 1.0])
        else:
            probs_list.append([])
            active_indices.append(i)
            hyp = format_nli_hypothesis(ex.claim, ex.field_type)
            active_pairs.append((ev, hyp))

    if active_pairs:
        if hasattr(predictor, "predict_batch_logits"):
            logits_list = predictor.predict_batch_logits(active_pairs)
        else:
            logits_list = [predictor.predict_pair_logits(p[0], p[1]) for p in active_pairs]

        project_logits = [_reorder_native_nli_to_project(logits) for logits in logits_list]
        arr = np.array(project_logits, dtype=np.float32)

        if scaler is not None and getattr(scaler, "is_fitted", False):
            scaled_probs = scaler.predict_proba(arr)
        else:
            exp_arr = np.exp(arr - np.max(arr, axis=-1, keepdims=True))
            scaled_probs = exp_arr / np.sum(exp_arr, axis=-1, keepdims=True)

        for orig_idx, p in zip(active_indices, scaled_probs.tolist()):
            probs_list[orig_idx] = [float(v) for v in p]

    return probs_list


def calibrate_v5_temperature(
    calib_examples: list[M2Example],
    predictor: Optional[Any] = None,
) -> Any:
    """Fit a TemperatureScaler for V5 strictly on the held-out calibration split (DEC-009).

    Never tunes on training, validation, or OOD splits.
    """
    from clindoc.m2.calibration import TemperatureScaler
    import numpy as np

    predictor = get_default_nli_predictor(predictor)
    valid_examples = []
    valid_labels = []

    for ex in calib_examples:
        ev = ex.evidence.strip()
        if ex.ocr_confidence >= 0.5 and len(ev) >= 3:
            valid_examples.append(ex)
            valid_labels.append(LABEL_TO_INT[ex.label.value])

    if not valid_examples:
        scaler = TemperatureScaler(init_temperature=1.0)
        scaler.is_fitted = True
        return scaler

    pairs = [(ex.evidence.strip(), format_nli_hypothesis(ex.claim, ex.field_type)) for ex in valid_examples]
    if hasattr(predictor, "predict_batch_logits"):
        logits_list = predictor.predict_batch_logits(pairs)
    else:
        logits_list = [predictor.predict_pair_logits(p[0], p[1]) for p in pairs]

    proj_logits = np.array([_reorder_native_nli_to_project(l) for l in logits_list], dtype=np.float32)
    scaler = TemperatureScaler(init_temperature=1.0)
    scaler.fit(proj_logits, np.array(valid_labels, dtype=np.int64))
    return scaler


# ─── Baseline 6: Frozen BioClinical ModernBERT Embeddings + Linear Probe ───────

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
