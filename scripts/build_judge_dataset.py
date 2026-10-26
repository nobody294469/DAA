"""Build the Stage C judge fine-tuning set from the open V3 benchmark (scripts/train_judge_lora.py input).

Authority: data/m2_verification_v3 (SHA-256 locked, document-level train/calibration/validation/OOD
splits). Each example is rendered with the live StageCJudge prompt builder + local_judge_messages
(compact), so training text matches inference text exactly. OOD documents are never written here:
they stay the held-out test (eval/eval_local_judge.py-style scoring on escalated OOD claims).
Rationales are short templates (the benchmark has no human rationales); labels are the real targets.

--open-per-source adds label-balanced claims from the open note corpora (scripts/build_open_claims.py);
--flag-copies repeats SUPPORTED lab.flag claims, the judge's weakest case on escalated OOD claims.

Usage (repo root):  $env:PYTHONPATH="src"; python scripts/build_judge_dataset.py [--out data/judge_sft]
  v2 judge set:     ... --out data/judge_sft_v2 --open-per-source 400 --flag-copies 3
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import re

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.m2.dataset import load_examples
from clindoc.m2.splits import load_train_examples, load_validation_examples
from clindoc.verify.stage_c import DEFAULT_STAGE_C_SYSTEM_PROMPT, StageCJudge, StageCJudgeOutput, local_judge_messages

REPO = Path(__file__).resolve().parents[1]
V3 = REPO / "data" / "m2_verification_v3"
OPEN = REPO / "data" / "m2_open"
_NUM_UNIT = re.compile(r"^(-?[\d.,]+)\s*(.*)$")

RATIONALE = {
    "SUPPORTED": "The source document states {claim} as written.",
    "INSUFFICIENT_EVIDENCE": "The source document does not mention this item, so the claim cannot be verified from it.",
    "value_mismatch": "The source document gives a different value than the claim ({claim}).",
    "dose_mismatch": "The source document gives a different dose than the claim ({claim}).",
    "wrong_entity": "The source document names a different test or medication than the claim ({claim}).",
    "temporal_mismatch": "The source document places this at a different time than the claim ({claim}).",
}


def claim_fields(field_type: str, text: str) -> tuple[str, float | None, str | None]:
    """Split a V3 claim string ("Hematocrit: 92.2 %") into the value / numeric / unit the live pipeline carries."""
    value = text.rsplit(": ", 1)[-1].strip()
    if field_type in ("lab.value", "med.dose") and (m := _NUM_UNIT.match(value)):
        num = m.group(1).replace(",", "")
        try:
            return num, float(num), (m.group(2).strip() or None)
        except ValueError:
            pass
    return value, None, None


def to_example(ex) -> dict:
    value, numeric, unit = claim_fields(ex.field_type, ex.claim)
    claim = Claim(claim_id=ex.example_id, document_id=ex.source_doc_id, group_id=ex.group_id, field_type=ex.field_type,
                  value=value, value_numeric=numeric, unit_raw=unit, extraction_method="deterministic",
                  extraction_confidence=1.0, evidence_id=ex.evidence_id or "ev", region_id="r")
    evidence = Evidence(evidence_id=ex.evidence_id or "ev", document_id=ex.source_doc_id, page_number=ex.page_number,
                        text=ex.evidence, char_start=0, char_end=len(ex.evidence), bbox=BBoxModel(x0=0, y0=0, x1=1, y1=1))
    prompt = StageCJudge._build_judge_prompt(None, claim, evidence, ex.evidence, None, None, None, claim_text=ex.claim)
    ctype = ex.contradiction_type if ex.label == "DISCREPANCY" else None
    if ex.label == "DISCREPANCY" and ctype is None:
        ctype = "value_mismatch"
    key = ctype if ex.label == "DISCREPANCY" else ex.label
    target = StageCJudgeOutput(label=ex.label, confidence=0.95, contradiction_type=ctype,
                               rationale=RATIONALE.get(key, RATIONALE["value_mismatch"]).format(claim=ex.claim))
    return {"example_id": ex.example_id, "field_type": ex.field_type, "label": ex.label,
            "messages": local_judge_messages(prompt, DEFAULT_STAGE_C_SYSTEM_PROMPT, StageCJudgeOutput, compact=True),
            "response": target.model_dump_json()}


def open_sample(per_source: int, seed: int) -> list:
    """Label-balanced sample of the open-note train claims (scripts/build_open_claims.py output)."""
    rng = random.Random(seed)
    out = []
    for path in sorted(OPEN.glob("*_train.jsonl")):
        by = {}
        for ex in load_examples(path):
            by.setdefault(ex.label, []).append(ex)
        share = {"SUPPORTED": 0.45, "DISCREPANCY": 0.45, "INSUFFICIENT_EVIDENCE": 0.10}
        for label, rows in sorted(by.items()):
            out += rng.sample(rows, min(len(rows), round(per_source * share[label])))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=REPO / "data" / "judge_sft")
    ap.add_argument("--open-per-source", type=int, default=0,
                    help="add N label-balanced train claims from each data/m2_open/<source>_train.jsonl")
    ap.add_argument("--flag-copies", type=int, default=1,
                    help="copies of each SUPPORTED lab.flag train claim (the judge's weak spot on OOD)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    examples = load_examples(V3 / "all_examples.jsonl")
    manifest = json.loads((V3 / "split_manifest.json").read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    train = load_train_examples(examples, manifest)
    train += [ex for ex in train if ex.field_type == "lab.flag" and ex.label == "SUPPORTED"] * (args.flag_copies - 1)
    train += open_sample(args.open_per_source, args.seed) if args.open_per_source else []
    random.Random(args.seed).shuffle(train)
    for name, rows in (("train", train), ("validation", load_validation_examples(examples, manifest))):
        built = [to_example(ex) for ex in rows]
        with open(args.out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for b in built:
                f.write(json.dumps(b, ensure_ascii=False) + "\n")
        print(f"{name}: {len(built)} examples {dict(Counter(b['label'] for b in built))}")


if __name__ == "__main__":
    main()
