"""Metrics for OCR evaluation and decoupled OCR vs extraction failure analysis."""

from difflib import SequenceMatcher
from typing import Any

from clindoc.ocr.schema import DocumentOCRRecord


def levenshtein_distance(s1: str, s2: str) -> int:
    """Compute exact Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    r0 = list(range(len(s2) + 1))
    r1 = [0] * (len(s2) + 1)

    for i, c1 in enumerate(s1):
        r1[0] = i + 1
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            r1[j + 1] = min(r1[j] + 1, r0[j + 1] + 1, r0[j] + cost)
        r0, r1 = r1, r0

    return r0[len(s2)]


def compute_cer(reference: str, hypothesis: str) -> float:
    """Compute Character Error Rate (CER = edit_dist / len(ref))."""
    if not reference and not hypothesis:
        return 0.0
    if not reference:
        return 1.0
    dist = levenshtein_distance(reference, hypothesis)
    return min(1.0, dist / len(reference))


def compute_wer(reference: str, hypothesis: str) -> float:
    """Compute Word Error Rate (WER)."""
    r_words = reference.split()
    h_words = hypothesis.split()
    if not r_words and not h_words:
        return 0.0
    if not r_words:
        return 1.0

    # Token-level Levenshtein
    matcher = SequenceMatcher(None, r_words, h_words)
    errors = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            errors += max(i2 - i1, j2 - j1)
        elif tag == "delete":
            errors += i2 - i1
        elif tag == "insert":
            errors += j2 - j1

    return min(1.0, errors / len(r_words))


def compute_token_f1(reference: str, hypothesis: str) -> dict[str, float]:
    """Compute token-level precision, recall, and F1."""
    r_tokens = [t.lower() for t in reference.split()]
    h_tokens = [t.lower() for t in hypothesis.split()]

    if not r_tokens and not h_tokens:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not r_tokens or not h_tokens:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    # Count matching tokens with repetition tracking
    from collections import Counter
    r_cnt = Counter(r_tokens)
    h_cnt = Counter(h_tokens)

    overlap = sum(min(r_cnt[t], h_cnt[t]) for t in h_cnt)
    p = overlap / len(h_tokens) if h_tokens else 0.0
    r = overlap / len(r_tokens) if r_tokens else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0.0 else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def compute_ocr_quality_metrics(
    clean_record: DocumentOCRRecord,
    degraded_record: DocumentOCRRecord,
) -> dict[str, Any]:
    """Compare clean source OCR against degraded OCR to evaluate OCR performance."""
    clean_full_text = " ".join(l.text.strip() for l in clean_record.lines)
    deg_full_text = " ".join(l.text.strip() for l in degraded_record.lines)

    cer = compute_cer(clean_full_text, deg_full_text)
    wer = compute_wer(clean_full_text, deg_full_text)
    token_metrics = compute_token_f1(clean_full_text, deg_full_text)

    mean_conf = (
        sum(l.confidence for l in degraded_record.lines) / len(degraded_record.lines)
        if degraded_record.lines
        else 0.0
    )

    return {
        "cer": round(cer, 4),
        "wer": round(wer, 4),
        "token_precision": token_metrics["precision"],
        "token_recall": token_metrics["recall"],
        "token_f1": token_metrics["f1"],
        "mean_confidence": round(mean_conf, 4),
        "clean_line_count": len(clean_record.lines),
        "degraded_line_count": len(degraded_record.lines),
    }


def classify_field_failure(
    target_token: str,
    is_field_correct: bool,
    degraded_ocr_text: str,
) -> str:
    """Classify whether a field extraction discrepancy was caused by OCR or downstream extraction.
    
    Returns:
        'SUCCESS': Field extracted correctly.
        'OCR_FAILURE': The target text/token was absent or illegible in the degraded OCR stream.
        'EXTRACTION_FAILURE': The target text was present in the OCR stream, but downstream extraction failed.
    """
    if is_field_correct:
        return "SUCCESS"

    # Check if target token or string was captured in degraded OCR text
    clean_target = str(target_token).strip().lower()
    if clean_target and clean_target in degraded_ocr_text.lower():
        return "EXTRACTION_FAILURE"
    return "OCR_FAILURE"
