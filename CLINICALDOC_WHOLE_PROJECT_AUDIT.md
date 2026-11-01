# ClinicalDoc — Comprehensive Whole-Project Technical, Clinical-Safety, Security & Completion Audit

**Document ID**: `CLINICALDOC_WHOLE_PROJECT_AUDIT.md`  
**Date**: October 31, 2026  
**Auditor**: Principal Software Architect, Senior Clinical NLP/AI Engineer & Security Specialist (Antigravity)  
**Target Repository**: `ClinicalDoc` (`clindoc2fhir`)  
**Audit Scope**: Whole Repository (Architecture, Pipelines, NLP/NER, FHIR R4B, Four-Eyes Security, Ingestion, Frontends, Benchmarks, Documentation)  
**Execution Mode**: READ-ONLY AUDIT & STRATEGIC PLANNING  

---

## 1. Executive Summary

ClinicalDoc is an ambitious, modular clinical-document understanding and structured data extraction system designed to convert semi-structured, unstructured, and degraded clinical documentation into validated HL7 FHIR R4B resources. The project has undergone multiple developmental phases (Phase 0 through Phase 5.2.3), accumulating an extensive codebase spanning Python backend services, optical character recognition (PaddleOCR / PyMuPDF), hybrid entity extraction (Deterministic regex, GLiNER NER, and Qwen2.5-7B-Instruct grammar-constrained LLM), terminology mapping (LOINC, UCUM, RxNorm), a 3-stage calibrated verification cascade (ModernBERT Stage B and selective LLM Stage C), an authenticated human review service with Mode 0 Four-Eyes separation of duties, and a dual-tier FHIR R4B exporter.

### Key Audit Findings

1. **Functional Prototype Status**:
   The repository represents a **high-potential research/pilot prototype**, not a certified clinical product. While 1,466 unit and integration tests pass, the system is not yet hospital-deployment-ready and possesses **zero real-world clinical patient validation**. All published benchmark figures stem from synthetic and semi-synthetic document generators (120-document V3 benchmark, 12-document synthetic cascade, 60-document expanded cascade). The n2c2 2018 Track 2 data access request remains at `NOT STARTED`.

2. **Pipeline Architecture Disconnection (Critical Dual-Path Defect)**:
   The repository contains **two divergent execution pipelines**:
   - **Stateless CLI Pipeline (`src/clindoc/cli.py`)**: Directly transforms documents to FHIR Bundles and JSON audit reports in a single pass. It **completely bypasses** the PostgreSQL database, the human review service (`ReviewService`), the audit ledger, and Mode 0 human verification rules.
   - **Stateful API / Worker Pipeline (`src/clindoc/api`, `jobs/worker.py`, `services/fhir_export.py`)**: Follows the approved productization architecture (PD-001, PD-002), storing documents in encrypted blob storage, running asynchronous background workers, enforcing review states (`PENDING_REVIEW`), and requiring Four-Eyes document approval prior to export.

3. **REQ-44 Production Inactivation**:
   The newly verified medication lifecycle and duration partitioning rules (REQ-44 / Sub-Milestone 4.4.1) default to `enforce_req44_lifecycle=False` in `src/clindoc/fhir/mapper.py`. Neither production export call site in `src/clindoc/services/fhir_export.py` nor the CLI orchestrator passes `enforce_req44_lifecycle=True`. Consequently, the strict fail-closed and lifecycle decoupling rules verified in test suites are **dormant in the production runtime path**.

4. **Multi-Hop Four-Eyes Ancestry Bypass (Active Security Defect)**:
   Inspection of `src/clindoc/review/service.py:838-851` confirms that the Four-Eyes separation-of-duties check inspects **only the immediate single-hop predecessor** (`claim.supersedes_claim_id`). In a multi-hop edit sequence ($v_1 \to v_2 \to v_3 \to v_4$), an actor who created or edited version $v_2$ can approve version $v_4$ because the traversal does not walk the full ancestor lineage. While Gate 9 in `FHIRExportService` blocks export of claims lacking audit ledger verification, the review service itself permits this separation-of-duties violation.

5. **Concurrency Flaw in JSONL Audit Ledger**:
   `ReviewAuditLedger.append_event` (`src/clindoc/review/service.py:335-385`) executes an uncoordinated read-modify-write cycle on the filesystem without file-level locks (`flock`). Concurrent reviewer operations will read identical trailing line states, causing sequence number collisions and cryptographic hash chain corruption.

6. **Five Known Baseline Test Failures Explained**:
   The five persistent test failures in the repository are completely understood:
   - `test_cli.py` & `test_document_ingestion.py`: Fail because `.txt` was added to `SUPPORTED_EXTENSIONS` in `ocr/ingest.py` without updating the tests that assert `.txt` raises `UnsupportedFileTypeError`.
   - `test_m2_v2_corrections.py`: Fails due to an artificial `assert len(supported) == len(discrepancy)` assertion on unbalanced synthetic negative generation.
   - `test_section_detection_production.py`: Fails because deterministic extraction was improved to suppress non-lab numbers (MRN/Age), breaking a test that asserted the older buggy leakage occurred when section detection was disabled.
   - `test_vllm_deployment.py`: Fails due to a naive substring check for `"verify=False"` matching a string inside an error message that explicitly forbids `verify=False` in `ehr_transport.py`.

---

## 2. Overall Project Verdict

| Dimension | Verdict | Justification |
| :--- | :---: | :--- |
| **Demonstrability** | **YES (Controlled)** | The system can be demonstrated reliably as a pilot prototype using synthetic documents via the CLI or the FastAPI backend with the React Review Workbench or legacy Streamlit UI. |
| **Clinical Export Safety** | **STRICTLY UNSAFE** | Clinical export must remain **hard-blocked**. Blocking factors: (1) Multi-hop Four-Eyes ancestry bypass in `ReviewService`, (2) Dormant REQ-44 lifecycle enforcement in `FHIRExportService` and `cli.py`, (3) Unlocked concurrent JSONL audit logging. |
| **Hospital / Production Readiness** | **NO** | Academic/pilot prototype. No real-world clinical validation; no multi-tenant isolation; mock LLM fallbacks active in DEV profile; pending DUA on external clinical benchmarks. |
| **Completeness within Agreed Scope** | **~85%** | Core algorithms (OCR, regex extraction, GLiNER fallback, ModernBERT Stage B, FHIR R4B mapping, triage engine, RBAC, encrypted storage) are implemented and functional, but lack pipeline convergence and final security hardening. |

---

## 3. Current Repository and Workspace State

### 3.1 Git Metadata
- **Current Branch**: `master`
- **Head Commit Hash**: `45562fc37912a35ab08a036a4d8b4cc5bbb64630`
- **Remote Tracking**: `origin/master` (Up to date)

