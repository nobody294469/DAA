"""eval_stage_b.py — score a Stage B .pt checkpoint on any M2Example JSONL (accuracy, macro-F1, per class).

Run from the repo root with PYTHONPATH=src:
  python scripts/eval_stage_b.py --checkpoint data/m2_results_v3/checkpoints/<run>.pt --data data/m2_open/test.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from clindoc.m2.dataset import load_examples
from clindoc.m2.evaluate import evaluate_model_on_split
from clindoc.m2.model import ClinicalVerifier, load_tokenizer
from clindoc.m2.schema import M2ModelConfig
from clindoc.verify.stage_b import resolve_base_model_id


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--data", type=Path, action="append", required=True)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = p.parse_args()

    ckpt = torch.load(args.checkpoint, map_location="cpu", weights_only=True)
    config = M2ModelConfig(**ckpt["config"])
    base = resolve_base_model_id(config.model_id)
    model = ClinicalVerifier(model_id=base, aux_mode="none", load_pretrained=False)
    model.load_state_dict(ckpt["model_state"], strict=True)
    model.to(args.device)
    tokenizer = load_tokenizer(base)
    for path in args.data:
        ev = evaluate_model_on_split(model, tokenizer, load_examples(path), args.checkpoint.stem, "ood",
                                     0, None, config, args.device, aux_mode="none",
                                     temperature=float(ckpt.get("temperature") or 1.0))
        cm = ev.confusion_matrix
        acc = sum(cm[i][i] for i in range(len(cm))) / sum(map(sum, cm))
        print(json.dumps({"checkpoint": args.checkpoint.name, "data": str(path), "n": sum(map(sum, cm)),
                          "accuracy": round(acc, 4), "macro_f1": round(ev.macro_f1, 4),
                          "discrepancy_recall": ev.discrepancy_recall, "confusion_matrix": cm}))


if __name__ == "__main__":
    main()
