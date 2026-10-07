# PROJECT_SPEC.md
## AI-Based Multimodal Clinical Document-to-EHR Conversion and Verification

**Status:** APPROVED ARCHITECTURE — implementation baseline
**Version:** 1.0
**Owner:** Project team (4 engineers)
**Purpose of this document:** This is the single source of truth for the project. Any AI coding agent or team member implementing any part of the system MUST implement it as specified here, not as remembered from prior discussion. Where a decision has not been finalized, this document says `DECISION REQUIRED` explicitly rather than inventing a default — an implementer MUST stop and get that decision resolved (by updating this document) before building the dependent component.

### 0.1 Requirement language

This document uses RFC-2119-style keywords with the following meanings:

- **MUST / MUST NOT** — non-negotiable. Non-compliant implementations are considered incorrect regardless of convenience.
- **SHOULD / SHOULD NOT** — strong default. Deviation requires a written justification added to `docs/decisions/` (see §0.3).
- **MAY** — optional, permitted, not required for any acceptance criterion.
- **DECISION REQUIRED** — a concrete choice has not been made. Do not substitute your own default silently. Raise it, get it resolved, record the resolution in `docs/decisions/`, then proceed.

### 0.2 Out-of-band artifacts this spec assumes

This spec assumes the existence of (or requirement to create) the following, referenced throughout:

- `schemas/` — JSON Schema files for every contract in §7–§10 (machine-validated, not just documented here).
- `docs/decisions/` — one markdown file per `DECISION REQUIRED` item once resolved, named `DEC-NNN-short-title.md`, containing: decision, date, options considered, rationale.
- `benchmark/` — all dataset generation code and generated data (§18–§20).
- `experiments/` — one directory per experiment ID from §22, each with its own config, results, and a `REPORT.md`.

### 0.3 How to use this document if you are an AI coding agent

1. Read §1–§4 fully before writing any code, to understand the system as a whole.
2. Before implementing component *X*, read §5 (responsibilities), the relevant subsection of §7 (I/O contract), and any schema in §8–§10 that the component consumes or produces.
3. If a `DECISION REQUIRED` blocks the component you were asked to build, stop and surface it — do not pick a library/model yourself and proceed silently.
4. Every component MUST be implemented against its JSON Schema contract with schema validation in code (not just typing discipline) — invalid inputs/outputs MUST raise, not warn.
5. Do not build anything listed in §28 ("what must NOT be implemented"), even if it seems like a natural extension.

---

## 1. Problem Statement and Project Objective

### 1.1 Problem statement

Clinical documents (lab reports, prescriptions, discharge summaries, clinical notes, scanned PDFs/images) contain information that needs to be converted into structured, interoperable healthcare data (FHIR R4B). Existing "LLM reads document, LLM writes FHIR" approaches produce structured output with no mechanism for a downstream consumer (clinician, auditor, downstream system) to know whether any given structured field is actually supported by the source document, is a misreading/hallucination, or was never present in the source at all.

### 1.2 Objective

Build a system that:

1. Extracts clinical claims from heterogeneous clinical documents using a **hybrid extraction router** (deterministic parsers + biomedical NER for structured content, a schema-constrained LLM for free-text narrative content only).
2. Normalizes extracted values to standard terminologies (LOINC, RxNorm, UCUM) via retrieval.
3. **Verifies every structured claim against its source evidence** through a **verification cascade** (deterministic checker → biomedical encoder/NLI classifier → LLM judge, escalated only on disagreement or low confidence), producing one of `SUPPORTED`, `DISCREPANCY`, `INSUFFICIENT_EVIDENCE`, with calibrated confidence and full provenance (text span, page, bounding box, OCR confidence).
4. Emits FHIR R4B resources with verification and provenance data attached as extensions.
5. Is evaluated with document-level and template-level out-of-distribution splits, controlled robustness degradation, and — where feasible in the project timeline — a real de-identified clinical note validation slice, not solely on clean synthetic data.

### 1.3 What "done" means

The project is complete when: a clinical document (including degraded scans) can be submitted to the pipeline and produce (a) a FHIR R4B bundle, (b) a machine-readable audit report where every element of the bundle is traceable to a claim, an evidence span, a verification verdict, a calibrated confidence, and (where escalated) an LLM rationale, and (c) the full experiment suite in §22 has been run and reported with the metrics in §21, including OOD and robustness results.

---

## 2. Exact Scope

### 2.1 Document types (MUST support)

- Laboratory reports (CBC, CMP, Lipid panel; tabular structured layout)
- Medication regimens / prescriptions (semi-structured: drug, dose, route, frequency, duration)
- Discharge summaries / clinical notes (free-text narrative sections — NOTE: in narrative clinical notes, extraction is strictly bounded to medication regimens and laboratory observations mentioned in clinical prose; diagnoses, conditions, procedures, allergies, and general clinical prose concepts are explicitly out of scope for the core pipeline, per §8.2 and DEC-020)
- Both born-digital PDFs and scanned/rasterized images of the above

### 2.2 Input modalities (MUST support)

- Born-digital PDF (text layer present)
- Scanned PDF / image (PDF page rasterized or photographed; no reliable text layer)
- Documents with visual degradation as defined in §20.2 (blur, compression, resolution loss, rotation/skew, contrast/brightness shift, local artifacts, combined degradation)

### 2.3 Output (MUST produce)

- FHIR R4B `Bundle` containing `Patient`, `Organization`, `Practitioner` (as available), `DiagnosticReport`, `Observation` (labs), medication-related resources (`MedicationStatement` and/or `MedicationRequest` — see §15.3) — each annotated with verification + provenance extensions (§16).
- A separate machine-readable **audit report** (JSON) enumerating every claim, its evidence, its verification verdict, confidence, and (if escalated) rationale — this MUST exist independently of the FHIR bundle, not only as buried extensions, so it can be evaluated and displayed without a FHIR parser.

### 2.4 Terminology scope (MUST support)

- LOINC (lab test codes)
- RxNorm (medication codes)
- UCUM (units of measure)
- SNOMED CT is explicitly **deferred** (see §3) — do not implement in the core pipeline; a stub interface MAY exist but MUST NOT be wired into the critical path.

### 2.5 LLM scope (MUST be narrow — see §13 for full restrictions)

The LLM is used ONLY for: (a) narrative-section structured extraction, (b) verification-cascade judge/adjudication on escalated cases, (c) rationale generation for escalated verification cases, (d) one standalone "LLM-only end-to-end" experimental baseline (§22). The LLM MUST NOT be the default/primary extractor or default/primary verifier for the production pipeline.

---

## 3. Non-Goals

The following are explicitly **out of scope** for this project. Do not implement them; do not silently drift toward them:

