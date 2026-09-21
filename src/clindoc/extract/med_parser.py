"""Medication regimen parser combining GLiNER-BioMed NER with deterministic rule refinement."""

import json
from pathlib import Path
import re
from typing import Any

from clindoc.extract.gliner_bridge import GLiNERBioMedBridge
from clindoc.ocr.schema import DocumentOCRRecord, LineBox
from clindoc.schema.evidence import SourceEvidence
from clindoc.schema.lab import ExtractedPatientInfo
from clindoc.schema.medication import ExtractedMedication, ExtractedPrescriptionDocument

DEFAULT_CATALOG_PATH = Path("configs/medication_subset.json")


def _load_medication_catalog(path: Path = DEFAULT_CATALOG_PATH) -> dict[str, Any]:
    if not path.exists():
        proj_path = Path(__file__).resolve().parents[3] / path
        if proj_path.exists():
            path = proj_path
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


class MedicationRegimenParser:
    """Parser that extracts structured medication regimens from OCR lines using GLiNER + rules."""

    def __init__(
        self,
        bridge: GLiNERBioMedBridge | None = None,
        catalog_path: Path = DEFAULT_CATALOG_PATH,
    ):
        self.bridge = bridge or GLiNERBioMedBridge()
        self.catalog = _load_medication_catalog(catalog_path)

    def _extract_patient_and_metadata(
        self, lines: list[LineBox]
    ) -> tuple[ExtractedPatientInfo | None, str | None, str | None, str | None]:
        """Extract patient demographics, prescription date, prescriber, and facility from OCR lines."""
        name = None
        dob = None
        patient_id = None
        gender = None
        presc_date = None
        prescriber = None
        facility = lines[0].text.strip() if lines else None

        for line in lines:
            txt = line.text.strip()

            # Patient Name
            m_name = re.search(r"Patient(?: Name)?:\s*([^\|\n]+)", txt, re.IGNORECASE)
            if not m_name:
                m_name = re.search(r"PATIENT:\s*([A-Za-z\s]+?)(?:\s*\(MRN|\s*\||\n|$)", txt)
            if m_name and not name:
                name = m_name.group(1).strip()

            # DOB
            m_dob = re.search(r"DOB:\s*(\d{4}-\d{2}-\d{2})", txt, re.IGNORECASE)
            if m_dob and not dob:
                dob = m_dob.group(1).strip()

            # MRN
            m_mrn = re.search(r"MRN:\s*([A-Za-z0-9\-]+)", txt, re.IGNORECASE)
            if m_mrn and not patient_id:
                patient_id = m_mrn.group(1).strip()

            # Gender
            m_gender = re.search(r"Gender:\s*([MF])\b", txt, re.IGNORECASE)
            if m_gender and not gender:
                gender = m_gender.group(1).upper()

            # Date
            m_date = re.search(
                r"(?:Date|Discharge Date|Date Filled|Order Date):\s*(\d{4}-\d{2}-\d{2})",
                txt,
                re.IGNORECASE,
            )
            if m_date and not presc_date:
                presc_date = m_date.group(1).strip()

            # Prescriber
            m_doc = re.search(
                r"(?:Prescriber|Attending Clinician|Ordering Provider):\s*([^\|\n]+)",
                txt,
                re.IGNORECASE,
            )
            if m_doc and not prescriber:
                prescriber = m_doc.group(1).strip()

        patient_info = None
        if name or patient_id or dob:
            patient_info = ExtractedPatientInfo(
                patient_id=patient_id,
                name=name,
                dob=dob,
                gender=gender,  # type: ignore
            )

        return patient_info, presc_date, prescriber, facility

    def _normalize_med_name(self, raw_name: str) -> tuple[str, str | None]:
        """Normalize drug name against curated catalog, returning (canonical_name, rxcui)."""
        raw_clean = raw_name.strip().lower()
        if raw_clean in self.catalog:
            entry = self.catalog[raw_clean]
            return entry["canonical_name"], entry.get("rxcui")

        for key, entry in self.catalog.items():
            if key in raw_clean or raw_clean in key:
                return entry["canonical_name"], entry.get("rxcui")

        return raw_name.strip().title(), None

    def _parse_dose(self, text: str) -> tuple[float | None, str | None]:
        """Extract numeric dose and unit from text string, preferring mass/volume over counts."""
        m = re.search(r"(\d+(?:\.\d+)?)\s*(mg|mcg|g|mL)\b", text, re.IGNORECASE)
        if not m:
            m = re.search(r"(\d+(?:\.\d+)?)\s*(units?|puffs?)\b", text, re.IGNORECASE)
        if m:
            return float(m.group(1)), m.group(2).lower()
        return None, None

    def _parse_dosage_form(self, text: str) -> str | None:
        """Extract dosage form from text."""
        forms = [
            "extended-release tablet",
            "delayed-release capsule",
            "oral suspension",
            "chewable tablet",
            "capsule",
            "tablet",
            "inhaler",
            "solution",
            "suspension",
            "patch",
        ]
        text_lower = text.lower()
        for f in forms:
            if re.search(r"\b" + re.escape(f) + r"\b", text_lower):
                return f
        if re.search(r"\btabs?\b", text_lower):
            return "tablet"
        if re.search(r"\bcaps?\b", text_lower):
            return "capsule"
        return None

    def _parse_route(self, text: str) -> str | None:
        """Extract administration route."""
        text_lower = text.lower()
        if "by inhalation" in text_lower:
            return "by inhalation"
        if "by mouth" in text_lower:
            return "by mouth"
        if re.search(r"\bpo\b", text_lower):
            return "PO"
        if re.search(r"\boral\b", text_lower):
            return "oral"
        if re.search(r"\binhalation\b", text_lower):
            return "inhalation"
        if re.search(r"\btopical\b", text_lower):
            return "topical"
        if re.search(r"\bsubcutaneous\b", text_lower):
            return "subcutaneous"
        return None

    def _parse_frequency(self, text: str) -> str | None:
        """Extract dosing frequency, checking longest patterns first."""
        freq_patterns = [
            "once daily in the morning with food",
            "once daily before breakfast",
            "once daily at bedtime",
            "once daily in the morning",
            "every morning on an empty stomach",
            "every 4 to 6 hours as needed",
            "every 4-6 hours prn",
            "as needed for shortness of breath",
            "three times daily",
            "three times a day",
            "twice daily",
            "twice a day",
            "four times daily",
            "every 12 hours",
            "every 24 hours",
            "every 8 hours",
            "every 6 hours",
            "every morning",
            "every evening",
            "at bedtime",
            "once daily",
            "daily",
            "qday",
            "qid",
            "tid",
            "bid",
            "qhs",
            "qd",
            "prn",
        ]
        text_lower = text.lower()
        for fp in freq_patterns:
            pattern = r"\b" + re.escape(fp) + r"\b"
            if re.search(pattern, text_lower):
                if fp in ("tid", "bid", "qid", "qd", "qhs", "prn"):
                    return fp.upper()
                if fp == "every 4-6 hours prn":
                    return "every 4-6 hours PRN"
                return fp
        return None

    def _parse_duration(self, text: str) -> str | None:
        """Extract duration of therapy (calendar duration e.g. '7 days', '30 days', or explicit 'as needed')."""
        m_as_needed = re.search(r"Duration:\s*(as needed)\b", text, re.IGNORECASE)
        if m_as_needed:
            return "as needed"
        m = re.search(r"(?:for|x|duration:\s*)\s*(\d+\s*(?:days?|weeks?|months?))\b", text, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        m_dur = re.search(r"\b(\d+\s*(?:days?|weeks?|months?))\b", text, re.IGNORECASE)
        if m_dur:
            return m_dur.group(1).lower()
        return None

    def _parse_quantity_and_refills(self, text: str) -> tuple[int | None, int | None]:
        """Extract quantity and refill numbers."""
        qty = None
        refills = None

        # First look for explicit quantity prefixes
        m_qty = re.search(
            r"(?:Quantity:\s*#?|Qty:\s*#?|Dispense:\s*#?|Dispense Quantity:\s*#?)\s*(\d+)",
            text,
            re.IGNORECASE,
        )
        if m_qty:
            qty = int(m_qty.group(1))
        else:
            # Match standalone #N not preceded by Order or RX
            m_hash = re.search(
                r"(?<!Order\s)(?<!Order)(?<!RX\s)(?<!RX)#(\d+)", text, re.IGNORECASE
            )
            if m_hash:
                qty = int(m_hash.group(1))

        m_ref = re.search(r"(?:Refills?:\s*|Ref:\s*|Refills Left:\s*)(\d+)", text, re.IGNORECASE)
        if m_ref:
            refills = int(m_ref.group(1))

        return qty, refills

    def _extract_document_blocks(self, lines: list[LineBox]) -> list[dict[str, Any]]:
        """Identify medication blocks from OCR lines across all template formats."""
        is_table = any("Prescribed Discharge Medications" in l.text for l in lines)
        is_pharmacy_label = any(
            "Discard After:" in l.text or "Pharmacy Lic" in l.text for l in lines
        )

        drug_line_indices: list[tuple[int, str, LineBox]] = []
        for idx, l in enumerate(lines):
            txt_lower = l.text.lower()
            if l.text.strip() in (
                "Medication",
                "Strength / Dose",
                "Route & Frequency",
                "Instructions (Sig)",
                "Qty / Refills",
            ):
                continue
            for drug_key, entry in self.catalog.items():
                if re.search(r"\b" + re.escape(drug_key) + r"\b", txt_lower):
                    drug_line_indices.append((idx, entry["canonical_name"], l))
                    break

        blocks: list[dict[str, Any]] = []

        if is_table:
            # Table rows in discharge summary: sort by column bracket then y
            def _get_col(x: float) -> int:
                if x < 152:
                    return 0
                if x < 237:
                    return 1
                if x < 337:
                    return 2
                if x < 487:
                    return 3
                return 4

            drug_line_indices.sort(key=lambda item: item[2].bbox[1])
            for i, (idx, drug_name, drug_line) in enumerate(drug_line_indices):
                y_start = drug_line.bbox[1] - 4.0
                if i + 1 < len(drug_line_indices):
                    y_end = drug_line_indices[i + 1][2].bbox[1] - 4.0
                else:
                    y_end = y_start + 40.0

                row_lines = [
                    l
                    for l in lines
                    if l.bbox[1] >= y_start
                    and l.bbox[3] <= y_end + 10.0
                    and "Important:" not in l.text
                    and "Signature:" not in l.text
                    and l.text.strip()
                    not in (
                        "Medication",
                        "Strength / Dose",
                        "Route & Frequency",
                        "Instructions (Sig)",
                        "Qty / Refills",
                    )
                ]
                row_lines.sort(key=lambda l: (_get_col(l.bbox[0]), l.bbox[1]))
                combined_text = " ".join(l.text.strip() for l in row_lines)
                blocks.append(
                    {
                        "drug_name": drug_name,
                        "anchor_line": drug_line,
                        "lines": row_lines,
                        "combined_text": combined_text,
                    }
                )
        elif is_pharmacy_label:
            # Pharmacy container label: directions precede medication line
            for i, (idx, drug_name, drug_line) in enumerate(drug_line_indices):
                start_idx = max(0, idx - 2)
                end_idx = min(len(lines), idx + 3)
                group = [
                    lines[j]
                    for j in range(start_idx, end_idx)
                    if not any(
                        lines[j].text.startswith(hdr)
                        for hdr in ["PATIENT:", "RX#", "CAUTION:", "Signature:"]
                    )
                ]
                combined_text = " ".join(l.text.strip() for l in group)
                blocks.append(
                    {
                        "drug_name": drug_name,
                        "anchor_line": drug_line,
                        "lines": group,
                        "combined_text": combined_text,
                    }
                )
        else:
            # Prescription pad or MAR order sheet
            for i, (idx, drug_name, drug_line) in enumerate(drug_line_indices):
                next_idx = (
                    drug_line_indices[i + 1][0]
                    if i + 1 < len(drug_line_indices)
                    else len(lines)
                )
                end_idx = min(next_idx, idx + 8)
                group = [
                    lines[j]
                    for j in range(idx, end_idx)
                    if not any(
                        lines[j].text.startswith(hdr)
                        for hdr in [
                            "Signature:",
                            "Important:",
                            "Pharmacist:",
                            "Refill By:",
                            "CAUTION:",
                        ]
                    )
                ]
                combined_text = " ".join(l.text.strip() for l in group)
                blocks.append(
                    {
                        "drug_name": drug_name,
                        "anchor_line": drug_line,
                        "lines": group,
                        "combined_text": combined_text,
                    }
                )

        return blocks

    def _build_extracted_medication(
        self, block: dict[str, Any], entities: list[dict[str, Any]]
    ) -> ExtractedMedication:
        """Combine GLiNER predicted entities with deterministic rules for one medication block."""
        anchor: LineBox = block["anchor_line"]
        full_text: str = block["combined_text"]

        gliner_ents: dict[str, str] = {}
        for ent in entities:
            lbl = ent["label"]
            if lbl not in gliner_ents:
                gliner_ents[lbl] = ent["text"]

        # 1. Drug name: prefer GLiNER medication, fallback to block detected drug
        raw_med = gliner_ents.get("medication", block["drug_name"])
        canonical_name, rxcui = self._normalize_med_name(raw_med)

        # 2. Dose & Unit: check GLiNER dosage or regex on full text
        dose, unit = self._parse_dose(gliner_ents.get("dosage", ""))
        if dose is None:
            dose, unit = self._parse_dose(full_text)

        # 3. Dosage form: check GLiNER dosage form or regex
        dosage_form = self._parse_dosage_form(gliner_ents.get("dosage form", ""))
        if not dosage_form:
            dosage_form = self._parse_dosage_form(full_text)

        # 4. Route: check GLiNER route or regex
        route = self._parse_route(gliner_ents.get("route", ""))
        if not route:
            route = self._parse_route(full_text)

        # 5. Frequency: check GLiNER frequency and text regex, preferring longer specific phrase
        freq_gliner = self._parse_frequency(gliner_ents.get("frequency", ""))
        freq_text = self._parse_frequency(full_text)
        if freq_gliner and freq_text:
            freq = freq_text if len(freq_text) > len(freq_gliner) else freq_gliner
        else:
            freq = freq_gliner or freq_text

        # 6. Duration: check GLiNER duration or regex
        dur = self._parse_duration(gliner_ents.get("duration", ""))
        if not dur:
            dur = self._parse_duration(full_text)

        # 7. Quantity & Refills: regex on full text
        qty, refills = self._parse_quantity_and_refills(full_text)

        # 8. Sig: extract from "Sig: ...", "DIRECTIONS: ...", "Instructions: ...", or fallback
        sig_m = re.search(
            r"(?:Sig:\s*|DIRECTIONS:\s*|Directions:\s*|Instructions:\s*|Administration Instructions:\s*)([^\n]+)",
            full_text,
            re.IGNORECASE,
        )
        if sig_m:
            sig_raw = sig_m.group(1).strip()
            sig_clean = re.sub(
                r"\s*(?:Dispense|Refills?|QTY|MEDICATION|Order #|Duration).*$",
                "",
                sig_raw,
                flags=re.IGNORECASE,
            ).strip()
            sig = sig_clean if sig_clean else sig_raw
        else:
            sig = full_text

        # Create SourceEvidence with line bounding box and OCR confidence
        evidence = SourceEvidence(
            text=anchor.text.strip(),
            page=anchor.page,
            bbox=anchor.bbox,
            ocr_confidence=anchor.confidence,
        )

        return ExtractedMedication(
            medication_name=canonical_name,
            dose=dose,
            dose_unit=unit,
            dosage_form=dosage_form,
            route=route,
            frequency=freq,
            duration=dur,
            quantity=qty,
            refills=refills,
            sig=sig,
            normalized_name=canonical_name,
            rxcui=rxcui,
            source_evidence=evidence,
            source_model="gliner_biomed_v1.0",
        )

    def parse_document(
        self, ocr_record: DocumentOCRRecord
    ) -> ExtractedPrescriptionDocument:
        """Parse a single OCR record into a structured ExtractedPrescriptionDocument."""
        patient, presc_date, prescriber, facility = self._extract_patient_and_metadata(
            ocr_record.lines
        )

        blocks = self._extract_document_blocks(ocr_record.lines)
        texts_to_predict = [b["combined_text"] for b in blocks]

        ner_results: list[list[dict[str, Any]]] = []
        if texts_to_predict:
            ner_results = self.bridge.predict_batch(texts_to_predict)

        extracted_medications = [
            self._build_extracted_medication(b, ents)
            for b, ents in zip(blocks, ner_results)
        ]

        return ExtractedPrescriptionDocument(
            doc_id=ocr_record.doc_id,
            patient=patient,
            date=presc_date,
            prescriber=prescriber,
            facility=facility,
            medications=extracted_medications,
            metadata={"total_extracted_medications": len(extracted_medications)},
        )

    def parse_documents(
        self, ocr_records: list[DocumentOCRRecord]
    ) -> list[ExtractedPrescriptionDocument]:
        """Batch parse multiple OCR records with a single GLiNER inference call."""
        all_blocks: list[tuple[int, dict[str, Any]]] = []
        doc_metadata: list[dict[str, Any]] = []

        for doc_idx, rec in enumerate(ocr_records):
            patient, presc_date, prescriber, facility = self._extract_patient_and_metadata(
                rec.lines
            )
            doc_metadata.append({
                "doc_id": rec.doc_id,
                "patient": patient,
                "date": presc_date,
                "prescriber": prescriber,
                "facility": facility,
            })
            blocks = self._extract_document_blocks(rec.lines)
            for b in blocks:
                all_blocks.append((doc_idx, b))

        all_texts = [b["combined_text"] for _, b in all_blocks]
        ner_results = self.bridge.predict_batch(all_texts) if all_texts else []

        doc_medications: dict[int, list[ExtractedMedication]] = {
            i: [] for i in range(len(ocr_records))
        }

        for (doc_idx, block), entities in zip(all_blocks, ner_results):
            med = self._build_extracted_medication(block, entities)
            doc_medications[doc_idx].append(med)

        results = []
        for i, meta in enumerate(doc_metadata):
            meds = doc_medications[i]
            results.append(
                ExtractedPrescriptionDocument(
                    doc_id=meta["doc_id"],
                    patient=meta["patient"],
                    date=meta["date"],
                    prescriber=meta["prescriber"],
                    facility=meta["facility"],
                    medications=meds,
                    metadata={"total_extracted_medications": len(meds)},
                )
            )

        return results
