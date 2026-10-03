"""Build src/clindoc/web/data/results.json: the curated benchmark numbers served by GET /api/results.

Authority:
  - experiments/FINAL_REPORT_DATA_SHEET.md (sole authoritative numerical reference)
  - Machine-readable artefacts listed per section under "sources" (JSON wins where a report
    table disagrees: V10 REPORT §3/§4/§5 and README's "+3.53%" are known to be wrong).

Deterministic: no timestamps, insertion-ordered keys, rounded floats. Headline numbers are
asserted so drift in any source fails the build loudly. Run from anywhere:
  python scripts/build_web_data.py

Output schema (also embedded as the "_schema" key):
  headline            KPI tiles (cascade vs ModernBERT vs Qwen, autonomous tier, runtime, benchmark size)
  leaderboard         [{id, name, n, errors, accuracy, macro_f1, discrepancy_recall, runtime_seconds, ...}] V1..V9
  confusion_matrices  {labels, matrices: {model_id: 3x3 rows=truth S,D,I | null}, stage_a_abstained}
  per_class           {model_id: {label: {precision, recall, f1, support}}}
  per_field           {fields, models: {model_id: {field: {n, correct, errors, accuracy, ...}}}}
  per_template        {model_id: {template_c|mar_order_sheet|narrative: {n, correct, errors, accuracy}}}
  risk_coverage       {stage_b: [points], cascade: [points], operating_point, aurc}
  escalation          {n_escalated, rate, reasons, by_field}
  prompt_case_study   {v0, v1, delta, v1_lab_flag_instruction}
  dataset             {documents, claims, groups, splits, labels, severity, templates, ...}
  robustness          {configuration, rows: [{track, dataset, degradation, severity, mean_cer, extraction_f1, ...}]}
  extraction          {deterministic, gliner_e2, narrative_e3}
  fhir                {bundles, valid_bundles, provenance, coding, compound_accuracy, resource_counts}
  stage_b_reference   reported V6 training run (reports/M2_V3_MODERNBERT_TRAINING_REPORT.md)
  verify_presets      Playground presets drawn from real V3 OOD examples
  sources             {section: [repo-relative paths]}
"""
from __future__ import annotations

from collections import Counter, defaultdict
import glob
import hashlib
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from clindoc.verify.claim_text import build_claim_text  # noqa: E402

OUT = REPO / "src" / "clindoc" / "web" / "data" / "results.json"
LABELS = ["SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"]
FIELDS = ["lab.value", "lab.flag", "med.name", "med.dose", "med.route", "med.frequency"]

P_DATA_SHEET = "experiments/FINAL_REPORT_DATA_SHEET.md"
P_V1_V5 = "experiments/V10-ood-template-evaluation/v1_v5_v3_ood_results.json"
P_V8_EVAL = "experiments/V8-verification-cascade/phase4_cascade_eval_report.json"
P_V8_DIAG = "experiments/V8-verification-cascade/phase4_cascade_diagnostics.json"
P_V9_EVAL = "experiments/V8-verification-cascade/v9_qwen_only/phase4_cascade_eval_report.json"
P_V9_DIAG = "experiments/V8-verification-cascade/v9_qwen_only/phase4_cascade_diagnostics.json"
P_V0 = "experiments/V8-verification-cascade/V3_PHASE4_BASELINE_SUMMARY.json"
P_V1_ANALYSIS = "experiments/V8-verification-cascade/V1_LABFLAG_OOD_ANALYSIS.md"
P_V12 = "experiments/V8-verification-cascade/v12_risk_coverage_analysis.json"
P_SPLITS = "data/m2_verification_v3/split_manifest.json"
P_META = "data/m2_verification_v3/metadata.json"
P_GEN = "data/m2_verification_v3/generation_manifest.json"
P_EXAMPLES = "data/m2_verification_v3/all_examples.jsonl"
P_ROBUST = "data/benchmark_results/m1_robustness_report.json"
P_E2 = "experiments/E2-gliner-extraction/e2_results.json"
P_E3 = "experiments/E3-narrative-extraction/e3_results.json"
P_E4 = "experiments/E4-hybrid-router-extraction/REPORT.md"
P_E2E = "eval/end_to_end_results.json"
P_E2E_REM = "eval/end_to_end_results_remediated.json"
P_FHIR_REM = "data/fhir_phase5_v8_remediated"
P_FHIR_HIST = "data/fhir_phase5_v8"
P_TRAIN = "reports/M2_V3_MODERNBERT_TRAINING_REPORT.md"
P_STAGE_C = "src/clindoc/verify/stage_c.py"


def load(rel: str):
    with open(REPO / rel, encoding="utf-8") as f:
        return json.load(f)


def r4(x: float) -> float:
    return round(float(x), 4)


def close(actual: float, expected: float, what: str, tol: float = 5e-5) -> None:
    assert abs(actual - expected) <= tol, f"{what}: computed {actual} != expected {expected}"


def summarize(cm: list[list[int]]) -> dict:
    """Accuracy / macro-F1 / discrepancy recall / per-class from a 3x3 matrix (rows = truth)."""
    n = sum(map(sum, cm))
    correct = sum(cm[i][i] for i in range(3))
    per_class = {}
    for i, lbl in enumerate(LABELS):
        tp, support, predicted = cm[i][i], sum(cm[i]), sum(row[i] for row in cm)
        p = tp / predicted if predicted else 0.0
        r = tp / support if support else 0.0
        per_class[lbl] = {"precision": r4(p), "recall": r4(r),
                          "f1": r4(2 * p * r / (p + r) if p + r else 0.0), "support": support}
    f1s = []
    for i in range(3):  # unrounded macro-F1
        tp, support, predicted = cm[i][i], sum(cm[i]), sum(row[i] for row in cm)
        p, r = (tp / predicted if predicted else 0.0), (tp / support if support else 0.0)
        f1s.append(2 * p * r / (p + r) if p + r else 0.0)
    return {"n": n, "correct": correct, "errors": n - correct, "accuracy": r4(correct / n),
            "macro_f1": r4(sum(f1s) / 3), "discrepancy_recall": r4(cm[1][1] / sum(cm[1])),
            "per_class": per_class}


