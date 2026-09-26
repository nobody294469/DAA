"""PDF rendering and text-grounding engine for synthetic clinical narrative documents.

Renders born-digital PDFs using ReportLab, extracts exact token bounding boxes and
lines via PyMuPDF (born-digital OCR cache), reconstructs canonical page text,
localizes ground-truth mentions to exact character offsets, and produces machine-readable
ground-truth records conforming to DEC-014, DEC-015, DEC-017, and DEC-003.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
import uuid

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from clindoc.ocr.born_digital import extract_born_digital_pdf
from clindoc.ocr.schema import DocumentOCRRecord
from clindoc.m2.context import reconstruct_canonical_page_text, extract_context_window
from clindoc.synth.narrative_schema import (
    NarrativeDocumentGroundTruth,
    NarrativeClaimGroundTruth,
    NarrativeLabEntity,
    NarrativeMedEntity,
)


def _format_num(val: float) -> str:
    if val == int(val):
        return str(int(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def render_narrative_pdf(
    doc_record: NarrativeDocumentGroundTruth,
    pdf_path: Path,
) -> None:
    """Render a synthetic clinical narrative document to a born-digital PDF."""
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()

    facility_style = ParagraphStyle(
        "NarrativeFacility",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1A365D"),
    )
    title_style = ParagraphStyle(
        "NarrativeTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#2B6CB0"),
        spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "NarrativeMeta",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#4A5568"),
    )
    sec_heading_style = ParagraphStyle(
        "NarrativeHeading",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#1A202C"),
        spaceBefore=8,
        spaceAfter=3,
    )
    body_style = ParagraphStyle(
        "NarrativeBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#2D3748"),
        spaceAfter=6,
    )

    story = []

    # 1. Header Banner
    story.append(Paragraph(doc_record.facility_name, facility_style))
    template_display_name = doc_record.template_id.replace("_", " ").upper()
    story.append(Paragraph(f"CLINICAL DOCUMENTATION — {template_display_name}", title_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1A365D"), spaceAfter=6))

    # 2. Patient Demographics & Encounter Metadata
    pat = doc_record.patient
    gender_full = "Male" if pat.gender == "M" else "Female"
    story.append(Paragraph(
        f"<b>Patient:</b> {pat.name} &nbsp;|&nbsp; "
        f"<b>MRN:</b> {pat.patient_id} &nbsp;|&nbsp; "
        f"<b>DOB:</b> {pat.dob} &nbsp;|&nbsp; "
        f"<b>Gender:</b> {gender_full} &nbsp;|&nbsp; "
        f"<b>Doc ID:</b> {doc_record.doc_id}",
        meta_style
    ))
    story.append(Paragraph(
        f"<b>Encounter Date:</b> {doc_record.admission_date} &nbsp;|&nbsp; "
        f"<b>Discharge/Note Date:</b> {doc_record.discharge_date} &nbsp;|&nbsp; "
        f"<b>Provider:</b> {doc_record.attending_physician}",
        meta_style
    ))
    story.append(HRFlowable(width="100%", thickness=0.75, color=colors.HexColor("#E2E8F0"), spaceBefore=4, spaceAfter=8))

    # 3. Clinical Sections
    for sec in doc_record.sections:
        story.append(Paragraph(sec.title, sec_heading_style))
        story.append(Paragraph(sec.text, body_style))
        story.append(Spacer(1, 4))

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=40,
        rightMargin=40,
        topMargin=36,
        bottomMargin=36,
    )
    doc.build(story)


def process_and_ground_narrative_document(
    doc_record: NarrativeDocumentGroundTruth,
    pdf_path: Path,
    ocr_cache_dir: Path,
    ground_truth_dir: Path,
) -> NarrativeDocumentGroundTruth:
    """Render PDF, extract text, localize character offsets, build atomic claims, and save ground truth."""
    ocr_cache_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_dir.mkdir(parents=True, exist_ok=True)

    # 1. Render PDF
    render_narrative_pdf(doc_record, pdf_path)

    # 2. Extract born-digital OCR record
    ocr_record = extract_born_digital_pdf(pdf_path, doc_id=doc_record.doc_id)
    ocr_cache_path = ocr_cache_dir / f"{doc_record.doc_id}.json"
    with open(ocr_cache_path, "w", encoding="utf-8") as f:
        json.dump(ocr_record.model_dump(), f, indent=2)

    # 3. Reconstruct canonical page text
    page_text, row_records = reconstruct_canonical_page_text(
        [l.model_dump() for l in ocr_record.lines],
        is_tabular=False,
        page_number=1,
    )

    claims: list[NarrativeClaimGroundTruth] = []

    # Helper function to find regex span in page_text and enclosing bbox
    def _localize_span(mention_text: str) -> tuple[int, int, str, list[float]]:
        # Match tokens allowing whitespace / newlines
        token_regex = r'\s+'.join(re.escape(w) for w in mention_text.split())
        m = re.search(token_regex, page_text, flags=re.IGNORECASE)
        if not m:
            # Fallback: search first 3 tokens
            tokens = mention_text.split()
            sub_regex = r'\s+'.join(re.escape(w) for w in tokens[:3])
            m = re.search(sub_regex, page_text, flags=re.IGNORECASE)
        if not m:
            raise ValueError(
                f"Could not localize evidence mention {mention_text!r} in {doc_record.doc_id} canonical page text!"
            )

        c_start, c_end = m.span()
        verbatim_span = page_text[c_start:c_end]

        # Enclosing bounding box from overlapping line records
        overlapping = [
            l for l in row_records
            if l["char_start"] < c_end and l["char_end"] > c_start
        ]
        if overlapping:
            bx0 = min(l["bbox"][0] for l in overlapping)
            by0 = min(l["bbox"][1] for l in overlapping)
            bx1 = max(l["bbox"][2] for l in overlapping)
            by1 = max(l["bbox"][3] for l in overlapping)
            bbox = [round(bx0, 2), round(by0, 2), round(bx1, 2), round(by1, 2)]
        else:
            bbox = [40.0, 100.0, 500.0, 120.0]

        return c_start, c_end, verbatim_span, bbox

    # 4. Localize Lab Entities & Build Atomic Claims
    for l_entity in doc_record.lab_entities:
        cs, ce, verbatim, bbox = _localize_span(l_entity.evidence_text)
        l_entity.char_start = cs
        l_entity.char_end = ce
        l_entity.evidence_text = verbatim
        l_entity.bbox = bbox
        l_entity.page_number = 1

        ev_id = str(uuid.uuid4())

        # Tier 1: lab.value
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=l_entity.group_id,
            field_type="lab.value",
            value=f"{l_entity.test_name}: {_format_num(l_entity.value)} {l_entity.unit}",
            canonical_value=l_entity.value,
            unit=l_entity.unit,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 1: lab.flag
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=l_entity.group_id,
            field_type="lab.flag",
            value=f"{l_entity.test_name} flag: {l_entity.flag}",
            canonical_value=l_entity.flag,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 2 Deterministic: lab.unit
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=l_entity.group_id,
            field_type="lab.unit",
            value=l_entity.unit,
            canonical_value=l_entity.unit,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 2 Deterministic: lab.reference_range
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=l_entity.group_id,
            field_type="lab.reference_range",
            value=l_entity.ref_range_text,
            canonical_value=l_entity.ref_range_text,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 2 Deterministic: lab.test_name
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=l_entity.group_id,
            field_type="lab.test_name",
            value=l_entity.test_name,
            canonical_value=l_entity.test_name,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

    # 5. Localize Med Entities & Build Atomic Claims
    for m_entity in doc_record.med_entities:
        cs, ce, verbatim, bbox = _localize_span(m_entity.evidence_text)
        m_entity.char_start = cs
        m_entity.char_end = ce
        m_entity.evidence_text = verbatim
        m_entity.bbox = bbox
        m_entity.page_number = 1

        ev_id = str(uuid.uuid4())

        # Tier 1: med.name
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=m_entity.group_id,
            field_type="med.name",
            value=f"Medication: {m_entity.medication_name}",
            canonical_value=m_entity.medication_name,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 1: med.dose
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=m_entity.group_id,
            field_type="med.dose",
            value=f"{m_entity.medication_name} dose: {_format_num(m_entity.dose)} {m_entity.dose_unit}",
            canonical_value=m_entity.dose,
            unit=m_entity.dose_unit,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 1: med.route
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=m_entity.group_id,
            field_type="med.route",
            value=f"{m_entity.medication_name} route: {m_entity.route}",
            canonical_value=m_entity.route,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 1: med.frequency
        claims.append(NarrativeClaimGroundTruth(
            claim_id=str(uuid.uuid4()),
            group_id=m_entity.group_id,
            field_type="med.frequency",
            value=f"{m_entity.medication_name} frequency: {m_entity.frequency}",
            canonical_value=m_entity.frequency,
            evidence_id=ev_id,
            evidence_text=verbatim,
            char_start=cs,
            char_end=ce,
            bbox=bbox,
            contradiction_type=None,
        ))

        # Tier 2 Deterministic: med.duration
        if m_entity.duration:
            claims.append(NarrativeClaimGroundTruth(
                claim_id=str(uuid.uuid4()),
                group_id=m_entity.group_id,
                field_type="med.duration",
                value=m_entity.duration,
                canonical_value=m_entity.duration,
                evidence_id=ev_id,
                evidence_text=verbatim,
                char_start=cs,
                char_end=ce,
                bbox=bbox,
                contradiction_type=None,
            ))

    doc_record.claims = claims

    # 6. Save Ground Truth JSON
    gt_path = ground_truth_dir / f"{doc_record.doc_id}.json"
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(doc_record.model_dump(), f, indent=2)

    return doc_record


def build_and_render_all_narrative_documents(
    output_pdf_dir: Path = Path("data/narrative_rendered"),
    ocr_cache_dir: Path = Path("data/narrative_ocr_cache"),
    ground_truth_dir: Path = Path("data/narrative_ground_truth"),
    seed: int = 42,
) -> list[NarrativeDocumentGroundTruth]:
    """Generate, render, extract, ground, and save all 20 narrative documents."""
    from clindoc.synth.narrative_generator import generate_all_narrative_documents

    raw_docs = generate_all_narrative_documents(seed=seed)
    processed_docs = []

    print(f"\nBuilding and rendering {len(raw_docs)} synthetic narrative documents...")
    for doc in raw_docs:
        pdf_path = output_pdf_dir / f"{doc.doc_id}.pdf"
        processed = process_and_ground_narrative_document(
            doc,
            pdf_path=pdf_path,
            ocr_cache_dir=ocr_cache_dir,
            ground_truth_dir=ground_truth_dir,
        )
        processed_docs.append(processed)
        print(f"  [{processed.split}] Rendered & grounded {processed.doc_id} ({processed.template_id})")

    return processed_docs