### 3.2 Workspace File Status
- **Tracked Modified Files (24)**:
  - Configuration: `configs/extraction.yaml`, `configs/loinc_subset.json`, `configs/ucum_subset.json`
  - Extraction: `src/clindoc/extract/deterministic.py`, `evaluate.py`, `gliner_fallback.py`, `llm_extractor.py`, `router.py`
  - FHIR Layer: `src/clindoc/fhir/constants.py`, `mapper.py`, `validator.py`
  - Ingestion & OCR: `src/clindoc/ingest/service.py`, `m2/context.py`, `ocr/__init__.py`, `ocr/born_digital.py`, `ocr/ingest.py`
  - Review & Export: `src/clindoc/review/service.py`, `src/clindoc/services/fhir_export.py`
  - Verification: `src/clindoc/verify/stage_a.py`
  - Tests: `tests/test_canonical_fhir_mapping.py`, `test_extraction_subsystem.py`, `test_fhir_export.py`, `test_pd022_qualitative_labs.py`
- **Untracked Files (68)**:
  - Documentation: Phase 5.2.1 through 5.2.3 milestone reports, REQ-44 remediation documents, readiness assessments.
  - Source Modules: `src/clindoc/extract/medication_association.py`, `section_detector.py`, `segmentation.py`, `ocr/layout.py`, `ocr/table_layout.py`, `testing/synthetic_export.py`.
  - Test Suites: `tests/test_phase523_milestone*.py`, `test_phase51*.py`, `test_phase4*.py`.
  - External Evaluation: `external_eval/` directory (12-doc and 60-doc benchmark suites).

### 3.3 Runtime Environment
- **Python Version**: Python 3.11.9
- **Virtual Environments Present**:
  - Primary: `d:\daa\.venv` (PyTorch, FastAPI, Streamlit, transformers, fhir.resources 7.1.0)
  - Specialized: `d:\daa\.venv_ner` (GLiNER / spaCy isolation)
  - Specialized: `d:\daa\.venv_ocr` (PaddleOCR / PaddlePaddle isolation)

---

## 4. Actual Architecture

