#!/usr/bin/env python3
"""Rigorous Apples-to-Apples Evaluation on Frozen 200-Record Glaucoma Subset.

Authority: User Request / DEC-004 / DEC-019
Location: external_eval/eval_frozen_200.py

Evaluates on EXACTLY the same 200 records (150 positive, 50 negative):
1. Standalone GLiNER-BioMed
2. DEC-004 Cascade (DeterministicExtractor + GLiNERFallbackExtractor)

Ground Truth Conceptual Separation:
- med.name: from DRUG
- med.dose: from DOSAGE and STRENGTH
- med.frequency: from FREQUENCY
- med.duration: from DURATION
- med.route: genuine physiological routes only (excludes OU, OD, OS)
- ocular.laterality: OU, OD, OS, BOTH EYES, LEFT EYE, RIGHT EYE, IN EYE (reported separately)
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from adapters.glaucoma_text_adapter import snippet_to_document_text
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.evaluate import normalize_field_value
from clindoc.extract.gliner_fallback import SubprocessGLiNERProvider

# Laterality terms to separate from physiological route
LATERALITY_TERMS = {
    "OU", "OD", "OS", "BOTH EYES", "LEFT EYE", "RIGHT EYE",
    "OU-", "LE", "RE", "IN EYE", "BOTHEYES", "BOTHEYES",
}

LABEL_MAP_CLEAN = {
    "DRUG": "med.name",
    "FREQUENCY": "med.frequency",
    "DOSAGE": "med.dose",
    "STRENGTH": "med.dose",
    "DURATION": "med.duration",
}

PRED_LABEL_MAP_GLINER = {
    "medication": "med.name",
    "dosage": "med.dose",
    "route": "med.route",
    "frequency": "med.frequency",
    # dosage form is NOT mapped to route per audit findings
}


def calc_prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1


def evaluate_predictions(
    records: list[dict[str, Any]],
    preds_per_record: list[list[dict[str, Any]]],
    fields: list[str],
) -> tuple[dict[str, dict[str, int]], dict[str, tuple[float, float, float]], tuple[float, float, float], dict[str, list[dict]]]:
    """Bipartite canonical value matching per field across all records."""
    counts = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    errors = defaultdict(list)

    for r, preds in zip(records, preds_per_record):
        gt_list = r["parsed_ground_truth"]

        for fld in fields:
            fgts = [g for g in gt_list if g["field"] == fld]
            fpreds = [p for p in preds if p["field"] == fld]

            matched_g = set()
            matched_p = set()

            for gi, g in enumerate(fgts):
                gn = normalize_field_value(fld, g["text"]) if fld.startswith("med.") else g["text"].lower().strip()
                for pi, p in enumerate(fpreds):
                    if pi in matched_p:
                        continue
                    pn = normalize_field_value(fld, p["text"]) if fld.startswith("med.") else p["text"].lower().strip()
                    if gn and pn and (gn == pn or gn in pn or pn in gn):
                        matched_g.add(gi)
                        matched_p.add(pi)
                        break

            tp = len(matched_p)
            fp = len(fpreds) - len(matched_p)
            fn = len(fgts) - len(matched_g)

            counts[fld]["tp"] += tp
            counts[fld]["fp"] += fp
            counts[fld]["fn"] += fn

            # Collect errors
            for gi, g in enumerate(fgts):
                if gi not in matched_g and len(errors[f"{fld}_FN"]) < 5:
                    errors[f"{fld}_FN"].append({
                        "record_id": r["record_id"],
                        "text": r["text"],
                        "gt": g["text"],
                        "candidates": [p["text"] for p in fpreds],
                    })
            for pi, p in enumerate(fpreds):
                if pi not in matched_p and len(errors[f"{fld}_FP"]) < 5:
                    errors[f"{fld}_FP"].append({
                        "record_id": r["record_id"],
                        "text": r["text"],
                        "pred": p["text"],
                        "gt_available": [g["text"] for g in fgts],
                    })

    metrics = {fld: calc_prf(counts[fld]["tp"], counts[fld]["fp"], counts[fld]["fn"]) for fld in fields}
    
    # Micro average across standard clinical fields (excluding optional diagnostic ocular.laterality)
    clin_fields = [f for f in fields if f.startswith("med.")]
    tot_tp = sum(counts[f]["tp"] for f in clin_fields)
    tot_fp = sum(counts[f]["fp"] for f in clin_fields)
    tot_fn = sum(counts[f]["fn"] for f in clin_fields)
    micro = calc_prf(tot_tp, tot_fp, tot_fn)

    return counts, metrics, micro, errors


def main():
    subset_path = REPO_ROOT / "external_eval" / "glaucoma_eval_subset_200.json"
    if not subset_path.exists():
        raise FileNotFoundError(f"Subset file not found: {subset_path}")

    subset_data = json.loads(subset_path.read_text(encoding="utf-8"))
    records = subset_data["records"]
    print(f"Loaded frozen subset: {len(records)} records ({subset_data['positive_records_count']} positive, {subset_data['negative_records_count']} negative).")

    # 1. Parse Ground Truth with strict conceptual separation of Route vs Laterality
    total_gt_counts = Counter()
    for r in records:
        parsed_gt = []
        for s, e, lbl in r["labels"]:
            raw_text = r["text"][s:e].strip()
            if not raw_text:
                continue

            if lbl == "ROUTE":
                val_upper = raw_text.upper()
                if val_upper in LATERALITY_TERMS or any(term in val_upper for term in ["EYE", "OU", "OD", "OS"]):
                    parsed_gt.append({"field": "ocular.laterality", "orig_label": lbl, "text": raw_text, "start": s, "end": e})
                    total_gt_counts["ocular.laterality"] += 1
                else:
                    parsed_gt.append({"field": "med.route", "orig_label": lbl, "text": raw_text, "start": s, "end": e})
                    total_gt_counts["med.route"] += 1
            elif lbl in LABEL_MAP_CLEAN:
                fld = LABEL_MAP_CLEAN[lbl]
                parsed_gt.append({"field": fld, "orig_label": lbl, "text": raw_text, "start": s, "end": e})
                total_gt_counts[fld] += 1
            else:
                total_gt_counts[f"excluded_{lbl}"] += 1

        r["parsed_ground_truth"] = parsed_gt

    print("\nGround Truth Summary in Frozen 200 Subset:")
    for fld, cnt in total_gt_counts.most_common():
        print(f"  {fld:20}: {cnt}")

    # 2. Run Standalone GLiNER Inference
    provider = SubprocessGLiNERProvider()
    texts = [r["text"] for r in records]
    print(f"\nRunning standalone GLiNER-BioMed inference across {len(texts)} records...")
    t0 = time.time()
    raw_gliner_preds = provider.predict_batch(texts)
    t_gliner = time.time() - t0
    print(f"GLiNER inference completed in {t_gliner:.2f}s ({t_gliner/len(texts):.3f}s/record).")

    # Format GLiNER predictions
    gliner_preds_formatted = []
    for preds in raw_gliner_preds:
        formatted = []
        for p in preds:
            plbl = p.get("label")
            ptxt = p.get("text", "").strip()
            if plbl in PRED_LABEL_MAP_GLINER and ptxt:
                formatted.append({
                    "field": PRED_LABEL_MAP_GLINER[plbl],
                    "text": ptxt,
                    "confidence": p.get("score", 1.0),
                    "source": "gliner",
                })
        gliner_preds_formatted.append(formatted)

    # 3. Run DEC-004 Cascade Inference
    # Production cascade rule:
    # DeterministicExtractor runs first.
    # Claims with confidence >= 0.75 are accepted.
    # Tier 2 (med.duration) is always accepted deterministically (DEC-019).
    # GLiNER fallback provides claims for Tier 1 fields not extracted with high confidence.
    det_extractor = DeterministicExtractor()
    cascade_preds_formatted = []
    threshold = 0.75

    t0_cas = time.time()
    for r, g_preds in zip(records, gliner_preds_formatted):
        doc = snippet_to_document_text(r["record_id"], r["text"])
        page = doc.pages[0]
        page_text = page.get_page_text()
        _, det_claims = det_extractor.extract_med_line(
            r["record_id"], page, page_text, 0, len(page_text)
        )

        cas_preds = []
        det_resolved_spans = set()

        # Step A: Accept high-confidence deterministic claims and Tier-2 claims
        for dc in det_claims:
            if dc.field_type == "med.duration" or dc.extraction_confidence >= threshold:
                cas_preds.append({
                    "field": dc.field_type,
                    "text": dc.value,
                    "confidence": dc.extraction_confidence,
                    "source": "deterministic",
                })
                det_resolved_spans.add((dc.field_type, dc.value.lower().strip()))

        # Step B: GLiNER Fallback for Tier 1 fields
        # If GLiNER predicted an entity that was NOT already extracted deterministically, include it!
        for gp in g_preds:
            if gp["field"] != "med.duration":
                key = (gp["field"], gp["text"].lower().strip())
                if key not in det_resolved_spans:
                    cas_preds.append(gp)

        cascade_preds_formatted.append(cas_preds)

    t_cascade = time.time() - t0_cas
    print(f"DEC-004 cascade simulation completed in {t_cascade:.2f}s.")

    # 4. Evaluate Both on EXACT SAME RECORDS
    eval_fields = ["med.name", "med.dose", "med.route", "med.frequency", "med.duration", "ocular.laterality"]

    gliner_counts, gliner_metrics, gliner_micro, gliner_errors = evaluate_predictions(
        records, gliner_preds_formatted, eval_fields
    )

    cascade_counts, cascade_metrics, cascade_micro, cascade_errors = evaluate_predictions(
        records, cascade_preds_formatted, eval_fields
    )

    # 5. Print Comparison Table
    print("\n" + "="*95)
    print("APPLES-TO-APPLES COMPARISON ON FROZEN 200-RECORD SUBSET (Canonical Match)")
    print("="*95)
    print(f"{'Field':18} | {'--- GLiNER-BioMed Standalone ---':^35} | {'--- DEC-004 Cascade ---':^35}")
    print(f"{'':18} | {'TP':>4} {'FP':>4} {'FN':>4} {'P':>6} {'R':>6} {'F1':>6} | {'TP':>4} {'FP':>4} {'FN':>4} {'P':>6} {'R':>6} {'F1':>6}")
    print("-" * 95)

    for fld in eval_fields:
        gc = gliner_counts[fld]
        gm = gliner_metrics[fld]
        cc = cascade_counts[fld]
        cm = cascade_metrics[fld]
        print(f"{fld:18} | {gc['tp']:4d} {gc['fp']:4d} {gc['fn']:4d} {gm[0]:6.3f} {gm[1]:6.3f} {gm[2]:6.3f} | {cc['tp']:4d} {cc['fp']:4d} {cc['fn']:4d} {cm[0]:6.3f} {cm[1]:6.3f} {cm[2]:6.3f}")

    print("-" * 95)
    print(f"{'MICRO AVG (med.*)':18} | {sum(gliner_counts[f]['tp'] for f in eval_fields if f.startswith('med.')):4d} {sum(gliner_counts[f]['fp'] for f in eval_fields if f.startswith('med.')):4d} {sum(gliner_counts[f]['fn'] for f in eval_fields if f.startswith('med.')):4d} {gliner_micro[0]:6.3f} {gliner_micro[1]:6.3f} {gliner_micro[2]:6.3f} | {sum(cascade_counts[f]['tp'] for f in eval_fields if f.startswith('med.')):4d} {sum(cascade_counts[f]['fp'] for f in eval_fields if f.startswith('med.')):4d} {sum(cascade_counts[f]['fn'] for f in eval_fields if f.startswith('med.')):4d} {cascade_micro[0]:6.3f} {cascade_micro[1]:6.3f} {cascade_micro[2]:6.3f}")
    print("="*95)

    # 6. Detailed Investigation of med.name performance
    print("\n--- DETAILED INVESTIGATION: med.name DIFFERENCE ---")
    print(f"GLiNER med.name  : TP={gliner_counts['med.name']['tp']}, FP={gliner_counts['med.name']['fp']}, FN={gliner_counts['med.name']['fn']} -> P={gliner_metrics['med.name'][0]:.4f}, R={gliner_metrics['med.name'][1]:.4f}, F1={gliner_metrics['med.name'][2]:.4f}")
    print(f"Cascade med.name : TP={cascade_counts['med.name']['tp']}, FP={cascade_counts['med.name']['fp']}, FN={cascade_counts['med.name']['fn']} -> P={cascade_metrics['med.name'][0]:.4f}, R={cascade_metrics['med.name'][1]:.4f}, F1={cascade_metrics['med.name'][2]:.4f}")

    # Inspect differences per record
    name_diffs = []
    for r, gp, cp in zip(records, gliner_preds_formatted, cascade_preds_formatted):
        g_names = {p["text"].lower().strip() for p in gp if p["field"] == "med.name"}
        c_names = {p["text"].lower().strip() for p in cp if p["field"] == "med.name"}
        if g_names != c_names:
            name_diffs.append({
                "record_id": r["record_id"],
                "text": r["text"],
                "gliner_names": list(g_names),
                "cascade_names": list(c_names),
                "gt_drugs": [g["text"] for g in r["parsed_ground_truth"] if g["field"] == "med.name"],
            })

    print(f"Records where GLiNER and Cascade produced different med.name extractions: {len(name_diffs)}")
    for d in name_diffs[:5]:
        print(f"  Record {d['record_id']}: GT={d['gt_drugs']} | GLiNER={d['gliner_names']} | Cascade={d['cascade_names']} | Text: {repr(d['text'][:70])}")

    # 7. False Positives on Negative Records Audit
    neg_records_indices = [i for i, r in enumerate(records) if r.get("is_negative")]
    print(f"\n--- AUDIT: FALSE POSITIVES ON ZERO-ANNOTATION NEGATIVE RECORDS ({len(neg_records_indices)} records) ---")
    gliner_neg_fps = 0
    cascade_neg_fps = 0
    neg_fp_examples = []

    for i in neg_records_indices:
        r = records[i]
        gp = gliner_preds_formatted[i]
        cp = cascade_preds_formatted[i]
        clin_gp = [p for p in gp if p["field"].startswith("med.")]
        clin_cp = [p for p in cp if p["field"].startswith("med.")]
        if clin_gp:
            gliner_neg_fps += len(clin_gp)
        if clin_cp:
            cascade_neg_fps += len(clin_cp)
        if clin_gp or clin_cp:
            neg_fp_examples.append({
                "record_id": r["record_id"],
                "text": r["text"],
                "gliner_fp": [f"{p['field']}: '{p['text']}'" for p in clin_gp],
                "cascade_fp": [f"{p['field']}: '{p['text']}'" for p in clin_cp],
            })

    print(f"Total false positives generated on 50 zero-label records:")
    print(f"  Standalone GLiNER : {gliner_neg_fps} FPs across {sum(1 for e in neg_fp_examples if e['gliner_fp'])} records")
    print(f"  DEC-004 Cascade   : {cascade_neg_fps} FPs across {sum(1 for e in neg_fp_examples if e['cascade_fp'])} records")
    print("Examples of false positives on negative clinical text:")
    for e in neg_fp_examples[:4]:
        print(f"  Record {e['record_id']}: text={repr(e['text'][:75])} | GLiNER FPs={e['gliner_fp']} | Cascade FPs={e['cascade_fp']}")

    # 8. Save Full Audit Artifact
    out_artifact = REPO_ROOT / "external_eval" / "results_frozen_200.json"
    artifact_payload = {
        "evaluation_subset": "external_eval/glaucoma_eval_subset_200.json",
        "sample_size": len(records),
        "positive_records": subset_data["positive_records_count"],
        "negative_records": subset_data["negative_records_count"],
        "gliner_metrics": gliner_metrics,
        "gliner_counts": gliner_counts,
        "gliner_micro": gliner_micro,
        "cascade_metrics": cascade_metrics,
        "cascade_counts": cascade_counts,
        "cascade_micro": cascade_micro,
        "negative_records_fps": {
            "standalone_gliner_fps": gliner_neg_fps,
            "cascade_fps": cascade_neg_fps,
            "examples": neg_fp_examples,
        },
        "med_name_differences_count": len(name_diffs),
        "med_name_differences_examples": name_diffs[:10],
    }
    out_artifact.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")
    print(f"\nSaved comprehensive evaluation artifact to {out_artifact.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
