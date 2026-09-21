AI-Based Multimodal Clinical Document-to-EHR Conversion and Verification

Project specification — authoritative source for the coding agent
Status: Active
Purpose: 2–3 month AI/ML healthcare academic/industry project
Primary implementation environment: Windows + Antigravity, Python 3.11 virtual environment
Hardware: NVIDIA RTX 3050 6 GB locally; Google Colab GPUs available for training/fine-tuning

1. Project Overview

We are building an AI-assisted system that converts heterogeneous clinical documents into structured healthcare data and verifies whether the extracted information is supported by the source document.

The system is NOT intended to replace clinicians, make diagnoses, provide treatment decisions, or write to a real hospital EHR.

The central research contribution is automated verification of extracted clinical information, rather than merely extracting text from PDFs.

Core pipeline

Clinical PDF / Image
        ↓
Document ingestion
        ↓
Text extraction / OCR + layout information
        ↓
Clinical information extraction
        ↓
Normalization
        ↓
FHIR R4 mapping
        ↓
Verification
        ↓
Per-field confidence / risk + explanations
        ↓
Human review UI

Example:

Source:
Amoxicillin 500 mg three times daily for 5 days

Extracted:
Amoxicillin | 500 mg | twice daily | 5 days

Verifier:
⚠ Potential extraction discrepancy

Source evidence: "three times daily"
Canonical source frequency: 3/day
Extracted frequency: 2/day
Reason: FREQ_MISMATCH

The system should always be able to explain why a field was flagged.

2. Project Goals

Primary goals

Extract useful clinical information from documents.

Preserve source evidence for every extracted field.

Normalize clinical concepts into standard terminology.

Convert extracted information into FHIR resources.

Detect extraction/normalization inconsistencies automatically.

Present flagged fields and evidence to a human reviewer.

Produce measurable experimental results.

Secondary goals

Compare a zero/few-shot biomedical extractor against a fine-tuned clinical encoder.

Evaluate OCR errors separately from NLP extraction errors.

Evaluate the verification subsystem using both naturally occurring and deliberately injected errors.

Demonstrate that verification can identify suspicious structured fields rather than blindly trusting extraction.

3. Important Engineering Principles

3.1 Do not over-engineer

The project must be completed within approximately 2–3 months.

Do not introduce:

microservices

unnecessary APIs

databases

Kubernetes

Docker unless later justified

authentication

cloud deployment

hospital integration

unnecessary frontend frameworks

Initial architecture should be one Python project/library + CLI + Streamlit UI.

3.2 Do not build everything at once

Development is milestone-based.

The coding agent MUST implement only the currently active milestone.

Do not implement future milestones unless explicitly requested.

3.3 Do not redesign the architecture without a concrete technical reason

This document is the authoritative architecture.

If implementation reveals a genuine incompatibility or technical problem, stop and report:

the problem

evidence

proposed alternatives

recommended change

Do not silently redesign the system.

3.4 Reproducibility

Experiments should use:

fixed random seeds

versioned configuration

reproducible scripts

saved metrics

deterministic train/test splits where practical

4. Current Milestone: M0a

M0a = Laboratory Report Vertical Slice

This is the ONLY milestone currently being implemented.

M0a input

Synthetic clinical/laboratory report containing laboratory information.

The medication section may be empty during M0a.

M0a pipeline

Synthea structured data
        ↓
Synthetic lab report rendering
        ↓
PDF / image
        ↓
PyMuPDF text extraction OR PaddleOCR
        ↓
Cached OCR/text representation
        ↓
Deterministic lab-row reconstruction
        ↓
Lab field extraction
        ↓
LOINC + UCUM normalization
        ↓
Intermediate JSON
        ↓
FHIR Patient + Observation + DocumentReference
        ↓
Basic verification
        ↓
Streamlit reviewer UI

M0a verification

Implement only:

Evidence grounding

OCR confidence associated with evidence

Unit validation

Reference-range/plausibility checks

FHIR structural validation

Do NOT implement the learned verifier yet.

5. M0a Success Criteria

