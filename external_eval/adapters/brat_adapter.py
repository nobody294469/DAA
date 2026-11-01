"""Standalone BRAT Annotation Adapter for ClinicalDoc Evaluation.

Authority: User Request / PROJECT_SPEC §18.3, §21 / DEC-010, DEC-017
Location: external_eval/adapters/brat_adapter.py

Converts BRAT (.ann) standoff entity annotations and (.txt) clinical source documents
into ClinicalDoc-compatible GroundTruthClaim instances without modifying production code.

Key Invariants:
1. Never silently discard any annotation (tracks accepted, unsupported, and rejected).
2. Verifies character offsets against verbatim document text.
3. Unambiguous mappings only: maps Drug, Strength/Dosage, Route, Frequency, Duration.
4. Preserves Form, Reason, ADE, etc. as documented unsupported annotations (no schema invention).
5. Does not fabricate layout geometry or OCR bounding boxes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from clindoc.extract.contracts import TIER_1_FIELDS, TIER_2_FIELDS
from clindoc.extract.evaluate import GroundTruthClaim
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict

# Canonical clinical fields from DEC-017
ALL_CANONICAL_FIELDS: frozenset[str] = TIER_1_FIELDS | TIER_2_FIELDS

# Unambiguous BRAT entity label mappings to canonical field types
UNAMBIGUOUS_LABEL_MAP: dict[str, str] = {
    # Medication Name
    "drug": "med.name",
    "drug_name": "med.name",
    "medication": "med.name",
    # Medication Dose / Strength
    "strength": "med.dose",
    "dosage": "med.dose",
    "dose": "med.dose",
    # Route of administration
    "route": "med.route",
    "mode": "med.route",
    # Frequency
    "frequency": "med.frequency",
    # Treatment Duration
    "duration": "med.duration",
    # Laboratory fields (where explicit in standoff)
    "lab_value": "lab.value",
    "lab.value": "lab.value",
    "lab_unit": "lab.unit",
    "lab.unit": "lab.unit",
    "lab_flag": "lab.flag",
    "lab.flag": "lab.flag",
    "lab_reference_range": "lab.reference_range",
    "lab.reference_range": "lab.reference_range",
    "lab_test_name": "lab.test_name",
    "lab.test_name": "lab.test_name",
}

# Recognized medical labels intentionally unsupported by ClinicalDoc's 10-field Claim schema
KNOWN_UNSUPPORTED_LABELS: dict[str, str] = {
    "form": "Dosage form (e.g., tablet, capsule, drops) is separate from route and dose in ClinicalDoc (§8, DEC-017).",
    "dosage_form": "Dosage form is separate from route/dose in ClinicalDoc.",
    "reason": "Clinical indication / reason for prescription is not represented in the canonical 10-field Claim schema.",
    "ade": "Adverse drug events are outside the canonical 10-field Claim schema (requires separate ADE safety model).",
    "adverse_drug_event": "Adverse drug event is outside the canonical Claim schema.",
    "severity": "Severity attributes are outside the canonical Claim schema.",
    "status": "Medication status is a temporal attribute, not an atomic entity claim.",
}


@dataclass
class TextSpan:
    """Represents a continuous character offset span."""
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class RawBratEntity:
    """Parsed entity directly from BRAT .ann file."""
    entity_id: str
    raw_label: str
    spans: list[TextSpan]
    annotated_text: str
    line_num: int
    raw_line: str

    @property
    def is_discontinuous(self) -> bool:
        return len(self.spans) > 1

    @property
    def start(self) -> int:
        return self.spans[0].start if self.spans else 0

    @property
    def end(self) -> int:
        return self.spans[-1].end if self.spans else 0


@dataclass
class ParseIssue:
    """Diagnostic detail for rejected or warning annotations."""
    issue_type: Literal[
        "malformed_line",
        "invalid_offsets",
        "out_of_bounds",
        "offset_text_mismatch",
        "unsupported_label",
        "discontinuous_span",
        "empty_text",
        "unparsed_record",
    ]
    line_num: int
    raw_line: str
    message: str
    entity_id: Optional[str] = None
    expected_text: Optional[str] = None
    actual_text: Optional[str] = None


@dataclass
class BratParseResult:
    """Complete, auditable output from BRAT standoff ingestion."""
    document_id: str
    source_text: str
    accepted_claims: list[GroundTruthClaim] = field(default_factory=list)
    unsupported_annotations: list[dict[str, Any]] = field(default_factory=list)
    rejected_annotations: list[ParseIssue] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    total_lines_read: int = 0

    @property
    def accepted_count(self) -> int:
        return len(self.accepted_claims)

    @property
    def unsupported_count(self) -> int:
        return len(self.unsupported_annotations)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected_annotations)

    def summary(self) -> dict[str, Any]:
        """Audit summary of ingestion."""
        field_counts = {}
        for c in self.accepted_claims:
            field_counts[c.field_type] = field_counts.get(c.field_type, 0) + 1

        unsupported_label_counts = {}
        for u in self.unsupported_annotations:
            lbl = u["raw_label"]
            unsupported_label_counts[lbl] = unsupported_label_counts.get(lbl, 0) + 1

        rejected_reason_counts = {}
        for r in self.rejected_annotations:
            rejected_reason_counts[r.issue_type] = rejected_reason_counts.get(r.issue_type, 0) + 1

        return {
            "document_id": self.document_id,
            "document_length_chars": len(self.source_text),
            "total_lines_read": self.total_lines_read,
            "accepted_claims_count": self.accepted_count,
            "accepted_by_field": field_counts,
            "unsupported_annotations_count": self.unsupported_count,
            "unsupported_by_label": unsupported_label_counts,
            "rejected_annotations_count": self.rejected_count,
            "rejected_by_reason": rejected_reason_counts,
            "relations_count": len(self.relations),
            "attributes_count": len(self.attributes),
        }


def parse_entity_line(line: str, line_num: int) -> tuple[Optional[RawBratEntity], Optional[ParseIssue]]:
    """Parse a single BRAT entity line (e.g. T1\\tDrug 12 20\\tAspirin)."""
    parts = line.strip().split("\t")
    if len(parts) < 3:
        return None, ParseIssue(
            issue_type="malformed_line",
            line_num=line_num,
            raw_line=line,
            message=f"Expected 3 tab-delimited columns (ID, label+offsets, text), found {len(parts)}",
        )

    entity_id = parts[0].strip()
    label_offsets = parts[1].strip()
    text = "\t".join(parts[2:]).strip()  # Preserve internal tabs if any

    if not entity_id.startswith("T"):
        return None, ParseIssue(
            issue_type="malformed_line",
            line_num=line_num,
            raw_line=line,
            entity_id=entity_id,
            message=f"Entity ID must begin with 'T', found '{entity_id}'",
        )

    # Split label and offsets: first token is label, remainder are offset pairs
    lo_parts = label_offsets.split(" ", 1)
    if len(lo_parts) < 2:
        return None, ParseIssue(
            issue_type="malformed_line",
            line_num=line_num,
            raw_line=line,
            entity_id=entity_id,
            message=f"Cannot separate label from offsets in '{label_offsets}'",
        )

    raw_label = lo_parts[0].strip()
    raw_spans_str = lo_parts[1].strip()

    # Parse spans (may be discontinuous, separated by ';')
    span_segments = raw_spans_str.split(";")
    spans: list[TextSpan] = []

    for seg in span_segments:
        seg_coords = seg.strip().split()
        if len(seg_coords) != 2:
            return None, ParseIssue(
                issue_type="invalid_offsets",
                line_num=line_num,
                raw_line=line,
                entity_id=entity_id,
                message=f"Span segment '{seg}' does not contain exactly two coordinates",
            )
        try:
            start = int(seg_coords[0])
            end = int(seg_coords[1])
        except ValueError:
            return None, ParseIssue(
                issue_type="invalid_offsets",
                line_num=line_num,
                raw_line=line,
                entity_id=entity_id,
                message=f"Non-integer coordinates in span '{seg}'",
            )

        if start > end or start < 0:
            return None, ParseIssue(
                issue_type="invalid_offsets",
                line_num=line_num,
                raw_line=line,
                entity_id=entity_id,
                message=f"Invalid span bounds: start ({start}) > end ({end}) or negative",
            )

        spans.append(TextSpan(start=start, end=end))

    if not text:
        return None, ParseIssue(
            issue_type="empty_text",
            line_num=line_num,
            raw_line=line,
            entity_id=entity_id,
            message="Annotation has empty text payload",
        )

    return RawBratEntity(
        entity_id=entity_id,
        raw_label=raw_label,
        spans=spans,
        annotated_text=text,
        line_num=line_num,
        raw_line=line,
    ), None


def parse_brat_content(
    document_id: str,
    source_text: str,
    ann_text: str,
) -> BratParseResult:
    """Parse BRAT standoff annotations against source document text."""
    result = BratParseResult(document_id=document_id, source_text=source_text)
    doc_len = len(source_text)

    lines = ann_text.splitlines()
    result.total_lines_read = len(lines)

    for line_idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        line_num = line_idx + 1

        # Check line type
        if line.startswith("T"):
            entity, issue = parse_entity_line(line, line_num)
            if issue is not None:
                result.rejected_annotations.append(issue)
                continue
            if entity is None:
                continue

            # 1. Validate offsets against document boundaries
            if entity.end > doc_len:
                result.rejected_annotations.append(ParseIssue(
                    issue_type="out_of_bounds",
                    line_num=line_num,
                    raw_line=line,
                    entity_id=entity.entity_id,
                    message=f"Span end {entity.end} exceeds document length {doc_len}",
                ))
                continue

            # 2. Validate text against document content at offsets
            if entity.is_discontinuous:
                # Handle discontinuous spans explicitly
                actual_parts = [source_text[s.start:s.end] for s in entity.spans]
                actual_combined = " ".join(actual_parts)
                # Verify pieces match
                mismatch = False
                for span_item in entity.spans:
                    if span_item.start > doc_len or span_item.end > doc_len:
                        mismatch = True
                        break
                if mismatch:
                    result.rejected_annotations.append(ParseIssue(
                        issue_type="out_of_bounds",
                        line_num=line_num,
                        raw_line=line,
                        entity_id=entity.entity_id,
                        message="Discontinuous span coordinates exceed document bounds",
                    ))
                    continue

                # Flag discontinuous span explicitly
                result.rejected_annotations.append(ParseIssue(
                    issue_type="discontinuous_span",
                    line_num=line_num,
                    raw_line=line,
                    entity_id=entity.entity_id,
                    expected_text=entity.annotated_text,
                    actual_text=actual_combined,
                    message=f"Discontinuous span ({len(entity.spans)} segments) cannot be represented as single scalar span in Claim contract",
                ))
                continue

            # Single continuous span
            actual_doc_text = source_text[entity.start:entity.end]
            if actual_doc_text != entity.annotated_text:
                # Check if mismatch is due to Windows newline \r\n normalization
                actual_stripped = actual_doc_text.replace("\r", "")
                annotated_stripped = entity.annotated_text.replace("\r", "")
                if actual_stripped != annotated_stripped:
                    result.rejected_annotations.append(ParseIssue(
                        issue_type="offset_text_mismatch",
                        line_num=line_num,
                        raw_line=line,
                        entity_id=entity.entity_id,
                        expected_text=entity.annotated_text,
                        actual_text=actual_doc_text,
                        message=f"Offset mismatch at [{entity.start}:{entity.end}]: expected '{entity.annotated_text}', got '{actual_doc_text}'",
                    ))
                    continue

            # 3. Check label mapping
            label_clean = entity.raw_label.lower().strip()
            if label_clean in UNAMBIGUOUS_LABEL_MAP:
                canonical_field = UNAMBIGUOUS_LABEL_MAP[label_clean]
                val_numeric = None
                raw_unit = None
                if canonical_field in ("med.dose", "lab.value"):
                    m = re.search(r"(-?\d+(?:\.\d+)?)", entity.annotated_text)
                    if m:
                        try:
                            val_numeric = float(m.group(1))
                            unit_candidate = entity.annotated_text[m.end():].strip()
                            if unit_candidate:
                                raw_unit = unit_candidate
                        except ValueError:
                            pass

                claim = GroundTruthClaim(
                    document_id=document_id,
                    field_type=canonical_field,
                    value=entity.annotated_text,
                    value_numeric=val_numeric,
                    unit_raw=raw_unit,
                    char_start=entity.start,
                    char_end=entity.end,
                    evidence_text=actual_doc_text,
                    page_number=1,
                )
                result.accepted_claims.append(claim)
            else:
                # Unsupported label - record with audit explanation
                explanation = KNOWN_UNSUPPORTED_LABELS.get(
                    label_clean, f"Label '{entity.raw_label}' is outside ClinicalDoc's 10-field Claim schema."
                )
                result.unsupported_annotations.append({
                    "entity_id": entity.entity_id,
                    "raw_label": entity.raw_label,
                    "start": entity.start,
                    "end": entity.end,
                    "text": entity.annotated_text,
                    "line_num": line_num,
                    "reason": explanation,
                })

        elif line.startswith("R"):
            # Relation annotation (e.g. R1\tStrength-Drug Arg1:T2 Arg2:T1)
            parts = line.split("\t")
            if len(parts) >= 2:
                rel_info = parts[1].strip().split()
                rel_name = rel_info[0] if rel_info else "UNKNOWN"
                args = {p.split(":")[0]: p.split(":")[1] for p in rel_info[1:] if ":" in p}
                result.relations.append({
                    "relation_id": parts[0].strip(),
                    "relation_name": rel_name,
                    "args": args,
                    "raw_line": line,
                })
        elif line.startswith("A") or line.startswith("M"):
            # Attribute annotation (e.g. A1\tStatus T1 Active)
            parts = line.split("\t")
            if len(parts) >= 2:
                attr_info = parts[1].strip().split()
                result.attributes.append({
                    "attribute_id": parts[0].strip(),
                    "info": attr_info,
                    "raw_line": line,
                })
        else:
            result.rejected_annotations.append(ParseIssue(
                issue_type="malformed_line",
                line_num=line_num,
                raw_line=line,
                message=f"Unrecognized BRAT annotation line format: '{line}'",
            ))

    return result


def parse_brat_file(txt_path: Path | str, ann_path: Path | str) -> BratParseResult:
    """Parse BRAT annotation file and corresponding text document."""
    txt_p = Path(txt_path)
    ann_p = Path(ann_path)

    if not txt_p.exists():
        raise FileNotFoundError(f"Source text document not found at: {txt_p}")
    if not ann_p.exists():
        raise FileNotFoundError(f"Annotation file not found at: {ann_p}")

    doc_id = txt_p.stem
    source_text = txt_p.read_text(encoding="utf-8", errors="replace")
    ann_text = ann_p.read_text(encoding="utf-8", errors="replace")

    return parse_brat_content(doc_id, source_text, ann_text)


def brat_result_to_document_text(result: BratParseResult) -> DocumentText:
    """Wrap BRAT plain text into a canonical DocumentText container for evaluation.

    Note: Degenerate default bounding boxes (0, 0, 0, 0) are used because BRAT
    text documents lack optical layout/geometry. Does NOT validate OCR or layout.
    """
    words = result.source_text.split()
    tokens = [
        Token(
            text=w,
            bbox=BBoxDict(x0=0.0, y0=0.0, x1=0.0, y1=0.0),
            confidence=None,
            line_id="line_0",
        )
        for w in words
    ]
    page = DocumentPage(
        page_number=1,
        width_px=1000,
        height_px=1000,
        tokens=tokens,
    )
    return DocumentText(
        document_id=result.document_id,
        source_type="born_digital",
        pages=[page],
    )