```
                                  CLINICALDOC ARCHITECTURE
                                  
   [Inbound Document] (PDF, PNG, TIFF, TXT, DOCX)
           │
           ├──► [CLI Mode: clindoc.cli] ──► (Bypasses Review & DB) ──► [Direct FHIR Bundle JSON]
           │
           ▼ [API Mode: clindoc.api]
   ┌────────────────────────────────────────────────────────────────────────────────────────┐
   │ 1. Ingestion & Storage                                                                 │
   │    POST /api/v1/documents ──► Quarantine ──► SHA-256 Deduplication                     │
   │    AES-256-GCM Encrypted Blob Store ──► PostgreSQL (Document: INGESTED)               │
   └────────────────────────────────────────┬───────────────────────────────────────────────┘
                                            ▼
   ┌────────────────────────────────────────────────────────────────────────────────────────┐
   │ 2. Background Asynchronous Processing (clindoc.jobs.worker)                            │
   │    JobQueue (SKIP LOCKED) ──► Status: PROCESSING                                      │
   │    ├── OCR / Born-Digital Parser (PyMuPDF / PaddleOCR) ──► DocumentText JSON           │
   │    ├── Layout & Section Detection (Header heuristic + bounding box clustering)         │
   │    ├── Extraction Router (clindoc.extract.router)                                      │
   │    │     ├─ RegionClassifier (Table vs Narrative vs Rx Pad)                           │
   │    │     ├─ DeterministicExtractor (Regex for Labs & Meds)                             │
   │    │     ├─ GLiNERFallbackExtractor (Tier 1 Low-Confidence Fallback)                   │
   │    │     └─ NarrativeExtractor (Qwen2.5-7B vLLM / MockLLMProvider)                     │
   │    ├── Clause Segmentation & Medication Association (Boundary partitioning)            │
   │    ├── Terminology Normalization (TerminologyNormalizer: LOINC, UCUM, RxNorm)          │
   │    └── Verification Cascade (clindoc.verify.cascade)                                   │
   │          ├─ Stage A: Deterministic Verifier (Exact/Fuzzy String & Value Match)         │
   │          ├─ Stage B: Learned Verifier (ModernBERT-Biomedical, Temp Scaler T=1.1465)    │
   │          └─ Stage C: Selective LLM Judge (VLLMJudgeClient / DefaultMockLLMJudge)       │
   │    Persist Claims & VerificationResults ──► Document: PENDING_REVIEW                   │
   └────────────────────────────────────────┬───────────────────────────────────────────────┘
                                            ▼
   ┌────────────────────────────────────────────────────────────────────────────────────────┐
   │ 3. Human Review & Four-Eyes Governance (Mode 0 Mandatory Review)                       │
   │    React Workbench (frontend/) OR Legacy Streamlit UI (src/clindoc/ui/app.py)          │
   │    ReviewService (src/clindoc/review/service.py)                                       │
   │    ├── TriageEngine (Sorts claims by risk / discrepancy)                               │
   │    ├── Clinician Actions: ACCEPT, EDIT, REJECT, ESCALATE, REOPEN                       │
   │    ├── Inline Re-Verification: Edited claims trigger Stage A/B re-verification         │
   │    ├── Four-Eyes Separation: Operator != Reviewer; Modifier != Approver                │
   │    │     └── [DEFECT: Single-hop supersedes_claim_id check only]                       │
   │    └── Audit Logging: PostgreSQL AuditEvents + Offline ReviewAuditLedger (JSONL)       │
   │          └── [DEFECT: Unlocked concurrent append in ReviewAuditLedger]                 │
   └────────────────────────────────────────┬───────────────────────────────────────────────┘
                                            ▼
   ┌────────────────────────────────────────────────────────────────────────────────────────┐
   │ 4. Formal Export & FHIR Generation (clindoc.services.fhir_export)                      │
   │    POST /api/v1/documents/{id}/export                                                  │
   │    Gate 9 Safety & Eligibility Checks:                                                 │
   │    ├── Document Approval: Status APPROVED, is_export_eligible=True                     │
   │    ├── Patient Identity: Status CONFIRMED                                              │
   │    ├── Claim States: Mode 0 requires 100% terminal review (ACCEPTED / EDITED)          │
   │    ├── Audit Ledger Proof: Cryptographic hash chain & HMAC verification                │
   │    └── claims_to_fhir_bundle(enforce_req44_lifecycle=False)  ◄── [DEFECT: Dormant]     │
   │          ├── MedicationRequest / MedicationStatement                                   │
   │          ├── Observation (Lab Quantitative & Qualitative)                              │
   │          ├── DiagnosticReport, Patient, DocumentReference, Provenance                  │
   │          └── FHIRValidator (fhir.resources.R4B pydantic schema check)                  │
   │    Output: Validated FHIR R4B Collection Bundle JSON                                    │
   └────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. End-to-End Workflow Assessment

| Stage | Responsible Module | Inputs | Outputs | Actual Status | Findings / Gaps |
| :--- | :--- | :--- | :--- | :---: | :--- |
| **1. Upload & Screening** | `api/routes/documents.py`, `ingest/service.py` | Uploaded File, Patient Context | Blob Storage Ref, Document DB Record | **Functional** | Magic-byte checking, size limits, SHA-256 deduplication, and AES-256-GCM storage are functional. |
| **2. OCR & Parsing** | `ocr/ingest.py`, `ocr/born_digital.py` | PDF / Image / Text file | `DocumentText` schema | **Functional / Divergent** | Born-digital PDF extraction via PyMuPDF works cleanly. `.txt` is in `SUPPORTED_EXTENSIONS` but unhandled in CLI tests. PaddleOCR isolated to worker. |
| **3. Layout & Sectioning** | `extract/section_detector.py`, `ocr/layout.py` | `DocumentText` | Section spans, Bounding boxes | **Functional** | Regex-anchored section detection works. Non-lab demographic leakage suppression is active. |
| **4. Entity Extraction** | `extract/router.py`, `deterministic.py`, `gliner_fallback.py`, `llm_extractor.py` | `DocumentText` | Raw `Claim` and `Evidence` lists | **Functional / Mock Fallback** | Deterministic extraction handles structured tables. In `DEV` profile, LLM narrative defaults to `MockLLMProvider` (regex heuristic). |
| **5. Clause Partitioning** | `extract/segmentation.py`, `medication_association.py` | Medication Claims, Text | Regimen Groups, Bounded Clauses | **Functional** | Boundary $[L_k, R_k)$ partitioning, sequence numbering, and drug-colon normalization operational. |
| **6. Normalization** | `normalize/normalizer.py`, `ucum.py`, `hybrid.py` | Raw claims, Code maps | Normalized LOINC, UCUM, RxNorm | **Functional** | Static subset mappings for LOINC (`configs/loinc_subset.json`) and UCUM. RxNorm text-normalization fallback active. |
| **7. Verification Cascade** | `verify/cascade.py`, `stage_a.py`, `stage_b.py`, `stage_c.py` | Claims, Evidence, Page text | `VerificationResult` records | **Functional** | Stage A (deterministic) + Stage B (ModernBERT $T=1.1465$) operational. Stage C defaults to `DefaultMockLLMJudge` in `DEV`. |
| **8. Human Review (Mode 0)**| `review/service.py`, `review/approval.py` | Claims, DB session, Reviewer action | Updated Claim versions, `ReviewAction` | **Defect Present** | Optimistic locking and state transitions work. Multi-hop Four-Eyes ancestry bypass is present (`service.py:838`). |
| **9. Audit Trail** | `api/audit/service.py`, `review/service.py` | Review events, Document events | DB `AuditEvent`, JSONL ledger | **Defect Present** | RFC 8785 canonicalization and SHA-256 hashing operational. Concurrent JSONL append lacks file locking. |
| **10. FHIR R4B Export** | `services/fhir_export.py`, `fhir/mapper.py`, `fhir/validator.py` | Approved Claims, Evidence, Approvals | FHIR R4B Bundle | **Defect Present** | Schema validation passes. Gate 9 blocks unapproved exports. `enforce_req44_lifecycle` defaults to `False` and is omitted by caller. |

---

## 6. Implemented / Partial / Missing Feature Matrix

| Functional Area | Specification Reference | Implemented & Working | Partially Implemented | Missing / Unimplemented |
| :--- | :--- | :--- | :--- | :--- |
| **Ingestion** | PRODUCTIZATION_SPEC §1.2 | PDF (born-digital & image), PNG, TIFF | DOCX, TXT adapters (stubs exist) | Streaming chunked upload for multi-GB files |
| **Storage Security** | PD-007, PD-019 | AES-256-GCM blob storage, PBKDF2 keys | Secret key rotation scripts | Automated KMS / Cloud HSM integration |
| **Extraction** | PROJECT_SPEC §11, DEC-001 | Deterministic regex (Labs & Meds) | GLiNER fallback (subprocess IPC) | Real-time vLLM grammar engine locally |
| **Segmentation** | REQ-44, Phase 5.2.3 M2 | Transition boundary $[L_k, R_k)$ splitting | Multi-column table geometry layout | Complex nested conditional taper schemas |
| **Terminology** | PROJECT_SPEC §14, DEC-002 | Curated LOINC (40 items), UCUM subsets | RxNorm string matching fallback | Live FHIR Terminology Service client |
| **Verification** | PROJECT_SPEC §12, DEC-018 | Stage A string/value; Stage B ModernBERT | Stage C Qwen2.5 (tested in Colab) | Production local inference under RTX 3050 |
| **Review Workflow** | PRODUCTIZATION_SPEC §7.3, PD-002 | Mode 0 state machine, inline re-verify | Multi-hop Four-Eyes (1-hop only) | Batch multi-claim bulk review |
| **Audit Ledger** | PD-012, Phase 5.2.3 M4.2 | RFC 8785 JSONL, SHA-256 hash chaining | HMAC checkpointing (manual CLI) | Concurrent file locking (`flock`/`portalocker`)|
| **FHIR Generation** | PROJECT_SPEC §15, §16, PD-016 | Obs, MedRequest, MedStatement, Bundle | REQ-44 lifecycle enforcement (dormant)| Condition, Procedure, Encounter resources |
| **FHIR Validation** | PROJECT_SPEC §15 | `fhir.resources.R4B` pydantic validator | Referential integrity, status rules | Official HL7 Java Validator CLI integration |
| **User Interface** | PD-013 | Legacy Streamlit UI (`src/clindoc/ui/`) | React Workbench (`frontend/` complete) | Production reverse proxy / SSO integration |

---

## 7. Clinical NLP and Extraction Assessment

### 7.1 Architecture of Extraction Subsystems
1. **Deterministic Extractor (`src/clindoc/extract/deterministic.py`)**:
   - Primary extractor for structured laboratory tables and prescription lines.
   - High precision ($1.00$ on clean synthetic lab and medication forms).
   - Regex patterns explicitly bounded to prevent demographic number leakage (MRN, Age, Phone) into numeric lab values.
2. **GLiNER NER Fallback (`src/clindoc/extract/gliner_fallback.py`)**:
   - Secondary extractor triggered when deterministic confidence $< 0.75$ (DEC-004).
   - Restricted strictly to Tier 1 entities (`med.name`, `med.dose`, `lab.test_name`, `lab.value`).
   - High standalone accuracy on medications (`med.name` F1 $0.9448$, `med.dose` F1 $0.9424$), but poor recall on complex multi-analyte lab tables (overall Macro-F1 $0.3455$).
3. **LLM Narrative Extractor (`src/clindoc/extract/llm_extractor.py`)**:
   - Designed for unstructured discharge narratives and clinic consultation notes.
   - Constrained by `LLM_OUTPUT_SCHEMA` to permitted narrative fields (DEC-013 / DEC-020).
   - **Production Reality**: In local testing and default execution, `MockLLMProvider` is utilized. Real Qwen2.5-7B-Instruct-AWQ inference was conducted on remote GPU infrastructure (Colab T4).

### 7.2 Evidence Grounding and Provenance
- All claims carry verbatim evidence text, character offsets (`char_start`, `char_end`), page numbers, and bounding box coordinates (`BBoxModel`).
- Zero-offset or ungrounded claims are rejected prior to export.

### 7.3 Benchmark Performance Ledger (Isolated Scopes)

| Benchmark Scope | Denominator | Accuracy / Micro-F1 | Macro-F1 | Discrepancy Recall | Evaluation Mode | Canonical Artifact |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **E1 Deterministic (Labs)** | 50 docs / 705 obs | Prec: 1.000 / Rec: 1.000 | 1.0000 | N/A | Deterministic Python | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **E1 Deterministic (Meds)** | 50 docs / 87 reg | Prec: 1.000 / Rec: 1.000 | 1.0000 | N/A | Deterministic Python | `experiments/E4-hybrid-router-extraction/REPORT.md` |
| **E2 GLiNER Standalone** | 120 docs / 4,456 GT | Prec: 0.439 / Rec: 0.174 | 0.3455 | N/A | Subprocess NER | `experiments/E2-gliner-extraction/REPORT.md` |
| **E3 Narrative Extraction** | 20 docs / 540 GT | Prec: 0.678 / Rec: 0.144 | 0.2047 | N/A | MockLLMProvider | `experiments/E3-narrative-extraction/REPORT.md` |
| **V3 OOD Verification (V8)**| 34 docs / 1,826 claims | Acc: 0.9748 (1,780/1,826) | 0.9538 | 0.9826 (961/978) | ModernBERT + Scaler | `experiments/FINAL_REPORT_DATA_SHEET.md` |
| **Synthetic Cascade** | 12 docs / 10 fields | 100% manifest match | 1.0000 | 1.0000 | Cascade Pipeline | `external_eval/tests/test_synthetic_benchmark.py` |
| **Expanded Cascade** | 60 docs / 10 fields | 100% manifest match | 1.0000 | 1.0000 | Cascade Pipeline | `external_eval/tests/test_expanded_benchmark.py` |

---

## 8. Medication Association and REQ-44 Audit

### 8.1 Current Implementation State
Medication regimen partitioning and duration association were recently remediated under REQ-44 (Sub-Milestone 4.4.1):
- **Clause Partitioning**: Regex-anchored boundary detection $[L_k, R_k)$ using `TAPER_TRANSITION_REGEX` (`then`, `followed by`, `taper to`).
- **Semantic Role Disambiguation**: Bounded sentence window inspection distinguishes `ROLE_REGIMEN_DURATION` ("for 2 weeks") from `ROLE_FOLLOWUP_REVIEW` ("review after 1 week"), `ROLE_MONITORING_REASSESSMENT` ("repeat labs in 5 days"), and `ROLE_NON_MED_CLINICAL_HISTORY` ("symptom duration 6 months").
- **Narrative SIG Preservation**: Verbatim phrasing of individual clauses is preserved in `Dosage.text`, normalizing drug prefixes while stripping non-medication clinical sentences.
- **Dose Summation Prevention**: Taper doses are never summed into an aggregate number.

### 8.2 Critical Gap: Inactive Lifecycle Enforcement in Production Paths
In `src/clindoc/fhir/mapper.py:2796`:
```python
def claims_to_fhir_bundle(
    claims: Sequence[Claim],
    evidence_map: dict[str, Evidence],
    verification_map: dict[str, VerificationResult],
    document_id: str = "doc-1",
    document_type: str = "generic_note",
    enforce_req44_lifecycle: bool = False,  # <--- DEFAULT FALSE
) -> Bundle:
```
In `src/clindoc/services/fhir_export.py` (lines 743 and 1029):
```python
bundle = claims_to_fhir_bundle(
    claims=exportable_claims,
    evidence_map=evidence_map,
    verification_map=verification_map,
    document_id=resolved_doc_id,
    document_type=document_type,
    # enforce_req44_lifecycle IS NOT PASSED!
)
```
In `src/clindoc/cli.py` (line 244):
```python
bundle = claims_to_fhir_bundle(
    claims=claims,
    evidence_map=evidence_map,
    verification_map=verification_map,
    document_id=doc_text.document_id,
    # enforce_req44_lifecycle IS NOT PASSED!
)
```
**Impact**:
When `enforce_req44_lifecycle` is `False`:
1. Historical medication mentions on Rx pad layouts map to `MedicationRequest(status="completed")`, **fabricating an active prescription order**.
2. Unreferenced prescription cancellations in clinic notes map to `MedicationStatement(status="stopped")` without cessation evidence.
3. Patient cessation and prescription cancellation remain coupled.
**Conclusion**: The REQ-44 lifecycle safety guarantees exist in `mapper.py` and pass in `tests/test_phase523_milestone4_4.py`, but are **completely bypassed** in production export and CLI operations.

---

## 9. FHIR R4B Conformance Assessment

### 9.1 Base Standard & Release Target
- The codebase targets **HL7 FHIR R4B (v4.3.0)** Base via `fhir.resources==7.1.0`.
- All resources are imported from `fhir.resources.R4B.*`.
- US Core profiles are **not** targeted or claimed.

### 9.2 Supported Resource Mappings
1. **MedicationRequest**: Used for planned or active prescription orders from prescription pads.
2. **MedicationStatement**: Used for progress notes, patient-reported therapies, historical medications, and completed regimens.
3. **Observation**: Used for laboratory findings (both quantitative numbers with UCUM units and qualitative determinations via `valueString` per PD-022).
4. **DiagnosticReport**: Groups related observations from laboratory panels under a single document-anchored report.
5. **Patient**: Minimal resource representing grounded patient demographics (ID, name).
6. **DocumentReference**: Preserves source document metadata, SHA-256 hash, and MIME type.
7. **Provenance**: Resource-level and element-level provenance linking FHIR resources back to document evidence spans and verification timestamps.

### 9.3 Validation Rigor & Limitations
- `FHIRValidator` validates resources by deserializing through Pydantic schemas in `fhir.resources.R4B`.
- It enforces internal reference integrity (every `#ref` resolves to a resource in `Bundle.entry`).
- It checks standard FHIR status code vocabularies.
- **Limitation**: It does **not** perform terminology validation against external terminology servers (e.g. tx.fhir.org); it relies solely on local JSON dictionary lookups (`configs/loinc_subset.json` and `configs/ucum_subset.json`).

