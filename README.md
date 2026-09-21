# Clindoc2FHIR: AI-Based Multimodal Clinical Document-to-EHR Conversion and Verification

An AI-assisted system that converts heterogeneous clinical documents into structured FHIR R4 healthcare data and automatically verifies whether extracted information is supported by the source document.

> **Active Milestone: M0a (Laboratory Report Vertical Slice)**  
> M0a is currently the **only** active milestone. It implements a baseline end-to-end slice for synthetic laboratory reports from ingestion to basic verification, FHIR generation, and human review.

---

## Key Principles & Scope
- **Verification Focus:** The central contribution is automated verification and provenance grounding of extracted clinical data rather than unverified extraction.
- **Safety Boundary:** The system is an assistive review tool. It does **not** make clinical diagnoses, decide treatments, or write to production hospital EHR systems.
- **Milestone-Based Development:** Only M0a features are in scope. Advanced features (medication extraction, learned verifiers, 7B+ LLMs, etc.) are explicitly deferred to later milestones.

---

## M0a Pipeline

```
Synthetic Lab Report (PDF)
        ↓
PyMuPDF / PaddleOCR Ingestion
        ↓
Cached OCR/Text Representation
        ↓
Deterministic Lab-Row Reconstruction & Field Extraction
        ↓
LOINC + UCUM Normalization
        ↓
Intermediate JSON Schema
        ↓
FHIR R4 Mapping (Patient, Observation, DocumentReference)
        ↓
Rule-Based Verification (Evidence Grounding, OCR Confidence, Unit & Plausibility Checks)
        ↓
Streamlit Reviewer UI
```

---

## Environment & Prerequisites
- **OS:** Windows 11 (64-bit)
- **Python:** 3.11.x (dedicated virtual environment)
- **GPU:** NVIDIA RTX 3050 (6 GB VRAM) with CUDA support
- **Java:** OpenJDK 17 (available for synthetic data generation)
- **Git:** Available for version control

---

## Repository Structure

```
.
├── PROJECT_SPEC.md      # Authoritative project specification
├── README.md            # Project summary and quickstart
├── pyproject.toml       # Build definition and dependency specification
├── configs/             # Configuration files (m0a_baseline.yaml)
├── data/                # Data storage (gitignored cache & outputs)
│   ├── synthea/         # Synthea generated source records
│   ├── rendered/        # Synthetic rendered PDF/image lab reports
│   └── ocr_cache/       # Cached OCR/text extraction outputs
├── src/
│   └── clindoc/         # Core application package
└── tests/               # Unit and integration test suite
```
