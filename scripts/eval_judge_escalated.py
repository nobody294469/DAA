"""eval_judge_escalated.py — score a Stage C judge on the V3 OOD claims the cascade escalated to Stage C.

These are the hardest claims (Stage A and B disagreed or were unsure), from documents the judge never
trained on. Ground truth and the frozen paper Qwen-7B verdicts come from the V8 cascade diagnostics.
Claims are rebuilt the way the live pipeline sends them (value + unit + claim_text).

Run from the repo root with PYTHONPATH=src:
  python scripts/eval_judge_escalated.py --model data/judge_lora/Qwen2.5-1.5B-Instruct-v3 [--per-label 5]
  (--per-label 0 scores every escalated claim, 381 of them)
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from build_judge_dataset import claim_fields  # noqa: E402

from clindoc.extract.contracts import BBoxModel, Claim, Evidence  # noqa: E402
from clindoc.verify.stage_c import LocalTransformersJudgeClient, StageCJudge  # noqa: E402

DIAG = REPO / "experiments/V8-verification-cascade/phase4_cascade_diagnostics.json"


def escalated(per_label: int) -> list[dict]:
    rows = [r for r in json.loads(DIAG.read_text(encoding="utf-8"))["diagnostics"]["ood"] if r["stage_c"]["invoked"]]
    if not per_label:
        return rows
    by = defaultdict(list)
    for r in rows:  # deterministic, field-diverse sample per ground-truth label
        by[r["ground_truth_label"]].append(r)
    sample = []
    for _, group in sorted(by.items()):
        group.sort(key=lambda r: (r["field_type"], r["example_id"]))
        sample += group[::max(1, len(group) // per_label)][:per_label]
    return sample


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, help="LoRA adapter dir or local model dir")
    ap.add_argument("--per-label", type=int, default=5)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    judge = StageCJudge(client=LocalTransformersJudgeClient(args.model, device=args.device))
    rows, hits, hits_7b, per = escalated(args.per_label), 0, 0, Counter()
    t0 = time.time()
    for i, r in enumerate(rows, 1):
        c, e = r["claim"], r["evidence"]
        value, numeric, unit = claim_fields(r["field_type"], c["value"])
        claim = Claim(claim_id=c["claim_id"], document_id=r["document_id"], group_id=r.get("group_id"),
                      field_type=r["field_type"], value=value, value_numeric=numeric, unit_raw=unit,
                      negated=bool(c.get("negated")), extraction_method="deterministic", extraction_confidence=1.0,
                      evidence_id=e["evidence_id"], region_id="r")
        ev = Evidence(evidence_id=e["evidence_id"], document_id=r["document_id"], page_number=1, text=e["text"],
                      char_start=0, char_end=len(e["text"]), bbox=BBoxModel(x0=0, y0=0, x1=1, y1=1))
        res = judge.judge(claim=claim, evidence=ev, context_text=e["text"], stage_a_label=r["stage_a"]["label"],
                          stage_b_label=r["stage_b"]["label"], stage_b_confidence=r["stage_b"]["calibrated_confidence"],
                          claim_text=c["value"])
        gt = r["ground_truth_label"]
        hits += res.label == gt
        hits_7b += r["stage_c"]["label"] == gt
        per[(gt, res.label == gt)] += 1
        if i % 25 == 0 or i == len(rows):
            print(f"{i}/{len(rows)} local {hits / i:.1%} | paper Qwen-7B {hits_7b / i:.1%} | "
                  f"{(time.time() - t0) / i:.1f}s/claim", flush=True)
    by_label = {lab: f"{per[(lab, True)]}/{per[(lab, True)] + per[(lab, False)]}"
                for lab in sorted({k[0] for k in per})}
    print(json.dumps({"model": Path(args.model).name, "n": len(rows), "accuracy": round(hits / len(rows), 4),
                      "paper_qwen7b_accuracy": round(hits_7b / len(rows), 4), "by_label": by_label}))


if __name__ == "__main__":
    main()