- Clinical decision support / treatment recommendation of any kind.
- Determining whether an extracted claim is *clinically correct* or *clinically appropriate* (e.g., "is this dose safe") — the system verifies **document-fidelity** (does the structured claim match the source text), never clinical plausibility. See §5.6 and §6 for the hard boundary between these two concepts.
- SNOMED CT integration (deferred beyond this project's timeline).
- Longitudinal / cross-visit reasoning (e.g., detecting "copied-forward" notes across multiple encounters, tracking medication changes over time across documents).
- Disambiguating patient-history vs. family-history mentions, or physician-plan vs. actually-administered medication (acknowledged as a real, hard, and currently unsolved problem for this system — see §23.2).
- Training a custom layout/vision-language model (e.g., a from-scratch LayoutLMv3). Off-the-shelf pretrained components MAY be used as tools (§7.1) but MUST NOT be fine-tuned from scratch as a research deliverable of this project.
- Real-time / low-latency inference. The system is batch/offline; there is no requirement for sub-second response times anywhere in the pipeline.
- Multi-patient record linkage, deduplication across documents, or a patient-level longitudinal database.
- Production-grade authentication, deployment, HIPAA-compliant hosting, or any real-PHI handling infrastructure. All real-data work (§18.3) uses only properly de-identified, credentialed-access research datasets under their own data use agreements — this project does not process any live/production PHI.
- General external medical-knowledge RAG (e.g., retrieving clinical guidelines to judge a claim). See §6.4 — explicitly prohibited from the verification path.

---

## 4. System Architecture

### 4.1 Architecture diagram

```
Clinical PDF/Image
        │
        ▼
┌───────────────────────────┐
│  1. INGESTION & OCR       │  PyMuPDF (born-digital) / PaddleOCR (scanned)
│  → text + bbox + page +   │
│    per-token OCR confidence│
└───────────────────────────┘
        │  DocumentText (schema §7.1)
        ▼
┌───────────────────────────────────────────────────┐
│  2. EXTRACTION ROUTER (schema §11)                 │
│                                                     │
│  Structured/tabular content (labs, med regimen     │
│  tables/lines)                                     │
│    → deterministic parser (regex/rule engine)      │
│    → GLiNER-BioMed (biomedical NER)                │
│                                                     │
│  Free-text narrative (discharge summary sections,  │
│  impressions, plans) OR low-confidence spans        │
│  escalated from the deterministic/GLiNER path       │
│    → schema-constrained LLM extractor              │
└───────────────────────────────────────────────────┘
        │  Claim[] (schema §8), each with raw evidence pointer
        ▼
┌───────────────────────────┐
│  3. TERMINOLOGY            │  Retrieval over LOINC / RxNorm / UCUM
│  NORMALIZATION (RAG)       │  (embedding + lexical hybrid retrieval,
│                             │   §14)
└───────────────────────────┘
        │  Claim[] with normalized_code, code_confidence
        ▼
┌─────────────────────────────────────────────────────┐
│  4. VERIFICATION CASCADE (schema §10, routing §12)   │
│                                                       │
│  Stage A: deterministic value/unit/code checker      │
│  Stage B: ModernBERT/NLI semantic classifier         │
│           (escalates to Stage C on disagreement       │
│            with Stage A, or confidence < threshold)  │
│  Stage C: LLM judge — final label + rationale        │
│           (only reached for escalated claims)        │
└─────────────────────────────────────────────────────┘
        │  VerificationResult[] (schema §10)
        ▼
┌───────────────────────────┐
│  5. FHIR R4B GENERATION    │  Claims + normalized codes + verification
│                             │  + provenance → FHIR resources with
│                             │  verification/provenance extensions
└───────────────────────────┘
        │
        ▼
┌───────────────────────────┐
│  6. AUDIT REPORT            │  Standalone JSON: every claim ↔ evidence
│                             │  ↔ verdict ↔ confidence ↔ rationale
└───────────────────────────┘
        │
        ▼
FHIR Bundle + Audit Report (final deliverable of one pipeline run)
```

### 4.2 Architectural principles (MUST)

- **No stage is allowed to silently swallow low confidence.** Every component that produces a confidence score below its configured threshold MUST propagate an explicit `escalate: true` flag rather than picking a default label.
- **The LLM is never the sole source of truth for any claim.** Every LLM output in the extraction router or verification cascade MUST be traceable back to the evidence span it was conditioned on, and MUST go through schema validation before being accepted.
- **Verification is about document-fidelity, not clinical correctness.** This boundary is enforced structurally: the verification cascade (§4.1 step 4) receives ONLY the claim and its evidence text/context from the same document. It MUST NOT receive, and MUST NOT be given access to, any external medical knowledge base, guideline text, or clinical-appropriateness signal. See §6.4.
- **Every FHIR element MUST be attributable** to exactly one claim ID and one verification result ID (§16).

---

## 5. Component Responsibilities

| # | Component | Responsibility | MUST NOT |
|---|---|---|---|
| 1 | Ingestion & OCR | Convert PDF/image → `DocumentText` (text, page, bbox, OCR confidence per token/line) | MUST NOT perform any clinical interpretation |
| 2 | Extraction Router | Decide, per document region, whether deterministic/GLiNER or LLM path handles it (§11) | MUST NOT run the LLM path on already-high-confidence structured spans by default |
| 3 | Deterministic Parser | Regex/rule-based extraction of lab values, units, reference ranges, flags, and medication regimen fields from structured/templated text | MUST NOT attempt free-text narrative parsing |
| 4 | GLiNER-BioMed Extractor | Biomedical NER extraction supplementing/cross-checking the deterministic parser on structured & semi-structured spans | MUST NOT be used on long free-text narrative paragraphs as primary extractor |
| 5 | LLM Narrative Extractor | Schema-constrained extraction of claims from free-text narrative sections and escalated low-confidence spans | MUST NOT generate a claim value not traceable to a specific evidence span in the input it was given; MUST NOT be used on structured tabular content by default |
| 6 | Terminology Normalizer | Map raw extracted strings (drug names, test names, units) to LOINC/RxNorm/UCUM codes via retrieval | MUST NOT invent a code when retrieval confidence is below threshold — MUST emit `code_confidence` and leave `normalized_code: null` instead |
| 7 | Verification Stage A (deterministic) | Rule-based check of claim value/unit/dose against evidence text (string/numeric comparison after unit normalization) | MUST NOT use any ML model |
| 8 | Verification Stage B (ModernBERT/NLI) | Learned semantic classification of (claim, evidence) → label + calibrated confidence | MUST NOT be treated as final if it disagrees with Stage A or its confidence is below threshold — MUST escalate instead |
| 9 | Verification Stage C (LLM judge) | Adjudicate cases where A and B disagree or B is low-confidence; produce final label + confidence + rationale | MUST NOT be invoked for every claim — only escalated ones (§12); MUST NOT reference outside medical knowledge, only the claim + evidence + document context provided |
| 10 | FHIR Generator | Map normalized, verified claims to FHIR R4B resources with verification/provenance extensions | MUST NOT emit a FHIR resource for a claim that has no corresponding `VerificationResult` |
| 11 | Audit Report Generator | Produce standalone JSON audit trail, independent of FHIR | MUST NOT omit any claim present in the FHIR bundle |
| 12 | Calibration Module | Post-hoc calibration (temperature scaling or isotonic regression, see §17) of Stage B confidence scores | MUST NOT calibrate on the same data used to select the calibration method's hyperparameters (train/val separation required) |
| 13 | Evaluation Harness | Run experiments in §22, compute metrics in §21, on train/val/OOD/robustness splits (§19) | MUST NOT mix documents across OOD/CV split boundaries when computing any reported metric |

---

## 6. Data Flow: Document → Audit Report

Step-by-step, with the object produced at each step and its schema reference:

1. **Input**: raw file (`.pdf`, `.png`, `.jpg`, `.tiff`) + `document_id` (assigned at ingestion).
2. **OCR/Ingestion** → `DocumentText` object (§7.1): list of pages, each with lines/tokens, each token has `text`, `bbox`, `confidence` (OCR engine's own confidence, `null` for born-digital extraction), `page_number`.
3. **Extraction Router** (§11) inspects each document region (heuristic: template/layout classifier + regex triggers for "structured" regions like lab tables and regimen lines) and assigns it to either the deterministic/GLiNER path or the LLM narrative path.
4. **Extraction** (deterministic/GLiNER path or LLM path) → list of `Claim` objects (§8), each referencing an `Evidence` object (§9) by ID (not embedding the evidence inline — evidence is a separate, deduplicated store keyed by `(document_id, page, char_offsets)`).
5. **Terminology Normalization** (§14) enriches each `Claim` with `normalized_code`, `code_system`, `code_confidence`.
6. **Verification Cascade** (§12) consumes each `(Claim, Evidence)` pair → one `VerificationResult` object (§10) per claim.
7. **FHIR Generation** (§15) consumes `Claim + VerificationResult + Evidence` → FHIR R4B resources, each carrying provenance/verification extensions (§16) that reference the originating `claim_id` and `verification_id`.
8. **Audit Report Generation** produces a single JSON document per input document: `{document_id, claims: [...], evidence: [...], verifications: [...], fhir_bundle_ref}` — this is the artifact evaluated in §21 and shown in any demo UI.

---

## 7. Input/Output Contracts

All contracts below MUST be implemented as JSON Schema files under `schemas/` and validated at every component boundary (input validation before processing, output validation before returning/persisting). Field types below use JSON Schema-ish shorthand.

### 7.1 `DocumentText` (output of Ingestion/OCR)

```json
{
  "document_id": "string (uuid)",
  "source_type": "born_digital | scanned_image",
  "pages": [
    {
      "page_number": "integer, 1-indexed",
      "width_px": "integer",
      "height_px": "integer",
      "tokens": [
        {
          "text": "string",
          "bbox": {"x0": "float", "y0": "float", "x1": "float", "y1": "float"},
          "confidence": "float 0-1, null if born-digital (treat null as 1.0 downstream but preserve null distinctly for reporting)",
          "line_id": "string"
        }
      ]
    }
  ],
  "degradation_metadata": {
    "applied": "boolean",
    "types": ["blur|jpeg_compression|resolution_reduction|rotation_skew|contrast_brightness|local_artifact|combined"],
    "params": "object, degradation-specific parameters, required when applied=true (benchmark generation only, §18.2)"
  }
}
```

### 7.2 Extraction Router input/output

**Input:** `DocumentText`, plus a `region_hint` per line/block (from a lightweight layout/template classifier — see `DECISION REQUIRED-1`).

**Output:** `RoutingDecision[]`:

```json
{
  "document_id": "string",
  "region_id": "string",
  "route": "deterministic | gliner | llm_narrative",
  "reason": "string, e.g. 'matched lab-table regex', 'low deterministic confidence, escalated', 'free-text narrative block'",
  "confidence": "float 0-1"
}
```

### 7.3 Deterministic Parser / GLiNER-BioMed I/O

**Input:** `DocumentText` region + `RoutingDecision`.
**Output:** `Claim[]` (§8) with `extraction_method: "deterministic"` or `"gliner"`, `extraction_confidence`.

### 7.4 LLM Narrative Extractor I/O

**Input:** the narrative text block (raw string), the corresponding `DocumentText` tokens/bboxes for that block (for span alignment post-hoc), and a fixed JSON output schema (below) passed to the LLM via schema-constrained decoding (MUST use structured-output enforcement — e.g. grammar-constrained decoding or a JSON-schema-enforcing library; free-form prompting with hopeful parsing is NOT acceptable, see §13.4).

**LLM extraction output schema (enforced, not just requested):**

```json
{
  "claims": [
    {
      "field_type": "string, one of the controlled vocabulary in §8.2",
      "value": "string",
      "evidence_text": "string, VERBATIM substring of the input block given to the LLM",
      "negated": "boolean",
      "temporal_status": "current | historical | discontinued | planned | unspecified"
    }
  ]
}
```

Post-processing MUST verify `evidence_text` is an exact substring of the input block (string containment check). If it is not, the claim MUST be discarded and logged as an `llm_span_hallucination` error (§20.3) — MUST NOT be silently accepted or fuzzy-matched into acceptance.

### 7.5 Terminology Normalizer I/O

**Input:** `Claim` (raw `value`, `field_type`).
**Output:** same `Claim`, enriched with:

```json
{
  "normalized_code": "string or null",
  "code_system": "LOINC | RxNorm | UCUM | null",
  "code_confidence": "float 0-1",
  "retrieval_candidates": [
    {"code": "string", "display": "string", "score": "float"}
  ]
}
```

If `code_confidence` < threshold (`DECISION REQUIRED-2`), `normalized_code` MUST be `null` and `code_system` MUST be `null`, while `retrieval_candidates` is still populated for audit purposes.

### 7.6 Verification Cascade I/O

**Input:** `Claim` + its linked `Evidence` (§9) + document-local context (surrounding text window, size `DECISION REQUIRED-3`, default recommendation: ±200 characters around the evidence span).

**Output:** `VerificationResult` (§10).

### 7.7 FHIR Generator I/O

**Input:** `Claim[] + VerificationResult[] + Evidence[]` for one document.
**Output:** FHIR R4B `Bundle` (JSON), schema-valid per the official FHIR R4B StructureDefinitions (validation MUST run against the HL7 FHIR R4B JSON schema or a validator library — not hand-checked).

### 7.8 Audit Report I/O

**Input:** same as FHIR Generator.
**Output:**

```json
{
  "document_id": "string",
  "generated_at": "ISO8601 timestamp",
  "fhir_bundle_id": "string",
  "claims": ["Claim, §8"],
  "evidence": ["Evidence, §9"],
  "verifications": ["VerificationResult, §10"],
  "summary": {
    "n_claims": "integer",
    "n_supported": "integer",
    "n_discrepancy": "integer",
    "n_insufficient_evidence": "integer",
    "n_escalated_to_llm": "integer"
  }
}
```

---

## 8. Claim Schema

```json
{
  "claim_id": "string (uuid), MUST be globally unique",
  "document_id": "string",
  "group_id": "string (uuid) or null — links atomic claims belonging to the same clinical entity instance (e.g. same medication order or lab test row, DEC-015)",
  "field_type": "string, controlled vocabulary — see §8.2",
  "value": "string, the extracted raw value as a string (numeric values stored as strings with a separate parsed numeric field, see §8.3)",
  "value_numeric": "float or null — parsed numeric value if field_type is numeric (lab.value, med.dose)",
  "unit_raw": "string or null",
  "extraction_method": "deterministic | gliner | llm_narrative",
  "extraction_confidence": "float 0-1",
  "evidence_id": "string, foreign key into Evidence store (§9)",
  "normalized_code": "string or null",
  "code_system": "LOINC | RxNorm | UCUM | null",
  "code_confidence": "float 0-1",
  "negated": "boolean, default false",
  "temporal_status": "current | historical | discontinued | planned | unspecified",
  "region_id": "string, links back to RoutingDecision.region_id"
}
```

### 8.2 `field_type` controlled vocabulary (MUST use exactly these values; extending this list requires a recorded decision)

`lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`, `lab.test_name`, `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`

*Canonical naming rule (DEC-017):* The canonical string is `lab.reference_range` (not `lab.ref_range`). Obsolete types (`lab.loinc_code`, `med.dose_unit`, `med.quantity`) are deprecated and excluded. Narrative extraction is strictly confined to these 10 fields (DEC-020).

### 8.3 Numeric and Qualitative Laboratory Claims (amended per PD-022)

Any claim with `field_type = "med.dose"` or any quantitative claim with `field_type = "lab.value"` MUST populate `value_numeric` after unit normalization is attempted (UCUM) if a valid numeric quantity can be parsed, and MUST preserve `unit_raw` even if normalization fails.

For qualitative laboratory findings with `field_type = "lab.value"` (e.g., "Negative", "Positive", "Reactive", "Non-Reactive", "Detected", "Not Detected", "Equivocal"), `value_numeric` MUST be explicitly `null`, and `unit_raw` / unit codes MUST be `null` unless independently applicable.

---

## 9. Evidence Schema

```json
{
  "evidence_id": "string (uuid)",
  "document_id": "string",
  "page_number": "integer",
  "text": "string, exact verbatim source text span",
  "char_start": "integer, offset within the page's concatenated OCR text",
  "char_end": "integer",
  "bbox": {"x0": "float", "y0": "float", "x1": "float", "y1": "float"},
  "ocr_confidence": "float 0-1 or null (null = born-digital, no OCR confidence applicable)"
}
```

Evidence objects MUST be deduplicated: if two claims cite the exact same `(document_id, char_start, char_end)`, they MUST reference the same `evidence_id`, not create duplicate evidence records.

---

## 10. Verification Result Schema

This is the core differentiator artifact of the project. Every field is required unless marked optional.

```json
{
  "verification_id": "string (uuid)",
  "claim_id": "string, foreign key",
  "label": "SUPPORTED | DISCREPANCY | INSUFFICIENT_EVIDENCE",
  "confidence": "float 0-1, CALIBRATED (see §17) — not a raw softmax score",
  "stage_reached": "A | B | C — which cascade stage produced the final label",
  "stage_a_label": "SUPPORTED | DISCREPANCY | INSUFFICIENT_EVIDENCE | null (null only if Stage A does not apply to this field_type)",
  "stage_b_label": "SUPPORTED | DISCREPANCY | INSUFFICIENT_EVIDENCE | null",
  "stage_b_raw_confidence": "float 0-1 or null, pre-calibration",
  "contradiction_type": "unit_mismatch | value_mismatch | dose_mismatch | negation_mismatch | temporal_mismatch | wrong_entity | code_mismatch | null (null when label != DISCREPANCY)",
  "normalized_code_agreement": "boolean or null (null when either claim or evidence lacks a resolvable code)",
  "rationale": "string or null — populated ONLY when stage_reached == 'C'",
  "evidence_id": "string, foreign key into Evidence (§9) — duplicated here for convenience, MUST match claim.evidence_id",
  "escalated": "boolean — true if Stage B triggered escalation to Stage C, regardless of whether label changed"
}
```

Constraints (MUST):

- `rationale` MUST be `null` when `stage_reached` is `A` or `B` (no LLM was invoked — do not backfill a rationale from a template).
- If `label == DISCREPANCY`, `contradiction_type` MUST NOT be `null`.
- `confidence` MUST come from the calibration module (§17), never a raw uncalibrated model score, at any `stage_reached` value where a learned model is involved (B or C).

---

## 11. Extraction Routing Rules

Routing is decided per document **region** (a region is a layout-detected block: a table, a line matching a medication-regimen pattern, or a narrative paragraph).

**MUST rules:**

1. A region MUST be routed to `deterministic` first if it matches one of the pre-defined structured patterns: lab-result table row (test name + value + unit + reference range + flag), or a medication-regimen line matching the regex family for `DRUG DOSE ROUTE FREQUENCY [DURATION]`.
2. If the deterministic parser's `extraction_confidence` for a region is below threshold (`DECISION REQUIRED-4`, recommended default 0.7), the region MUST be re-routed: first to `gliner` as a cross-check; if GLiNER also disagrees with the deterministic output or has confidence below the same threshold, escalate to `llm_narrative` with the region text and a note that this is a low-confidence structured region (not narrative) so the LLM extractor's prompt/schema can be field-type-aware rather than open-ended.
3. Any region not matching a structured pattern (free paragraph text — impressions, plans, narrative history) MUST be routed directly to `llm_narrative`.
4. A region MUST NOT be routed to more than one path and produce two competing, un-reconciled `Claim` sets — if both deterministic/GLiNER and an escalation LLM path produce claims for the *same* evidence span, the reconciliation rule is: deterministic claim wins if its confidence ≥ threshold; otherwise LLM claim wins; the losing claim MUST still be logged (not deleted) with a `superseded: true` flag for error analysis, but MUST NOT be passed to verification/FHIR generation.
5. Routing decisions and reasons MUST be logged per §7.2 for every region, including regions that produced zero claims (needed for extraction recall analysis, §21).

**MUST NOT rules:**

- The LLM narrative path MUST NOT be the default route for lab tables or medication-regimen lines when deterministic confidence is adequate — this would violate the "hybrid, not LLM-primary" architecture decision (§2.5, §13).

---

## 12. Verification Cascade Routing Rules

### 12.1 Field Verification Tiers (DEC-019)

- **Tier 1 (Learned Verification Cascade — Stages A, B, C):**
  Applies to fields with high semantic variability, numerical ranges, or critical clinical sensitivity:
  `lab.value`, `lab.flag`, `med.name`, `med.dose`, `med.route`, `med.frequency`.
  These 6 fields participate in learned verification (Stage B ModernBERT and Stage C LLM Judge upon escalation).
- **Tier 2 (Deterministic Verification — Stage A Only):**
  Applies to fields governed by standardized unit tables, exact string intervals, or closed duration tokens:
  `lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`.
  These 4 fields are resolved definitively at Stage A using normalized lexical and numeric interval rules, bypassing Stage B unless an explicit normalization conflict arises.

### 12.2 Cascade Stages

**Stage A (deterministic):**

- Applies to `field_type` in `{lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`, `med.dose`, `med.route`, `med.frequency`, `med.duration}` where both claim and evidence contain a comparable normalized value/unit/token.
- Does NOT apply (produces `stage_a_label: null`) to `field_type` in `{lab.test_name, med.name}` when the comparison is primarily lexical/semantic rather than numeric — these MUST proceed directly to Stage B.
- Output: `SUPPORTED` if normalized value/unit/token matches evidence exactly (after UCUM unit conversion where applicable); `DISCREPANCY` if a comparable value/unit/token exists but does not match; `INSUFFICIENT_EVIDENCE` if no comparable evidence span exists at all for that field on that claim.

**Stage B (ModernBERT/NLI):**

- Always runs for Tier 1 fields (every Tier 1 claim gets a Stage B prediction), for two reasons: (a) it's the primary signal for name-type fields where Stage A doesn't apply, (b) it's needed as a cross-check even where Stage A does apply.
- Escalate to Stage C (`escalated: true`) if ANY of:
  - Stage A produced a non-null label that disagrees with Stage B's label.
  - Stage B's calibrated confidence < threshold (`DECISION REQUIRED-5` / `DEC-005`, recommended starting point 0.6, to be tuned via the risk-coverage analysis in §21.5).
  - Stage B predicts `INSUFFICIENT_EVIDENCE` (this class is safety-critical and disagreement/uncertainty here is treated conservatively — always double-checked).
- If not escalated: `stage_reached = "B"`, final `label = stage_b_label`, `confidence` = Stage B's calibrated confidence, `rationale = null`.

**Stage C (LLM judge):**

- Receives: claim, evidence text, document-local context window (§7.6), Stage A label (if any), Stage B label + confidence.
- MUST return: final `label`, a `confidence` (calibrated per DEC-018: via token logprob temperature scaling when served with grammar constraints on vLLM, or via 1D isotonic regression if self-reported scalar float — do not treat raw LLM confidence as inherently calibrated), a `rationale` (free text, 1–3 sentences), and — if `label == DISCREPANCY` — a `contradiction_type` from the controlled vocabulary in §10.
- MUST NOT be given access to any resource other than the claim/evidence/context passed in (no web search, no external retrieval, no medical knowledge base) — enforced by using a tool-free LLM call.

**MUST NOT (global cascade rule):**

- The system MUST NOT invoke Stage C for every claim as a default configuration. A configuration flag MAY force always-Stage-C for experimental/ablation purposes (§22, experiment V9-adjacent ablation) but the **production/default configuration MUST use the escalation logic above**, and the escalation rate MUST be reported as a metric (§21.5).

---

## 13. LLM Responsibilities and Restrictions

### 13.1 Where the LLM IS used (MUST, exhaustive list — no other use is in scope)

1. Narrative-section claim extraction (§7.4), schema-constrained.
2. Verification Stage C judge + rationale generation (§12).
3. One standalone experimental baseline: "LLM performs extraction AND verification end-to-end with no cascade/hybrid components" (§22, experiment V9) — for comparison only, never the production path.

### 13.2 Model selection

`DECISION REQUIRED-6`: exact LLM model and serving method for narrative extraction / judge role. Candidates to evaluate empirically before committing (do not pick without a documented comparison in `docs/decisions/`): a 3B–8B instruction-tuned open model runnable in 4-bit quantization on the T4 16GB (e.g., a Qwen2.5-Instruct or Llama-3.1-8B-Instruct class model, or a Phi-3.5-mini class model) versus a hosted API model used only for the experimental baseline and small-scale judge calls where local serving is impractical. Record VRAM footprint, latency per call, and JSON-schema-conformance rate for each candidate as part of the decision record.

### 13.3 MUST restrictions

- The LLM MUST NOT be called with unconstrained free-form prompting and "best-effort" JSON parsing as the sole safeguard — output MUST go through schema-constrained decoding or a validating parser that rejects and retries/logs on failure (§7.4).
- The LLM MUST NOT receive, in the verification role, any information beyond the claim, its evidence, and local document context (§6, §12) — no external knowledge injection.
- The LLM's self-reported confidence MUST NOT be used directly as the system's reported confidence — it MUST pass through the calibration pipeline (§17) like any other model score.
- The LLM MUST NOT be the sole extractor for any `field_type` on structured document regions when deterministic/GLiNER confidence is adequate (§11).
- Every LLM extraction claim MUST have its `evidence_text` verified as a verbatim substring of the input (§7.4) before being accepted into the `Claim` store.

### 13.4 SHOULD

- Extraction and judge prompts SHOULD be version-controlled as separate files under `prompts/`, not embedded as inline strings in application code, so prompt changes are auditable and diffable independent of code changes.
- The judge prompt SHOULD explicitly instruct the model to output `INSUFFICIENT_EVIDENCE` when no relevant evidence is present, rather than guessing — this SHOULD be validated with adversarial test cases (§24).

---

## 14. Terminology Retrieval Responsibilities

### 14.1 Scope

Retrieval-based normalization for: `lab.test_name` → LOINC, `med.name` → RxNorm, `unit_raw` → UCUM.

### 14.2 Method (MUST)

- Build a local retrieval index over the relevant terminology's canonical display names/synonyms (LOINC full table, RxNorm concept table, UCUM unit list — all are freely obtainable reference data; licensing terms for LOINC/RxNorm redistribution MUST be checked and complied with before bundling any terminology data file into the repo).
- Retrieval MUST be **hybrid**: a lexical component (exact/fuzzy string match, e.g. BM25 or simple edit-distance shortlist) AND a semantic embedding component (`DECISION REQUIRED-7`: exact embedding model — candidates: SapBERT, BioLORD, or a general sentence-embedding model as a fallback baseline if a biomedical one underperforms in evaluation). The two signals MUST be combined into the single `code_confidence` score via a documented, fixed combination rule (e.g., weighted sum or a small trained reranker) — not an ad hoc if/else.
- UCUM normalization for units MAY use a simpler deterministic lookup table (units are a closed, small vocabulary) rather than full retrieval — deterministic lookup is preferred here per the "deterministic first" principle.

### 14.3 MUST NOT

- Terminology retrieval MUST NOT be used to alter or "correct" the claim's extracted value — it only assigns a code alongside the original value. If retrieval strongly suggests the extracted drug name is a typo of a known RxNorm concept, that discrepancy MAY be surfaced as a secondary QA signal but MUST NOT silently replace `value` in the `Claim` object (auditability requirement — the claim must always reflect what was actually extracted from the source).
- This retrieval MUST NOT be conflated with or reused as the verification-cascade's evidence retrieval — they are different retrieval systems over different corpora (terminology database vs. the document itself) and MUST be implemented as separate modules.

---

## 15. FHIR Mapping Requirements

### 15.1 Resources (MUST implement, R4B)

- `Patient`, `Organization`, `Practitioner` — populated from document header fields where present; synthetic/placeholder-safe when absent, but MUST NOT be fabricated with realistic-looking fake identifiers that could be mistaken for real PHI in a demo — use clearly synthetic placeholder values (e.g., `"SYNTHETIC-PATIENT-001"`) in all benchmark data.
- `DiagnosticReport` — one per lab panel, referencing constituent `Observation`s.
- `Observation` — one per `lab.value` claim (with `component` sub-structure for panels where appropriate), carrying the LOINC code, UCUM unit, reference range, and interpretation flag.
- Medication resources — `MedicationStatement` for narrative-derived "patient is taking X" claims (temporal_status = current/historical/discontinued), `MedicationRequest` for prescription-order-derived claims (temporal_status = planned or from an explicit order/prescription document). `DECISION REQUIRED-8`: exact rule for which resource type a given `med.*` claim maps to when `temporal_status` is `unspecified` — default recommendation is `MedicationStatement` with a documented assumption, but this MUST be explicitly decided and recorded, not left to whichever engineer implements the mapper that week.

### 15.2 Validity and Observation Value Mapping (amended per PD-022)

- Every generated `Bundle` MUST validate against the official FHIR R4B JSON schema / a FHIR validator (e.g., the HL7 validator or an equivalent library) as part of the test suite (§24) — "FHIR validity rate" is a reported metric (§21.4), meaning some invalid bundles may occur during development, but the CI test suite MUST fail if the rate is not 100% on the benchmark test set at each phase's acceptance gate.
- **Quantitative vs. Qualitative Observation Mapping (PD-022):** For quantitative `lab.value` claims (`value_numeric` populated), `Observation.valueQuantity` MUST carry the numeric value and UCUM unit. For qualitative `lab.value` claims (`value_numeric` is `null`), `Observation.valueString` MUST be used to carry the verbatim qualitative determination (e.g., `"Negative"`, `"Non-Reactive"`). Qualitative findings MUST NEVER default to or be coerced into `Observation.valueQuantity = 0.0`.

### 15.3 Coding

- `Observation.code`, `Observation.valueQuantity.unit` (UCUM), and medication `.code` fields MUST use the `normalized_code`/`code_system` from the `Claim` object when available (`code_confidence` above threshold), and MUST fall back to a `text`-only coding (FHIR's `CodeableConcept.text`) with no `.coding[]` entry when normalization failed — MUST NOT emit a fabricated or best-guess code.

---

## 16. Provenance Requirements

Every FHIR resource instance that originates from a `Claim` MUST carry a provenance extension. Per **DEC-016**, provenance uses a two-tier model to faithfully capture composite clinical resources containing multiple atomic claims with distinct verification results:

### 16.1 Resource-Level Provenance

The top-level resource (`Observation`, `MedicationStatement`, `MedicationRequest`, `DiagnosticReport`) carries a provenance extension linking to the anchor/primary entity claim (e.g. `lab.value` for `Observation`, `med.name` for `MedicationStatement`) and the summary verification verdict:

```json
{
  "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
  "extension": [
    {"url": "claimId", "valueString": "<claim_id>"},
    {"url": "verificationId", "valueString": "<verification_id>"},
    {"url": "verificationLabel", "valueCode": "SUPPORTED|DISCREPANCY|INSUFFICIENT_EVIDENCE"},
    {"url": "confidence", "valueDecimal": "<calibrated confidence>"},
    {"url": "stageReached", "valueCode": "A|B|C"},
    {"url": "evidenceText", "valueString": "<verbatim evidence text>"},
    {"url": "sourcePage", "valueInteger": "<page_number>"},
    {"url": "sourceBoundingBox", "valueString": "<'x0,y0,x1,y1'>"},
    {"url": "ocrConfidence", "valueDecimal": "<evidence.ocr_confidence, omit if null>"}
  ]
}
```

### 16.2 Element-Level Provenance Sub-Extensions

Constituent elements within composite resources (`Observation.valueQuantity`, `Observation.interpretation`, `Observation.referenceRange`, `Medication*.dosage[0].doseQuantity`, `Medication*.dosage[0].timing`) MUST carry element-level provenance extensions linking to their specific atomic `claim_id` and `verification_id`:

```json
{
  "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
  "extension": [
    {"url": "claimId", "valueString": "<constituent_claim_id>"},
    {"url": "verificationId", "valueString": "<constituent_verification_id>"},
    {"url": "verificationLabel", "valueCode": "SUPPORTED|DISCREPANCY|INSUFFICIENT_EVIDENCE"},
    {"url": "confidence", "valueDecimal": "<calibrated confidence>"},
    {"url": "evidenceText", "valueString": "<constituent verbatim evidence text>"}
  ]
}
```

**MUST:**

- Every `Observation`, medication resource, and `DiagnosticReport` MUST carry this extension (or, for `DiagnosticReport` which aggregates multiple claims, a list-valued equivalent referencing each constituent claim).
- The audit report (§7.8) MUST be derivable purely from provenance extensions plus the claim/evidence/verification stores — i.e., provenance in FHIR and the audit JSON MUST never diverge; they are two views of the same underlying data, generated from the same source objects in the same pipeline run.

---

## 17. Confidence/Calibration Requirements

### 17.1 What must be calibrated (MUST)

- Stage B (ModernBERT/NLI) confidence.
- Stage C (LLM judge) confidence.
- Terminology `code_confidence` SHOULD also be calibrated if used for any threshold-based decision (§14.2), though this is lower priority than verification calibration.

### 17.2 Method

`DECISION REQUIRED-9`: exact calibration method. Recommended starting point: **temperature scaling** fit on a held-out calibration split (carved out of the CV/training documents, NEVER from the OOD template holdout — calibration MUST be validated for whether it transfers to OOD, and this transfer/failure MUST itself be reported as a result, not assumed away). Isotonic regression MAY be used as an alternative/comparison if temperature scaling underperforms (report both in ablations if so).

### 17.3 MUST

- Reliability diagrams and ECE/Brier score (§21) MUST be computed and reported separately for (a) the CV/validation split and (b) the OOD template-holdout split — calibration quality on OOD is a first-class reported result, not an afterthought.
- Calibration MUST be re-fit whenever the underlying Stage B model is retrained (i.e., calibration parameters are versioned alongside model checkpoints, not treated as a one-time global constant).

---

## 18. Dataset Construction Rules

### 18.1 Synthetic core benchmark (MUST, builds on existing M0 work)

- Source of ground truth: Synthea-generated (or equivalent structured synthetic patient records) → rendered into document templates (lab report templates, prescription templates, discharge-summary-style narrative templates) with associated FHIR-valid structured ground truth.
- Ground truth per document MUST include: all claims with their true values, true evidence text spans, true bounding boxes, true LOINC/RxNorm/UCUM codes, and — critically — a machine-generated **true verification label** for each (claim, evidence) pair used in verification training/eval (this is the M2-style dataset).
- Document templates (DEC-014): MUST span 50 lab reports, 50 medication forms, and 20 narrative discharge notes (120 documents total across both CV and OOD splits), sufficient to support template-level OOD holdout (§19).

### 18.2 Robustness/degradation benchmark (MUST, builds on existing M1 work)

- Two tracks, both required: **(A)** visual degradation applied to rendered document images prior to OCR (blur, JPEG compression, resolution reduction, rotation/skew, contrast/brightness change, local artifacts, and combined degradation — each as a separate, labeled condition, not only a single "noisy" bucket); **(B)** direct OCR/text-level corruption (character corruption, decimal corruption, increasing noise levels) applied post-OCR, bypassing the image pipeline, to isolate text-level robustness from image-level robustness.
- Each degradation condition MUST be parameterized with at least 3 severity levels (e.g., mild/moderate/severe) so degradation curves (not single-point comparisons) can be plotted.

### 18.3 Real-data validation slice (SHOULD, not MUST — feasibility-gated)

- Target: n2c2 (2018 medication/ADE track, or 2014 de-identification/heart-disease track) as the primary candidate given faster access than MIMIC-IV/MIMIC-IV-Note (which require PhysioNet credentialing + CITI training + a data use agreement, with unpredictable approval timelines).
- `DECISION REQUIRED-10`: exact dataset(s) and access approval status — the team MUST start the access request process in the first implementation week (§25, Phase 0) regardless of when this slice will actually be used, because approval timelines are the single biggest scheduling risk in the project (see also the risk noted in the architecture review this spec formalizes).
- Because these real datasets do NOT have the project's own structured ground truth (LOINC/RxNorm codes, verification labels) built in, this slice MUST be treated as a **qualitative evaluation only** (error analysis, failure case cataloguing per §23) unless the team additionally hand-annotates a subset — hand-annotation, if attempted, MUST use a documented annotation guideline and MUST report inter-annotator agreement if more than one annotator is used.
- If access is not granted in time, this slice MAY be dropped, but its absence MUST be stated explicitly as a limitation in the final report — MUST NOT be silently omitted.

### 18.4 Class balance (MUST — this corrects the previously discovered flaw)

- No `field_type` may be trained/evaluated with only one verification class represented. Every `field_type` in the controlled vocabulary (§8.2) that participates in verification MUST have non-trivial representation (a floor, e.g. minimum 5% of that field type's examples, `DECISION REQUIRED-11` for the exact floor) of at least `SUPPORTED` and `DISCREPANCY`; `INSUFFICIENT_EVIDENCE` MUST be represented across a majority of field types even if at lower absolute counts, since it is rarer by construction.
- Dataset generation code MUST include an automated check (run in CI, §24) that asserts this balance property on every regenerated dataset version — this is a direct, automated safeguard against repeating the v1 flaw, not just a one-time manual fix.

### 18.5 Hard negatives (MUST — see full taxonomy in §20)

- The dataset generator MUST deliberately construct near-miss discrepancies (single-digit dose changes, decimal shifts e.g. 5mg vs 50mg, unit swaps e.g. mg vs g, off-by-one frequency errors e.g. BID vs TID), not rely solely on random field corruption for the `DISCREPANCY` class, because random corruption is measurably easier to detect and would inflate reported metrics relative to real-world difficulty.

---

## 19. Train/Validation/OOD Splitting Rules

**MUST:**

1. Splitting MUST be **document-level** at minimum: no two examples derived from the same source document may appear in different splits.
2. Splitting MUST additionally be **template-level** for the OOD split: a fixed set of entire document templates MUST be held out entirely from training and standard validation, used ONLY as the OOD test set. (Existing project convention: 29 OOD documents / 71 CV documents — MUST be preserved as the template assignment unless a documented reason changes it; if the document counts/templates change due to dataset regeneration under §18.4's balance fix, the OOD/CV template assignment MUST still be fixed and recorded, not re-randomized per experiment run.)
3. Cross-validation on the non-OOD (CV) documents MUST use document-level GroupKFold (existing convention) — no claim-level shuffling that could leak same-document claims across folds.
4. If/when real-data (§18.3) is incorporated, splitting MUST additionally be **patient-level** (no same patient's notes across splits) in addition to document-level.
5. The calibration-fitting split (§17.2) MUST be carved out of CV documents only, and MUST be disjoint from whichever CV fold(s) are used for final Stage B model selection — i.e., a three-way split within the non-OOD pool (train / calibration / validation-for-model-selection) is required, not just train/val.

**MUST NOT:**

- No experiment may report an OOD metric computed on any document that was used, directly or indirectly (e.g., via retrieval-index construction, via few-shot prompt examples for the LLM), in training or calibration of any component being evaluated.

---

## 20. Hard-Negative / Error Taxonomy

### 20.1 Verification `contradiction_type` taxonomy (MUST, matches §10 schema)

`unit_mismatch`, `value_mismatch`, `dose_mismatch`, `negation_mismatch`, `temporal_mismatch`, `wrong_entity`, `code_mismatch` — every `DISCREPANCY`-labeled example in the dataset MUST be tagged with one of these at generation time (ground truth), enabling per-type recall/precision reporting (§21).

### 20.2 Visual/OCR degradation taxonomy (MUST, matches §18.2)

`blur`, `jpeg_compression`, `resolution_reduction`, `rotation_skew`, `contrast_brightness`, `local_artifact`, `combined`; text-level: `character_corruption`, `decimal_corruption`, `noise_level_{1,2,3}`.

### 20.3 Extraction/pipeline error taxonomy (MUST — for error logging, not just verification)

`llm_span_hallucination` (§7.4), `routing_disagreement` (§11), `extraction_miss` (claim present in ground truth, absent from output), `extraction_spurious` (claim in output, absent from ground truth), `normalization_failure` (code_confidence below threshold), `fhir_validation_failure`.

All error types MUST be logged with enough context (document_id, claim_id where applicable, stage) to support the per-category error analysis required in §23.

---

## 21. Evaluation Metrics

### 21.1 Extraction metrics (MUST)

- Per-`field_type` precision/recall/F1, computed separately for {deterministic-only, GLiNER, LLM-narrative, hybrid-combined} extraction paths.
- Exact match rate (normalized string/value match against ground truth).
- Extraction performance broken down by degradation condition and severity (§18.2/20.2), i.e. a degradation curve, not a single aggregate number.

### 21.2 Evidence-linking metrics (MUST)

- Evidence span precision/recall (character-offset overlap or IoU against ground-truth bounding box, `DECISION REQUIRED-12` for exact overlap threshold, recommended default IoU ≥ 0.5 or character-offset exact-match — pick one and record it).

### 21.3 Verification metrics (MUST, existing + additions)

- Macro-F1, per-class (SUPPORTED/DISCREPANCY/INSUFFICIENT_EVIDENCE) precision/recall/F1.
- Discrepancy recall (safety-critical metric, reported with extra prominence).
- AUROC (one-vs-rest per class, plus macro-averaged).
- Brier score, ECE — reported separately for CV/validation and OOD (§17.3).
- Confusion matrix — CV and OOD separately.
- Per-`field_type` breakdown of all of the above.
- Per-`contradiction_type` breakdown (§20.1) of discrepancy recall specifically.
- Reliability diagrams (calibration curves) — CV and OOD separately.

### 21.4 FHIR-level metrics (MUST)

- FHIR schema validity rate (must-pass gate, §15.2, target 100% on benchmark test set).
- FHIR semantic correctness rate (right resource type used per §15.1 rules; right coding system attached when `code_confidence` was above threshold) — checked via automated rule-based assertions, not manual review, so this is repeatable in CI.

### 21.5 Selective prediction / cascade metrics (MUST — this is a headline contribution, not optional)

- Escalation rate (fraction of claims reaching Stage C).
- Risk-coverage curve and Area Under the Risk-Coverage Curve (AURC) for the full cascade compared against Stage B alone (single-model, no escalation) — this directly demonstrates whether the cascade design is earning its complexity.
- Accuracy conditional on stage reached (accuracy of claims resolved at Stage A only, Stage B only, escalated-to-C) — this shows whether escalation is actually being used on genuinely harder cases.

### 21.6 Terminology normalization metrics (SHOULD)

- Top-1 and top-5 retrieval accuracy against ground-truth LOINC/RxNorm codes; calibration of `code_confidence` against actual top-1 correctness.

### 21.7 End-to-end metrics (MUST)

- Document → final verified FHIR bundle accuracy: fraction of ground-truth claims that appear in the final bundle with the correct value, correct code (when expected), and correct verification label, end-to-end through the whole pipeline (this is distinct from — and MUST be reported separately from — per-stage metrics, since per-stage numbers can look good while end-to-end accuracy is poor due to compounding errors).

---

## 22. Required Experiments and Baselines

Each experiment MUST have its own directory under `experiments/<ID>/` with a fixed config and a `REPORT.md` summarizing results against §21 metrics.

### 22.1 Extraction experiments (MUST unless marked optional)

- **E1** — Deterministic-only extraction, per-field F1.
- **E2** — GLiNER-BioMed-only extraction, per-field F1.
- **E3** — LLM-only extraction (schema-constrained), per-field F1.
- **E4** — Hybrid extraction router (production architecture, §11), per-field F1.
- **E5** — E1–E4 re-evaluated across every degradation condition/severity in §18.2 (degradation curves). *(SHOULD extend to all four; MUST at minimum cover E4.)*

### 22.2 Verification experiments (MUST unless marked optional)

- **V1** — Deterministic rule-based verifier only (Stage A alone, on fields where it applies; `INSUFFICIENT_EVIDENCE`/name-fields default to a trivial baseline for comparison).
- **V2** — Levenshtein-distance baseline.
- **V3** — TF-IDF + Logistic Regression baseline.
- **V4** — Embedding-similarity baseline (SapBERT/BioLORD cosine, zero training).
- **V5** — Off-the-shelf NLI model, zero/few-shot (no fine-tuning).
- **V6** — ModernBERT fine-tuned, text-only, retrained on the corrected v2-balanced dataset (§18.4) — supersedes the discarded v1 numbers.
- **V7** — ModernBERT fine-tuned, OCR-aware (auxiliary OCR-confidence/field-type/template features).
- **V8** — Full production cascade (A → B → C with escalation logic, §12).
- **V9** — LLM-only end-to-end verifier (prompted, no fine-tuning, no cascade) — comparison baseline per §13.1(3).
- **V10** — OOD evaluation (template holdout) for every one of V1–V9.
- **V11** — Ablation: remove auxiliary features from V7 one at a time. *(SHOULD, optional if time-constrained.)*
- **V12** — Selective prediction / risk-coverage analysis for V8 vs. V6 alone (§21.5).
- **V13 (OPTIONAL)** — LoRA-fine-tuned small LLM as verifier, compared to V6/V8/V9.

### 22.3 Essential vs. optional summary

- **Essential (MUST complete):** E1–E4, V1–V8, V10, V12, plus §22.4 dataset-integrity check.
- **Should-complete if time allows:** E5 (full matrix across all four extraction paths), V9, V11.
- **Optional:** V13, real-data qualitative slice experiments beyond a basic run-through.

### 22.4 Dataset-integrity check (MUST, meta-experiment)

- Before any verification experiment is trusted, the automated class-balance check (§18.4) MUST be run and MUST pass on the exact dataset version used for that experiment, and this MUST be recorded in the experiment's `REPORT.md` (dataset version hash + balance-check pass/fail).

---

## 23. Failure Cases

### 23.1 MUST implement / handle correctly (in-scope, tested per §24)

- Negation ("no evidence of," "discontinued," "denies").
- Unit/dose expression variants (e.g., `mg` vs `g`, `BID` vs `twice daily`) — MUST be normalized before Stage A comparison.
- Common clinical abbreviations relevant to the document types in §2.1.
- Multiple medications listed in a single sentence/line.
- OCR-induced corruption (covered by the robustness benchmark, §18.2).
- Missing evidence → correctly yielding `INSUFFICIENT_EVIDENCE` rather than a forced guess.
- Hard-negative near-miss discrepancies (§18.5, §20.1).

### 23.2 Explicitly acknowledged as OUT OF SCOPE (MUST NOT attempt a partial/fragile implementation — better absent than half-working)

- Copied-forward notes across multiple visits/encounters (requires longitudinal modeling not built here).
- Patient-history vs. family-history disambiguation.
- Physician plan vs. actually-administered medication disambiguation.
- Full temporal-change tracking across multiple dates within or across documents.

These MUST be listed in the final report as acknowledged limitations, not silently absent.

---

## 24. Testing Requirements

**MUST:**

- Unit tests for every component in §5 against its I/O contract (§7) using schema validation — invalid-input and invalid-output cases MUST be tested (does the component correctly raise/reject, not just handle valid cases).
- The `llm_span_hallucination` check (§7.4) MUST have dedicated adversarial test cases (LLM outputs an evidence_text that is NOT a substring — verify rejection).
- The dataset class-balance check (§18.4) MUST run in CI on every dataset regeneration.
- FHIR schema validation (§15.2) MUST run in CI against every document in the benchmark test set at each phase gate (§26).
- The verification cascade escalation logic (§12) MUST have unit tests covering every escalation trigger condition independently (Stage A/B disagreement; Stage B low confidence; Stage B predicts INSUFFICIENT_EVIDENCE) plus a test confirming Stage C is NOT invoked when none of these trigger.
- Split-integrity tests (§19): automated check that no document_id (and, once real data is added, no patient_id) appears in more than one of {train, calibration, CV-validation, OOD}.
- Provenance completeness test (§16): every FHIR resource in a generated bundle has the required provenance extension fields populated and internally consistent with the audit report for the same document.

**SHOULD:**

- Golden-file regression tests for FHIR output on a small fixed set of benchmark documents, to catch unintended mapping-rule drift.

---

## 25. Implementation Phases

### Phase 0 — Setup & Decisions (MUST complete first)

- Resolve all `DECISION REQUIRED` items that block Phase 1 (`DECISION REQUIRED-1` layout/region classifier approach, `DECISION REQUIRED-4` deterministic-confidence threshold at minimum).
- Start real-data access request(s) per §18.3 regardless of later prioritization.
- Set up `schemas/`, `docs/decisions/`, `experiments/` scaffolding and CI skeleton.

### Phase 1 — Corrected Dataset & Ingestion

- Fix and regenerate the M2 dataset per §18.4 (class balance) and §18.5 (hard negatives); implement and pass the automated balance check.
- Implement/confirm OCR ingestion producing valid `DocumentText` (§7.1) for both born-digital and scanned paths.
- Implement the M1-style robustness/degradation generation (§18.2) if not already fully reusable from prior work.

### Phase 2 — Extraction Router + Hybrid Extraction

- Implement routing rules (§11).
- Implement/confirm deterministic + GLiNER paths (extending prior work).
- Implement schema-constrained LLM narrative extractor (§7.4, §13), including the evidence-substring verification check.
- Run E1–E4 (§22.1).

### Phase 3 — Terminology Normalization

- Implement hybrid retrieval normalizer (§14) for LOINC/RxNorm; deterministic UCUM lookup.
- Run §21.6 metrics.

### Phase 4 — Verification Cascade

- Implement Stage A, retrain Stage B (ModernBERT) on the corrected dataset, implement Stage C (LLM judge) with escalation logic (§12).
- Implement calibration module (§17).
- Run V1–V8, V10, V12 (§22.2).

### Phase 5 — FHIR Generation + Provenance + Audit Report

- Implement FHIR mapping (§15), provenance extensions (§16), audit report generator (§7.8).
- Run FHIR validity/semantic-correctness checks (§21.4).

### Phase 6 — End-to-End Evaluation & Real-Data Slice

- Run end-to-end metrics (§21.7).
- Run real-data qualitative validation slice if access was granted (§18.3), with failure-case cataloguing (§23).
- Complete remaining should/optional experiments as time allows (E5, V9, V11, V13).

### Phase 7 — Reporting

- Compile final report: architecture, all experiment results, acknowledged limitations (§23.2), risk-coverage/selective-prediction analysis as headline result.

---

## 26. Acceptance Criteria per Phase

- **Phase 0:** All Phase-1-blocking `DECISION REQUIRED` items have a corresponding file in `docs/decisions/`. CI skeleton runs (even if trivially) on an empty/placeholder test.
- **Phase 1:** Regenerated dataset passes the automated class-balance check (§18.4) with zero single-class fields. `DocumentText` schema validation passes on 100% of a sample ingestion run across both source types. Degradation benchmark produces documents at all required severity levels for all conditions in §18.2.
- **Phase 2:** E1–E4 completed and reported; hybrid (E4) extraction F1 MUST be reported per-field, per-document-type, and MUST NOT silently omit any field_type. `llm_span_hallucination` rejection test passes. Routing decisions logged for 100% of regions in the benchmark test set.
- **Phase 3:** Retrieval top-1 accuracy reported; `normalized_code: null` correctly enforced below the code_confidence threshold in 100% of tested below-threshold cases.
- **Phase 4:** V1–V8, V10, V12 completed. V6 numbers MUST differ from and explicitly supersede the discarded v1 numbers in all reporting (v1 numbers may appear only in a clearly labeled "superseded / dataset flaw" appendix, never in the headline results). Escalation-trigger unit tests all pass.
- **Phase 5:** 100% FHIR schema validity on the benchmark test set (hard gate — Phase 5 is not accepted below 100% here, since this is a deterministic mapping check, not a statistical metric). Provenance completeness test passes on 100% of generated bundles.
- **Phase 6:** End-to-end accuracy reported; OOD results reported for every metric family in §21 that has an OOD variant; real-data slice either completed with a qualitative report or its absence explicitly documented as a limitation.
- **Phase 7:** Final report contains, at minimum, every metric family in §21, the acknowledged-limitations list from §23.2, and the selective-prediction/risk-coverage result as a distinguished headline finding.

---

## 27. Resource/Compute Constraints

- Local hardware: RTX 3050 Laptop GPU, 6 GB VRAM — assume this is available for lightweight inference/dev iteration only (deterministic components, GLiNER inference, small-batch ModernBERT inference/dev debugging).
- Colab T4, 16 GB VRAM — assume this is the environment for: ModernBERT fine-tuning runs, LLM inference in 4-bit quantization for both the narrative extractor and judge roles, and any batch evaluation runs across the full benchmark.
- The system MUST be designed to run entirely offline/batch — no requirement or design assumption of persistent GPU availability or low-latency serving.
- LLM components (§13) MUST be run in quantized form (4-bit recommended default, `DECISION REQUIRED-6` covers exact model/quantization choice) to fit T4 16GB budget alongside ModernBERT and other components potentially loaded concurrently during evaluation runs — if concurrent VRAM budget is a problem, pipeline stages MAY be run sequentially with model unloading between stages rather than requiring simultaneous residency of all models.
- No requirement, anywhere in this project, for multi-GPU training or distributed inference.

---

## 28. What Must NOT Be Implemented

Restating and consolidating from §3 and throughout, for a single unambiguous checklist:

- Clinical decision support or treatment recommendations of any kind.
- Verification of clinical correctness/appropriateness (as opposed to document-fidelity) — §6, §14.3.
- SNOMED CT integration in the critical path.
- Longitudinal/cross-visit reasoning; copied-forward-note detection.
- Patient-history vs. family-history disambiguation.
- Physician-plan vs. actual-administration disambiguation.
- A custom-trained (from scratch or fully fine-tuned) layout/vision-language model (LayoutLMv3-style) — off-the-shelf pretrained tools MAY be used, but this is not a training deliverable of this project.
- Real-time/low-latency serving infrastructure.
- Multi-patient record linkage or a longitudinal patient database.
- Production authentication/deployment/HIPAA-hosting infrastructure, or any handling of live/production PHI.
- General external medical-knowledge RAG wired into the verification cascade (§6.4, §12) — this would break the document-fidelity vs. clinical-correctness boundary that is central to the system's auditability claim.
- LLM as the default/primary extractor or default/primary verifier in the production configuration (§2.5, §11, §12) — permitted only as the narrow, explicitly-scoped roles in §13.1, plus the one standalone comparison baseline (V9).
- Silent invention of any `DECISION REQUIRED` item's value by an implementing agent without recording the decision in `docs/decisions/`.

---

## Appendix A — Formal Decision Catalog (DEC-001 through DEC-019)

All decisions resolved or scoped during Phase 0 per `PHASE0_DECISION_RESOLUTION_PACKAGE.md` and `PHASE0_ARCHITECTURE_RESOLUTION.md`.

| ID | Decision Topic | Status | Category | Resolution / Policy Summary | Link to Decision Record |
|---|---|---|---|---|---|
| **DEC-001** | Region/Layout Classification Method (§11) | **LOCKED** | Architectural Policy | Rule-based heuristics (regex for regimens + column alignment for tables); optional off-the-shelf PP-Structure table supplement. | [`DEC-001-region-classification.md`](file:///d:/daa/docs/decisions/DEC-001-region-classification.md) |
| **DEC-002** | Terminology `code_confidence` Threshold (§7.5) | **EMPIRICALLY OPEN** | Statistical Threshold | Swept independently per code-system to target ≥95% precision on accepted codes in Phase 3. Provisional dev default: 0.75 for LOINC/RxNorm; UCUM exact-match lookup. | [`DEC-002-code-confidence-threshold.md`](file:///d:/daa/docs/decisions/DEC-002-code-confidence-threshold.md) |
| **DEC-003** | Verification Context Window Size (§7.6) | **EMPIRICALLY OPEN** | Experimental Parameter | Paragraph-bounded window max 500 chars; fallback to ±250 chars fixed window. Provisional dev default locked pending Phase 1 negation/temporal ablation check. | [`DEC-003-verification-context-window.md`](file:///d:/daa/docs/decisions/DEC-003-verification-context-window.md) |
| **DEC-004** | Deterministic Extraction Confidence Re-routing Threshold (§11) | **EMPIRICALLY OPEN** | Statistical Threshold | Swept {0.5–0.9} in Phase 2, biased toward recall floor, bounded by ≤30% escalation rate. Provisional dev default: 0.75. | [`DEC-004-deterministic-extraction-threshold.md`](file:///d:/daa/docs/decisions/DEC-004-deterministic-extraction-threshold.md) |
| **DEC-005** | Stage B Escalation Threshold (§12) | **EMPIRICALLY OPEN** | Statistical Threshold | Evaluated on calibrated confidence; final value chosen at risk-coverage curve knee in Phase 4 (V12). Provisional dev default: 0.60. | [`DEC-005-stage-b-escalation-threshold.md`](file:///d:/daa/docs/decisions/DEC-005-stage-b-escalation-threshold.md) |
| **DEC-006** | LLM Model & Serving Stack (§13.2, §27) | **EMPIRICALLY OPEN** | Engineering Benchmark | 7–8B instruction-tuned model (Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct), 4-bit, vLLM on Colab T4. Benchmarked in Phase 0/1 for JSON-schema conformance & substring validity. | [`DEC-006-llm-model-and-serving.md`](file:///d:/daa/docs/decisions/DEC-006-llm-model-and-serving.md) |
| **DEC-007** | Terminology Embedding Model & Scoring Combination (§14.2) | **EMPIRICALLY OPEN** | Empirical Model Selection | SapBERT primary (BioLORD fallback) + fixed weighted sum with BM25 lexical score, weight grid-searched in Phase 3. UCUM deterministic lookup. | [`DEC-007-terminology-retrieval-model.md`](file:///d:/daa/docs/decisions/DEC-007-terminology-retrieval-model.md) |
| **DEC-008** | Medication Resource Mapping Rule (§15.1) | **LOCKED** | Mapping Policy | Hybrid rule: source document type is primary (`MedicationRequest` for prescription/order; `MedicationStatement` for note/summary); `temporal_status: planned` overrides to `MedicationRequest`. | [`DEC-008-medication-resource-mapping-rule.md`](file:///d:/daa/docs/decisions/DEC-008-medication-resource-mapping-rule.md) |
| **DEC-009** | Verification Confidence Calibration Method (§17.2) | **EMPIRICALLY OPEN** | Empirical Model Selection | Multi-class temperature scaling by default (single scalar fit on CV calibration split). Isotonic regression evaluated as comparison fallback if OOD ECE degrades in Phase 4. | [`DEC-009-confidence-calibration-method.md`](file:///d:/daa/docs/decisions/DEC-009-confidence-calibration-method.md) |
| **DEC-010** | Real-Data Dataset Target & Strategy (§18.3) | **LOCKED** | Dataset Policy | Primary target: n2c2 2018 Track 2 (medication/ADE); MIMIC-IV deferred to avoid parallel credentialing risk. Qualitative evaluation only. DUA initiated in Phase 0. | [`DEC-010-real-data-dataset-selection.md`](file:///d:/daa/docs/decisions/DEC-010-real-data-dataset-selection.md) |
| **DEC-011** | Dataset Class-Balance Floor (§18.4) | **LOCKED** | Data Quality Policy | Combined rule: $\max(0.10 \times N_{\text{field}}, 30)$ for `SUPPORTED` and `DISCREPANCY`; minimum 15 for `INSUFFICIENT_EVIDENCE` where applicable. Automated CI check enforces. | [`DEC-011-class-balance-floor.md`](file:///d:/daa/docs/decisions/DEC-011-class-balance-floor.md) |
| **DEC-012** | Evidence-Linking Evaluation Metric (§21.2) | **LOCKED** | Metric Definition | Character-offset overlap F1 (extractive-QA style) is the primary headline metric; bounding-box IoU ≥ 0.5 reported as secondary diagnostic. | [`DEC-012-evidence-linking-metric.md`](file:///d:/daa/docs/decisions/DEC-012-evidence-linking-metric.md) |
| **DEC-013** | LLM Structured-Output Enforcement Mechanism (§7.4, §13.3) | **EMPIRICALLY OPEN** | Engineering Benchmark | Grammar/JSON-schema-constrained decoding via serving stack native support (vLLM); prompt+parse+bounded-retry (max 2) fallback. Validated in Phase 0/1. | [`DEC-013-structured-output-mechanism.md`](file:///d:/daa/docs/decisions/DEC-013-structured-output-mechanism.md) |
| **DEC-014** | Narrative Benchmark Synthesis Scope (§18.1) | **LOCKED** | Dataset Policy | Add targeted 20-document synthetic narrative discharge summary benchmark (15 CV, 5 OOD) containing HPI/Plan prose with embedded lab and medication mentions. | [`DEC-014-narrative-benchmark-synthesis.md`](file:///d:/daa/docs/decisions/DEC-014-narrative-benchmark-synthesis.md) |
| **DEC-015** | Claim Entity Grouping Key (`group_id`) (§8.1) | **LOCKED** | Data Model Policy | Add `group_id: str \| null` to `Claim` schema to link atomic field claims originating from the same clinical entity instance (e.g. multi-order medication lines). | [`DEC-015-claim-entity-grouping.md`](file:///d:/daa/docs/decisions/DEC-015-claim-entity-grouping.md) |
| **DEC-016** | FHIR Provenance Extension Hierarchy (§16) | **LOCKED** | Data Model Policy | Two-tier architecture: top-level resource provenance links to primary entity claim; element-level sub-extensions link constituent atomic claims (`valueQuantity`, `dosage`). | [`DEC-016-fhir-provenance-hierarchy.md`](file:///d:/daa/docs/decisions/DEC-016-fhir-provenance-hierarchy.md) |
| **DEC-017** | Canonical Field Vocabulary & Feature Dimension (§8.2) | **LOCKED** | Schema & Model Policy | Enforce exact 10 canonical field types; rename `lab.ref_range` to `lab.reference_range`; deprecate obsolete types; resize ModernBERT auxiliary feature dimension to $\text{AUX\_DIM} = 18$. | [`DEC-017-canonical-field-vocabulary.md`](file:///d:/daa/docs/decisions/DEC-017-canonical-field-vocabulary.md) |
| **DEC-018** | Stage C Confidence Calibration Mechanism (§12, §17.1) | **LOCKED** | Model Calibration Policy | Dual-mode calibration: unnormalized token logprob temperature scaling for local constrained vLLM serving; 1D isotonic regression for self-reported scalar confidence. | [`DEC-018-stage-c-confidence-calibration.md`](file:///d:/daa/docs/decisions/DEC-018-stage-c-confidence-calibration.md) |
| **DEC-019** | Verification Field Tiering (§12.1) | **LOCKED** | Verification Architecture | Tier 1 (Learned Cascade: `lab.value`, `lab.flag`, `med.*`) runs Stages A/B/C; Tier 2 (Deterministic: `lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`) resolved at Stage A. | [`DEC-019-verification-field-tiering.md`](file:///d:/daa/docs/decisions/DEC-019-verification-field-tiering.md) |

## Appendix B — Glossary

- **Claim** — a single extracted structured fact from a document (§8).
- **Evidence** — the verbatim source text span (+ location) a claim is grounded in (§9).
- **Cascade** — the three-stage verification pipeline, Stage A (deterministic) → B (ModernBERT/NLI) → C (LLM judge) (§12).
- **Escalation** — the act of passing a claim from Stage B to Stage C due to disagreement or low confidence.
- **OOD (template holdout)** — evaluation on document templates entirely unseen during training/validation (§19).
- **Document-fidelity verification** — verifying that a structured claim matches its source document text; explicitly distinct from clinical-correctness verification, which this project does not perform (§3, §6).