---

## 10. Security and Four-Eyes Audit

### 10.1 Multi-Hop Four-Eyes Ancestry Bypass (FINDING SEC-01)
- **Severity**: P0 — Critical Security Blocker
- **Location**: `src/clindoc/review/service.py:838-851`
- **Code Inspection**:
  ```python
  # 2. Modifier of previous version cannot accept
  if claim.supersedes_claim_id is not None:
      try:
          prev_claim = session.get(Claim, claim.supersedes_claim_id)
      except Exception:
          prev_claim = None
      if prev_claim is not None:
          if prev_claim.created_by is not None and str(prev_claim.created_by) == actor_str:
              raise FourEyesViolationError(...)
          if prev_claim.payload and prev_claim.payload.get("edited_by") == actor_str:
              raise FourEyesViolationError(...)
  ```
- **Vulnerability Mechanism**:
  The check inspects only `prev_claim`. If an edit sequence involves three or more versions:
  1. Actor A edits Claim $v_1 \to v_2$ (`created_by = A`).
  2. Actor B edits Claim $v_2 \to v_3$ (`created_by = B`).
  3. Actor C edits Claim $v_3 \to v_4$ (`created_by = C`).
  4. Actor A reviews and ACCEPTs Claim $v_4$:
     - Is Actor A the creator of $v_4$? No ($v_4$ was created by C).
     - Is Actor A the creator of $v_3$ (the immediate predecessor)? No ($v_3$ was created by B).
     - The loop terminates. Actor A is permitted to ACCEPT Claim $v_4$, directly violating the Four-Eyes principle by approving their own ancestral changes.