def confusion(pairs) -> list[list[int]]:
    cm = [[0] * 3 for _ in range(3)]
    for truth, pred in pairs:
        if pred in LABELS:
            cm[LABELS.index(truth)][LABELS.index(pred)] += 1
    return cm


def grouped(records, key, pred) -> dict:
    """{group: {n, correct, errors, accuracy, macro_f1, discrepancy_recall}} for a predictor."""
    buckets = defaultdict(list)
    for r in records:
        buckets[key(r)].append((r["ground_truth_label"], pred(r)))
    out = {}
    for k in sorted(buckets):
        s = summarize(confusion(buckets[k]))
        out[k] = {x: s[x] for x in ("n", "correct", "errors", "accuracy", "macro_f1", "discrepancy_recall")}
    return out


def template_group(t: str) -> str:
    return "narrative" if t.startswith("narrative") else t


def sha256(rel: str) -> str:
    h = hashlib.sha256()
    with open(REPO / rel, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fhir_scan(rel_dir: str) -> dict:
    """Count resources and terminology codings in a directory of FHIR bundles."""
    c = Counter()
    for p in sorted(glob.glob(str(REPO / rel_dir / "*_fhir_bundle.json"))):
        with open(p, encoding="utf-8") as f:
            bundle = json.load(f)
        c["bundles"] += 1
        for entry in bundle.get("entry", []):
            res = entry["resource"]
            rtype = res["resourceType"]
            c[rtype] += 1
            if rtype == "Observation":
                codings = (res.get("code") or {}).get("coding") or []
                c["loinc"] += any(cd.get("system") == "http://loinc.org" and cd.get("code") for cd in codings)
                vq = res.get("valueQuantity") or {}
                c["quantities"] += bool(vq)
                c["ucum"] += vq.get("system") == "http://unitsofmeasure.org" and bool(vq.get("code"))
            elif rtype in ("MedicationRequest", "MedicationStatement"):
                codings = (res.get("medicationCodeableConcept") or {}).get("coding") or []
                c["rxnorm"] += any("rxnorm" in (cd.get("system") or "") and cd.get("code") for cd in codings)
                for dosage in res.get("dosage") or res.get("dosageInstruction") or []:
                    for dr in dosage.get("doseAndRate") or []:
                        dq = dr.get("doseQuantity") or {}
                        c["quantities"] += bool(dq)
                        c["ucum"] += dq.get("system") == "http://unitsofmeasure.org" and bool(dq.get("code"))
    return c


_CLAIM_PATTERNS = {
    "lab.value": r"^(?P<entity>.+?): (?P<value>-?[\d.]+) ?(?P<unit>.*)$",
    "lab.flag": r"^(?P<entity>.+) flag: (?P<value>.+)$",
    "med.name": r"^Medication: (?P<value>.+)$",
    "med.dose": r"^(?P<entity>.+) dose: (?P<value>[\d.]+) ?(?P<unit>.*)$",
    "med.route": r"^(?P<entity>.+) route: (?P<value>.+)$",
    "med.frequency": r"^(?P<entity>.+) frequency: (?P<value>.+)$",
}


def parse_claim(field_type: str, claim: str) -> dict:
    """Split a V3 claim string back into entity/value/unit (round-trip asserted)."""
    m = re.match(_CLAIM_PATTERNS[field_type], claim)
    assert m, f"unparseable {field_type} claim: {claim!r}"
    parts = {"entity": m.groupdict().get("entity"), "value": m["value"], "unit": m.groupdict().get("unit") or None}
    assert build_claim_text(field_type, parts["value"], parts["unit"], parts["entity"]) == claim, claim
    return parts


def main() -> None:
    sheet = (REPO / P_DATA_SHEET).read_text(encoding="utf-8")
    v1_v5 = load(P_V1_V5)
    v8_eval = load(P_V8_EVAL)["results"]["ood"]
    v8 = load(P_V8_DIAG)["diagnostics"]["ood"]
    v9_eval = load(P_V9_EVAL)["results"]["ood"]
    v9 = load(P_V9_DIAG)["diagnostics"]["ood"]
    v0 = load(P_V0)["results"]["ood"]
    v12 = load(P_V12)
    splits = load(P_SPLITS)
    meta = load(P_META)
    gen = load(P_GEN)
    with open(REPO / P_EXAMPLES, encoding="utf-8") as f:
        examples = [json.loads(line) for line in f]
    by_id = {e["example_id"]: e for e in examples}
    assert len(v8) == len(v9) == 1826 and all(r["example_id"] in by_id for r in v8)
    v9_by_id = {r["example_id"]: r for r in v9}
    template_of = lambda r: template_group(by_id[r["example_id"]]["doc_template"])  # noqa: E731

    # ─── Models recomputed from frozen diagnostics ────────────────────────────
    truth = lambda r: r["ground_truth_label"]  # noqa: E731
    stage_b = lambda r: r["stage_b"]["label"]  # noqa: E731
    final = lambda r: r["cascade"]["final_label"]  # noqa: E731
    v9_final = lambda r: v9_by_id[r["example_id"]]["cascade"]["final_label"]  # noqa: E731
    cm_v6 = confusion((truth(r), stage_b(r)) for r in v8)
    cm_v8 = confusion((truth(r), final(r)) for r in v8)
    cm_v9 = confusion((truth(r), v9_final(r)) for r in v8)
    cm_a = confusion((truth(r), r["stage_a"]["label"]) for r in v8)
    a_abstained = sum(r["stage_a"]["label"] is None for r in v8)
    s6, s8, s9 = summarize(cm_v6), summarize(cm_v8), summarize(cm_v9)

    assert cm_v6 == [[671, 58, 0], [12, 966, 0], [4, 8, 107]], cm_v6
    assert cm_a == [[721, 8, 0], [0, 749, 193], [0, 119, 0]] and a_abstained == 36, cm_a
    close(s8["accuracy"], 0.9748, "V8 accuracy"); assert s8["correct"] == 1780 and s8["errors"] == 46
    close(s8["macro_f1"], 0.9538, "V8 macro-F1"); close(s8["macro_f1"], v8_eval["overall"]["macro_f1"], "V8 F1 vs eval")
    close(s8["discrepancy_recall"], 0.9826, "V8 discrepancy recall")
    close(s6["accuracy"], 0.9551, "V6 accuracy"); close(s6["macro_f1"], 0.9519, "V6 F1"); assert s6["errors"] == 82
    close(s6["discrepancy_recall"], 0.9877, "V6 discrepancy recall")
    close(s9["accuracy"], 0.9365, "V9 accuracy"); close(s9["macro_f1"], v9_eval["overall"]["macro_f1"], "V9 F1")
    assert s9["errors"] == 116
    close(s9["discrepancy_recall"], 0.9755, "V9 discrepancy recall")

    stage_c = [r for r in v8 if r["cascade"]["stage_reached"] == "C"]
    stage_b_resolved = [r for r in v8 if r["cascade"]["stage_reached"] == "B"]
    c_correct = sum(r["cascade"]["is_correct"] for r in stage_c)
    b_correct = sum(r["cascade"]["is_correct"] for r in stage_b_resolved)
    assert (len(stage_c), c_correct, len(stage_b_resolved), b_correct) == (381, 345, 1445, 1435)
    assert all(r["cascade"]["is_correct"] == (final(r) == truth(r)) for r in v8)

    # ─── Headline ─────────────────────────────────────────────────────────────
    op = v12["production_operating_point"]
    conf_only = next(p for p in v12["stage_b_risk_coverage_representative_points"] if p["target_coverage"] == 0.7913)
    conf_only_errors = round(conf_only["risk"] * op["stage_b_resolved_count"])
    assert conf_only_errors == 61 and op["stage_b_resolved_errors"] == 10
    runtime_v8, runtime_v9 = v8_eval["elapsed_seconds"], v9_eval["elapsed_seconds"]
    assert (runtime_v8, runtime_v9) == (1598.59, 6203.75)
    headline = {
        "cascade_accuracy": s8["accuracy"], "cascade_correct": s8["correct"], "n_claims_ood": s8["n"],
        "cascade_macro_f1": s8["macro_f1"], "cascade_discrepancy_recall": s8["discrepancy_recall"],
        "cascade_discrepancies_caught": cm_v8[1][1], "n_discrepancies_ood": sum(cm_v8[1]),
        "cascade_errors": s8["errors"], "modernbert_errors": s6["errors"], "qwen_errors": s9["errors"],
        "error_reduction_vs_modernbert": r4(1 - s8["errors"] / s6["errors"]),
        "error_reduction_vs_qwen": r4(1 - s8["errors"] / s9["errors"]),
        "autonomous_coverage": r4(op["stage_b_resolved_coverage"]),
        "autonomous_claims": op["stage_b_resolved_count"],
        "autonomous_accuracy": r4(op["stage_b_resolved_accuracy"]),
        "autonomous_errors": op["stage_b_resolved_errors"],
        "confidence_only_accuracy_at_same_coverage": r4(conf_only["selective_accuracy"]),
        "confidence_only_errors_at_same_coverage": conf_only_errors,
        "risk_reduction_factor": round(conf_only["risk"] / op["stage_b_resolved_risk"], 1),
        "escalated_to_llm": len(stage_c), "escalation_rate": r4(len(stage_c) / s8["n"]),
        "stage_c_accuracy": r4(c_correct / len(stage_c)),
        "runtime_cascade_seconds": runtime_v8, "runtime_qwen_seconds": runtime_v9,
        "speedup_vs_qwen": round(runtime_v9 / runtime_v8, 2),
        "benchmark_documents": splits["document_counts"]["total"], "benchmark_claims": len(examples),
    }
    close(headline["error_reduction_vs_modernbert"], 0.4390, "error reduction vs ModernBERT")
    close(headline["error_reduction_vs_qwen"], 0.6034, "error reduction vs Qwen")
    close(headline["autonomous_coverage"], 0.7913, "coverage"); close(headline["autonomous_accuracy"], 0.9931, "auto acc")
    close(headline["confidence_only_accuracy_at_same_coverage"], 0.9578, "confidence-only accuracy")
    assert headline["risk_reduction_factor"] == 6.1 and headline["benchmark_documents"] == 120
    assert headline["benchmark_claims"] == 5629 and "**46 / 1,826**" in sheet

    # ─── Leaderboard ──────────────────────────────────────────────────────────
    names = {
        "V1_deterministic": ("V1", "Deterministic Matcher", "Rule-based parser"),
        "V2_levenshtein": ("V2", "Levenshtein Distance", "Character distance baseline"),
        "V3_tfidf_lr": ("V3", "TF-IDF + Logistic Regression", "Linear baseline"),
        "V4_embedding_similarity": ("V4", "SapBERT Embedding Similarity", "Biomedical bi-encoder cosine"),
        "V5_zeroshot_nli": ("V5", "PubMedBERT Zero-Shot NLI", "Zero-shot biomedical NLI"),
    }
    leaderboard, matrices, per_class, per_field, per_template = [], {}, {}, {}, {}
    for key, (mid, name, arch) in names.items():
        m = v1_v5[key]
        leaderboard.append({"id": mid, "name": name, "architecture": arch, "n": m["total_examples"],
                            "correct": m["correct"], "errors": m["total_errors"], "accuracy": m["accuracy"],
                            "macro_f1": m["macro_f1"], "discrepancy_recall": m["discrepancy_recall"],
                            "runtime_seconds": m["runtime_seconds"], "runtime_approx": False, "source": P_V1_V5})
        matrices[mid] = m["confusion_matrix"]
        per_class[mid] = {k: {x: (r4(v[x]) if x != "support" else v[x]) for x in ("precision", "recall", "f1", "support")}
                          for k, v in m["per_class"].items()}
        per_field[mid] = {f: m["per_field"][f] for f in FIELDS}
        per_template[mid] = m["per_template"]
    assert [r["errors"] for r in leaderboard] == [545, 939, 667, 1000, 409]

    def row(mid, name, arch, s, runtime, approx, source):
        return {"id": mid, "name": name, "architecture": arch, "n": s["n"], "correct": s["correct"],
                "errors": s["errors"], "accuracy": s["accuracy"], "macro_f1": s["macro_f1"],
                "discrepancy_recall": s["discrepancy_recall"], "runtime_seconds": runtime,
                "runtime_approx": approx, "source": source}

    leaderboard.append(row("V6", "ModernBERT Standalone", "Fine-tuned BioClinical-ModernBERT (Stage B alone)",
                           s6, 45.0, True, P_V8_DIAG))
    v0_errors = 74  # data sheet §3; 1,435 Stage-B-correct + 316 Stage-C-correct implies 75 (source off by one)
    assert "| 74 / 1,826 |" in sheet
    leaderboard.append({"id": "V8_V0", "name": "Cascade (Prompt V0)", "architecture": "Stage A -> B -> C, frozen baseline prompt",
                        "n": 1826, "correct": 1826 - v0_errors, "errors": v0_errors,
                        "accuracy": v0["cascade"]["accuracy"], "macro_f1": v0["cascade"]["macro_f1"],
                        "discrepancy_recall": v0["cascade"]["discrepancy_recall"], "runtime_seconds": 1500.0,
                        "runtime_approx": True, "source": P_V0})
    leaderboard.append(row("V8_V1", "Cascade (Prompt V1, production)", "Stage A -> B -> C, lab.flag prompt V1",
                           s8, runtime_v8, False, P_V8_DIAG))
    leaderboard.append(row("V9", "Standalone Qwen-7B", "Prompted Qwen2.5-7B-Instruct-AWQ on every claim",
                           s9, runtime_v9, False, P_V9_DIAG))
    for mid, cm, s in (("V6", cm_v6, s6), ("V8_V1", cm_v8, s8), ("V9", cm_v9, s9)):
        matrices[mid] = cm
        per_class[mid] = s["per_class"]
    matrices["V8_V0"] = None  # V0 diagnostics were not frozen
    matrices["stage_a"] = cm_a

    per_field["V6"] = grouped(v8, lambda r: r["field_type"], stage_b)
    per_field["V8_V1"] = grouped(v8, lambda r: r["field_type"], final)
    per_field["V9"] = grouped(v8, lambda r: r["field_type"], v9_final)
    for f, acc in {"lab.value": 0.9890, "lab.flag": 0.9148, "med.name": 0.9579, "med.dose": 1.0,
                   "med.frequency": 0.9882, "med.route": 0.9726}.items():
        close(per_field["V8_V1"][f]["accuracy"], acc, f"V8 {f} accuracy")
        close(per_field["V8_V1"][f]["accuracy"], v8_eval["by_field_type"][f]["accuracy"], f"V8 {f} vs eval")
    for mid, pred in (("V6", stage_b), ("V8_V1", final), ("V9", v9_final)):
        per_template[mid] = {k: {x: v[x] for x in ("n", "correct", "errors", "accuracy")}
                             for k, v in grouped(v8, template_of, pred).items()}
    t8 = per_template["V8_V1"]
    assert (t8["template_c"]["errors"], t8["mar_order_sheet"]["errors"], t8["narrative"]["errors"]) == (39, 6, 1)
    close(t8["template_c"]["accuracy"], 0.9714, "template_c"); close(t8["narrative"]["accuracy"], 0.9938, "narrative")

    # ─── Risk-coverage & escalation ───────────────────────────────────────────
    aurc = v12["aurc_summary"]
    assert (aurc["aurc_stage_b_calibrated"], aurc["aurc_stage_b_raw"], aurc["aurc_cascade_joint"]) == (0.0346, 0.0347, 0.0543)
    risk_coverage = {
        "stage_b": v12["stage_b_risk_coverage_representative_points"],
        "cascade": v12["cascade_risk_coverage_representative_points"],
        "operating_point": {
            "coverage": headline["autonomous_coverage"], "n_covered": op["stage_b_resolved_count"],
            "threshold": 0.60,
            "dual_gate": {"accuracy": r4(op["stage_b_resolved_accuracy"]), "risk": r4(op["stage_b_resolved_risk"]),
                          "errors": op["stage_b_resolved_errors"]},
            "confidence_only": {"accuracy": r4(conf_only["selective_accuracy"]), "risk": r4(conf_only["risk"]),
                                "errors": conf_only_errors, "confidence_threshold": conf_only["confidence_threshold"]},
            "risk_reduction_factor": headline["risk_reduction_factor"],
            "escalated": {"n": len(stage_c), "accuracy": r4(c_correct / len(stage_c)),
                          "stage_b_counterfactual_accuracy": r4(op["counterfactual_stage_b_on_escalated_accuracy"])},
        },
        "aurc": {"stage_b_calibrated": aurc["aurc_stage_b_calibrated"], "stage_b_raw": aurc["aurc_stage_b_raw"],
                 "cascade_joint": aurc["aurc_cascade_joint"]},
    }
    reasons = Counter()
    for r in stage_c:
        reason = r["stage_b"]["escalation_reason"] or "unknown"
        m = re.match(r"stage_a_disagreement \((A=\w+, B=\w+)\)", reason)
        reasons["stage_a_disagreement " + m[1] if m else reason.split(" ")[0]] += 1
    assert sum(reasons.values()) == 381 and reasons["low_confidence"] == 3
    by_field = {}
    for f in FIELDS:
        rows = [r for r in v8 if r["field_type"] == f]
        esc = [r for r in rows if r["cascade"]["stage_reached"] == "C"]
        by_field[f] = {"n": len(rows), "escalated": len(esc), "rate": r4(len(esc) / len(rows)),
                       "stage_c_correct": sum(r["cascade"]["is_correct"] for r in esc),
                       "stage_c_accuracy": r4(sum(r["cascade"]["is_correct"] for r in esc) / len(esc)) if esc else None}
        assert len(esc) == v8_eval["by_field_type"][f]["stage_distribution"]["C"]
    escalation = {"n_escalated": len(stage_c), "rate": headline["escalation_rate"],
                  "reasons": dict(reasons.most_common()), "by_field": by_field,
                  "disagreement_share": r4(sum(v for k, v in reasons.items() if k.startswith("stage_a")) / len(stage_c))}

    # ─── Prompt V0 -> V1 case study ───────────────────────────────────────────
    flag_c = [r for r in stage_c if r["field_type"] == "lab.flag"]
    flag_correct = sum(r["cascade"]["is_correct"] for r in flag_c)
    v1_errors_by_field = Counter(r["field_type"] for r in stage_c if not r["cascade"]["is_correct"])
    short = {"SUPPORTED": "supported", "DISCREPANCY": "discrepancy", "INSUFFICIENT_EVIDENCE": "insufficient"}
    v1_transitions = Counter(f"{short[truth(r)]}_to_{short[final(r)]}" for r in stage_c if not r["cascade"]["is_correct"])
    assert set(v1_transitions) <= set(v0["stage_c"]["error_distribution"]) and sum(v1_transitions.values()) == 36
    assert (len(flag_c), flag_correct, 247 - v0["stage_c"]["errors_by_field"]["lab.flag"]) == (247, 220, 191)
    stage_c_src = (REPO / P_STAGE_C).read_text(encoding="utf-8")
    instr = re.search(r'LAB_FLAG_TABULAR_INSTRUCTION = \((.*?)\n\)', stage_c_src, re.S)[1]
    instruction = "".join(json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', instr))
    prompt_case_study = {
        "description": "Single-variable intervention: only the Stage C prompt for lab.flag claims changed "
                       "(ModernBERT weights, T=1.146522, Stage A rules and tau=0.60 untouched).",
        "v0": {"cascade_accuracy": v0["cascade"]["accuracy"], "cascade_macro_f1": v0["cascade"]["macro_f1"],
               "cascade_discrepancy_recall": v0["cascade"]["discrepancy_recall"],
               "stage_c_accuracy": v0["stage_c"]["accuracy"], "stage_c_errors": v0["stage_c"]["errors"],
               "stage_c_correct": 381 - v0["stage_c"]["errors"],
               "lab_flag_stage_c": {"correct": 247 - v0["stage_c"]["errors_by_field"]["lab.flag"], "n": 247},
               "errors_by_field": v0["stage_c"]["errors_by_field"],
               "error_transitions": v0["stage_c"]["error_distribution"]},
        "v1": {"cascade_accuracy": s8["accuracy"], "cascade_macro_f1": s8["macro_f1"],
               "cascade_discrepancy_recall": s8["discrepancy_recall"],
               "stage_c_accuracy": r4(c_correct / len(stage_c)), "stage_c_errors": len(stage_c) - c_correct,
               "stage_c_correct": c_correct, "lab_flag_stage_c": {"correct": flag_correct, "n": len(flag_c)},
               "errors_by_field": {f: v1_errors_by_field.get(f, 0) for f in v0["stage_c"]["errors_by_field"]},
               "error_transitions": {k: v1_transitions.get(k, 0) for k in v0["stage_c"]["error_distribution"]}},
        "v1_lab_flag_instruction": instruction,
    }
    pv0, pv1 = prompt_case_study["v0"], prompt_case_study["v1"]
    assert (pv0["cascade_accuracy"], pv0["cascade_macro_f1"], pv0["stage_c_accuracy"], pv0["stage_c_errors"]) == (0.9595, 0.9422, 0.8294, 65)
    assert (pv1["cascade_accuracy"], pv1["cascade_macro_f1"], pv1["stage_c_accuracy"], pv1["stage_c_errors"]) == (0.9748, 0.9538, 0.9055, 36)
    prompt_case_study["delta"] = {
        "cascade_accuracy": r4(pv1["cascade_accuracy"] - pv0["cascade_accuracy"]),
        "cascade_macro_f1": r4(pv1["cascade_macro_f1"] - pv0["cascade_macro_f1"]),
        "stage_c_accuracy": r4(pv1["stage_c_accuracy"] - pv0["stage_c_accuracy"]),
        "stage_c_errors": pv1["stage_c_errors"] - pv0["stage_c_errors"],
        "lab_flag_stage_c_correct": flag_correct - pv0["lab_flag_stage_c"]["correct"],
    }

    # ─── Dataset ──────────────────────────────────────────────────────────────
    hashes = {P_EXAMPLES: sha256(P_EXAMPLES), P_SPLITS: sha256(P_SPLITS)}
    assert hashes[P_EXAMPLES].startswith("40278c01") and hashes[P_SPLITS].startswith("cf41cc1d"), hashes
    split_names = ["train", "calibration", "validation", "ood"]
    doc_split = {d: s for s in split_names for d in splits[f"{s}_doc_ids"]}
    split_rows = {s: [e for e in examples if doc_split[e["source_doc_id"]] == s] for s in split_names}
    dataset_splits = {}
    for s in split_names:
        rows = split_rows[s]
        clean = sum(e["severity"] == "clean" for e in rows)
        dataset_splits[s] = {
            "documents": splits["document_counts"][s], "claims": splits["example_counts"][s],
            "clean": clean, "degraded": len(rows) - clean, "groups": len({e["group_id"] for e in rows}),
            "labels": {lbl: splits["label_counts"][s].get(lbl, 0) for lbl in LABELS},
            "families": splits["doc_family_counts"][s], "fields": splits["field_counts"][s],
            "templates": dict(sorted(splits["template_counts"][s].items())),
        }
        assert len(rows) == splits["example_counts"][s]
    # README §8's per-split clean/degraded/group columns do not match the SHA-locked data; computed here.
    assert [dataset_splits[s]["groups"] for s in split_names] == [812, 247, 322, 697]
    dataset = {
        "documents": splits["document_counts"]["total"], "claims": len(examples),
        "groups": gen["total_groups"], "families": {"lab": 50, "med": 50, "narrative": 20},
        "clean": sum(d["clean"] for d in dataset_splits.values()),
        "degraded": sum(d["degraded"] for d in dataset_splits.values()),
        "splits": dataset_splits,
        "labels": {lbl: meta["label_counts"][lbl] for lbl in LABELS},
        "severity": {sev: {lbl: meta["by_severity"][sev].get(lbl, 0) for lbl in LABELS} for sev in ("clean", "low", "medium", "high")},
        "fields": {f: {lbl: meta["by_field_type"][f].get(lbl, 0) for lbl in LABELS} for f in FIELDS},
        "templates": {t: {lbl: v.get(lbl, 0) for lbl in LABELS} for t, v in sorted(meta["by_doc_template"].items())},
        "generation_methods": {k: sum(v.values()) for k, v in meta["by_generation_method"].items()},
        "contradiction_types": gen["by_contradiction_type"],
        "leakage": splits["audits"],
        "hashes": hashes,
    }
    assert (dataset["groups"], dataset["clean"], dataset["degraded"]) == (2078, 3878, 1751)
    assert dataset["labels"] == {"SUPPORTED": 2270, "DISCREPANCY": 2925, "INSUFFICIENT_EVIDENCE": 434}

    # ─── Robustness (E5 / M1) ─────────────────────────────────────────────────
    robust = load(P_ROBUST)
    robustness = {"configuration": robust["configuration"], "rows": robust["robustness_curves"]}
    assert len(robustness["rows"]) == 48

    # ─── Extraction ───────────────────────────────────────────────────────────
    e2, e3 = load(P_E2), load(P_E3)
    keep = ("precision", "recall", "f1", "tp", "fp", "fn", "gt_count", "pred_count")

    def ext_block(o: dict) -> dict:
        return {"micro_precision": o["micro_precision"], "micro_recall": o["micro_recall"], "micro_f1": o["micro_f1"],
                "macro_f1": o["macro_f1"], "gt_claims": o["total_gt_claims"], "pred_claims": o["total_pred_claims"],
                "per_field": {f: {k: v[k] for k in keep} for f, v in o["metrics_per_field"].items()}}

    e3_main = e3["primary_narrative_benchmark"]
    extraction = {
        "deterministic": {  # E1/E4: clean born-digital tables, data sheet §2
            "lab": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "n": 705, "documents": 50},
            "med": {"precision": 1.0, "recall": 1.0, "f1": 1.0, "n": 87, "documents": 50},
        },
        "gliner_e2": {"model": e2["model_name"], "is_mock": e2["is_mock"], "documents": e2["evaluation_scope"]["total_documents"],
                      **ext_block(e2["overall"]),
                      "by_document_type": {k: v["micro_f1"] for k, v in e2["by_document_type"].items()}},
        "narrative_e3": {"model": e3["model_name"], "is_mock": e3["is_mock"], "documents": e3_main["num_documents"],
                         **ext_block(e3_main)},
    }
    assert "| 705 / 705 |" in sheet and "| 87 / 87 |" in sheet
    assert (extraction["gliner_e2"]["micro_f1"], extraction["gliner_e2"]["macro_f1"]) == (0.2489, 0.3455)
    assert (extraction["narrative_e3"]["micro_f1"], extraction["narrative_e3"]["macro_f1"]) == (0.2382, 0.2047)

    # ─── FHIR / interoperability ──────────────────────────────────────────────
    batch = load(f"{P_FHIR_REM}/batch_summary.json")
    e2e, e2e_rem = load(P_E2E), load(P_E2E_REM)
    rem, hist = fhir_scan(P_FHIR_REM), fhir_scan(P_FHIR_HIST)
    assert (rem["bundles"], rem["loinc"], rem["Observation"], rem["rxnorm"], rem["ucum"], rem["quantities"]) == (34, 603, 603, 94, 697, 697)
    assert (hist["loinc"], hist["rxnorm"], hist["ucum"]) == (0, 0, 0)
    n_med = rem["MedicationRequest"] + rem["MedicationStatement"]
    strict = lambda d: d["compound_metrics"]["strict_fhir_bundle_accuracy"]  # noqa: E731
    fhir = {
        "bundles": batch["total_documents"], "valid_bundles": batch["total_documents"] - batch["validation_failures"],
        "claims": batch["total_claims"],
        "provenance": {"claims_in_audit_report": e2e_rem["component_metrics"]["claims_in_audit_report"]["count"],
                       "claims_in_bundle": e2e_rem["component_metrics"]["claims_in_fhir_bundle_provenance"]["count"],
                       "real_bboxes": batch["real_bboxes_recovered"], "absence_bboxes": batch["unmapped_bboxes"]},
        "coding": {
            "remediated": {"loinc": rem["loinc"], "observations": rem["Observation"], "rxnorm": rem["rxnorm"],
                           "medications": n_med, "ucum": rem["ucum"], "quantities": rem["quantities"]},
            "historical": {"loinc": hist["loinc"], "observations": hist["Observation"], "rxnorm": hist["rxnorm"],
                           "medications": hist["MedicationRequest"] + hist["MedicationStatement"],
                           "ucum": hist["ucum"], "quantities": hist["quantities"]},
        },
        "compound_accuracy": {
            "historical": {"numerator": strict(e2e)["numerator"], "denominator": strict(e2e)["denominator"], "rate": strict(e2e)["rate"]},
            "remediated": {"numerator": strict(e2e_rem)["numerator"], "denominator": strict(e2e_rem)["denominator"], "rate": strict(e2e_rem)["rate"]},
        },
        "in_bundle_verification": {
            "correct": e2e_rem["component_metrics"]["correct_verification_label"]["count_in_bundle"],
            "total": e2e_rem["component_metrics"]["claims_in_fhir_bundle_provenance"]["count"],
            "rate": e2e_rem["component_metrics"]["correct_verification_label"]["rate_in_bundle"]},
        "resource_counts": {k: rem[k] for k in ("Patient", "Organization", "Practitioner", "DiagnosticReport",
                                                "Observation", "MedicationRequest", "MedicationStatement")},
    }
    assert fhir["valid_bundles"] == 34 and fhir["provenance"]["claims_in_audit_report"] == 1826 and n_med == 94
    assert (fhir["compound_accuracy"]["historical"]["rate"], fhir["compound_accuracy"]["remediated"]["rate"]) == (0.1292, 0.5986)

    # ─── Stage B reference run (V6) ───────────────────────────────────────────
    report = (REPO / P_TRAIN).read_text(encoding="utf-8")
    history = [(1, 0.7655, 0.6896, 0.6224), (2, 0.3802, 0.0618, 0.9809), (3, 0.0736, 0.0556, 0.9784),
               (4, 0.0546, 0.0920, 0.9793), (5, 0.0260, 0.0648, 0.9795)]
    for ep, tl, vl, vf in history:  # the markdown table is the only source; assert we copied it right
        assert re.search(rf"\|\s*\**{ep}\**\s*\|\s*\**{tl:.4f}\**\s*\|\s*\**{vl:.4f}\**\s*\|\s*\**{vf:.4f}\**\s*\|", report), ep
    for token in ("1.146522", "0.9809", "0.9519", "0.9551", "0.9898", "484.25", "596,113,470"):
        assert token in report, token
    stage_b_reference = {
        "run_name": "modernbert_text_only_v3", "base_model": "thomas-sounack/BioClinical-ModernBERT-base",
        "checkpoint": "data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt",
        "checkpoint_size_bytes": 596113470, "hardware": "NVIDIA GeForce RTX 3050 6GB Laptop GPU",
        "parameters": 149016579, "max_seq_length": 256, "batch_size": 16, "duration_s": 484.25,
        "temperature": 1.146522, "best_epoch": 2,
        "history": [{"epoch": ep, "train_loss": tl, "val_loss": vl, "val_macro_f1": vf} for ep, tl, vl, vf in history],
        "validation": {"accuracy": 0.9898, "macro_f1": 0.9809, "discrepancy_recall": 0.9913, "ece": 0.0048, "brier": 0.0188,
                       "confusion_matrix": [[362, 0, 0], [4, 455, 0], [0, 5, 57]]},
        "calibration": {"temperature": 1.146522, "ece_before": 0.0063, "ece_after": 0.0032, "nll_before": 0.0542, "nll_after": 0.0513},
        "ood": {"accuracy": s6["accuracy"], "macro_f1": s6["macro_f1"], "discrepancy_recall": s6["discrepancy_recall"],
                "ece": 0.0365, "brier": 0.0858, "confusion_matrix": cm_v6},
    }

    # ─── Playground presets (real V3 OOD examples) ────────────────────────────
    ood_docs = set(splits["ood_doc_ids"])
    ood = [e for e in examples if e["source_doc_id"] in ood_docs]

    def pick(field, label, doc_prefix, **extra):
        return next(e for e in ood if e["field_type"] == field and e["label"] == label
                    and e["source_doc_id"].startswith(doc_prefix) and e["severity"] == "clean"
                    and all(e.get(k) == v for k, v in extra.items()))

    def preset(name, description, ex, field_type=None, **override):
        parts = parse_claim(ex["field_type"], ex["claim"])
        p = {"name": name, "description": description, "field_type": field_type or ex["field_type"],
             "entity": parts["entity"], "value": parts["value"], "unit": parts["unit"],
             "evidence_text": ex["evidence"], "expected_label": ex["label"],
             "source_example_id": ex["example_id"], "source_document_id": ex["source_doc_id"]}
        p.update(override)
        return p

    lab_ok = pick("lab.value", "SUPPORTED", "LAB_")
    verify_presets = [
        preset("Lab value - supported", "Tier 1 numeric lab value that matches the report row.", lab_ok),
        preset("Lab value - discrepancy", "Tier 1 numeric lab value perturbed against the report (value_mismatch).",
               pick("lab.value", "DISCREPANCY", "LAB_", contradiction_type="value_mismatch")),
        preset("Lab flag", "Tier 1 abnormal-flag claim (the field the V1 Stage C prompt targets).",
               pick("lab.flag", "SUPPORTED", "LAB_")),
        preset("Medication name", "Tier 1 medication name on an order sheet.", pick("med.name", "SUPPORTED", "MED_")),
        preset("Medication dose - discrepancy", "Tier 1 dose altered against the order (dose_mismatch).",
               pick("med.dose", "DISCREPANCY", "MED_", contradiction_type="dose_mismatch")),
        preset("Medication route", "Tier 1 route of administration.", pick("med.route", "SUPPORTED", "MED_")),
        preset("Medication frequency", "Tier 1 dosing frequency.", pick("med.frequency", "SUPPORTED", "MED_")),
        preset("Unobserved claim", "Claim whose entity never appears in the evidence window (INSUFFICIENT_EVIDENCE).",
               pick("med.dose", "INSUFFICIENT_EVIDENCE", "MED_")),
        preset("Lab unit (Tier 2)", "Tier 2 field resolved by Stage A alone; derived from the supported lab value example.",
               lab_ok, field_type="lab.unit", value=parse_claim("lab.value", lab_ok["claim"])["unit"], unit=None),
    ]
    assert all(p["value"] for p in verify_presets) and len(verify_presets) == 9

    sources = {
        "headline": [P_DATA_SHEET, P_V8_DIAG, P_V9_DIAG, P_V12, P_V8_EVAL, P_V9_EVAL],
        "leaderboard": [P_V1_V5, P_V8_DIAG, P_V0, P_V9_DIAG, P_DATA_SHEET],
        "confusion_matrices": [P_V1_V5, P_V8_DIAG, P_V9_DIAG],
        "per_class": [P_V1_V5, P_V8_DIAG, P_V9_DIAG],
        "per_field": [P_V1_V5, P_V8_DIAG, P_V9_DIAG],
        "per_template": [P_V1_V5, P_V8_DIAG, P_EXAMPLES],
        "risk_coverage": [P_V12],
        "escalation": [P_V8_DIAG, P_V8_EVAL],
        "prompt_case_study": [P_V0, P_V1_ANALYSIS, P_V8_DIAG, P_STAGE_C],
        "dataset": [P_SPLITS, P_META, P_GEN, P_EXAMPLES],
        "robustness": [P_ROBUST],
        "extraction": [P_E2, P_E3, P_E4, P_DATA_SHEET],
        "fhir": [f"{P_FHIR_REM}/batch_summary.json", P_FHIR_REM, P_FHIR_HIST, P_E2E, P_E2E_REM],
        "stage_b_reference": [P_TRAIN, P_V8_DIAG],
        "verify_presets": [P_EXAMPLES, P_SPLITS],
    }
    doc = __doc__ or ""
    schema = {"description": "Curated Clindoc2FHIR benchmark numbers for the web UI (V3 OOD split = 1,826 claims "
                             "unless noted). Generated by scripts/build_web_data.py; do not edit by hand.",
              "labels": LABELS, "matrix_layout": "rows = ground truth, columns = prediction, order = labels"}
    schema.update({line.split()[0]: " ".join(line.split()[1:])
                   for line in doc.split("Output schema (also embedded as the \"_schema\" key):")[1].splitlines()
                   if line.startswith("  ") and not line.startswith("   ")})
    results = {
        "_schema": schema, "headline": headline, "leaderboard": leaderboard,
        "confusion_matrices": {"labels": LABELS, "matrices": matrices, "stage_a_abstained": a_abstained},
        "per_class": per_class, "per_field": {"fields": FIELDS, "models": per_field}, "per_template": per_template,
        "risk_coverage": risk_coverage, "escalation": escalation, "prompt_case_study": prompt_case_study,
        "dataset": dataset, "robustness": robustness, "extraction": extraction, "fhir": fhir,
        "stage_b_reference": stage_b_reference, "verify_presets": verify_presets, "sources": sources,
    }
    for rel in {p for ps in sources.values() for p in ps}:
        assert (REPO / rel).exists(), rel
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size:,} bytes, {len(results)} sections)")


if __name__ == "__main__":
    main()
