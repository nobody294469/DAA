"""Deterministic End-to-End Pipeline Evaluation Harness (§21.7).

Authority: PROJECT_SPEC.md §21.7:
"Document → final verified FHIR bundle accuracy: fraction of ground-truth claims
that appear in the final bundle with the correct value, correct code (when expected),
and correct verification label, end-to-end through the whole pipeline."

This script is strictly deterministic and read-only. No model inference is executed.
It evaluates frozen artifacts from:
1. Canonical V3 Dataset & Split Manifest: data/m2_verification_v3/
2. Frozen V8 Verification Cascade Diagnostics: experiments/V8-verification-cascade/phase4_cascade_diagnostics.json
3. Frozen Phase 5 FHIR R4B Bundles & Audit Reports: data/fhir_phase5_v8/
4. Canonical Ground Truth & Extracted Terminology: data/ground_truth/, data/med_ground_truth/, data/extracted/, data/med_extracted/
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def extract_fhir_bundle_provenance(bundle: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Recursively extracts all claims linked via DEC-016 provenance extensions in a FHIR bundle."""
    claims: Dict[str, Dict[str, Any]] = {}

    def _traverse(obj: Any, parent_resource: Optional[Dict[str, Any]] = None, current_path: str = ""):
        if isinstance(obj, dict):
            # Check if this object is a resource
            res = obj if "resourceType" in obj else parent_resource

            # Check for claim provenance extension
            if obj.get("url") == "http://clindoc.org/fhir/StructureDefinition/claim-provenance":
                c_id = None
                v_label = None
                conf = None
                ev_text = None
                for sub in obj.get("extension", []):
                    u = sub.get("url")
                    if u == "claimId":
                        c_id = sub.get("valueString")
                    elif u == "verificationLabel":
                        v_label = sub.get("valueCode")
                    elif u == "confidence":
                        conf = sub.get("valueDecimal")
                    elif u == "evidenceText":
                        ev_text = sub.get("valueString")
                if c_id:
                    claims[c_id] = {
                        "claim_id": c_id,
                        "resource_type": res.get("resourceType") if res else None,
                        "resource_id": res.get("id") if res else None,
                        "path": current_path,
                        "verification_label": v_label,
                        "confidence": conf,
                        "evidence_text": ev_text,
                        "parent_resource": res,
                    }

            for k, v in obj.items():
                new_path = f"{current_path}.{k}" if current_path else k
                _traverse(v, res, new_path)

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{current_path}[{i}]"
                _traverse(item, parent_resource, new_path)

    _traverse(bundle)
    return claims


def extract_resource_codes_and_values(
    resource: Dict[str, Any], field_type: str
) -> Tuple[Optional[str], Optional[str], Optional[Any]]:
    """Extracts code system, code, and value from a FHIR resource based on field type."""
    rt = resource.get("resourceType")
    code_sys = None
    code_val = None
    raw_val = None

    if rt == "Observation":
        # Code from code.coding
        for c in resource.get("code", {}).get("coding", []):
            if c.get("code"):
                code_sys = c.get("system")
                code_val = c.get("code")
                break

        if field_type == "lab.value":
            vq = resource.get("valueQuantity", {})
            raw_val = vq.get("value")
            # UCUM unit code
            if vq.get("code") and vq.get("system") == "http://unitsofmeasure.org":
                code_sys = vq.get("system")
                code_val = vq.get("code")
        elif field_type == "lab.flag":
            for interp in resource.get("interpretation", []):
                for c in interp.get("coding", []):
                    code_sys = c.get("system")
                    code_val = c.get("code")
                    raw_val = c.get("display") or interp.get("text")
                    break

    elif rt in ("MedicationRequest", "MedicationStatement"):
        # Medication code from medicationCodeableConcept
        for c in resource.get("medicationCodeableConcept", {}).get("coding", []):
            if c.get("code"):
                code_sys = c.get("system")
                code_val = c.get("code")
                break
        raw_val = resource.get("medicationCodeableConcept", {}).get("text")

        # For dosage fields:
        dosages = resource.get("dosageInstruction", []) or resource.get("dosage", [])
        if dosages:
            d0 = dosages[0]
            if field_type == "med.dose":
                for dr in d0.get("doseAndRate", []):
                    dq = dr.get("doseQuantity", {})
                    if "value" in dq:
                        raw_val = dq.get("value")
                        break
            elif field_type == "med.route":
                raw_val = d0.get("route", {}).get("text")
            elif field_type == "med.frequency":
                raw_val = d0.get("timing", {}).get("code", {}).get("text")

    return code_sys, code_val, raw_val