- **Remediation**:
  Replace the single-step lookup with a `while` loop walking the entire `supersedes_claim_id` ancestry chain back to root ($v_1$), collecting all `created_by` and `edited_by` UUIDs into an `ancestor_editors` set, and asserting `actor_uuid not in ancestor_editors`.

### 10.2 Role-Based Access Control (RBAC)
- Enforces 5 canonical roles: `DOCUMENT_OPERATOR`, `CLINICAL_REVIEWER`, `SUPERVISORY_REVIEWER`, `SYSTEM_ADMINISTRATOR`, `AUDITOR`.
- JWT token authentication via OpenID Connect (OIDC) / OAuth2 with PKCE (PD-006).
- Document and claim endpoints correctly enforce role dependencies via FastAPI `Security` / `Depends`.

---

## 11. Audit Ledger and Export Safety

### 11.1 Concurrency Flaw in ReviewAuditLedger (FINDING SEC-02)
- **Severity**: P0 — Critical Integrity Defect
- **Location**: `src/clindoc/review/service.py:335-385`
- **Mechanism**:
  ```python
  def append_event(self, ...):
      # Step 1: Open and read file to find last line, sequence number, and prev_hash
      if self.ledger_path.exists():
          with open(self.ledger_path, "r", encoding="utf-8") as f:
              for line in f:
                  last_line = line.strip()
          parsed = json.loads(last_line)
          seq_num = parsed.get("sequence_number", 0) + 1
          prev_hash = parsed.get("current_event_hash", "")
      
      # Step 2: Compute new envelope and hash
      ...
      # Step 3: Re-open in append mode and write
      with open(self.ledger_path, "a", encoding="utf-8") as f:
          f.write(line_json + "\n")
  ```
- **Vulnerability**:
  In a multi-user review environment (FastAPI with multiple async worker threads or multi-process uvicorn), two concurrent review decisions on different claims will execute Step 1 simultaneously. Both read the same `last_line`, compute the identical `sequence_number` and `previous_event_hash`, and append conflicting records. The ledger becomes corrupted and fails subsequent verification (`AuditLedgerVerificationError: SequenceGapError` or `DuplicateAuditEventError`).
- **Remediation**:
  Use atomic file locking (`portalocker.Lock` or OS `fcntl.flock`) covering the read-compute-write block, or delegate ledger sequencing to a PostgreSQL transaction.

### 11.2 Export Gate 9 Assessment
- In `src/clindoc/services/fhir_export.py:488-615`:
  Gate 9 enforces durable proof verification. When `enforce_audit_evidence=True` (or in production), every claim must be backed by a verified audit ledger entry matching the document SHA-256.
- In-memory mock claims without audit ledger entries are rejected with `SUPPRESSED_UNVERIFIED_AUDIT_LEDGER`.
- Document approval requires explicit sign-off by an authorized Approver, and patient identity must be marked `CONFIRMED`.

---

## 12. Ingestion, Privacy and Operational Security

### 12.1 Supported Document Types & Test Failure Breakdown
- **Configured Extensions**: `SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".txt", ".docx"}` (`src/clindoc/ocr/ingest.py:22`).
- **Root Cause of Baseline Test Failures 1 & 2**:
  - `tests/test_cli.py:147` (`test_cli_unsupported_extension_fails`): Creates `document.txt` and expects CLI to fail. But `ocr/ingest.py` treats `.txt` as supported!
  - `tests/test_document_ingestion.py:238` (`test_13_unreadable_invalid_document_handling`): Writes `notes.txt` and asserts `pytest.raises(UnsupportedFileTypeError)`. Because `.txt` is in `SUPPORTED_EXTENSIONS`, no exception is raised!
  - **Resolution**: Harmonize `.txt` support. Either complete the plaintext ingestion adapter in the CLI and update the tests to use `.xyz`/`.unsupported`, or remove `.txt` from `SUPPORTED_EXTENSIONS`.

### 12.2 TLS Verification Substring False Alarm (Baseline Failure 5)
- In `tests/test_vllm_deployment.py:153`:
  ```python
  for py_file in py_files:
      content = py_file.read_text(encoding="utf-8")
      assert "verify=False" not in content
  ```
- In `src/clindoc/services/ehr_transport.py:180`:
  ```python
  raise ConfigurationError("ssl_verify=False is strictly forbidden in production.")
  ```
- The test fails because it searches for the literal substring `"verify=False"` and hits the error message forbidding it!

### 12.3 Data Encryption & Privacy
- **Blob Storage**: AES-256-GCM authenticated encryption using PBKDF2 key derivation (`src/clindoc/storage/blob.py`). Zero plaintext clinical document images or PDFs are stored on disk.
- **Audit Trails**: All audit log payloads undergo PHI screening (`validate_metadata_phi_free`) before database persistence or JSONL serialization.

---

## 13. Frontend and Human Review Assessment

