"""generate_v3_benchmark.py — Generate canonical M2 v3 verification benchmark.

Adheres strictly to:
  - Generates to data/m2_verification_v3/
  - Leaves data/m2_verification/ untouched
  - Enforces PreSerializationValidationGate
  - Builds canonical splits (86 CV, 34 OOD, 52 train, 17 calib, 17 val)
  - Enforces DEC-011 class balance floors
  - Saves all_examples.jsonl, split_manifest.json, generation_manifest.json, metadata.json
"""
import datetime
import json
from pathlib import Path

from clindoc.m2.generation import generate_m2_dataset
from clindoc.m2.splits import build_splits, save_split_manifest, validate_no_leakage
from clindoc.m2.dataset import save_examples, compute_metadata, assert_class_balance, print_dataset_summary

OUT_DIR = Path("data/m2_verification_v3")
OUT_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 70)
print("GENERATING M2 VERIFICATION BENCHMARK V3 (FULL REGENERATION)")
print(f"Target Directory: {OUT_DIR}")
print("=" * 70)

# 1. Generate full dataset with PreSerializationValidationGate
examples, stats = generate_m2_dataset(include_narrative=True, seed=42)
print(f"\nGenerated {len(examples)} candidate-passed examples.")
print("Rejection diagnostics:")
for reason, count in stats.get("diagnostics", {}).items():
    print(f"  {reason}: {count}")

# 2. Build stratified splits
manifest = build_splits(examples, n_splits=5, seed=42)
validate_no_leakage(manifest)
print("\nSplit validation: PASSED (zero document, group, or OOD leakage).")
print(f"Document counts: {manifest['document_counts']}")

# 3. Assign split tags to examples
ood_set = set(manifest["ood_doc_ids"])
for ex in examples:
    ex.split = "ood" if ex.source_doc_id in ood_set else "cv"

# 4. Save dataset and split manifest
save_examples(examples, OUT_DIR / "all_examples.jsonl")
save_split_manifest(manifest, OUT_DIR / "split_manifest.json")
print(f"Saved {OUT_DIR / 'all_examples.jsonl'}")
print(f"Saved {OUT_DIR / 'split_manifest.json'}")

# 5. Compute and save metadata
metadata = compute_metadata(examples, manifest["ood_doc_ids"], manifest["cv_doc_ids"])
meta_dict = metadata.model_dump()
meta_dict["generation_stats"] = stats
with open(OUT_DIR / "metadata.json", "w", encoding="utf-8") as f:
    json.dump(meta_dict, f, indent=2)
print(f"Saved {OUT_DIR / 'metadata.json'}")

# 6. Assert Class Balance Floor (DEC-011)
assert_class_balance(metadata, Path("configs/class_balance_floor.yaml"))
print("DEC-011 class-balance floor assertion: PASSED.")

# 7. Save generation manifest
gen_manifest = {
    "dataset_version": "3.0-corrected-canonical",
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "seed": 42,
    "total_documents": len(manifest["cv_doc_ids"]) + len(manifest["ood_doc_ids"]),
    "cv_documents": len(manifest["cv_doc_ids"]),
    "ood_documents": len(manifest["ood_doc_ids"]),
    "total_examples": len(examples),
    "total_groups": metadata.total_groups,
    "by_field_type": metadata.by_field_type,
    "by_class": metadata.label_counts,
    "by_contradiction_type": metadata.by_contradiction_type,
    "class_balance_status": "PASSED_DEC_011",
    "context_window_config": metadata.context_window_config,
    "rejection_diagnostics": stats.get("diagnostics", {}),
    "accounting": {
        "clean_lab": sum(1 for e in examples if e.claim_type == "lab" and e.severity == "clean"),
        "clean_med": sum(1 for e in examples if e.claim_type == "med" and e.severity == "clean" and not e.doc_template.startswith("narrative_")),
        "degraded_lab": sum(1 for e in examples if e.claim_type == "lab" and e.severity != "clean"),
        "degraded_med": sum(1 for e in examples if e.claim_type == "med" and e.severity != "clean" and not e.doc_template.startswith("narrative_")),
        "narrative": sum(1 for e in examples if e.doc_template.startswith("narrative_")),
    }
}
with open(OUT_DIR / "generation_manifest.json", "w", encoding="utf-8") as f:
    json.dump(gen_manifest, f, indent=2)
print(f"Saved {OUT_DIR / 'generation_manifest.json'}")

print_dataset_summary(metadata)
print("\nFULL V3 GENERATION COMPLETE!")