M0a is successful when:

End-to-end

A fresh checkout can run one command that processes the test documents and produces:

Document
→ extraction
→ normalized intermediate JSON
→ FHIR
→ verification flags
→ UI

Evidence

100% of extracted fields must contain:

original value

normalized value where applicable

source text/evidence

page number

bounding box where available

OCR confidence where available

FHIR

All emitted resources must pass local structural validation.

Note:

FHIR-valid does NOT mean clinically correct.
It means the generated resource conforms structurally to the expected FHIR model.

Evaluation

A reproducible metrics script must report field-level accuracy on a frozen test split.

Initial target:

Lab field accuracy: ≥90% on clean synthetic test data

This is a target for the baseline, not a guaranteed result.

6. M0b — Future, NOT CURRENTLY IMPLEMENTED

After M0a is stable, M0b adds medication extraction.

Medication section
        ↓
GLiNER-BioMed
+
deterministic sig parser
        ↓
drug / strength / dose / route / frequency / duration
        ↓
RxNorm normalization
        ↓
FHIR medication resource
        ↓
canonical verification

Example verification:

Source:
three times daily

Extracted:
2/day

Verifier:
FREQ_MISMATCH

M0b will also introduce controlled error injection for:

wrong frequency

dose digit swap

wrong unit

Do NOT implement M0b during M0a.

7. Model Strategy

The project should use specialized biomedical/clinical models rather than assuming a large general-purpose LLM is necessary.

Primary M0 extractor

GLiNER-BioMed + deterministic rules/parsers

Reason:

usable without immediate fine-tuning

suitable for biomedical entity extraction

provides an independent architecture for later verification experiments

Primary later clinical encoder

BioClinical ModernBERT-base

Use later, after establishing the GLiNER baseline.

It will be fine-tuned for clinical NER and compared against the M0 baseline.

The purpose is to answer a measurable research question:

Does fine-tuning a domain-specific clinical encoder improve extraction enough to justify its additional complexity?

Training can be performed on Google Colab if local VRAM is insufficient.

Large generative LLM

NOT part of the core system.

Do not add a 7–14B model merely because it is available.

A small quantized generative model may be evaluated later only if experiments demonstrate a measurable gap that it can address.

8. OCR Strategy

Primary OCR

PaddleOCR

Reason:

word/line bounding boxes

OCR confidence

practical document OCR

useful evidence localization

For born-digital PDFs, use the PDF text layer where reliable rather than unnecessarily OCRing everything.

OCR output should be cached.

Example conceptual OCR record:

{
  "text": "Hemoglobin",
  "bbox": [100, 200, 180, 220],
  "confidence": 0.97,
  "page": 1
}

Later OCR experiments

Possible later candidates:

PaddleOCR-VL

Tesseract

docTR

Do NOT add multiple OCR engines during the first M0a implementation unless specifically needed for a concrete failure.

9. Clinical Terminology

Initial M0 terminology:

Laboratory

LOINC

UCUM

Medication later

RxNorm

Do not make SNOMED CT a dependency for M0.

Synthea conditions may use SNOMED CT and licensing/access considerations must be handled before Condition is introduced.

10. FHIR Strategy

Target standard:

FHIR R4

M0a resources:

Patient

Observation

DocumentReference

M0b will add the appropriate medication resource after verifying the semantics of the source document.

The exact medication resource must reflect whether the document represents a prescription/order or a medication statement/current use.

Use fhir.resources for local structural modeling/validation.

Use the official HL7 FHIR validator later as an authoritative validation metric if required.

Do not build real EHR write functionality.

11. Intermediate Representation

Every extracted field should use a structured representation similar to:

{
  "field": "hemoglobin",
  "value": "13.2",
  "normalized_value": 13.2,
  "unit": "g/dL",
  "normalized_unit": "g/dL",
  "source_evidence": {
    "text": "Hemoglobin 13.2 g/dL",
    "page": 1,
    "bbox": [100, 200, 300, 225],
    "ocr_confidence": 0.97
  },
  "source_model": "lab_parser",
  "verification": {
    "risk": 0.05,
    "flags": []
  }
}