### 13.1 User Interface Inventory
1. **React + Vite Review Workbench (`frontend/`)**:
   - Modern, fully featured web application adhering to PD-013.
   - Includes `QueueView` (document list with triage sorting), `WorkbenchView` (split document viewer, bounding box overlay, claim review cards with ACCEPT, EDIT, REJECT, ESCALATE actions), and `SystemStatusView`.
   - Connected via `frontend/src/api/client.ts` to FastAPI `/api/v1` routes with correlation ID tracing and Bearer token handling.
2. **Legacy Streamlit Reviewer (`src/clindoc/ui/app.py`, `app.py`)**:
   - Prototype review interface under strict freeze.
   - Bypasses the REST API, calling internal service objects directly.

### 13.2 Gap Analysis
- While the React frontend has complete component implementations, it lacks end-to-end integration tests connecting the React client against a running FastAPI backend in CI.
- The CLI workflow provides no web interface, executing strictly headless.

---

## 14. Testing and Benchmark Assessment

### 14.1 Full Test Suite Inventory
- **Total Tests Collected**: 1,471 tests across 104 test files.
- **Passing Tests**: 1,466 (99.66%).
- **Failing Tests**: 5 (0.34%).
- **Execution Time**: ~4 minutes 42 seconds.

### 14.2 Detailed Analysis of the 5 Historical Failures

| Test Identifier | Failure Signature | Actual Root Cause | Recommended Fix |
| :--- | :--- | :--- | :--- |
| `tests/test_cli.py::test_cli_unsupported_extension_fails` | `assert 0 == 1` | CLI process succeeds on `document.txt` because `.txt` was added to `SUPPORTED_EXTENSIONS`. | Change test input to `document.unsupported` or `document.xyz`. |
| `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling` | `DID NOT RAISE UnsupportedFileTypeError` | Ingesting `notes.txt` does not raise because `.txt` is in `SUPPORTED_EXTENSIONS`. | Change test input from `notes.txt` to `notes.badext`. |
| `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation` | `assert 335 == 705` | Negative perturbation generator produces multiple discrepancy variations per item, violating a 1:1 balance assertion. | Update test to verify minimum counts and label fidelity rather than strict 1:1 equality, or balance generator. |
| `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline` | `assert ('987654' in ['105'] or '61' in ['105'])` | Extractor regex was hardened to require lab test names, eliminating demographic number leakage even when section detection is off. | Update test to reflect that demographic numbers are no longer extracted as lab values. |
| `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification` | `Found insecure 'verify=False' in ehr_transport.py` | Naive substring search matches an error string forbidding `ssl_verify=False`. | Make regex in test match code assignments (e.g. `re.search(r"verify\s*=\s*False", ...)`). |

### 14.3 Benchmark Isolation Audit
- Denominators are strictly maintained across test files.
- `external_eval/tests/test_synthetic_benchmark.py` (12 docs) and `test_expanded_benchmark.py` (60 docs) pass 8/8 with 0 denominator drift.
- Synthetic benchmarks are nowhere presented as real-world clinical validation in codebase artifacts.

---

## 15. Dependency, Build and Reproducibility Assessment

### 15.1 Dependency Health
- Dependencies in `pyproject.toml` are pinned for critical packages (`fhir.resources==7.1.0`).
- Lockfile `uv.lock` is present and valid.
- Python 3.11 requirement is enforced.

### 15.2 Environment & Hardware Barriers
- PaddleOCR and GPU-dependent packages require separate virtual environments on Windows due to CUDA / PyTorch conflicts (`.venv_ocr`, `.venv_ner`).
- Real Qwen2.5-7B-Instruct AWQ execution requires 16GB+ VRAM (Colab T4 or server GPU). Local execution on commodity developer hardware (e.g. 4GB/6GB VRAM) must use the `dev` profile with mock providers.

---

## 16. Historical Milestone and Documentation Reconciliation

| Milestone | Intended Objective | Implementation Status | Current Source of Truth Document | Reconciliation Status |
| :--- | :--- | :---: | :--- | :--- |
| **Phase 0** | Baseline architecture & decision resolution | COMPLETE | `PHASE0_ARCHITECTURE_RESOLUTION.md`, `PROJECT_SPEC.md` | Authoritative for research baseline |
| **Phase 1-4** | Extraction, M2 verifier, Stage B ModernBERT, Stage C Judge | COMPLETE | `experiments/FINAL_REPORT_DATA_SHEET.md`, `reports/FINAL_PROJECT_REPORT.md` | Authoritative for ML metrics |
| **Phase 5.1** | Ingestion hardening, layout parsing, PDF extraction | COMPLETE | `reports/PHASE8_8_FHIR_R4B_EXPORT_REPORT.md` | Superseded by Phase 5.2.3 |
| **Phase 5.2** | Medication coreference & association | COMPLETE | `phase522_medication_association_design.md` | Superseded by Phase 5.2.3 M3/M4 |
| **Phase 5.2.3 M1** | Deterministic grounding & anchor ordering | COMPLETE | `phase523_milestone1_report.md` | Fully integrated in `extract/deterministic.py` |
| **Phase 5.2.3 M2** | Segmentation & clause boundary safety | COMPLETE | `phase523_milestone2_sentence_clause_segmentation_report.md` | Fully integrated in `extract/segmentation.py` |
| **Phase 5.2.3 M3** | Medication-dose-duration association | COMPLETE | `phase523_milestone3_implementation_report.md` | Fully integrated in `extract/medication_association.py` |
| **Phase 5.2.3 M4.2**| Audit ledger, HMAC trust anchor, Gate 9 binding | COMPLETE | `phase523_milestone4201_audit_integrity_addendum.md` | Active in `review/service.py` & `services/fhir_export.py` |
| **Phase 5.2.3 M4.4**| REQ-44 Regimen partitioning & lifecycle mapping | COMPLETE | `phase523_milestone44_1_req44_implementation_report.md` | Integrated in `fhir/mapper.py`; **DORMANT in production callers** |

---

## 17. Consolidated Defect Register

