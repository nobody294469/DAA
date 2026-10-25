"""Fine-tune a small local Stage C judge (Qwen2.5-Instruct) with LoRA on the V3 judge set.

Authority: DEC-006 (Qwen-family judge), DEC-013 (JSON output), src/clindoc/verify/lora.py.
Input: data/judge_sft/{train,validation}.jsonl from scripts/build_judge_dataset.py (OOD never seen).
Output: <out>/adapter.pt + adapter_config.json, loadable by `clindoc serve --llm <out>`.
Fits a 4 GB GPU for the 1.5B model: frozen fp16 base, fp32 LoRA, gradient checkpointing, and the
LM head evaluated only on the answer tokens (a full 151k-vocab logit tensor would not fit).

Usage (repo root):
  $env:PYTHONPATH="src"; python scripts/train_judge_lora.py --base ~/.cache/clindoc/Qwen2.5-1.5B-Instruct
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import random
import re
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from clindoc.verify.lora import inject_lora, save_lora

REPO = Path(__file__).resolve().parents[1]
TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj")
LABEL_RE = re.compile(r'"label"\s*:\s*"(SUPPORTED|DISCREPANCY|INSUFFICIENT_EVIDENCE)"')


def encode(tok, row: dict, max_len: int):
    """Token ids of prompt + answer, and the index where the answer starts (loss only after it)."""
    prefix = tok.apply_chat_template(row["messages"], add_generation_prompt=True, tokenize=True)
    answer = tok(row["response"] + "<|im_end|>", add_special_tokens=False)["input_ids"]
    if len(prefix) + len(answer) > max_len:
        return None
    return prefix + answer, len(prefix)


def stratified(rows: list[dict], n: int, seed: int) -> list[dict]:
    """Fixed, label-balanced-ish validation sample (deterministic)."""
    rng = random.Random(seed)
    by = {}
    for r in rows:
        by.setdefault(r["label"], []).append(r)
    per = max(1, n // len(by))
    out = []
    for lab in sorted(by):
        rng.shuffle(by[lab])
        out += by[lab][:per]
    return out


@torch.inference_mode()
def evaluate(model, tok, rows: list[dict], device) -> dict:
    model.eval()
    hits, conf = 0, Counter()
    for r in rows:
        ids = torch.tensor([tok.apply_chat_template(r["messages"], add_generation_prompt=True, tokenize=True)], device=device)
        # use_cache=True: training sets config.use_cache=False, which would recompute the whole prompt per token
        out = model.generate(ids, attention_mask=torch.ones_like(ids), max_new_tokens=24, do_sample=False,
                             pad_token_id=tok.eos_token_id, use_cache=True)
        m = LABEL_RE.search(tok.decode(out[0, ids.shape[1]:], skip_special_tokens=True))
        pred = m.group(1) if m else "UNPARSEABLE"
        hits += pred == r["label"]
        conf[(r["label"], pred)] += 1
    model.train()
    return {"accuracy": round(hits / len(rows), 4), "n": len(rows),
            "confusion": {f"{t}->{p}": c for (t, p), c in sorted(conf.items())}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="local base model dir (e.g. ~/.cache/clindoc/Qwen2.5-1.5B-Instruct)")
    ap.add_argument("--data", type=Path, default=REPO / "data" / "judge_sft")
    ap.add_argument("--out", type=Path, default=None, help="default data/judge_lora/<base name>-v3")
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--r", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--accum", type=int, default=8, help="examples per optimizer step (batch 1 x accum)")
    ap.add_argument("--max-len", type=int, default=1280)  # V3 judge prompts max 1,167 tokens
    ap.add_argument("--eval-n", type=int, default=150, help="validation examples scored after each epoch")
    ap.add_argument("--limit", type=int, default=0, help="train on the first N examples only (smoke test)")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    base = str(Path(args.base).expanduser())
    out = args.out or REPO / "data" / "judge_lora" / f"{Path(base).name}-v3"
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForCausalLM.from_pretrained(base, dtype=torch.float16 if device.type == "cuda" else torch.float32).to(device)
    n_train = inject_lora(model, targets=TARGETS, r=args.r, alpha=args.alpha)
    model.config.use_cache = False
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    print(f"base {Path(base).name}: LoRA r={args.r} on {len(TARGETS)} projections, {n_train / 1e6:.1f}M trainable params")

    load = lambda name: [json.loads(l) for l in open(args.data / f"{name}.jsonl", encoding="utf-8")]
    train_rows, val_rows = load("train"), stratified(load("validation"), args.eval_n, args.seed)
    if args.limit:
        train_rows = train_rows[: args.limit]
    encoded = [e for e in (encode(tok, r, args.max_len) for r in train_rows) if e]
    print(f"train {len(encoded)}/{len(train_rows)} examples (<= {args.max_len} tokens); validation sample {len(val_rows)}")

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    steps = math.ceil(len(encoded) / args.accum) * args.epochs
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / max(1, steps // 20)) *
                                              max(0.0, (steps - s) / steps))  # warmup then linear decay
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    head = model.get_output_embeddings()

    history, best = [], None
    before = evaluate(model, tok, val_rows, device)
    print(f"epoch 0 (base model, compact prompt): val accuracy {before['accuracy']}", flush=True)
    t0, step = time.time(), 0
    for epoch in range(1, args.epochs + 1):
        random.shuffle(encoded)
        run_loss = 0.0
        for i, (ids, start) in enumerate(encoded, 1):
            x = torch.tensor([ids], device=device)
            hidden = model.model(input_ids=x).last_hidden_state[0, start - 1:-1]  # positions that predict the answer
            logits = head(hidden).float()
            loss = torch.nn.functional.cross_entropy(logits, x[0, start:]) / args.accum
            scaler.scale(loss).backward()
            run_loss += loss.item() * args.accum
            if i % args.accum == 0 or i == len(encoded):
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad(set_to_none=True)
                sched.step()
                step += 1
            if i % 200 == 0:
                rate = (time.time() - t0) / (step * args.accum + 1e-9)
                print(f"epoch {epoch} {i}/{len(encoded)} loss {run_loss / i:.4f} "
                      f"eta {rate * (steps * args.accum - step * args.accum) / 60:.0f} min", flush=True)
        val = evaluate(model, tok, val_rows, device)
        history.append({"epoch": epoch, "train_loss": round(run_loss / len(encoded), 4), "val": val})
        print(f"epoch {epoch}: train loss {run_loss / len(encoded):.4f}, val accuracy {val['accuracy']}", flush=True)
        if best is None or val["accuracy"] > best:
            best = val["accuracy"]
            save_lora(model, out, {
                "base_model": base, "targets": list(TARGETS), "r": args.r, "alpha": args.alpha, "format": "compact",
                "trained_on": "data/judge_sft/train.jsonl (V3 train split)", "epoch": epoch, "val_before": before,
                "val": val, "history": history, "lr": args.lr, "accum": args.accum, "seed": args.seed,
                "minutes": round((time.time() - t0) / 60, 1)})
            print(f"  saved adapter -> {out}", flush=True)
    print(f"done in {(time.time() - t0) / 60:.1f} min; best val accuracy {best} (base model {before['accuracy']})")


if __name__ == "__main__":
    main()