Exact schema may be improved during implementation, but the principles must remain:

Every field needs provenance/evidence.

12. Verification Architecture

Verification is the main research contribution.

The verifier should eventually combine multiple independent signals.

Current M0a signals

1. Evidence grounding

Can the extracted field be traced back to source text/OCR evidence?

2. OCR confidence

Low-confidence OCR tokens should reduce trust.

3. Unit checks

Verify that the unit is valid for the laboratory measurement.

4. Plausibility/reference-range checks

Detect obviously inconsistent values or malformed ranges.

5. FHIR validation

Detect structurally invalid output.

Later verification signals

canonical re-derivation

independent extractor agreement

terminology validation

learned cross-encoder verifier

calibrated risk model

confidence/risk coverage analysis

Do not implement the learned verifier during M0a.

13. Dataset Strategy

Primary initial dataset

Synthea

Use synthetic patients and structured FHIR data.

Advantages:

synthetic

no real patient privacy issue

structured ground truth

suitable for generating documents programmatically

The first M0a dataset should be small.

Start around:

50–100 documents

Do not generate a huge corpus before the pipeline works.

Later scale

Approximately 300–500+ documents with:

multiple templates

multiple layouts

clean documents

mild degradation

severe degradation

Important anti-overfitting protocol

Split by patient.

Hold out an entire document template for testing.

Example:

Training:
Template A
Template B

Testing:
Template C

This is important because otherwise the model may simply memorize document layout.

Realism/OOD set

Later add a small hand-verified realism set with different layouts/fonts/text characteristics.

Credentialed datasets

Potential later datasets:

MIMIC-IV-Note

n2c2

i2b2

These are NOT critical dependencies for M0.

Never send credentialed/private clinical data to hosted LLM APIs.

14. Synthetic Document Generation

The ground truth should originate from structured synthetic data.

Conceptually:

Synthea FHIR
    ↓
Ground-truth structured fields
    ↓
Document template
    ↓
PDF
    ↓
Optional degradation
    ↓
OCR
    ↓
Prediction

Because the source structured data is known, we can evaluate predictions against ground truth.

Later degradation can include:

skew

blur

noise

compression

low DPI

contrast loss

Do not make handwriting part of the initial project.

15. Evaluation

Eventually evaluate:

OCR

CER

WER

Extraction

precision

recall

F1

strict/relaxed matching

field accuracy

Normalization

top-1 accuracy

top-5 accuracy

FHIR

structural validation rate

Verification

error detection recall

false-alarm rate

per-error-type performance

calibration

risk/coverage

end-to-end accuracy before vs after verification

16. Error Injection

Error injection is an important research evaluation technique.

Later introduce controlled errors such as:

wrong dose

wrong frequency

swapped digits

wrong unit

wrong drug

hallucinated entity

wrong date

OCR character confusion

Examples:

500 mg → 50 mg
3/day → 2/day
mg → mcg

Evaluate whether the verifier catches these.

However, do not rely only on injected errors.

Later also evaluate the natural errors produced by the actual extraction pipeline.

17. Future Milestones

M0a — CURRENT

Laboratory report vertical slice.

Do only this now.

M0b

Medication extraction and verification.

M1

scale dataset

multiple templates

fine-tune BioClinical ModernBERT-base

compare against GLiNER baseline

stronger FHIR validation

broader Observation support

M2

advanced verification

independent extractor agreement

error injection framework

learned verifier

fusion/calibration

M3

held-out template experiments

realism/OOD evaluation

ablations

optional additional models

M4

UI polish

final experiments

reproducibility

documentation

final demo

final report

18. Hardware and Environment

Local machine:

Windows

NVIDIA RTX 3050 6 GB

NVIDIA driver available

Java 17 available

Git available

Primary Python:

Python 3.11.x

A dedicated virtual environment MUST be used.

Do not rely on global Python packages.

Training strategy

Local GPU:

development

inference

small/debug experiments

OCR if appropriate

Google Colab:

full model fine-tuning

larger experiments

multiple seeds

experiments requiring more VRAM

Do not make Colab a dependency for the first M0a vertical slice.

19. Dependency Isolation

PaddlePaddle/PaddleOCR and PyTorch can create CUDA dependency conflicts.

Therefore:

keep the primary ML environment clean

if necessary, isolate PaddleOCR/PaddlePaddle in a separate environment

communicate through cached OCR JSON

CPU PaddleOCR is acceptable initially if it simplifies setup

Do not spend days optimizing OCR GPU performance.

20. Repository Direction

Expected eventual structure:

clindoc2fhir/
├── README.md
├── PROJECT_SPEC.md
├── pyproject.toml
├── configs/
├── data/
│   ├── synthea/
│   ├── rendered/
│   ├── degraded/
│   └── ocr_cache/
├── src/
│   └── clindoc/
│       ├── schema/
│       ├── ingest/
│       ├── ocr/
│       ├── layout/
│       ├── extract/
│       ├── normalize/
│       ├── fhir/
│       ├── verify/
│       ├── pipeline.py
│       └── cli.py
├── synth/
├── eval/
├── app/
│   └── streamlit_app.py
├── training/
├── tests/
└── docs/

The agent should NOT create every directory immediately.

Create only what the active milestone requires.

21. M0a Development Order

The coding agent should follow this order:

Stage 0

Environment audit.

Stage 1

Project skeleton + dependency setup.

Stage 2

Synthetic lab document generation.

Stage 3

Document ingestion.

Stage 4

PyMuPDF/PaddleOCR extraction.

Stage 5

OCR cache.

Stage 6

Lab-row parser.

Stage 7

LOINC/UCUM normalization.

Stage 8

Intermediate JSON schema.

Stage 9

FHIR mapping.

Stage 10

Basic verification.

Stage 11

Streamlit reviewer UI.

Stage 12

Frozen evaluation split + metrics.

Do not skip ahead.

22. Coding-Agent Rules

The coding agent must:

Read this file completely before making changes.

Treat it as the authoritative project specification.

Work only on the active milestone.

Avoid unnecessary dependencies.

Avoid unnecessary architecture.

Prefer simple, testable modules.

Write tests for important logic.

Keep configuration separate from code.

Never silently change the project architecture.

Report blockers rather than inventing solutions.

Never use real patient data during development unless explicitly authorized and properly handled.

Never send clinical/private data to external APIs.

Never claim clinical correctness merely because data is syntactically valid.

Keep a clear distinction between:

extraction

normalization

FHIR mapping

verification

23. What Is Explicitly Out of Scope for M0a

Do NOT build:

medication extraction

medication normalization

MedicationStatement/MedicationRequest implementation

Condition extraction

SNOMED integration

handwriting recognition

generative LLM

7B+ LLM

BioClinical ModernBERT fine-tuning

learned verifier

learned fusion model

PaddleOCR-VL

multiple OCR engines unless required for a specific failure

multiple document types

database

authentication

API server

Docker

cloud deployment

real EHR integration

real EHR writes

clinical decision support

diagnosis recommendations

treatment recommendations

multilingual support

24. Definition of Done for the Current Stage

The current stage is complete only when:

Synthetic lab PDF
        ↓
OCR/text extraction
        ↓
Lab fields
        ↓
LOINC/UCUM normalization
        ↓
FHIR resources
        ↓
Verification flags
        ↓
Streamlit UI

works reproducibly on a frozen test set.

The UI should show:

source document

extracted fields

source evidence

confidence

verification warnings

reasons for warnings

FHIR JSON

And an evaluation script must produce measurable results.

25. Final Project Thesis

The project should ultimately demonstrate:

Clinical document extraction should not be treated as trustworthy merely because an AI model produced structured output. The system should preserve source evidence, validate normalization and structured representations, detect inconsistencies, quantify risk, and route suspicious fields for human review.

The project is therefore an:

AI-assisted clinical document understanding, structured EHR conversion, and verification system.

It is not an autonomous medical system and must not be presented as one.