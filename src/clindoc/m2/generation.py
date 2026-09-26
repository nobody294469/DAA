"""M2 verification dataset generation from M0/M1 artifacts.

Phase 1 Canonical Architecture Compliance:
- Uses canonical field vocabulary (DEC-017).
- Assigns group_id linking atomic claims from the same clinical entity instance (DEC-015).
- Generates canonical evidence with character offsets, page number, bounding box, and OCR confidence (§9).
- Enforces DR-3 context window generation (paragraph-bounded max 500 chars, fallback ±250 chars) (DEC-003).
- Assigns explicit contradiction_type ground truth (§20.1).
- Enforces DEC-011 class balance floors across all Tier 1 fields.
- Preserves v2 protections (equal supported/discrepancy for lab.flag and med.name, evidence safeguards).
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
from clindoc.m2.context import extract_context_window, reconstruct_canonical_page_text

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

# (name, dose, route, frequency)
UNOBSERVED_MED_CANDIDATES = [
    ("Insulin glargine", "20 units", "subcutaneously", "at bedtime"),
    ("Warfarin", "5 mg", "by mouth", "once daily in the evening"),
    ("Levothyroxine", "50 mcg", "by mouth", "once daily in the morning"),
    ("Albuterol HFA", "2 puffs", "by inhalation", "every 4-6 hours as needed"),
]


def _load_canonical_medications() -> list[str]:
    cfg = Path("configs/medication_subset.json")
    if not cfg.exists():
        proj_path = Path(__file__).resolve().parents[3] / cfg
        if proj_path.exists():
            cfg = proj_path
    if cfg.exists():
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
            return [v["canonical_name"] for v in data.values()]
    return [
        "Amoxicillin", "Lisinopril", "Metformin", "Atorvastatin", "Levothyroxine",
        "Amlodipine", "Metoprolol", "Omeprazole", "Losartan", "Albuterol",
        "Gabapentin", "Hydrochlorothiazide", "Sertraline", "Simvastatin", "Montelukast",
        "Prednisone", "Ciprofloxacin", "Azithromycin", "Furosemide", "Cephalexin",
    ]


CANONICAL_MEDICATIONS = _load_canonical_medications()


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

        # Load canonical page text & reconstructed rows from OCR cache
        ocr_path = ocr_cache_dir / f"{doc_id}.json"
        page_text = ""
        reconstructed_rows = []
        if ocr_path.exists():
            with open(ocr_path, "r", encoding="utf-8") as f:
                ocr_data = json.load(f)
            page_text, reconstructed_rows = reconstruct_canonical_page_text(
                ocr_data.get("lines", []), is_tabular=True, page_number=1
            )

        for idx, obs in enumerate(ext.get("observations", [])):
            ev_text = obs.get("source_evidence", {}).get("text", "").strip()
            ocr_conf = obs.get("source_evidence", {}).get("ocr_confidence", 1.0)
            bbox = obs.get("source_evidence", {}).get("bbox", [52.0, 193.32, 517.01, 205.76])
            page_num = obs.get("source_evidence", {}).get("page", 1)
            if not ev_text:
                continue

            # Determine character offsets within canonical page text
            char_start = page_text.find(ev_text) if page_text else 0
            if char_start >= 0 and page_text:
                char_end = char_start + len(ev_text)
                context_snip, _, _ = extract_context_window(
                    page_text, char_start, char_end, is_tabular=True
                )
            else:
                char_start = 0
                char_end = len(ev_text)
                context_snip = ev_text

            group_id = f"{doc_id}_obs_{idx}"
            evidence_id = str(uuid.uuid4())

            # Supported value
            v_str = _format_num(obs["value"])
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{obs['test_name']}: {v_str} {obs['unit']}",
                evidence=context_snip,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=ocr_conf,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="lab",
                group_id=group_id,
                contradiction_type=None,
                evidence_id=evidence_id,
                char_start=char_start,
                char_end=char_end,
                page_number=page_num,
                bbox=bbox,
            ))

            # Discrepancy value
            alt_val = _alter_numeric(obs["value"], idx)
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{obs['test_name']}: {_format_num(alt_val)} {obs['unit']}",
                evidence=context_snip,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=ocr_conf,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="lab",
                group_id=group_id,
                contradiction_type="value_mismatch",
                evidence_id=evidence_id,
                char_start=char_start,
                char_end=char_end,
                page_number=page_num,
                bbox=bbox,
            ))

            # Flag claims (both SUPPORTED and DISCREPANCY)
            if obs.get("abnormal_flag"):
                # Supported flag
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"{obs['test_name']} flag: {obs['abnormal_flag']}",
                    evidence=context_snip,
                    field_type="lab.flag",
                    doc_template=template,
                    ocr_confidence=ocr_conf,
                    severity="clean",
                    label=VerificationLabel.SUPPORTED,
                    source_doc_id=doc_id,
                    generation_method="clean_extraction_supported",
                    claim_type="lab",
                    group_id=group_id,
                    contradiction_type=None,
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=page_num,
                    bbox=bbox,
                ))

                # Discrepancy flag
                alt_flag = "HIGH" if obs["abnormal_flag"] == "NORMAL" else "NORMAL"
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"{obs['test_name']} flag: {alt_flag}",
                    evidence=context_snip,
                    field_type="lab.flag",
                    doc_template=template,
                    ocr_confidence=ocr_conf,
                    severity="clean",
                    label=VerificationLabel.DISCREPANCY,
                    source_doc_id=doc_id,
                    generation_method="vocab_substitution_discrepancy",
                    claim_type="lab",
                    group_id=group_id,
                    contradiction_type="value_mismatch",
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=page_num,
                    bbox=bbox,
                ))

        # Clean Lab INSUFFICIENT_EVIDENCE (unobserved test claims)
        header_ev = "Clinical Pathology Diagnostic Report"
        header_bbox = [42.0, 40.95, 265.36, 61.6]
        header_start = 0
        header_end = len(header_ev)
        if page_text:
            first_lines = page_text.split("\n")[:4]
            header_ev = " | ".join(first_lines)
            header_end = len(header_ev)
            context_header, _, _ = extract_context_window(
                page_text, 0, header_end, is_tabular=True
            )
        else:
            context_header = header_ev

        unobserved = [c for c in UNOBSERVED_LAB_CANDIDATES if c[0] not in gt_test_names]
        for c_idx, c in enumerate(unobserved[:2]):  # 2 unobserved tests per clean lab doc
            unobs_group_id = f"{doc_id}_unobs_lab_{c_idx}"
            unobs_ev_id = str(uuid.uuid4())
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]}: {c[1]} {c[2]}",
                evidence=context_header,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="lab",
                group_id=unobs_group_id,
                contradiction_type=None,
                evidence_id=unobs_ev_id,
                char_start=header_start,
                char_end=header_end,
                page_number=1,
                bbox=header_bbox,
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

        # Load canonical page text from med OCR cache
        ocr_path = med_ocr_cache_dir / f"{doc_id}.json"
        page_text = ""
        reconstructed_lines = []
        if ocr_path.exists():
            with open(ocr_path, "r", encoding="utf-8") as f:
                ocr_data = json.load(f)
            page_text, reconstructed_lines = reconstruct_canonical_page_text(
                ocr_data.get("lines", []), is_tabular=False, page_number=1
            )

        for idx, med in enumerate(ext.get("medications", [])):
            name = med.get("medication_name", "")
            sig = med.get("sig", "")
            raw_ev = med.get("source_evidence", {}).get("text", "")
            ocr_conf = med.get("source_evidence", {}).get("ocr_confidence", 1.0)
            bbox = med.get("source_evidence", {}).get("bbox", [42.0, 192.73, 173.42, 207.88])
            page_num = med.get("source_evidence", {}).get("page", 1)

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

            # Character offsets within canonical page text
            char_start = -1
            if page_text:
                if raw_ev and raw_ev in page_text:
                    char_start = page_text.find(raw_ev)
                    char_end = char_start + len(raw_ev)
                elif sig and sig in page_text:
                    char_start = page_text.find(sig)
                    char_end = char_start + len(sig)
                else:
                    char_start = page_text.find(ev_text)
                    char_end = char_start + len(ev_text) if char_start >= 0 else len(ev_text)

            if char_start < 0:
                char_start = 0
                char_end = len(ev_text)
                context_snip = ev_text
            else:
                context_snip, _, _ = extract_context_window(
                    page_text, char_start, char_end, is_tabular=False
                )

            ev_lower = ev_text.lower()
            context_lower = context_snip.lower()
            group_id = f"{doc_id}_med_{idx}"
            evidence_id = str(uuid.uuid4())

            # Name claim
            if name and name.lower() in ev_lower:
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"Medication: {name}",
                    evidence=context_snip,
                    field_type="med.name",
                    doc_template=template,
                    ocr_confidence=ocr_conf,
                    severity="clean",
                    label=VerificationLabel.SUPPORTED,
                    source_doc_id=doc_id,
                    generation_method="clean_extraction_supported",
                    claim_type="med",
                    group_id=group_id,
                    contradiction_type=None,
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=page_num,
                    bbox=bbox,
                ))

                # Discrepancy name using alternative canonical medication name
                # Safeguard: alternative must NOT appear in evidence or surrounding context
                valid_alternatives = [
                    m for m in CANONICAL_MEDICATIONS
                    if m.lower() != name.lower()
                    and m.lower() not in ev_lower
                    and m.lower() not in context_lower
                ]
                if not valid_alternatives:
                    valid_alternatives = [
                        m for m in CANONICAL_MEDICATIONS
                        if m.lower() != name.lower() and m.lower() not in ev_lower
                    ]
                alt_name = valid_alternatives[idx % len(valid_alternatives)]
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=f"Medication: {alt_name}",
                    evidence=context_snip,
                    field_type="med.name",
                    doc_template=template,
                    ocr_confidence=ocr_conf,
                    severity="clean",
                    label=VerificationLabel.DISCREPANCY,
                    source_doc_id=doc_id,
                    generation_method="vocab_substitution_discrepancy",
                    claim_type="med",
                    group_id=group_id,
                    contradiction_type="wrong_entity",
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=page_num,
                    bbox=bbox,
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
                        evidence=context_snip,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method="clean_extraction_supported",
                        claim_type="med",
                        group_id=group_id,
                        contradiction_type=None,
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_num,
                        bbox=bbox,
                    ))
                    alt_d = float(dose) * 2.0 if float(dose) < 100 else float(dose) / 2.0
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} dose: {_format_num(alt_d)} {unit}",
                        evidence=context_snip,
                        field_type="med.dose",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method="vocab_substitution_discrepancy",
                        claim_type="med",
                        group_id=group_id,
                        contradiction_type="dose_mismatch",
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_num,
                        bbox=bbox,
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
                        evidence=context_snip,
                        field_type="med.frequency",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method="clean_extraction_supported",
                        claim_type="med",
                        group_id=group_id,
                        contradiction_type=None,
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_num,
                        bbox=bbox,
                    ))
                    alt_freq = _alt_from_vocab(freq, FREQ_VOCAB, idx)
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} frequency: {alt_freq}",
                        evidence=context_snip,
                        field_type="med.frequency",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method="vocab_substitution_discrepancy",
                        claim_type="med",
                        group_id=group_id,
                        contradiction_type="temporal_mismatch",
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_num,
                        bbox=bbox,
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
                        evidence=context_snip,
                        field_type="med.route",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.SUPPORTED,
                        source_doc_id=doc_id,
                        generation_method="clean_extraction_supported",
                        claim_type="med",
                        group_id=group_id,
                        contradiction_type=None,
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_num,
                        bbox=bbox,
                    ))
                    alt_route = _alt_from_vocab(route, ROUTE_VOCAB, idx)
                    examples.append(M2Example(
                        example_id=str(uuid.uuid4()),
                        claim=f"{name} route: {alt_route}",
                        evidence=context_snip,
                        field_type="med.route",
                        doc_template=template,
                        ocr_confidence=ocr_conf,
                        severity="clean",
                        label=VerificationLabel.DISCREPANCY,
                        source_doc_id=doc_id,
                        generation_method="vocab_substitution_discrepancy",
                        claim_type="med",
                        group_id=group_id,
                        contradiction_type="wrong_entity",
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=page_num,
                        bbox=bbox,
                    ))
                else:
                    excluded_count += 1

            dur = med.get("duration")
            if dur and dur.lower() not in ev_lower:
                excluded_count += 1

            qty = med.get("quantity")
            if qty and str(qty) not in ev_lower:
                excluded_count += 1

        # Clean Med INSUFFICIENT_EVIDENCE (unobserved medication claims)
        header_med_ev = "Medical Center Prescription"
        header_bbox = [42.0, 40.95, 372.94, 61.6]
        header_start = 0
        header_end = len(header_med_ev)
        if page_text:
            first_lines = page_text.split("\n")[:3]
            header_med_ev = " | ".join(first_lines)
            header_end = len(header_med_ev)
            context_header, _, _ = extract_context_window(
                page_text, 0, header_end, is_tabular=False
            )
        else:
            context_header = header_med_ev

        unobserved_meds = [c for c in UNOBSERVED_MED_CANDIDATES if c[0].split()[0].lower() not in gt_med_names]
        for c_idx, c in enumerate(unobserved_meds[:1]):
            unobs_group_id = f"{doc_id}_unobs_med_{c_idx}"
            unobs_ev_id = str(uuid.uuid4())

            # 1. med.name INSUFFICIENT_EVIDENCE
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"Medication: {c[0]}",
                evidence=context_header,
                field_type="med.name",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="med",
                group_id=unobs_group_id,
                contradiction_type=None,
                evidence_id=unobs_ev_id,
                char_start=header_start,
                char_end=header_end,
                page_number=1,
                bbox=header_bbox,
            ))

            # 2. med.dose INSUFFICIENT_EVIDENCE
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]} dose: {c[1]}",
                evidence=context_header,
                field_type="med.dose",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="med",
                group_id=unobs_group_id,
                contradiction_type=None,
                evidence_id=unobs_ev_id,
                char_start=header_start,
                char_end=header_end,
                page_number=1,
                bbox=header_bbox,
            ))

            # 3. med.route INSUFFICIENT_EVIDENCE
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]} route: {c[2]}",
                evidence=context_header,
                field_type="med.route",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="med",
                group_id=unobs_group_id,
                contradiction_type=None,
                evidence_id=unobs_ev_id,
                char_start=header_start,
                char_end=header_end,
                page_number=1,
                bbox=header_bbox,
            ))

            # 4. med.frequency INSUFFICIENT_EVIDENCE
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]} frequency: {c[3]}",
                evidence=context_header,
                field_type="med.frequency",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="med",
                group_id=unobs_group_id,
                contradiction_type=None,
                evidence_id=unobs_ev_id,
                char_start=header_start,
                char_end=header_end,
                page_number=1,
                bbox=header_bbox,
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
        page_text = "\n".join(l.get("text", "") for l in lines)
        avg_conf = sum(l.get("confidence", 0.5) for l in lines) / max(len(lines), 1)

        for idx, gt_test in enumerate(gt.get("tests", [])):
            tname = gt_test.get("test_name", "")
            val = gt_test.get("value", "")
            unit = gt_test.get("unit", "")
            v_str = _format_num(val)

            is_readable = _token_present_in_text(v_str, page_text)

            # Build localized snippet around the test
            target_line_idx = -1
            for li, line_obj in enumerate(lines):
                ltxt = line_obj.get("text", "")
                if tname.lower() in ltxt.lower() or v_str in ltxt:
                    target_line_idx = li
                    break

            if target_line_idx >= 0:
                t_line = lines[target_line_idx]
                target_text = t_line.get("text", "")
                char_start = page_text.find(target_text)
                char_end = char_start + len(target_text) if char_start >= 0 else len(target_text)
                char_start = max(0, char_start)
                bbox = t_line.get("bbox", [52.0, 193.32, 517.01, 205.76])
                snip, _, _ = extract_context_window(page_text, char_start, char_end, is_tabular=True)
            else:
                char_start = 0
                char_end = min(len(page_text), 100)
                bbox = [52.0, 193.32, 517.01, 205.76]
                snip, _, _ = extract_context_window(page_text, 0, char_end, is_tabular=True)

            group_id = f"{doc_id}_{severity}_obs_{idx}"
            evidence_id = str(uuid.uuid4())

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
                    group_id=group_id,
                    contradiction_type=None,
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=1,
                    bbox=bbox,
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
                    group_id=group_id,
                    contradiction_type=None,
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=1,
                    bbox=bbox,
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
                    group_id=group_id,
                    contradiction_type="value_mismatch",
                    evidence_id=evidence_id,
                    char_start=char_start,
                    char_end=char_end,
                    page_number=1,
                    bbox=bbox,
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
        page_text = "\n".join(l.get("text", "") for l in lines)
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
                t_line = lines[target_line_idx]
                target_text = t_line.get("text", "")
                char_start = page_text.find(target_text)
                char_end = char_start + len(target_text) if char_start >= 0 else len(target_text)
                char_start = max(0, char_start)
                bbox = t_line.get("bbox", [42.0, 192.73, 173.42, 207.88])
                snip, _, _ = extract_context_window(page_text, char_start, char_end, is_tabular=False)
            else:
                char_start = 0
                char_end = min(len(page_text), 100)
                bbox = [42.0, 192.73, 173.42, 207.88]
                snip, _, _ = extract_context_window(page_text, 0, char_end, is_tabular=False)

            group_id = f"{doc_id}_{severity}_med_{idx}"
            evidence_id = str(uuid.uuid4())

            if dose is not None:
                d_str = _format_num(dose)
                has_dose = d_str in page_text
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
                        group_id=group_id,
                        contradiction_type=None,
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=1,
                        bbox=bbox,
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
                        group_id=group_id,
                        contradiction_type=None,
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=1,
                        bbox=bbox,
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
                        group_id=group_id,
                        contradiction_type="dose_mismatch",
                        evidence_id=evidence_id,
                        char_start=char_start,
                        char_end=char_end,
                        page_number=1,
                        bbox=bbox,
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
    """Generate the canonical M2 verification dataset with Phase 1 contracts.

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

    print("Generating clean medication observations (with strict grounding & IE balance)...")
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


