"""evaluate.py — run all M2 baselines and model variants, compute metrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clindoc.m2.schema import M2Example, M2EvalResult, PerClassMetrics, LABEL_ORDER
from clindoc.m2.metrics import (
    per_class_metrics, macro_f1 as _macro_f1, slice_macro_f1 as _slice_macro_f1,
    discrepancy_recall as _disc_recall, auroc_binary, brier_score, ece, _confusion,
)

LABEL_TO_INT = {l: i for i, l in enumerate(LABEL_ORDER)}


def _build_eval_result(
    model_name: str,
    split: str,
    fold: int | None,
    seed: int | None,
    y_true: list[int],
    y_pred: list[int],
    y_probs: list[list[float]] | None,
    examples: list[M2Example],
) -> M2EvalResult:
    pc_raw = per_class_metrics(y_true, y_pred)
    cm = _confusion(y_true, y_pred)
    disc_idx = LABEL_ORDER.index("DISCREPANCY")

    auroc = None
    bs = None
    ece_val = None
    if y_probs:
        disc_binary = [1 if y == disc_idx else 0 for y in y_true]
        disc_scores = [p[disc_idx] for p in y_probs]
        auroc = auroc_binary(disc_binary, disc_scores)
        bs = brier_score(y_true, y_probs)
        ece_val = ece(y_true, y_probs)

    # Breakdowns
    def _breakdown(key_fn, metric_fn=_macro_f1):
        groups: dict[str, tuple[list[int], list[int]]] = {}
        for ex, yt, yp in zip(examples, y_true, y_pred):
            k = key_fn(ex)
            if k not in groups:
                groups[k] = ([], [])
            groups[k][0].append(yt)
            groups[k][1].append(yp)
        return {k: {"macro_f1": round(metric_fn(v[0], v[1]), 4), "n": len(v[0])} for k, v in groups.items()}

    return M2EvalResult(
        model_name=model_name,
        split=split,
        fold=fold,
        seed=seed,
        macro_f1=round(_macro_f1(y_true, y_pred), 4),
        discrepancy_recall=round(_disc_recall(y_true, y_pred), 4),
        per_class={k: PerClassMetrics(**v) for k, v in pc_raw.items()},
        confusion_matrix=cm,
        auroc_discrepancy=auroc,
        brier_score=bs,
        ece=ece_val,
        by_severity=_breakdown(lambda e: e.severity),
        by_field_type=_breakdown(lambda e: e.field_type, metric_fn=_slice_macro_f1),
        by_claim_type=_breakdown(lambda e: e.claim_type),
    )


def evaluate_embedding_similarity(
    examples: list[M2Example],
    params: dict | float = 0.75,
    embedder: Any | None = None,
    split: str = "cv",
    fold_idx: int | None = None,
) -> M2EvalResult:
    """Evaluate V4 embedding similarity baseline on a given split."""
    from clindoc.m2.baselines import predict_embedding_similarity
    y_true = [LABEL_TO_INT[ex.label.value] for ex in examples]
    y_pred = predict_embedding_similarity(examples, params=params, embedder=embedder)
    return _build_eval_result("embedding_similarity", split, fold_idx, None,
                              y_true, y_pred, None, examples)


def evaluate_zeroshot_nli(
    examples: list[M2Example],
    predictor: Any | None = None,
    split: str = "cv",
    fold_idx: int | None = None,
    scaler: Any | None = None,
) -> M2EvalResult:
    """Evaluate V5 off-the-shelf NLI baseline on a given split."""
    from clindoc.m2.baselines import predict_zeroshot_nli, predict_zeroshot_nli_proba
    y_true = [LABEL_TO_INT[ex.label.value] for ex in examples]
    y_pred = predict_zeroshot_nli(examples, predictor=predictor)
    y_probs = predict_zeroshot_nli_proba(examples, predictor=predictor, scaler=scaler)
    return _build_eval_result("zeroshot_nli", split, fold_idx, None,
                              y_true, y_pred, y_probs, examples)


def evaluate_baselines(
    train_examples: list[M2Example],
    val_examples: list[M2Example],
    fold_idx: int = 0,
    train_embs: list[list[float]] | None = None,
    val_embs: list[list[float]] | None = None,
    embedder: Any | None = None,
    v4_threshold: float | None = None,
    return_v4_params: bool = False,
    nli_predictor: Any | None = None,
    calib_examples: list[M2Example] | None = None,
) -> tuple[list[M2EvalResult], dict, object, object | None] | tuple[list[M2EvalResult], dict, object, object | None, dict | None]:
    from clindoc.m2.baselines import (
        predict_deterministic, train_levenshtein, predict_levenshtein,
        train_tfidf_lr, predict_tfidf_lr, predict_tfidf_lr_proba,
        train_frozen_modernbert_probe, predict_frozen_modernbert_probe,
        predict_frozen_modernbert_probe_proba,
        train_embedding_similarity, predict_embedding_similarity,
        predict_zeroshot_nli, predict_zeroshot_nli_proba,
        calibrate_v5_temperature,
    )
    results = []
    y_true = [LABEL_TO_INT[ex.label.value] for ex in val_examples]

    # B1: Deterministic verifier
    y_pred = predict_deterministic(val_examples)
    results.append(_build_eval_result("deterministic_verifier", "cv", fold_idx, None,
                                       y_true, y_pred, None, val_examples))

    # B2: Levenshtein
    lev_params = train_levenshtein(train_examples)
    y_pred = predict_levenshtein(val_examples, lev_params)
    results.append(_build_eval_result("levenshtein_threshold", "cv", fold_idx, None,
                                       y_true, y_pred, None, val_examples))

    # B3: TF-IDF + LR
    lr_model = train_tfidf_lr(train_examples)
    y_pred = predict_tfidf_lr(lr_model, val_examples)
    y_probs = predict_tfidf_lr_proba(lr_model, val_examples)
    results.append(_build_eval_result("tfidf_logreg", "cv", fold_idx, None,
                                       y_true, y_pred, y_probs, val_examples))

    # V4: Embedding similarity baseline
    v4_params = None
    if embedder is not None:
        v4_params = train_embedding_similarity(train_examples, embedder=embedder, threshold=v4_threshold)
        y_pred = predict_embedding_similarity(val_examples, params=v4_params, embedder=embedder)
        results.append(_build_eval_result("embedding_similarity", "cv", fold_idx, None,
                                           y_true, y_pred, None, val_examples))

    # V5: Off-the-shelf NLI baseline
    if nli_predictor is not None:
        nli_scaler = None
        if calib_examples is not None:
            nli_scaler = calibrate_v5_temperature(calib_examples, predictor=nli_predictor)
        y_pred_nli = predict_zeroshot_nli(val_examples, predictor=nli_predictor)
        y_probs_nli = predict_zeroshot_nli_proba(val_examples, predictor=nli_predictor, scaler=nli_scaler)
        results.append(_build_eval_result("zeroshot_nli", "cv", fold_idx, None,
                                           y_true, y_pred_nli, y_probs_nli, val_examples))

    # B6: Frozen ModernBERT probe
    probe_clf = None
    if train_embs is not None and val_embs is not None:
        y_train = [LABEL_TO_INT[ex.label.value] for ex in train_examples]
        probe_clf = train_frozen_modernbert_probe(train_embs, y_train)
        y_pred = predict_frozen_modernbert_probe(probe_clf, val_embs)
        y_probs = predict_frozen_modernbert_probe_proba(probe_clf, val_embs)
        results.append(_build_eval_result("frozen_modernbert_probe", "cv", fold_idx, None,
                                           y_true, y_pred, y_probs, val_examples))

    if return_v4_params:
        return results, lev_params, lr_model, probe_clf, v4_params
    return results, lev_params, lr_model, probe_clf


def evaluate_baselines_on_test(
    test_examples: list[M2Example],
    lev_params: dict,
    lr_model: object,
    probe_clf: object | None = None,
    test_embs: list[list[float]] | None = None,
    split: str = "ood",
    v4_params: dict | float | None = None,
    embedder: Any | None = None,
    nli_predictor: Any | None = None,
    nli_scaler: Any | None = None,
) -> list[M2EvalResult]:
    from clindoc.m2.baselines import (
        predict_deterministic, predict_levenshtein,
        predict_tfidf_lr, predict_tfidf_lr_proba,
        predict_frozen_modernbert_probe, predict_frozen_modernbert_probe_proba,
        predict_embedding_similarity,
        predict_zeroshot_nli, predict_zeroshot_nli_proba,
    )
    results = []
    y_true = [LABEL_TO_INT[ex.label.value] for ex in test_examples]

    # B1: Deterministic verifier
    y_pred = predict_deterministic(test_examples)
    results.append(_build_eval_result("deterministic_verifier", split, None, None,
                                       y_true, y_pred, None, test_examples))

    # B2: Levenshtein
    y_pred = predict_levenshtein(test_examples, lev_params)
    results.append(_build_eval_result("levenshtein_threshold", split, None, None,
                                       y_true, y_pred, None, test_examples))

    # B3: TF-IDF + LR
    y_pred = predict_tfidf_lr(lr_model, test_examples)
    y_probs = predict_tfidf_lr_proba(lr_model, test_examples)
    results.append(_build_eval_result("tfidf_logreg", split, None, None,
                                       y_true, y_pred, y_probs, test_examples))

    # V4: Embedding similarity baseline
    if v4_params is not None:
        y_pred = predict_embedding_similarity(test_examples, params=v4_params, embedder=embedder)
        results.append(_build_eval_result("embedding_similarity", split, None, None,
                                           y_true, y_pred, None, test_examples))

    # V5: Off-the-shelf NLI baseline
    if nli_predictor is not None:
        y_pred_nli = predict_zeroshot_nli(test_examples, predictor=nli_predictor)
        y_probs_nli = predict_zeroshot_nli_proba(test_examples, predictor=nli_predictor, scaler=nli_scaler)
        results.append(_build_eval_result("zeroshot_nli", split, None, None,
                                           y_true, y_pred_nli, y_probs_nli, test_examples))

    # B6: Frozen ModernBERT probe
    if probe_clf is not None and test_embs is not None:
        y_pred = predict_frozen_modernbert_probe(probe_clf, test_embs)
        y_probs = predict_frozen_modernbert_probe_proba(probe_clf, test_embs)
        results.append(_build_eval_result("frozen_modernbert_probe", split, None, None,
                                           y_true, y_pred, y_probs, test_examples))

    return results


def evaluate_model_on_split(
    model,
    tokenizer,
    examples: list[M2Example],
    model_name: str,
    split: str,
    fold: int | None,
    seed: int | None,
    config,
    device: str,
    aux_mode: str = "full",
) -> M2EvalResult:
    """Evaluate a trained ClinicalVerifier on a given split."""
    import torch
    from clindoc.m2.features import encode_aux_batch
    from clindoc.m2.model import format_input

    model.eval()
    y_true, y_pred, y_probs_list = [], [], []
    batch_size = max(config.batch_size, 32)

    with torch.no_grad():
        for start in range(0, len(examples), batch_size):
            batch = examples[start: start + batch_size]
            if not batch:
                continue
            texts = [format_input(ex.field_type, ex.claim, ex.evidence) for ex in batch]
            encoded = tokenizer(texts, padding=True, truncation=True,
                                max_length=config.max_seq_length, return_tensors="pt")
            input_ids = encoded["input_ids"].to(device)
            attn_mask = encoded["attention_mask"].to(device)

            aux_feat = None
            if aux_mode != "none":
                raw_aux = encode_aux_batch(
                    [ex.ocr_confidence for ex in batch],
                    [ex.field_type for ex in batch],
                    [ex.doc_template for ex in batch],
                    mode=aux_mode,
                )
                if raw_aux is not None:
                    aux_feat = raw_aux.to(device)

            if device == "cuda":
                with torch.amp.autocast("cuda", dtype=torch.float16):
                    logits = model(input_ids, attn_mask, aux_features=aux_feat)
            else:
                logits = model(input_ids, attn_mask, aux_features=aux_feat)

            probs = torch.softmax(logits, dim=-1).cpu().tolist()
            preds = [p.index(max(p)) for p in probs]
            labels = [LABEL_TO_INT[ex.label.value] for ex in batch]
            y_true.extend(labels)
            y_pred.extend(preds)
            y_probs_list.extend(probs)

    return _build_eval_result(model_name, split, fold, seed,
                               y_true, y_pred, y_probs_list, examples)