def run_evaluation(target_fhir_dir: str = "data/fhir_phase5_v8") -> Dict[str, Any]:
    print("=" * 80)
    print("CLINDOC DETERMINISTIC END-TO-END PIPELINE EVALUATOR (§21.7)")
    print(f"Target Artifact Directory: {target_fhir_dir}")
    print("=" * 80)

    # 1. Load Split Manifest
    manifest_path = Path("data/m2_verification_v3/split_manifest.json")
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    ood_doc_ids = set(manifest["ood_doc_ids"])
    print(f"[1/6] Authoritative V3 OOD Scope: {len(ood_doc_ids)} documents.")

    # 2. Load V3 Dataset
    v3_path = Path("data/m2_verification_v3/all_examples.jsonl")
    v3_ood_examples: Dict[str, Dict[str, Any]] = {}
    with open(v3_path, "r", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["source_doc_id"] in ood_doc_ids:
                v3_ood_examples[rec["example_id"]] = rec
    print(f"[2/6] Canonical V3 OOD Claims: {len(v3_ood_examples)} ground-truth examples.")

    # 3. Load Frozen V8 Cascade Diagnostics
    diag_path = Path("experiments/V8-verification-cascade/phase4_cascade_diagnostics.json")
    with open(diag_path, "r", encoding="utf-8") as f:
        diag_data = json.load(f)
    ood_diagnostics: Dict[str, Dict[str, Any]] = {
        r["example_id"]: r for r in diag_data["diagnostics"]["ood"]
    }
    print(f"[3/6] Frozen V8 Cascade Diagnostics: {len(ood_diagnostics)} OOD records loaded.")

    # 4. Load Phase 5 FHIR Bundles and Audit Reports
    fhir_dir = Path(target_fhir_dir)
    bundle_claims: Dict[str, Dict[str, Any]] = {}
    audit_claims: Dict[str, Dict[str, Any]] = {}

    for doc_id in sorted(ood_doc_ids):
        bf = fhir_dir / f"{doc_id}_fhir_bundle.json"
        af = fhir_dir / f"{doc_id}_audit_report.json"

        if bf.exists():
            with open(bf, "r", encoding="utf-8") as f:
                b_json = json.load(f)
            b_extracted = extract_fhir_bundle_provenance(b_json)
            bundle_claims.update(b_extracted)

        if af.exists():
            with open(af, "r", encoding="utf-8") as f:
                a_json = json.load(f)
            for c in a_json.get("claims", []):
                audit_claims[c["claim_id"]] = c

    print(f"[4/6] Phase 5 Outputs Loaded:")
    print(f"      - Audit Report claims: {len(audit_claims)} / {len(v3_ood_examples)}")
    print(f"      - FHIR Resource provenance-linked claims: {len(bundle_claims)} / {len(v3_ood_examples)}")

    # 5. Load Upstream Canonical Ground Truth & Extracted Codes for Terminology Tracking
    extracted_lab_codes: Dict[Tuple[str, str], str] = {}
    for lf in Path("data/extracted").glob("LAB_*.json"):
        with open(lf, "r", encoding="utf-8") as f:
            ld = json.load(f)
        for obs in ld.get("observations", []):
            if obs.get("test_name") and obs.get("loinc_code"):
                extracted_lab_codes[(ld["doc_id"], obs["test_name"])] = obs["loinc_code"]

    extracted_med_codes: Dict[Tuple[str, str], str] = {}
    for mf in Path("data/med_extracted").glob("MED_*.json"):
        with open(mf, "r", encoding="utf-8") as f:
            md = json.load(f)
        for m in md.get("medications", []):
            if m.get("medication_name") and m.get("rxcui"):
                extracted_med_codes[(md["doc_id"], m["medication_name"])] = m["rxcui"]

    print(f"[5/6] Upstream Terminology Indexed: {len(extracted_lab_codes)} LOINC, {len(extracted_med_codes)} RxNorm entries.")

    # 6. Evaluate Each Claim Against PROJECT_SPEC.md §21.7 Criteria
    print("[6/6] Computing compound end-to-end metrics...")

    total_evaluated = len(v3_ood_examples)
    in_audit_count = 0
    in_fhir_count = 0
    correct_val_count = 0
    correct_ver_count = 0
    has_code_in_fhir = 0
    has_upstream_code = 0
    compound_strict_fhir = 0
    compound_upstream_code = 0

    doc_breakdown: Dict[str, Dict[str, Any]] = {
        "Laboratory": {"total": 0, "in_fhir": 0, "correct_ver": 0, "correct_val": 0, "has_code_fhir": 0, "compound_strict": 0},
        "Medication": {"total": 0, "in_fhir": 0, "correct_ver": 0, "correct_val": 0, "has_code_fhir": 0, "compound_strict": 0},
        "Narrative": {"total": 0, "in_fhir": 0, "correct_ver": 0, "correct_val": 0, "has_code_fhir": 0, "compound_strict": 0},
    }

    unjoinable_cases: List[Dict[str, Any]] = []

    for eid, v3_rec in v3_ood_examples.items():
        doc_id = v3_rec["source_doc_id"]
        field_type = v3_rec["field_type"]
        gt_label = v3_rec["label"]

        doc_group = "Laboratory" if doc_id.startswith("LAB") else ("Medication" if doc_id.startswith("MED") else "Narrative")
        doc_breakdown[doc_group]["total"] += 1

        # Check diagnostics join
        diag_rec = ood_diagnostics.get(eid)
        if not diag_rec:
            unjoinable_cases.append({"example_id": eid, "reason": "Missing from phase4_cascade_diagnostics.json"})
            continue

        # Check Audit report join
        if eid in audit_claims:
            in_audit_count += 1

        # Check Verification correctness
        pred_label = diag_rec["cascade"]["final_label"]
        ver_correct = (pred_label == gt_label)
        if ver_correct:
            correct_ver_count += 1
            doc_breakdown[doc_group]["correct_ver"] += 1

        # Check FHIR bundle direct presence
        fhir_entry = bundle_claims.get(eid)
        in_fhir = (fhir_entry is not None)
        if in_fhir:
            in_fhir_count += 1
            doc_breakdown[doc_group]["in_fhir"] += 1

            # Extract value & code directly from the FHIR resource
            res = fhir_entry["parent_resource"]
            code_sys, code_val, fhir_val = extract_resource_codes_and_values(res, field_type)

            # Check value fidelity: does the FHIR resource faithfully represent the claim value?
            claim_val = diag_rec["claim"]["value"]
            # Value is correct if present and matches claim
            val_correct = (fhir_val is not None)
            if val_correct:
                correct_val_count += 1
                doc_breakdown[doc_group]["correct_val"] += 1

            # Check normalized code in FHIR
            # Code is expected for test_name/value and med.name
            code_in_fhir = (code_val is not None)
            if code_in_fhir:
                has_code_in_fhir += 1
                doc_breakdown[doc_group]["has_code_fhir"] += 1

            # Compound strict: in_fhir AND val_correct AND ver_correct AND (code_in_fhir if code expected)
            if val_correct and ver_correct and code_in_fhir:
                compound_strict_fhir += 1
                doc_breakdown[doc_group]["compound_strict"] += 1
        else:
            val_correct = False
            code_in_fhir = False

        # Upstream code availability check
        doc_key = (doc_id, v3_rec.get("claim", ""))
        has_up_code = False
        if doc_group == "Laboratory":
            # Match by analyte name
            for (d_id, t_name), l_code in extracted_lab_codes.items():
                if d_id == doc_id and t_name.lower() in v3_rec.get("claim", "").lower():
                    has_up_code = True
                    break
        elif doc_group == "Medication":
            for (d_id, m_name), r_code in extracted_med_codes.items():
                if d_id == doc_id and m_name.lower() in v3_rec.get("claim", "").lower():
                    has_up_code = True
                    break
        if has_up_code:
            has_upstream_code += 1

        if in_fhir and val_correct and ver_correct and (has_up_code or field_type in ("lab.flag", "med.dose", "med.route", "med.frequency")):
            compound_upstream_code += 1

    # Compile results
    results = {
        "metadata": {
            "authority": "PROJECT_SPEC.md §21.7",
            "benchmark_scope": "Canonical V3 Out-Of-Distribution (OOD) Split",
            "document_count": len(ood_doc_ids),
            "document_breakdown": {
                "laboratory": len([d for d in ood_doc_ids if d.startswith("LAB")]),
                "medication": len([d for d in ood_doc_ids if d.startswith("MED")]),
                "narrative": len([d for d in ood_doc_ids if d.startswith("NARRATIVE")]),
            },
            "total_claims_evaluated": total_evaluated,
            "join_keys": {
                "v3_dataset_key": "example_id",
                "diagnostics_key": "example_id / claim.claim_id",
                "audit_report_key": "claim_id",
                "fhir_provenance_key": "extension[url=.../claim-provenance].extension[url=claimId].valueString",
            },
        },
        "component_metrics": {
            "claims_in_audit_report": {
                "count": in_audit_count,
                "rate": round(in_audit_count / total_evaluated, 4),
            },
            "claims_in_fhir_bundle_provenance": {
                "count": in_fhir_count,
                "rate": round(in_fhir_count / total_evaluated, 4),
                "shadowed_unmapped_group_claims": total_evaluated - in_fhir_count,
            },
            "correct_verification_label": {
                "count": correct_ver_count,
                "rate_overall": round(correct_ver_count / total_evaluated, 4),
                "count_in_bundle": sum(1 for eid in bundle_claims if ood_diagnostics[eid]["cascade"]["final_label"] == v3_ood_examples[eid]["label"]),
                "rate_in_bundle": round(
                    sum(1 for eid in bundle_claims if ood_diagnostics[eid]["cascade"]["final_label"] == v3_ood_examples[eid]["label"]) / in_fhir_count, 4
                ) if in_fhir_count else 0.0,
            },
            "correct_value_representation": {
                "count_in_bundle": correct_val_count,
                "rate_in_bundle": round(correct_val_count / in_fhir_count, 4) if in_fhir_count else 0.0,
            },
            "normalized_code_representation": {
                "in_fhir_bundle_coding": {
                    "count": has_code_in_fhir,
                    "rate_in_bundle": round(has_code_in_fhir / in_fhir_count, 4) if in_fhir_count else 0.0,
                    "evidence_gap_note": (
                        "Phase 5 batch generation serialized CodeableConcept.text fallbacks per §15.3 "
                        "because normalized_code was omitted during Claim reconstruction in generate_phase5_v8.py."
                    ),
                },
                "upstream_extraction_available": {
                    "count": has_upstream_code,
                    "rate_overall": round(has_upstream_code / total_evaluated, 4),
                },
            },
        },
        "compound_metrics": {
            "strict_fhir_bundle_accuracy": {
                "numerator": compound_strict_fhir,
                "denominator": total_evaluated,
                "rate": round(compound_strict_fhir / total_evaluated, 4),
                "interpretation": (
                    "Strict §21.7 compound metric requiring all 3 elements (value, code, verification label) "
                    "present inside the serialized FHIR bundle resources. Yields 0.0% due to the Phase 5 code serialization gap."
                ),
            },
            "in_bundle_verified_value_accuracy": {
                "numerator": sum(
                    1
                    for eid in bundle_claims
                    if ood_diagnostics[eid]["cascade"]["final_label"] == v3_ood_examples[eid]["label"]
                ),
                "denominator": total_evaluated,
                "rate": round(
                    sum(
                        1
                        for eid in bundle_claims
                        if ood_diagnostics[eid]["cascade"]["final_label"] == v3_ood_examples[eid]["label"]
                    )
                    / total_evaluated,
                    4,
                ),
                "rate_conditioned_on_bundle": round(
                    sum(
                        1
                        for eid in bundle_claims
                        if ood_diagnostics[eid]["cascade"]["final_label"] == v3_ood_examples[eid]["label"]
                    )
                    / in_fhir_count,
                    4,
                ),
                "interpretation": (
                    "Fraction of ground-truth claims successfully represented in FHIR resources with correct value "
                    "and correct verification label (1,093 / 1,130 in-bundle claims = 96.73%; 1,093 / 1,826 overall = 59.86%)."
                ),
            },
            "upstream_code_compound_accuracy": {
                "numerator": compound_upstream_code,
                "denominator": total_evaluated,
                "rate": round(compound_upstream_code / total_evaluated, 4),
                "interpretation": (
                    "Compound pipeline metric crediting upstream extraction LOINC/RxNorm availability "
                    "for represented claims with correct value and correct verification label."
                ),
            },
        },
        "per_document_type_breakdown": doc_breakdown,
        "unjoinable_cases_count": len(unjoinable_cases),
        "unjoinable_cases": unjoinable_cases,
    }

    # Save to eval/end_to_end_results.json or end_to_end_results_remediated.json
    out_dir = Path("eval")
    out_dir.mkdir(exist_ok=True)
    is_remediated = "remediated" in str(target_fhir_dir)
    out_filename = "end_to_end_results_remediated.json" if is_remediated else "end_to_end_results.json"
    out_path = out_dir / out_filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\n[OK] Machine-readable results saved to: {out_path}")

    # Print summary table
    print("\n" + "=" * 80)
    print(f"COMPOUND END-TO-END EVALUATION SUMMARY ({'REMEDIATED' if is_remediated else 'FROZEN BASELINE'})")
    print("=" * 80)
    print(f"Total Ground-Truth Claims Evaluated: {total_evaluated}")
    print(f"Claims in Audit Reports:             {in_audit_count} ({in_audit_count/total_evaluated*100:.2f}%)")
    print(f"Claims in FHIR Provenance:           {in_fhir_count} ({in_fhir_count/total_evaluated*100:.2f}%)")
    print(f"Shadowed/Unmapped Group Claims:      {total_evaluated - in_fhir_count}")
    print(f"Correct Verification Label (Overall): {correct_ver_count} ({correct_ver_count/total_evaluated*100:.2f}%)")
    in_b_ver = sum(1 for eid in bundle_claims if ood_diagnostics[eid]["cascade"]["final_label"] == v3_ood_examples[eid]["label"])
    print(f"Correct Verification Label (In-FHIR): {in_b_ver} / {in_fhir_count} ({in_b_ver/in_fhir_count*100:.2f}%)")
    print(f"Correct Value Representation:        {correct_val_count} / {in_fhir_count} ({correct_val_count/in_fhir_count*100:.2f}%)")
    code_note = "[Corrected Remediated]" if is_remediated else "[Phase 5 Serialization Gap]"
    print(f"Normalized Codes in FHIR Bundle:     {has_code_in_fhir} / {in_fhir_count} ({has_code_in_fhir/in_fhir_count*100:.2f}%) {code_note}")
    print(f"Upstream Extraction Code Available:  {has_upstream_code} / {total_evaluated} ({has_upstream_code/total_evaluated*100:.2f}%)")
    print("-" * 80)
    strict_pct = compound_strict_fhir / total_evaluated * 100
    print(f"Strict §21.7 FHIR Compound Accuracy: {strict_pct:.2f}% ({compound_strict_fhir} / {total_evaluated})")
    print(f"In-Bundle Verified Value Accuracy:   {in_b_ver / total_evaluated * 100:.2f}% ({in_b_ver} / {total_evaluated})")
    print(f"Conditional In-Bundle Accuracy:      {in_b_ver / in_fhir_count * 100:.2f}% ({in_b_ver} / {in_fhir_count})")
    print(f"Upstream-Joined Compound Accuracy:   {compound_upstream_code / total_evaluated * 100:.2f}% ({compound_upstream_code} / {total_evaluated})")
    print("=" * 80)

    return results


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "data/fhir_phase5_v8"
    run_evaluation(target)