| Defect ID | Severity | Category | File Path / Location | Summary of Defect | Classification |
| :--- | :---: | :--- | :--- | :--- | :---: |
| **DEF-001** | **P0** | Security / Four-Eyes | `src/clindoc/review/service.py:838-851` | Multi-hop Four-Eyes ancestry bypass: only checks single immediate predecessor (`supersedes_claim_id`). | **VERIFIED** |
| **DEF-002** | **P0** | Clinical Safety / FHIR | `src/clindoc/services/fhir_export.py:743, 1029` | REQ-44 lifecycle enforcement is dormant in production export (`enforce_req44_lifecycle=False`). | **VERIFIED** |
| **DEF-003** | **P0** | Security / Integrity | `src/clindoc/review/service.py:335-385` | Concurrent JSONL audit ledger appends lack file-level locking, risking hash chain corruption. | **VERIFIED** |
| **DEF-004** | **P1** | Pipeline Architecture | `src/clindoc/cli.py:244-250` | Unified CLI completely bypasses Mode 0 review, DB persistence, and audit ledger, directly exporting FHIR. | **VERIFIED** |
| **DEF-005** | **P1** | Clinical Safety / FHIR | `src/clindoc/cli.py:244` | Unified CLI omits `enforce_req44_lifecycle=True`, running legacy ungrounded medication mapping. | **VERIFIED** |
| **DEF-006** | **P1** | Test Suite Integrity | `tests/test_cli.py:147`, `tests/test_document_ingestion.py:238` | Tests fail asserting `.txt` is unsupported, but `.txt` was added to `SUPPORTED_EXTENSIONS`. | **REPRODUCED** |
| **DEF-007** | **P1** | Test Suite Integrity | `tests/test_m2_v2_corrections.py:32` | Flawed assertion `len(supported) == len(discrepancy)` fails on multi-perturbation negative generation. | **REPRODUCED** |
| **DEF-008** | **P1** | Test Suite Integrity | `tests/test_section_detection_production.py:92` | Test asserts obsolete bug (header demographic number leakage) that was fixed in Phase 5.2.3. | **REPRODUCED** |
| **DEF-009** | **P1** | Test Suite Integrity | `tests/test_vllm_deployment.py:153` | Substring check for `"verify=False"` hits error string forbidding it in `ehr_transport.py`. | **REPRODUCED** |
| **DEF-010** | **P2** | Clinical NLP / ML | `src/clindoc/extract/router.py:176-187` | In DEV profile, narrative extraction silently defaults to regex heuristic (`MockLLMProvider`). | **VERIFIED** |
| **DEF-011** | **P2** | Verification / ML | `src/clindoc/verify/stage_c.py:288-295` | In DEV profile, Stage C judge silently defaults to heuristic `DefaultMockLLMJudge`. | **VERIFIED** |
| **DEF-012** | **P2** | Terminology | `src/clindoc/normalize/normalizer.py` | Local dictionary subsets only (40 LOINC codes); no dynamic or comprehensive terminology lookup. | **VERIFIED** |
| **DEF-013** | **P3** | User Interface | `src/clindoc/ui/app.py` | Legacy Streamlit UI calls backend services directly in-process rather than via REST API. | **VERIFIED** |
| **DEF-014** | **P3** | Operational / Deployment| `deploy/docker-compose.yml` | Requires separate multi-container orchestration for GPU vLLM server, not turnkey on desktop. | **VERIFIED** |

---

## 18. Prioritized Remediation Plan

### P0 — Critical Blockers (Must resolve before any clinical export reconsideration)

#### Action P0-1: Remediate Multi-Hop Four-Eyes Ancestry Traversal
- **Action ID**: `ACT-P0-1`
- **Target File**: `src/clindoc/review/service.py` (`_check_four_eyes_separation`)
- **Problem**: Reviewer can approve a claim if they edited version $v_{n-2}$, because only $v_{n-1}$ is checked.
- **Solution**: Traverse the full ancestry graph via `supersedes_claim_id` until reaching the root claim. Collect all `created_by` and `edited_by` values. Reject review action if `actor_uuid` is in the set.
- **Effort**: Small (2-3 hours).
- **Risk**: None. Enforces intended invariant.

#### Action P0-2: Enable REQ-44 Lifecycle Enforcement in Production Export and CLI
- **Action ID**: `ACT-P0-2`
- **Target Files**: `src/clindoc/services/fhir_export.py` (lines 743, 1029), `src/clindoc/cli.py` (line 244)
- **Problem**: `enforce_req44_lifecycle` defaults to `False` and is omitted by callers, leaving strict lifecycle rules inactive.
- **Solution**: Pass `enforce_req44_lifecycle=True` in both calls in `FHIRExportService` and in `clindoc.cli:process_document`. Alternatively, change default in `mapper.py` to `True`.
- **Effort**: Small (1 hour).
- **Risk**: Low. All 19 REQ-44 tests already pass with `True`.

#### Action P0-3: Implement Concurrency File Locking for ReviewAuditLedger
- **Action ID**: `ACT-P0-3`
- **Target File**: `src/clindoc/review/service.py` (`ReviewAuditLedger.append_event`)
- **Problem**: Concurrent JSONL appends cause sequence collisions and broken hash chains.
- **Solution**: Wrap read, sequence increment, hash calculation, and write within an exclusive file lock (`portalocker` or cross-platform lock).
- **Effort**: Small (2 hours).
- **Risk**: Low. Prevents audit log corruption under concurrent review.

---

### P1 — Prototype Completion & Defensibility Requirements

#### Action P1-1: Resolve the 5 Known Test Failures
- **Action ID**: `ACT-P1-1`
- **Target Files**: `tests/test_cli.py`, `tests/test_document_ingestion.py`, `tests/test_m2_v2_corrections.py`, `tests/test_section_detection_production.py`, `tests/test_vllm_deployment.py`
- **Problem**: 5 persistent test failures generate noise and obscure true regressions.
- **Solution**:
  - Update `test_cli.py` and `test_document_ingestion.py` to use `.unsupported_ext`.
  - Update `test_m2_v2_corrections.py` assertion to verify non-empty sets rather than 1:1 parity.
  - Update `test_section_detection_production.py` to assert non-extraction of demographic numbers.
  - Update `test_vllm_deployment.py` regex to check Python code assignments rather than error strings.
- **Effort**: Small (2-3 hours).
- **Acceptance Criteria**: 100% of tests pass (1,471 / 1,471).

#### Action P1-2: Align CLI Pipeline with Mode 0 Governance
- **Action ID**: `ACT-P1-2`
- **Target File**: `src/clindoc/cli.py`
- **Problem**: CLI exports final FHIR bundles directly from unreviewed claims, violating PD-002.
- **Solution**: Add `--draft` or `--unverified` flag requirement for CLI export, or route CLI document processing through a headless review session that records automated Mode 0 provenance before export. Clearly mark CLI bundles with an extension `http://clindoc.org/fhir/StructureDefinition/export-mode: unreviewed-cli-draft`.
- **Effort**: Medium (1 day).

#### Action P1-3: Integrate React Frontend into Automated CI Testing
- **Action ID**: `ACT-P1-3`
- **Target Files**: `frontend/`, `.github/workflows/` (or test runner)
- **Problem**: React Review Workbench is developed but not validated against FastAPI in CI.
- **Solution**: Add `npm run test` or Playwright end-to-end smoke test in test scripts.
- **Effort**: Medium (1-2 days).

---

### P2 — Important Architectural Improvements

