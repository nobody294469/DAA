"""Run full benchmark integrity audit and output summary JSON for report generation."""
import json
from collections import Counter, defaultdict
from pathlib import Path

from clindoc.m2.schema import M2Example
from clindoc.audit.benchmark_auditor import (
    BenchmarkAuditor,
    ProvenanceStatus,
    SemanticCategory,
    IntegrityLevel,
)

def run_audit():
    auditor = BenchmarkAuditor()
    canonical_path = Path("data/m2_verification/all_examples.jsonl")
    manifest_path = Path("data/m2_verification/split_manifest.json")
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    examples = []
    with open(canonical_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                examples.append(M2Example.model_validate_json(line))
                
    print(f"Loaded {len(examples)} examples.")
    
    records = auditor.audit_dataset(examples)
    print(f"Audited {len(records)} records.")
    
    # Document and Group counts
    docs = set(ex.source_doc_id for ex in examples)
    groups = set(ex.group_id for ex in examples)
    
    # Ground truth counts
    gt_counts = Counter(ex.label.value for ex in examples)
    
    # Field counts
    field_counts = Counter(ex.field_type for ex in examples)
    
    # CV vs OOD mapping
    ood_doc_ids = set(manifest.get("ood_doc_ids", []))
    cv_doc_ids = set(manifest.get("cv_doc_ids", []))
    
    split_counts = {"CV": 0, "OOD": 0, "OTHER": 0}
    for ex in examples:
        if ex.source_doc_id in ood_doc_ids:
            split_counts["OOD"] += 1
        elif ex.source_doc_id in cv_doc_ids:
            split_counts["CV"] += 1
        else:
            split_counts["OTHER"] += 1
            
    # Provenance counts
    prov_counts = Counter(r.provenance_status.value for r in records)
    
    # Semantic counts
    sem_counts = Counter(r.semantic_category.value for r in records)
    
    # Integrity counts
    integ_counts = Counter(r.integrity_level.value for r in records)
    
    # Remediation counts
    remed_counts = Counter(r.remediation_bucket for r in records)
    
    # Conflicts
    conflicts = [r for r in records if r.is_conflict_with_gt]
    conflict_breakdown = Counter(f"{r.ground_truth_label} -> {r.semantic_category.value}" for r in conflicts)
    
    # Breakdown by severity (clean vs degraded)
    sev_breakdown = defaultdict(lambda: Counter())
    for r in records:
        sev_breakdown[r.severity][r.integrity_level.value] += 1
        
    # High priority examples (e.g. Ex9, Ex12 style header fallbacks, out-of-bounds, wrong entity in SUPPORTED)
    header_fallbacks = [r for r in records if r.provenance_status == ProvenanceStatus.CORRUPTED_DEFAULT]
    out_of_bounds = [r for r in records if r.provenance_status == ProvenanceStatus.OUT_OF_BOUNDS]
    supported_conflicts = [r for r in records if r.ground_truth_label == "SUPPORTED" and r.semantic_category != SemanticCategory.SUPPORTS_CLAIM]
    
    # Assemble summary
    summary = {
        "dataset_summary": {
            "total_examples": len(examples),
            "total_documents": len(docs),
            "total_groups": len(groups),
            "label_counts": dict(gt_counts),
            "field_counts": dict(field_counts),
            "split_counts": split_counts,
            "manifest_example_counts": manifest.get("example_counts", {}),
            "manifest_doc_counts": manifest.get("document_counts", {}),
        },
        "provenance_validation": {
            "counts": dict(prov_counts),
            "valid_atomic": prov_counts.get("VALID_ATOMIC", 0),
            "valid_verbatim": prov_counts.get("VALID_VERBATIM", 0),
            "corrupted_default_header": prov_counts.get("CORRUPTED_DEFAULT", 0),
            "out_of_bounds": prov_counts.get("OUT_OF_BOUNDS", 0),
            "degraded_offset_mismatch": prov_counts.get("DEGRADED_OFFSET_MISMATCH", 0),
            "missing_docs": prov_counts.get("MISSING_DOC", 0),
        },
        "semantic_audit": dict(sem_counts),
        "integrity_assessment": {
            "levels": dict(integ_counts),
            "remediation": dict(remed_counts),
            "severity_breakdown": {k: dict(v) for k, v in sev_breakdown.items()},
        },
        "conflicts": {
            "total_conflicts": len(conflicts),
            "breakdown": dict(conflict_breakdown),
        },
        "high_priority_samples": [
            {
                "example_id": r.example_id,
                "doc_id": r.source_doc_id,
                "field": r.field_type,
                "claim": r.claim,
                "ground_truth": r.ground_truth_label,
                "severity": r.severity,
                "char_start": r.char_start,
                "char_end": r.char_end,
                "page": r.page_number,
                "provenance_status": r.provenance_status.value,
                "semantic_category": r.semantic_category.value,
                "integrity_level": r.integrity_level.value,
                "problem": r.problem_reason,
                "evidence_snippet": r.evidence_snippet,
            }
            for r in (header_fallbacks[:10] + out_of_bounds + supported_conflicts[:15])
        ]
    }
    
    Path("reports").mkdir(exist_ok=True)
    with open("reports/audit_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
        
    print("Audit summary successfully written to reports/audit_summary.json")
    print(json.dumps({
        "total": len(examples),
        "clean": integ_counts.get("CLEAN", 0),
        "suspicious": integ_counts.get("SUSPICIOUS", 0),
        "invalid": integ_counts.get("DEFINITELY_INVALID", 0),
        "manual_review": remed_counts.get("manual_review", 0),
    }, indent=2))

if __name__ == "__main__":
    run_audit()
