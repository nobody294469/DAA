#!/usr/bin/env python3
"""Freeze reproducible 200-record evaluation subset from Glaucoma_Med_Dataset.

Selection methodology:
- 150 positive records containing medication entities
- 50 negative records with zero clinical annotations
- Deterministic pseudo-random selection with seed 42
- Saved to external_eval/glaucoma_eval_subset_200.json
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
data_path = REPO_ROOT / "external_eval" / "glaucoma_med_dataset" / "Annotations" / "Combined_annotations.jsonl"
raw_lines = data_path.read_text(encoding="utf-8", errors="ignore").splitlines()

records = []
for idx, line in enumerate(raw_lines):
    line_clean = line.strip()
    if not line_clean:
        continue
    repaired = line_clean
    if re.search(r'\"id\":\s*\*+', repaired):
        repaired = re.sub(r'\"id\":\s*(\*+)', r'"id": "\1"', repaired)
    repaired = re.sub(r'\\(\*+)', r'\1', repaired)
    repaired = re.sub(r'\\([^"\\/bfnrtu])', r'\1', repaired)
    try:
        data = json.loads(repaired)
        records.append({
            "record_id": str(data.get("id", idx + 1)),
            "line_num": idx + 1,
            "text": data.get("text", ""),
            "labels": data.get("labels", []),
        })
    except Exception:
        pass

print(f"Total records loaded: {len(records)}")

RELEVANT_LABELS = {"DRUG", "ROUTE", "FREQUENCY", "DOSAGE", "STRENGTH", "DURATION"}
pos_records = []
neg_records = []

for r in records:
    labels = r.get("labels", [])
    has_rel = any(l[2] in RELEVANT_LABELS for l in labels)
    if has_rel:
        pos_records.append(r)
    elif len(labels) == 0:
        neg_records.append(r)

print(f"Positive records pool: {len(pos_records)}")
print(f"Zero-label negative records pool: {len(neg_records)}")

# Deterministic frozen subset: 150 positive + 50 negative = 200 total
rng = random.Random(42)
sampled_pos = rng.sample(pos_records, 150)
sampled_neg = rng.sample(neg_records, 50)

for r in sampled_pos:
    r["is_negative"] = False
for r in sampled_neg:
    r["is_negative"] = True

subset = sampled_pos + sampled_neg
rng.shuffle(subset)

out_file = REPO_ROOT / "external_eval" / "glaucoma_eval_subset_200.json"
subset_data = {
    "dataset": "Glaucoma_Med_Dataset (https://github.com/jche253/Glaucoma_Med_Dataset)",
    "subset_size": len(subset),
    "positive_records_count": len(sampled_pos),
    "negative_records_count": len(sampled_neg),
    "sampling_seed": 42,
    "selection_method": "Stratified deterministic random sample (150 positive records with medication labels, 50 negative records with zero labels, seed=42)",
    "record_ids": [r["record_id"] for r in subset],
    "records": subset,
}

out_file.write_text(json.dumps(subset_data, indent=2), encoding="utf-8")
print(f"Saved frozen 200-record evaluation subset to {out_file.relative_to(REPO_ROOT)}")

# Entity counts in frozen subset
entity_counts = Counter()
for r in subset:
    for s, e, lbl in r["labels"]:
        entity_counts[lbl] += 1

print("\nEntity counts in frozen subset:")
for lbl, cnt in entity_counts.most_common():
    print(f"  {lbl:15}: {cnt}")