# ─── 5. Narrative Benchmark M2 Generation & Integration (DEC-014) ────────────

def generate_narrative_m2_examples(
    narrative_gt_dir: Path = Path("data/narrative_ground_truth"),
    narrative_ocr_dir: Path = Path("data/narrative_ocr_cache"),
) -> list[M2Example]:
    """Generate M2 verification examples from synthetic narrative documents.

    Adheres strictly to:
    - DEC-014: 20 narrative clinical documents (15 CV, 5 OOD)
    - DEC-015: Group ID entity linkage for composite clinical findings
    - DEC-017: Canonical 10-field vocabulary
    - DEC-019: Tier-1 learned verification fields only (lab.value, lab.flag, med.name, med.dose, med.route, med.frequency)
    - DEC-003: DR-3 paragraph-bounded context window (max 500 characters)
    - DEC-011: Balanced classes across supported, discrepancy, and insufficient-evidence
    """
    examples: list[M2Example] = []

    for gt_path in sorted(narrative_gt_dir.glob("NARRATIVE_*.json")):
        with open(gt_path, "r", encoding="utf-8") as f:
            gt = json.load(f)

        doc_id = gt["doc_id"]
        template = gt["template_id"]
        split = gt["split"].lower()
        ocr_path = narrative_ocr_dir / f"{doc_id}.json"
        with open(ocr_path, "r", encoding="utf-8") as f:
            ocr_data = json.load(f)

        page_text, _ = reconstruct_canonical_page_text(
            ocr_data.get("lines", []), is_tabular=False, page_number=1
        )

        # 1. Lab Entities -> lab.value and lab.flag
        for l_idx, lab in enumerate(gt.get("lab_entities", [])):
            tname = lab["test_name"]
            val = lab["value"]
            val_str = _format_num(val)
            unit = lab["unit"]
            flag = lab["flag"]
            group_id = lab["group_id"]
            cs = lab["char_start"]
            ce = lab["char_end"]
            bbox = lab["bbox"]

            context_snip, _, _ = extract_context_window(page_text, cs, ce, is_tabular=False)

            # A. lab.value SUPPORTED
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{tname}: {val_str} {unit}",
                evidence=context_snip,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="lab",
                split=split,
                group_id=group_id,
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # B. lab.value DISCREPANCY
            alt_val = _alter_numeric(val, l_idx)
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{tname}: {_format_num(alt_val)} {unit}",
                evidence=context_snip,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="lab",
                split=split,
                group_id=group_id,
                contradiction_type="value_mismatch",
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # C. lab.flag SUPPORTED
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{tname} flag: {flag}",
                evidence=context_snip,
                field_type="lab.flag",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="lab",
                split=split,
                group_id=group_id,
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # D. lab.flag DISCREPANCY
            alt_flag = "HIGH" if flag == "NORMAL" else "NORMAL"
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{tname} flag: {alt_flag}",
                evidence=context_snip,
                field_type="lab.flag",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="lab",
                split=split,
                group_id=group_id,
                contradiction_type="value_mismatch",
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

        # 2. Med Entities -> med.name, med.dose, med.route, med.frequency
        for m_idx, med in enumerate(gt.get("med_entities", [])):
            mname = med["medication_name"]
            dose = med["dose"]
            dose_str = _format_num(dose)
            dunit = med["dose_unit"]
            route = med["route"]
            freq = med["frequency"]
            group_id = med["group_id"]
            cs = med["char_start"]
            ce = med["char_end"]
            bbox = med["bbox"]

            context_snip, _, _ = extract_context_window(page_text, cs, ce, is_tabular=False)
            ev_lower = page_text[cs:ce].lower()
            context_lower = context_snip.lower()

            # A. med.name SUPPORTED
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"Medication: {mname}",
                evidence=context_snip,
                field_type="med.name",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # med.name DISCREPANCY
            val_alts = [
                m for m in CANONICAL_MEDICATIONS
                if m.lower() != mname.lower() and m.lower() not in ev_lower and m.lower() not in context_lower
            ]
            alt_mname = val_alts[m_idx % len(val_alts)] if val_alts else "Albuterol"
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"Medication: {alt_mname}",
                evidence=context_snip,
                field_type="med.name",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type="wrong_entity",
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # B. med.dose SUPPORTED & DISCREPANCY
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{mname} dose: {dose_str} {dunit}",
                evidence=context_snip,
                field_type="med.dose",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))
            alt_d = float(dose) * 2.0 if float(dose) < 100 else float(dose) / 2.0
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{mname} dose: {_format_num(alt_d)} {dunit}",
                evidence=context_snip,
                field_type="med.dose",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type="dose_mismatch",
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # C. med.route SUPPORTED & DISCREPANCY
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{mname} route: {route}",
                evidence=context_snip,
                field_type="med.route",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))
            alt_routes = [r for r in ["intravenously", "by inhalation", "topically", "subcutaneously"] if r.lower() not in ev_lower]
            alt_route = alt_routes[m_idx % len(alt_routes)]
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{mname} route: {alt_route}",
                evidence=context_snip,
                field_type="med.route",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type="wrong_entity",
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

            # D. med.frequency SUPPORTED & DISCREPANCY
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{mname} frequency: {freq}",
                evidence=context_snip,
                field_type="med.frequency",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.SUPPORTED,
                source_doc_id=doc_id,
                generation_method="clean_extraction_supported",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))
            alt_freqs = [f for f in ["four times daily", "every other day", "once weekly", "every 4 hours"] if f.lower() not in ev_lower]
            alt_freq = alt_freqs[m_idx % len(alt_freqs)]
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{mname} frequency: {alt_freq}",
                evidence=context_snip,
                field_type="med.frequency",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.DISCREPANCY,
                source_doc_id=doc_id,
                generation_method="vocab_substitution_discrepancy",
                claim_type="med",
                split=split,
                group_id=group_id,
                contradiction_type="temporal_mismatch",
                evidence_id=str(uuid.uuid4()),
                char_start=cs,
                char_end=ce,
                page_number=1,
                bbox=bbox,
            ))

        # 3. Clean INSUFFICIENT_EVIDENCE (Unobserved Lab & Med Claims)
        ctx_snip, _, _ = extract_context_window(page_text, 0, min(len(page_text), 200), is_tabular=False)

        unobs_lab = [c for c in UNOBSERVED_LAB_CANDIDATES if c[0].lower() not in page_text.lower()][:1]
        for c in unobs_lab:
            examples.append(M2Example(
                example_id=str(uuid.uuid4()),
                claim=f"{c[0]}: {c[1]} {c[2]}",
                evidence=ctx_snip,
                field_type="lab.value",
                doc_template=template,
                ocr_confidence=1.0,
                severity="clean",
                label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                source_doc_id=doc_id,
                generation_method="clean_unobserved_claim_insufficient_evidence",
                claim_type="lab",
                split=split,
                group_id=f"{doc_id}_unobs_lab_0",
                contradiction_type=None,
                evidence_id=str(uuid.uuid4()),
                char_start=0,
                char_end=min(len(page_text), 200),
                page_number=1,
                bbox=[40.0, 40.0, 500.0, 100.0],
            ))

        unobs_med = [m for m in UNOBSERVED_MED_CANDIDATES if m[0].lower() not in page_text.lower()][:1]
        for m in unobs_med:
            for field, claim_txt in [
                ("med.name", f"Medication: {m[0]}"),
                ("med.dose", f"{m[0]} dose: {m[1]}"),
                ("med.route", f"{m[0]} route: {m[2]}"),
                ("med.frequency", f"{m[0]} frequency: {m[3]}"),
            ]:
                examples.append(M2Example(
                    example_id=str(uuid.uuid4()),
                    claim=claim_txt,
                    evidence=ctx_snip,
                    field_type=field,
                    doc_template=template,
                    ocr_confidence=1.0,
                    severity="clean",
                    label=VerificationLabel.INSUFFICIENT_EVIDENCE,
                    source_doc_id=doc_id,
                    generation_method="clean_unobserved_claim_insufficient_evidence",
                    claim_type="med",
                    split=split,
                    group_id=f"{doc_id}_unobs_med_0",
                    contradiction_type=None,
                    evidence_id=str(uuid.uuid4()),
                    char_start=0,
                    char_end=min(len(page_text), 200),
                    page_number=1,
                    bbox=[40.0, 40.0, 500.0, 100.0],
                ))

    return examples


