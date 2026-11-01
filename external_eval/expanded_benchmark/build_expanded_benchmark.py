"""Builder and verification script for the expanded synthetic benchmark (60 documents).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/build_expanded_benchmark.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT))

NOTES_DIR = BENCHMARK_DIR / "notes"
GT_DIR = BENCHMARK_DIR / "ground_truth"

from external_eval.expanded_benchmark.specs_encounters import ENCOUNTER_SPECS
from external_eval.expanded_benchmark.specs_labs import LAB_SPECS
from external_eval.expanded_benchmark.specs_prescriptions import PRESCRIPTION_SPECS


def build_and_verify() -> dict:
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    GT_DIR.mkdir(parents=True, exist_ok=True)

    all_specs = ENCOUNTER_SPECS + PRESCRIPTION_SPECS + LAB_SPECS
    assert len(all_specs) == 60, f"Expected 60 documents, found {len(all_specs)}"

    manifest_docs = []
    field_counts: dict[str, int] = {}
    total_claims = 0

    for spec in all_specs:
        doc_id = spec["doc_id"]
        raw_text = spec["text"]

        # Write text file
        note_path = NOTES_DIR / f"{doc_id}.txt"
        note_path.write_text(raw_text, encoding="utf-8")

        # Verify claims and calculate offsets
        claims_with_offsets = []
        search_start = 0

        for c in spec["claims"]:
            ev = c["evidence_text"]
            idx = raw_text.find(ev, search_start)
            if idx == -1:
                idx = raw_text.find(ev)
            assert idx != -1, f"Evidence '{ev}' not found in {doc_id} text!"

            start_char = idx
            end_char = idx + len(ev)

            # Strict character slice verification
            slice_text = raw_text[start_char:end_char]
            assert slice_text == ev, f"Slice mismatch in {doc_id}: '{slice_text}' != '{ev}'"

            search_start = end_char

            ft = c["field_type"]
            field_counts[ft] = field_counts.get(ft, 0) + 1
            total_claims += 1

            claims_with_offsets.append({
                "document_id": doc_id,
                "field_type": ft,
                "value": c["value"],
                "value_numeric": c.get("value_numeric"),
                "unit_raw": c.get("unit_raw"),
                "evidence_text": ev,
                "char_start": start_char,
                "char_end": end_char,
                "page_number": 1,
            })

        # Save ground truth JSON
        gt_path = GT_DIR / f"{doc_id}.json"
        gt_data = {
            "doc_id": doc_id,
            "category": spec["category"],
            "specialty": spec["specialty"],
            "title": spec["title"],
            "format": spec["format"],
            "features": spec.get("features", []),
            "is_synthetic": True,
            "total_claims": len(claims_with_offsets),
            "claims": claims_with_offsets,
        }
        gt_path.write_text(json.dumps(gt_data, indent=2), encoding="utf-8")

        manifest_docs.append({
            "doc_id": doc_id,
            "category": spec["category"],
            "specialty": spec["specialty"],
            "title": spec["title"],
            "format": spec["format"],
            "features": spec.get("features", []),
            "text_file": str(note_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "gt_file": str(gt_path.relative_to(REPO_ROOT)).replace("\\", "/"),
            "claim_count": len(claims_with_offsets),
        })

    manifest = {
        "benchmark_name": "ClinicalDoc Expanded Diverse Multi-Specialty Synthetic Benchmark",
        "benchmark_version": "2.0-frozen-expanded",
        "created_date": "2026-10-29",
        "license": "Apache 2.0 (Synthea-derived core + original synthetic test fixtures)",
        "total_documents": len(manifest_docs),
        "total_claims": total_claims,
        "document_breakdown": {
            "narrative_encounter": len(ENCOUNTER_SPECS),
            "structured_medication": len(PRESCRIPTION_SPECS),
            "structured_lab": len(LAB_SPECS),
        },
        "specialties_covered": sorted(list({s["specialty"] for s in all_specs})),
        "is_synthetic_evaluation": True,
        "phi_free_guarantee": True,
        "claims_by_field": field_counts,
        "documents": manifest_docs,
    }

    manifest_path = BENCHMARK_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"Successfully generated expanded synthetic benchmark with {len(manifest_docs)} documents.")
    print(f"Total claims verified: {total_claims}")
    print(f"Manifest written to: {manifest_path}")
    print("Claims breakdown:")
    for ft, count in sorted(field_counts.items()):
        print(f"  {ft:25s}: {count}")

    return manifest


if __name__ == "__main__":
    build_and_verify()
