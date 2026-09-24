"""M2 verification dataset generation from M0/M1 artifacts.

Label construction rules:
  SUPPORTED           — claim matches grounded evidence (both clean and degraded readable evidence)
  DISCREPANCY         — field mismatch where evidence is intact & readable
  INSUFFICIENT_EVIDENCE — target evidence token genuinely destroyed by OCR corruption or
                          claim refers to an unobserved/unprescribed clinical element.

Anti-shortcut / Independence:
  Every severity bucket (clean, low, medium, high) contains legitimate examples of
  SUPPORTED, DISCREPANCY, and INSUFFICIENT_EVIDENCE. Severity is NEVER a deterministic
  shortcut for the verification label.

Medication Grounding:
  Medication evidence combines drug entity and prescription administration directions (sig)
  so field-level claims (dose, frequency, route) are grounded in the evidence snippet.
  Ungrounded field claims are excluded.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from clindoc.m2.schema import (
    M2Example, VerificationLabel, DOC_TEMPLATE_ORDER,
    OOD_MED_TEMPLATE, OOD_LAB_TEMPLATE,
)

# ─── Vocabularies for semantic negatives ──────────────────────────────────────
FREQ_VOCAB = [
    "once daily", "twice daily", "three times daily", "four times daily",
    "every 8 hours", "every 12 hours", "every 6 hours", "every 4 hours",
    "q8h", "q12h", "q6h", "q4h", "TID", "BID", "QID", "QD",
    "once weekly", "twice weekly", "every other day",
]

DOSE_UNIT_VOCAB = ["mg", "mcg", "g", "mL", "units", "puffs"]

ROUTE_VOCAB = [
    "by mouth", "PO", "oral", "by inhalation", "inhaled",
    "topically", "subcutaneously", "intramuscularly", "intravenously",
]

DURATION_VOCAB = [
    "5 days", "7 days", "10 days", "14 days", "21 days", "30 days",
    "3 months", "6 months", "1 year", "indefinitely", "as needed",
]

LAB_FLAG_VOCAB = ["NORMAL", "HIGH", "LOW"]

LAB_UNIT_SUBS: dict[str, list[str]] = {
    "mg/dL": ["mmol/L", "mg/L", "g/L"],
    "mmol/L": ["mg/dL", "mEq/L"],
    "g/dL": ["mg/dL", "g/L"],
    "g/L": ["g/dL", "mg/dL"],
    "10*3/uL": ["10*6/uL", "/uL"],
    "10*6/uL": ["10*3/uL", "/uL"],
    "%": ["fraction", "g/dL"],
    "U/L": ["IU/L", "ukat/L"],
    "mEq/L": ["mmol/L", "mg/dL"],
    "fl": ["um3", "pL"],
    "pg": ["ng", "mg"],
}

# Unobserved clinical tests / medications for clean INSUFFICIENT_EVIDENCE
UNOBSERVED_LAB_CANDIDATES = [
    ("Troponin I", 0.02, "ng/mL", "Cardiac Panel"),
    ("Lipase", 45.0, "U/L", "Pancreatic Panel"),
    ("Hemoglobin A1c", 5.6, "%", "Endocrine Panel"),
    ("Thyroid Stimulating Hormone", 2.1, "uIU/mL", "Thyroid Panel"),
    ("Vitamin D, 25-Hydroxy", 34.0, "ng/mL", "Nutritional Panel"),
    ("Ferritin", 120.0, "ng/mL", "Iron Studies"),
]

UNOBSERVED_MED_CANDIDATES = [
    ("Insulin glargine", "20 units", "subcutaneously at bedtime"),
    ("Warfarin", "5 mg", "by mouth once daily in the evening"),
    ("Levothyroxine", "50 mcg", "by mouth once daily in the morning"),
    ("Albuterol HFA", "2 puffs", "by inhalation every 4-6 hours as needed"),
]


def _format_num(val: Any) -> str:
    if isinstance(val, (int, float)):
        return str(int(val)) if val == int(val) else str(val)
    return str(val)


def _alt_from_vocab(current: str, vocab: list[str], seed_val: int = 0) -> str:
    """Pick a different entry from vocab than current."""
    alts = [v for v in vocab if v.lower() != current.lower()]
    if not alts:
        return current
    return alts[seed_val % len(alts)]


def _alter_numeric(value: float, seed_val: int) -> float:
    """Substitute a numeric value with a realistic alternate."""
    multipliers = [0.5, 2.0, 0.25, 4.0, 0.1, 10.0]
    m = multipliers[seed_val % len(multipliers)]
    alt = round(value * m, 2)
    return alt if alt != value else round(value + 1.0, 2)


def _token_present_in_text(token: str, text: str) -> bool:
    """Check if a target token is present in OCR text."""
    token_clean = token.strip().lower()
    text_clean = text.lower()
    if token_clean in text_clean:
        return True
    digits = re.sub(r"[^0-9]", "", token_clean)
    if digits and len(digits) >= 2:
        return digits in re.sub(r"[^0-9]", "", text_clean)
    return False


def _lab_template_for_doc(gt: dict) -> str:
    return gt.get("template_id", "template_a")


# ─── 1. Clean Lab Generation ──────────────────────────────────────────────────

def generate_clean_lab_examples(
    extracted_dir: Path,
    gt_dir: Path,
    ocr_cache_dir: Path = Path("data/ocr_cache"),
) -> list[M2Example]:
    examples = []
    for ext_path in sorted(extracted_dir.glob("LAB_*.json")):
        with open(ext_path, "r", encoding="utf-8") as f:
            ext = json.load(f)
        doc_id = ext["doc_id"]
        gt_path = gt_dir / f"{doc_id}.json"
        if not gt_path.exists():
            continue
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)
        template = _lab_template_for_doc(gt)
        gt_test_names = {t["test_name"] for t in gt.get("tests", [])}

        for idx, obs in enumerate(ext.get("observations", [])):
            ev_text = obs.get("source_evidence", {}).get("text", "").strip()
            ocr_conf = obs.get("source_evidence", {}).get("ocr_confidence", 1.0)
            if not ev_text:
                continue

            # Supported value
            v_str = _format_num(obs["value"])
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{obs['test_name']}: {v_str} {obs['unit']}",
                evidence=ev_text,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=ocr_conf,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="lab",
            ))

            # Discrepancy value
            alt_val = _alter_numeric(obs["value"], idx)
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{obs['test_name']}: {_format_num(alt_val)} {obs['unit']}",
                evidence=ev_text,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=ocr_conf,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="lab",
            ))

            # Discrepancy flag
            if obs.get("abnormal_flag"):
                alt_flag = "HIGH" if obs["abnormal_flag"] == "NORMAL" else "NORMAL"
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"{obs['test_name']} flag: {alt_flag}",
                    evidence=ev_text,
                    field_type="lab.flag",
                    doc_template=template,
                    ocr_confidence=ocr_conf,
                    severity="clean",
                    label=VerificationLabel.DISCREPANCY,
                    source_doc_id=doc_id,
                    generation_method="vocab_substitution_discrepancy",
                    claim_type="lab",
                ))

        # Clean Lab INSUFFICIENT_EVIDENCE (unobserved test claims)
        clean_ocr_p = ocr_cache_dir / f"{doc_id}.json"
        header_ev = "Clinical Pathology Diagnostic Report"
        if clean_ocr_p.exists():
            clean_ocr = json.load(open(clean_ocr_p, encoding="utf-8"))
            lines = [l.get("text", "") for l in clean_ocr.get("lines", [])[:4]]
            if lines:
                header_ev = " | ".join(lines)

        unobserved = [c for c in UNOBSERVED_LAB_CANDIDATES if c[0] not in gt_test_names]
        for c in unobserved[:2]:  # 2 unobserved tests per clean lab doc
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]}: {c[1]} {c[2]}",
                evidence=header_ev,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="lab",
            ))
    return examples


# ─── 2. Clean Medication Generation ───────────────────────────────────────────

def generate_clean_med_examples(
    med_extracted_dir: Path,
    med_gt_dir: Path,
    med_ocr_cache_dir: Path = Path("data/med_ocr_cache"),
) -> tuple[list[M2Example], int]:
    examples = []
    excluded_count = 0

    for ext_path in sorted(med_extracted_dir.glob("MED_*.json")):
        with open(ext_path, "r", encoding="utf-8") as f:
            ext = json.load(f)
        doc_id = ext["doc_id"]
        gt_path = med_gt_dir / f"{doc_id}.json"
        if not gt_path.exists():
            continue
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)
        template = gt.get("template_type", "clinic_rx_pad")
        gt_med_names = {m.get("medication_name", "").split()[0].lower() for m in gt.get("medications", [])}

        for idx, med in enumerate(ext.get("medications", [])):
            name = med.get("medication_name", "")
            sig = med.get("sig", "")
            raw_ev = med.get("source_evidence", {}).get("text", "")
            ocr_conf = med.get("source_evidence", {}).get("ocr_confidence", 1.0)

            # Reconstruct grounded evidence combining drug name & directions
            if raw_ev and sig and raw_ev.lower() not in sig.lower():
                ev_text = f"{raw_ev}: {sig}"
            elif sig:
                ev_text = sig
            else:
                ev_text = raw_ev

            if not ev_text.strip():
                excluded_count += 1
                continue

            ev_lower = ev_text.lower()

            # Name claim
            if name and name.lower() in ev_lower:
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"Medication: {name}",
                    evidence=ev_text,
                    field_type="med.name",
                    doc_template=template,
                    ocr_confidence=ocr_conf,
                    severity="clean",
                    label=VerificationLabel.SUPPORTED,
                    source_doc_id=doc_id,
                    generation_method="clean_extraction_supported",
                    claim_type="med",
                ))
            else:
                excluded_count += 1

            # Dose claim
            dose = med.get("dose")
            unit = med.get("dose_unit", "mg")
            if dose is not None:
                d_str = _format_num(dose)
                if d_str in ev_lower:
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} dose: {d_str} {unit}",
                        evidence=ev_text,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method="clean_extraction_supported",
                        claim_type="med",
                    ))
                    alt_d = float(dose) * 2.0 if float(dose) < 100 else float(dose) / 2.0
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} dose: {_format_num(alt_d)} {unit}",
                        evidence=ev_text,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method="vocab_substitution_discrepancy",
                        claim_type="med",
                    ))
                else:
                    excluded_count += 1

            # Frequency claim
            freq = med.get("frequency")
            if freq:
                if freq.lower() in ev_lower:
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} frequency: {freq}",
                        evidence=ev_text,
                        field_type="med.frequency",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method="clean_extraction_supported",
                        claim_type="med",
                    ))
                    alt_freq = _alt_from_vocab(freq, FREQ_VOCAB, idx)
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} frequency: {alt_freq}",
                        evidence=ev_text,
                        field_type="med.frequency",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method="vocab_substitution_discrepancy",
                        claim_type="med",
                    ))
                else:
                    excluded_count += 1

            # Route claim
            route = med.get("route")
            if route:
                if route.lower() in ev_lower:
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} route: {route}",
                        evidence=ev_text,
                        field_type="med.route",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method="clean_extraction_supported",
                        claim_type="med",
                    ))
                    alt_route = _alt_from_vocab(route, ROUTE_VOCAB, idx)
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} route: {alt_route}",
                        evidence=ev_text,
                        field_type="med.route",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method="vocab_substitution_discrepancy",
                        claim_type="med",
                    ))
                else:
                    excluded_count += 1

            # Duration claim check
            dur = med.get("duration")
            if dur and dur.lower() not in ev_lower:
                excluded_count += 1

            # Quantity claim check
            qty = med.get("quantity")
            if qty and str(qty) not in ev_lower:
                excluded_count += 1

        # Clean Med INSUFFICIENT_EVIDENCE (unobserved medication claims)
        clean_med_ocr_p = med_ocr_cache_dir / f"{doc_id}.json"
        header_med_ev = "Medical Center Prescription"
        if clean_med_ocr_p.exists():
            clean_med_ocr = json.load(open(clean_med_ocr_p, encoding="utf-8"))
            lines = [l.get("text", "") for l in clean_med_ocr.get("lines", [])[:3]]
            if lines:
                header_med_ev = " | ".join(lines)

        unobserved_meds = [c for c in UNOBSERVED_MED_CANDIDATES if c[0].split()[0].lower() not in gt_med_names]
        for c in unobserved_meds[:1]:
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]} dose: {c[1]}",
                evidence=header_med_ev,
                field_type="med.dose",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="med",
            ))

    return examples, excluded_count


# ─── 3. Degraded Lab Generation (low, medium, high) ───────────────────────────

def generate_degraded_lab_examples(
    gt_dir: Path,
    corrupt_lab_dir: Path,
) -> list[M2Example]:
    examples = []
    if not corrupt_lab_dir.exists():
        return examples

    for corrupt_path in sorted(corrupt_lab_dir.glob("*.json")):
        stem = corrupt_path.stem
        m = re.match(r"^(LAB_\d+)_ocr_corrupt_(low|medium|high)$", stem)
        if not m:
            continue
        doc_id, severity = m.group(1), m.group(2)
        gt_path = gt_dir / f"{doc_id}.json"
        if not gt_path.exists():
            continue
        gt = json.load(open(gt_path, encoding="utf-8"))
        template = _lab_template_for_doc(gt)

        corrupt_ocr = json.load(open(corrupt_path, encoding="utf-8"))
        lines = corrupt_ocr.get("lines", [])
        corrupt_text = " ".join(l.get("text", "") for l in lines)
        avg_conf = sum(l.get("confidence", 0.5) for l in lines) / max(len(lines), 1)

        for idx, gt_test in enumerate(gt.get("tests", [])):
            tname = gt_test.get("test_name", "")
            val = gt_test.get("value", "")
            unit = gt_test.get("unit", "")
            v_str = _format_num(val)

            is_readable = _token_present_in_text(v_str, corrupt_text)

            # Build localized snippet around the test
            target_line_idx = -1
            for li, line_obj in enumerate(lines):
                ltxt = line_obj.get("text", "")
                if tname.lower() in ltxt.lower() or v_str in ltxt:
                    target_line_idx = li
                    break
            if target_line_idx >= 0:
                start_l = max(0, target_line_idx - 2)
                end_l = min(len(lines), target_line_idx + 4)
                snip = " | ".join(lines[k].get("text", "") for k in range(start_l, end_l))
            else:
                snip = corrupt_text[:250]

            if not is_readable:
                # Genuinely destroyed/absent token -> INSUFFICIENT_EVIDENCE
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"{tname}: {v_str} {unit}",
                    evidence=snip,
                    field_type="lab.value",
                    doc_template=template,
                    ocr_confidence=round(avg_conf, 3),
                    severity=severity,
                    label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                    source_doc_id=doc_id,
                    generation_method=f"m1_track_b_{severity}_ocr_failure",
                    claim_type="lab",
                ))
            else:
                # Readable token -> legitimate SUPPORTED and DISCREPANCY under noise
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"{tname}: {v_str} {unit}",
                    evidence=snip,
                    field_type="lab.value",
                    doc_template=template,
                    ocr_confidence=round(avg_conf, 3),
                    severity=severity,
                    label=VerificationLabel.SUPPORTED,
                    source_doc_id=doc_id,
                    generation_method=f"m1_track_b_{severity}_readable_supported",
                    claim_type="lab",
                ))
                alt_v = _alter_numeric(val, idx)
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"{tname}: {_format_num(alt_v)} {unit}",
                    evidence=snip,
                    field_type="lab.value",
                    doc_template=template,
                    ocr_confidence=round(avg_conf, 3),
                    severity=severity,
                    label=VerificationLabel.DISCREPANCY,
                    source_doc_id=doc_id,
                    generation_method=f"m1_track_b_{severity}_readable_discrepancy",
                    claim_type="lab",
                ))
    return examples


# ─── 4. Degraded Medication Generation (low, medium, high) ───────────────────

def generate_degraded_med_examples(
    med_gt_dir: Path,
    corrupt_med_dir: Path,
) -> list[M2Example]:
    examples = []
    if not corrupt_med_dir.exists():
        return examples

    for corrupt_path in sorted(corrupt_med_dir.glob("*.json")):
        stem = corrupt_path.stem
        m = re.match(r"^(MED_\d+)_ocr_corrupt_(low|medium|high)$", stem)
        if not m:
            continue
        doc_id, severity = m.group(1), m.group(2)
        gt_path = med_gt_dir / f"{doc_id}.json"
        if not gt_path.exists():
            continue
        gt = json.load(open(gt_path, encoding="utf-8"))
        template = gt.get("template_type", "clinic_rx_pad")

        corrupt_ocr = json.load(open(corrupt_path, encoding="utf-8"))
        lines = corrupt_ocr.get("lines", [])
        corrupt_text = " ".join(l.get("text", "") for l in lines)
        avg_conf = sum(l.get("confidence", 0.5) for l in lines) / max(len(lines), 1)

        for idx, med in enumerate(gt.get("medications", [])):
            name = med.get("medication_name", "")
            dose = med.get("dose")
            unit = med.get("dose_unit", "mg")

            # Localize snippet
            target_line_idx = -1
            for li, line_obj in enumerate(lines):
                ltxt = line_obj.get("text", "")
                if name.split()[0].lower() in ltxt.lower():
                    target_line_idx = li
                    break
            if target_line_idx >= 0:
                start_l = max(0, target_line_idx - 1)
                end_l = min(len(lines), target_line_idx + 4)
                snip = " | ".join(lines[k].get("text", "") for k in range(start_l, end_l))
            else:
                snip = corrupt_text[:250]

            if dose is not None:
                d_str = _format_num(dose)
                has_dose = d_str in corrupt_text
                if not has_dose:
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} dose: {d_str} {unit}",
                        evidence=snip,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=round(avg_conf, 3),
                        severity=severity,
                        label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                        source_doc_id=doc_id,
                        generation_method=f"m1_track_b_{severity}_ocr_failure",
                        claim_type="med",
                    ))
                else:
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} dose: {d_str} {unit}",
                        evidence=snip,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=round(avg_conf, 3),
                        severity=severity,
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method=f"m1_track_b_{severity}_readable_supported",
                        claim_type="med",
                    ))
                    alt_d = float(dose) * 2.0 if float(dose) < 100 else float(dose) / 2.0
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} dose: {_format_num(alt_d)} {unit}",
                        evidence=snip,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=round(avg_conf, 3),
                        severity=severity,
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method=f"m1_track_b_{severity}_readable_discrepancy",
                        claim_type="med",
                    ))
    return examples


# ─── Main Generation Entry Point ──────────────────────────────────────────────

def generate_m2_dataset(
    extracted_dir: Path = Path("data/extracted"),
    gt_dir: Path = Path("data/ground_truth"),
    med_extracted_dir: Path = Path("data/med_extracted"),
    med_gt_dir: Path = Path("data/med_ground_truth"),
    corrupt_lab_dir: Path = Path("data/degraded/ocr_cache/corrupt_lab"),
    corrupt_med_dir: Path = Path("data/degraded/ocr_cache/corrupt_med"),
    seed: int = 42,
) -> tuple[list[M2Example], dict]:
    """Generate the complete M2 dataset with decoupled severity and grounded evidence.

    Returns:
      (examples, stats_dict)
    """
    import random
    rng = random.Random(seed)
    examples: list[M2Example] = []

    print("Generating clean lab observations...")
    clean_lab = generate_clean_lab_examples(extracted_dir, gt_dir)
    print(f"  -> {len(clean_lab)} clean lab examples")
    examples.extend(clean_lab)

    print("Generating clean medication observations (with strict grounding)...")
    clean_med, excluded_med = generate_clean_med_examples(med_extracted_dir, med_gt_dir)
    print(f"  -> {len(clean_med)} clean med examples (excluded {excluded_med} ungrounded claims)")
    examples.extend(clean_med)

    print("Generating degraded lab observations (independent severity)...")
    deg_lab = generate_degraded_lab_examples(gt_dir, corrupt_lab_dir)
    print(f"  -> {len(deg_lab)} degraded lab examples")
    examples.extend(deg_lab)

    print("Generating degraded medication observations (independent severity)...")
    deg_med = generate_degraded_med_examples(med_gt_dir, corrupt_med_dir)
    print(f"  -> {len(deg_med)} degraded med examples")
    examples.extend(deg_med)

    rng.shuffle(examples)
    print(f"\nTotal M2 verification examples generated: {len(examples)}")
    stats = {
        "total_generated": len(examples),
        "excluded_ungrounded_med_claims": excluded_med,
    }
    return examples, stats