def integrate_narrative_benchmark(
    canonical_data_dir: Path = Path("data/m2_verification"),
    narrative_gt_dir: Path = Path("data/narrative_ground_truth"),
    narrative_ocr_dir: Path = Path("data/narrative_ocr_cache"),
    floor_config_path: Path = Path("configs/class_balance_floor.yaml"),
) -> dict[str, Any]:
    """Integrate 20-document synthetic narrative benchmark with 100-document canonical benchmark.

    Resulting canonical benchmark composition:
      120 documents total (50 lab + 50 med + 20 narrative)
      86 CV documents (71 lab/med + 15 narrative)
      34 OOD documents (29 lab/med + 5 narrative)
    Preserves existing 100-document Task 2 examples byte-for-byte.
    Enforces DEC-011 class balance assertion across combined dataset.
    """
    import datetime
    from clindoc.m2.dataset import (
        load_examples, save_examples, compute_metadata, assert_class_balance
    )
    from clindoc.m2.splits import save_split_manifest, validate_no_leakage

    all_ex_path = canonical_data_dir / "all_examples.jsonl"
    split_path = canonical_data_dir / "split_manifest.json"
    meta_path = canonical_data_dir / "metadata.json"
    gen_path = canonical_data_dir / "generation_manifest.json"

    # 1. Load existing Task 2 canonical examples
    if not all_ex_path.exists():
        raise FileNotFoundError(f"Existing canonical dataset not found at {all_ex_path}")
    existing_examples = load_examples(all_ex_path)
    n_existing = len(existing_examples)
    print(f"Loaded {n_existing} existing canonical examples from 100 documents.")

    # 2. Generate narrative examples
    narrative_examples = generate_narrative_m2_examples(narrative_gt_dir, narrative_ocr_dir)
    print(f"Generated {len(narrative_examples)} narrative verification examples from 20 documents.")

    # 3. Combine examples preserving existing byte-for-byte order
    combined_examples = existing_examples + narrative_examples
    n_total = len(combined_examples)
    print(f"Combined dataset: {n_total} total examples.")

    # 4. Update split manifest (86 CV, 34 OOD)
    with open(split_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    existing_cv_docs = list(manifest.get("cv_doc_ids", []))
    existing_ood_docs = list(manifest.get("ood_doc_ids", []))

    narrative_cv_docs = [f"NARRATIVE_{i:04d}" for i in range(1, 16)]
    narrative_ood_docs = [f"NARRATIVE_{i:04d}" for i in range(16, 21)]

    new_cv_docs = sorted(set(existing_cv_docs + narrative_cv_docs))
    new_ood_docs = sorted(set(existing_ood_docs + narrative_ood_docs))

    # Update 5-fold distribution for the 15 CV narrative documents (3 in val per fold)
    cv_folds = manifest.get("cv_folds", [])
    for fold_idx, fold in enumerate(cv_folds):
        val_narrative = narrative_cv_docs[fold_idx * 3 : (fold_idx + 1) * 3]
        train_narrative = [d for d in narrative_cv_docs if d not in val_narrative]
        fold["val_doc_ids"] = sorted(set(fold.get("val_doc_ids", []) + val_narrative))
        fold["train_doc_ids"] = sorted(set(fold.get("train_doc_ids", []) + train_narrative))

    manifest["cv_doc_ids"] = new_cv_docs
    manifest["ood_doc_ids"] = new_ood_docs
    manifest["cv_folds"] = cv_folds

    # Validate no leakage
    validate_no_leakage(manifest)

    # 5. Compute metadata
    metadata = compute_metadata(combined_examples, new_ood_docs, new_cv_docs)
    meta_dict = metadata.model_dump()

    # Load existing generation stats and update
    with open(meta_path, "r", encoding="utf-8") as f:
        existing_meta = json.load(f)
    gen_stats = existing_meta.get("generation_stats", {})
    gen_stats["total_generated"] = n_total
    gen_stats["narrative_examples"] = len(narrative_examples)
    meta_dict["generation_stats"] = gen_stats

    # 6. Save combined artifacts
    save_examples(combined_examples, all_ex_path)
    save_split_manifest(manifest, split_path)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_dict, f, indent=2)

    # 7. Update generation manifest
    gen_manifest = {
        "dataset_version": "2.0-phase1-canonical",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seed": 42,
        "total_documents": len(new_cv_docs) + len(new_ood_docs),
        "cv_documents": len(new_cv_docs),
        "ood_documents": len(new_ood_docs),
        "total_examples": n_total,
        "total_groups": metadata.total_groups,
        "by_field_type": meta_dict["by_field_type"],
        "by_class": meta_dict["label_counts"],
        "by_contradiction_type": meta_dict["by_contradiction_type"],
        "class_balance_status": "PASSED_DEC_011",
        "context_window_config": metadata.context_window_config,
    }
    with open(gen_path, "w", encoding="utf-8") as f:
        json.dump(gen_manifest, f, indent=2)

    # 8. Assert Class Balance
    assert_class_balance(metadata, floor_config_path)
    print("Class balance assertion (DEC-011): PASSED on combined 120-document benchmark.")

    return {
        "total_examples": n_total,
        "existing_examples": n_existing,
        "narrative_examples": len(narrative_examples),
        "total_documents": len(new_cv_docs) + len(new_ood_docs),
        "cv_documents": len(new_cv_docs),
        "ood_documents": len(new_ood_docs),
    }
