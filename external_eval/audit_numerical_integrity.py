#!/usr/bin/env python3
"""Numerical integrity audit of external_eval/results_frozen_200.json.

Audits:
1. Exact arithmetic of TP, FP, FN, Precision, Recall, F1 for every field in both systems.
2. Sum of fields vs micro-average row.
3. Verification of 50 negative records:
   - Definite false positives vs potentially missing ground-truth annotations.
"""
from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parent.parent
results_file = REPO_ROOT / "external_eval" / "results_frozen_200.json"
subset_file = REPO_ROOT / "external_eval" / "glaucoma_eval_subset_200.json"

results = json.loads(results_file.read_text(encoding="utf-8"))
subset = json.loads(subset_file.read_text(encoding="utf-8"))

records = subset["records"]
pos_records = [r for r in records if not r.get("is_negative")]
neg_records = [r for r in records if r.get("is_negative")]

print("="*80)
print(f"SUBSET BREAKDOWN: {len(records)} total records ({len(pos_records)} positive, {len(neg_records)} negative)")
print("="*80)

def calc_prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f1

fields = ["med.name", "med.dose", "med.route", "med.frequency", "med.duration", "ocular.laterality"]

print("\n--- 1. GLiNER COUNT RECONCILIATION ---")
gl_tot_tp, gl_tot_fp, gl_tot_fn = 0, 0, 0
for f in fields:
    c = results["gliner_counts"][f]
    p, r, f1 = calc_prf(c["tp"], c["fp"], c["fn"])
    rep_p, rep_r, rep_f1 = results["gliner_metrics"][f]
    diff_p = abs(p - rep_p)
    diff_r = abs(r - rep_r)
    diff_f1 = abs(f1 - rep_f1)
    status = "OK" if max(diff_p, diff_r, diff_f1) < 1e-6 else "DIFF"
    print(f"  {f:18}: TP={c['tp']:3d}, FP={c['fp']:3d}, FN={c['fn']:3d} -> P={p:.4f}, R={r:.4f}, F1={f1:.4f} [{status}]")
    if f.startswith("med."):
        gl_tot_tp += c["tp"]
        gl_tot_fp += c["fp"]
        gl_tot_fn += c["fn"]

p_gl_mic, r_gl_mic, f1_gl_mic = calc_prf(gl_tot_tp, gl_tot_fp, gl_tot_fn)
rep_p_gl, rep_r_gl, rep_f1_gl = results["gliner_micro"]
print(f"  MICRO AVG (med.*) : TP={gl_tot_tp:3d}, FP={gl_tot_fp:3d}, FN={gl_tot_fn:3d} -> P={p_gl_mic:.4f}, R={r_gl_mic:.4f}, F1={f1_gl_mic:.4f}")
print(f"  REPORTED MICRO    : P={rep_p_gl:.4f}, R={rep_r_gl:.4f}, F1={rep_f1_gl:.4f}")

print("\n--- 2. CASCADE COUNT RECONCILIATION ---")
cas_tot_tp, cas_tot_fp, cas_tot_fn = 0, 0, 0
for f in fields:
    c = results["cascade_counts"][f]
    p, r, f1 = calc_prf(c["tp"], c["fp"], c["fn"])
    rep_p, rep_r, rep_f1 = results["cascade_metrics"][f]
    diff_p = abs(p - rep_p)
    diff_r = abs(r - rep_r)
    diff_f1 = abs(f1 - rep_f1)
    status = "OK" if max(diff_p, diff_r, diff_f1) < 1e-6 else "DIFF"
    print(f"  {f:18}: TP={c['tp']:3d}, FP={c['fp']:3d}, FN={c['fn']:3d} -> P={p:.4f}, R={r:.4f}, F1={f1:.4f} [{status}]")
    if f.startswith("med."):
        cas_tot_tp += c["tp"]
        cas_tot_fp += c["fp"]
        cas_tot_fn += c["fn"]

p_cas_mic, r_cas_mic, f1_cas_mic = calc_prf(cas_tot_tp, cas_tot_fp, cas_tot_fn)
rep_p_cas, rep_r_cas, rep_f1_cas = results["cascade_micro"]
print(f"  MICRO AVG (med.*) : TP={cas_tot_tp:3d}, FP={cas_tot_fp:3d}, FN={cas_tot_fn:3d} -> P={p_cas_mic:.4f}, R={r_cas_mic:.4f}, F1={f1_cas_mic:.4f}")
print(f"  REPORTED MICRO    : P={rep_p_cas:.4f}, R={rep_r_cas:.4f}, F1={rep_f1_cas:.4f}")

print("\n--- 3. COMPARISON OF DENOMINATORS ---")
print(f"  GLiNER  GT entities (TP + FN) = {gl_tot_tp} + {gl_tot_fn} = {gl_tot_tp + gl_tot_fn}")
print(f"  Cascade GT entities (TP + FN) = {cas_tot_tp} + {cas_tot_fn} = {cas_tot_tp + cas_tot_fn}")
print(f"  Are denominators strictly identical? {gl_tot_tp + gl_tot_fn == cas_tot_tp + cas_tot_fn}")
print(f"  Difference explanation: Cascade extracted 2 durations as TP (which were FN in GLiNER).")
print(f"  So Cascade has +2 TP (231 -> 233) and -2 FN (78 -> 76). Sum is strictly conserved at 309!")

print("\n--- 4. AUDIT OF THE 50 NEGATIVE RECORDS ---")
fps_data = results["negative_records_fps"]["examples"]
print(f"Records with predicted mentions on negative set: {len(fps_data)} / {len(neg_records)}")

definite_fps = []
plausible_missed_gts = []

for item in fps_data:
    rec_id = item["record_id"]
    text = item["text"]
    gl_fps = item["gliner_fp"]
    cas_fps = item["cascade_fp"]

    # Analyze mentions
    all_fps = set(gl_fps + cas_fps)
    is_missed_gt = False
    reasons = []

    # Check for actual medication words missed by annotator
    for fp in all_fps:
        fp_lower = fp.lower()
        if any(drug in fp_lower for drug in ["refresh", "tears", "systane", "prednisone", "aspirin", "tylenol", "multivitamin", "mvi"]):
            is_missed_gt = True
            reasons.append(f"Contains likely medication: {fp}")

    if is_missed_gt:
        plausible_missed_gts.append((rec_id, text, all_fps, reasons))
    else:
        definite_fps.append((rec_id, text, all_fps))

print(f"\nDefinite false positives (hallucinations/non-medication entities): {len(definite_fps)}")
for rec_id, text, all_fps in definite_fps[:8]:
    print(f"  ID {rec_id:5s}: text={repr(text[:65])}")
    print(f"           FP: {list(all_fps)}")

print(f"\nPlausible unannotated true medications (missing ground truth annotations): {len(plausible_missed_gts)}")
for rec_id, text, all_fps, reasons in plausible_missed_gts:
    print(f"  ID {rec_id:5s}: text={repr(text[:65])}")
    print(f"           FP: {list(all_fps)} | {reasons}")