- **Action P2-1**: Refactor `src/clindoc/extract/router.py` to make mock LLM fallback explicitly opt-in with loud logging rather than silent default.
- **Action P2-2**: Expand local LOINC subset mapping from 40 common codes to top 200 hospital laboratory codes.
- **Action P2-3**: Add formal HL7 Java Validator CLI verification script for sample exported bundles in CI.

---

### P3 — Optional / Future Clinical Deployment Work

- **Action P3-1**: Implement true bidirectional EHR write-back using SMART-on-FHIR / FHIR REST API.
- **Action P3-2**: Formalize n2c2 2018 Track 2 and MIMIC-IV-Note data access agreements for real-world clinical fine-tuning.
- **Action P3-3**: Replace single-tenant SQLite/PostgreSQL architecture with multi-tenant institutional partitioning.

---

## 19. Recommended Implementation Sequence

```
                                  REMEDIATION ROADMAP
                                  
  ┌────────────────────────────────────────────────────────────────────────┐
  │ PHASE 1: Security & Core Safety Hardening (P0 Blockers)                │
  │ Duration: 1-2 Days                                                     │
  │ Scope:                                                                 │
  │   - ACT-P0-1: Multi-hop Four-Eyes ancestry traversal (service.py)       │
  │   - ACT-P0-2: Activate enforce_req44_lifecycle=True in export & CLI    │
  │   - ACT-P0-3: File-locking on ReviewAuditLedger append operations       │
  │ Exit Criteria:                                                         │
  │   - Multi-hop edit sequences rejected when modified by prior editor    │
  │   - REQ-44 lifecycle active in exported FHIR bundles                    │
  │   - Zero audit ledger corruption under simulated concurrent appends    │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │ PHASE 2: Test Suite Clean-Up & CLI Alignment (P1 Requirements)         │
  │ Duration: 2-3 Days                                                     │
  │ Scope:                                                                 │
  │   - ACT-P1-1: Resolve all 5 baseline test failures                     │
  │   - ACT-P1-2: Tag CLI exports with draft/unreviewed metadata           │
  │   - ACT-P1-3: Add frontend automated smoke test                        │
  │ Exit Criteria:                                                         │
  │   - 1,471 / 1,471 tests passing (100% green build)                     │
  │   - Full baseline reproducibility confirmed                            │
  └───────────────────────────────────┬────────────────────────────────────┘
                                      ▼
  ┌────────────────────────────────────────────────────────────────────────┐
  │ PHASE 3: Pilot Demonstration & Package Finalization                    │
  │ Duration: 2-3 Days                                                     │
  │ Scope:                                                                 │
  │   - Turnkey Docker Compose demonstration profile (FastAPI + React)     │
  │   - Comprehensive demonstration walkthrough with sample clinical notes │
  │   - Final pilot documentation package                                  │
  │ Exit Criteria:                                                         │
  │   - Single-command demonstration boot                                  │
  │   - Defensible, complete research/pilot prototype                      │
  └────────────────────────────────────────────────────────────────────────┘
```

---

## 20. Final Project Completion Checklist

### Required for Research/Pilot Prototype Completion:
- [x] End-to-end processing of synthetic clinical documents (PDF/Image) to FHIR.
- [x] High-precision deterministic extraction for common lab and medication tables.
- [x] ModernBERT Stage B verifier trained and calibrated ($T=1.1465$).
- [x] REQ-44 transition-grounded medication regimen partitioning and duration disambiguation.
- [x] Two-tier FHIR provenance (resource and element-level evidence linking).
- [ ] Multi-hop Four-Eyes ancestry traversal enforced (P0: `ACT-P0-1`).
- [ ] REQ-44 lifecycle enforcement active in production export paths (P0: `ACT-P0-2`).
- [ ] Concurrent file locking on offline audit ledger (P0: `ACT-P0-3`).
- [ ] All 1,471 repository tests passing with zero failures (P1: `ACT-P1-1`).
- [ ] Turnkey local pilot demonstration script linking FastAPI and React UI.

### Deferred to Future Clinical Deployment (Out of Scope for Pilot):
- [ ] Hospital EHR bidirectional write-back.
- [ ] HIPAA / DPDPA administrative and physical certification.
- [ ] Real-patient clinical evaluation on n2c2 / MIMIC-IV-Note.
- [ ] Cloud HSM key management integration.
- [ ] Multi-tenant institutional database sharding.

---

## 21. Explicitly Deferred Work

1. **Autonomous EHR Write-Back**: All FHIR output is generated as file-based collection bundles; automated push to hospital EHR systems is deferred (PD-016, PD-021).
2. **Autonomous Mode 1 / Mode 2 Export**: Autonomous pre-acceptance without human review is prohibited in this milestone (PD-002).
3. **Full Microservices Decomposition**: The modular monolith architecture in FastAPI is preserved; distributed microservice mesh is deferred (PD-001).
4. **Live External Terminology Server Integration**: Full dynamic ontological querying against remote FHIR terminology servers (e.g. Ontoserver) is deferred in favor of local LOINC/UCUM curated tables (DEC-002, DEC-007).

---

## 22. Known Limitations

1. **Synthetic Data Foundation**: All precision, recall, and cascade accuracy numbers (including the $97.48\%$ OOD cascade accuracy) are measured on synthetic or semi-synthetic document generators. Clinical performance on uncurated, handwritten, or noisy real-world clinical records has not been established.
2. **Hardware Constraints on LLM Inference**: Full local execution of the Qwen2.5-7B-Instruct grammar-constrained LLM requires 16GB+ VRAM, limiting local execution on standard developer laptops to mock/heuristic backends unless connected to an external vLLM instance.
3. **Vocabulary Boundaries**: Terminology normalization covers 40 primary laboratory analytes and common prescription medications; unmapped esoteric tests fall back to verbatim text representation (`Observation.code.text`).

---

## 23. Audit Methodology and Verification Evidence

- **Workspace Inspection**: Verified working tree state via Git CLI (`git status`, `git rev-parse HEAD`, `git branch`).
- **Code Trace Analysis**: Manually traced call paths across `cli.py`, `api/routes/documents.py`, `jobs/worker.py`, `extract/router.py`, `review/service.py`, `services/fhir_export.py`, and `fhir/mapper.py`.
- **Test Suite Execution**: Executed the entire repository test suite (`pytest -q`), observing exactly 1,466 passing tests and 5 failing tests in 282.12s.
- **Failure Forensics**: Isolated and reproduced the exact failure reasons for all 5 failing tests via direct code inspection and targeted unit execution.
- **Benchmark Manifest Verification**: Verified document counts, claims, and split integrity across `external_eval/synthetic_benchmark` (12 docs), `external_eval/expanded_benchmark` (60 docs), and `experiments/FINAL_REPORT_DATA_SHEET.md` (120 docs).
