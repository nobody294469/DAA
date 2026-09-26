# Phase 0 Architecture Resolution

**Document ID:** PAR-001  
**Status:** APPROVED ARCHITECTURAL SPECIFICATION & RESOLUTION  
**Version:** 1.0  
**Authority Hierarchy:**  
1. `PROJECT_SPEC.md` v1.0 (Master Architectural Specification)  
2. `PHASE0_DECISION_RESOLUTION_PACKAGE.md` (Phase 0 Decision Package)  
3. `PHASE0_ARCHITECTURE_RESOLUTION.md` (This Document — Formal Audit Resolution & Technical Bridge)  
4. Existing repository code, data, and tests (Evidence of current implementation state, non-authoritative)

---

## 1. Purpose

This document formally and unambiguously resolves the twenty findings (`AUDIT-01` through `AUDIT-20`) identified in the Phase 0 Consistency & Architecture Audit. 

In a document-driven engineering project, implementation code must never drift from architectural intent, nor may implementing agents make autonomous assumptions when facing specification gaps, vocabulary discrepancies, or missing pipeline stages. This document acts as the definitive binding contract that closes every gap, reconciles every discrepancy between specification and implementation, and defines the exact data models, contracts, and policies required before Phase 1 implementation begins.

---

## 2. Resolution Principles

1. **Document-Driven Primacy:** No line of production code, test harness, or dataset generation logic shall be written without tracing to an approved specification requirement or decision record.
2. **Zero Silent Architectural Inventions:** If a requirement is underspecified or an interface is ambiguous, implementation stops until the issue is formally documented, evaluated, and resolved.
3. **Specification as Master Authority:** Where existing code contradicts `PROJECT_SPEC.md`, the code is considered legacy technical debt requiring remediation, unless `PROJECT_SPEC.md` is explicitly amended via a documented decision.
4. **Empirical Openness vs. Policy Locking:** Architectural policies (grouping keys, field vocabularies, mapping rules, metric definitions) are locked immediately. Statistical and empirical parameters (classifier thresholds, model selection, calibration validation) remain explicitly open pending their designated experiment phases.
5. **Implementation State is Evidence, Not Law:** The presence of legacy files (such as M0 schemas or interim M2 v1 scripts) does not constitute architectural justification to bypass the master specification.
6. **No Premature Implementation:** Scaffolding, documentation, and schema definitions precede feature implementation. Phase gates are strictly enforced.

---

## 3. Audit Disposition Table

| Audit ID | Severity | Disposition | Final Resolution Summary | New Decision Record? |
|---|---|---|---|---|
| **AUDIT-01** | BLOCKER | ACCEPT WITH MODIFICATION | Synthesize a targeted 20-document narrative clinical note benchmark containing prose HPI/Plans with embedded lab/med ground-truth claims for E3/E4. | YES (`DEC-014`) |
| **AUDIT-02** | BLOCKER | ACCEPT | Add `group_id: str \| None` to `Claim` schema (§8) to associate atomic claims belonging to the same clinical entity instance. | YES (`DEC-015`) |
| **AUDIT-03** | BLOCKER | ACCEPT WITH MODIFICATION | Establish a two-tier provenance model: resource-level provenance links primary entity claim, and element-level sub-extensions link atomic field claims. | YES (`DEC-016`) |
| **AUDIT-04** | BLOCKER | ACCEPT WITH MODIFICATION | Standardize on the 10 canonical field types in §8.2; map `lab.ref_range` → `lab.reference_range`; deprecate obsolete types; resize `AUX_DIM` to 18. | YES (`DEC-017`) |
| **AUDIT-05** | BLOCKER | ACCEPT | Amend `src/clindoc/m2/splits.py` to generate a 3-way split (`train`, `calib`, `val`) within CV documents, preserving OOD isolation per §19.5. | NO (Executes §19.5) |
| **AUDIT-06** | BLOCKER | ACCEPT | Implement standard multi-class `TemperatureScaler` in `src/clindoc/m2/calibration.py` to fit scalar $T$ on CV calibration logits. | NO (Executes DR-9) |
| **AUDIT-07** | HIGH | ACCEPT WITH MODIFICATION | Clarify that Stage C LLM calibration uses token logprobs over the 3 label tokens (when serving locally) or 1D isotonic regression on self-reported scalar. | YES (`DEC-018`) |
| **AUDIT-08** | HIGH | ACCEPT WITH MODIFICATION | Reconcile field verification tiers: 6 complex fields receive learned Stage B/C verification; 4 deterministic fields are verified at Stage A. | YES (`DEC-019`) |
| **AUDIT-09** | HIGH | ACCEPT | In Phase 1 dataset regeneration, generate `Evidence` with character offsets and DR-3 context windows (paragraph-bounded 500 chars / fallback ±250 chars). | NO (Executes DR-3) |
| **AUDIT-10** | HIGH | ACCEPT | Refactor FHIR mapper in Phase 5 to map directly from `(Claim, VerificationResult, Evidence)` tuples using standard 3-class labels. | NO (Executes Phase 5) |
| **AUDIT-11** | HIGH | ACCEPT | Create `n2c2_access/REQUEST_STATUS.md` immediately in Phase 0 and initiate the research data use agreement. | NO (Action DR-10) |
| **AUDIT-12** | MEDIUM | ACCEPT | Establish required scaffolding directories (`schemas/`, `docs/decisions/`, `experiments/`, `benchmark/`) as initial Phase 0 setup. | NO (Executes §0.2) |
| **AUDIT-13** | MEDIUM | ACCEPT | Implement `ExtractionRouter` in `src/clindoc/extract/router.py` during Phase 2 conforming to DR-1 and DR-4. | NO (Phase 2 milestone) |
| **AUDIT-14** | MEDIUM | ACCEPT | Implement hybrid terminology retrieval (SapBERT + BM25) for LOINC and RxNorm in Phase 3 conforming to DR-2 and DR-7. | NO (Phase 3 milestone) |
| **AUDIT-15** | MEDIUM | ACCEPT | Implement automated assertion `assert_class_balance()` in dataset generation pipeline enforcing DR-11 in CI. | NO (Executes DR-11) |
| **AUDIT-16** | MEDIUM | ACCEPT | Implement atomic `VerificationResult` schema in `src/clindoc/schema/verification.py` per PROJECT_SPEC §10. | NO (Executes §10) |
| **AUDIT-17** | MEDIUM | ACCEPT | Implement `span_to_bbox()` utility in `src/clindoc/ocr/geometry.py` to map page character offsets to enclosing bounding boxes. | NO (Utility task) |
| **AUDIT-18** | LOW | ACCEPT | Move all legacy M2 v1 checkpoints, recovered headline JSONs, and test outputs to `data/m2_results_v1_legacy/` to eliminate contamination. | NO (Data hygiene) |
| **AUDIT-19** | LOW | ACCEPT WITH MODIFICATION | Add explicit `contradiction_type` tagging from §20.1 taxonomy to all synthetic discrepancy generators. | NO (Executes §20.1) |
| **AUDIT-20** | LOW | ACCEPT | Amend §2.1 and §8.2 to explicitly document that narrative extraction is strictly confined to medication regimens and laboratory findings in prose. | NO (Doc amendment) |

---

## 4. Detailed Resolutions

### AUDIT-01 — Absence of Narrative Prose Documents in Synthetic Benchmark
- **Observed state:** The benchmark contains 100 tabular documents (50 lab reports, 50 prescription/MAR forms). The `discharge_summary` template is rendered as a tabular reconciliation grid. There are zero narrative prose documents.
- **Relevant specification requirement:** PROJECT_SPEC §1.2, §2.1, §7.4, §11, §18.1, §22.1 (E3, E4).
- **Audit recommendation:** Create a dedicated synthetic narrative document generator and renderer.
- **Final disposition:** ACCEPT WITH MODIFICATION.
- **Final architectural resolution:** To avoid scope creep and overbuilding an open-domain clinical NLP generator, the project will generate a focused suite of **20 synthetic narrative discharge summaries** (15 CV, 5 OOD) in Phase 1. These documents will feature realistic clinical prose sections (History of Present Illness, Hospital Course, Discharge Plan) synthesized from structured patient profiles, containing explicitly grounded medication orders and lab mentions. Ground-truth character offsets for all mentions will be preserved.
- **Required PROJECT_SPEC change:** Amend §18.1 to specify the exact composition of the synthetic core benchmark: 50 lab reports, 50 prescription documents, and 20 narrative discharge notes (120 total documents).
- **Required implementation change:** Implement `src/clindoc/synth/narrative_generator.py` and `narrative_renderer.py` in Phase 1.
- **Required tests/experiments:** Ground-truth string alignment tests; E3 (LLM-only narrative extraction) and E4 (hybrid router) in Phase 2.
- **Decision record:** `DEC-014-narrative-benchmark-synthesis.md`.

---

### AUDIT-02 — Missing Grouping Identifier in Claim Schema
- **Observed state:** `Claim` schema (§8) is strictly flat and atomic. It contains `claim_id`, `document_id`, `field_type`, `value`, `evidence_id`, but lacks any entity-instance grouping key.
- **Relevant specification requirement:** PROJECT_SPEC §8, §15.1, §16.
- **Audit recommendation:** Add `group_id: str` to `Claim` schema.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** `Claim` schema is amended to include `group_id: str | None`. For structured tabular rows (e.g., a single lab test row with value, unit, flag, reference range) and medication orders (drug name, dose, route, frequency, duration), extraction components MUST assign an identical UUID or region-derived entity key to `group_id` for all atomic claims belonging to that single clinical entity instance. For orphan or isolated claims, `group_id` is `null`.
- **Required PROJECT_SPEC change:** Add `"group_id": "string (uuid) or null"` to §8.1.
- **Required implementation change:** Update `M2Example` and `Claim` Pydantic models to include `group_id`. Update extractors to assign `group_id`.
- **Required tests/experiments:** Unit test verifying multi-order documents (e.g. Metformin 500mg BID and Lisinopril 10mg QD) produce distinct `group_id` clusters.
- **Decision record:** `DEC-015-claim-entity-grouping.md`.

---

### AUDIT-03 — Incompatible 1-to-1 Provenance Extension Model vs. Composite FHIR Resources
- **Observed state:** PROJECT_SPEC §16 defines a single provenance extension block per resource instance assuming a single `claimId` and `verificationId`. FHIR `Observation` and `MedicationStatement` aggregate multiple claims that may have divergent verification verdicts.
- **Relevant specification requirement:** PROJECT_SPEC §4.2, §15, §16.
- **Audit recommendation:** Establish hierarchical sub-extension mapping for composite resources.
- **Final disposition:** ACCEPT WITH MODIFICATION.
- **Final architectural resolution:** Establish a clear two-tier provenance model:
  1. **Resource-Level Provenance:** The top-level FHIR resource carries the primary entity provenance linking to the anchor claim (e.g., `lab.value` for `Observation`, `med.name` for `MedicationStatement`) and the summary verification verdict.
  2. **Element-Level Sub-Extensions:** Constituent elements (`Observation.valueQuantity`, `Observation.interpretation`, `Observation.referenceRange`, `MedicationStatement.dosage[0]`) carry dedicated element-level provenance extensions referencing their specific atomic `claimId`, `verificationId`, `verificationLabel`, and calibrated `confidence`.
- **Required PROJECT_SPEC change:** Update §16 with the formal two-tier extension schema.
- **Required implementation change:** Update `src/clindoc/fhir/mapper.py` to generate element-level provenance extensions alongside resource-level provenance.
- **Required tests/experiments:** Provenance completeness test verifying every constituent field's extension resolves to its atomic claim and verification record.
- **Decision record:** `DEC-016-fhir-provenance-hierarchy.md`.

---

### AUDIT-04 — Field Type Vocabulary Mismatch & Architecture Dimension Collision
- **Observed state:** PROJECT_SPEC §8.2 defines 10 field types. `src/clindoc/m2/schema.py` defines 13 field types (including `lab.ref_range`, `lab.loinc_code`, `med.dose_unit`, `med.quantity`). `src/clindoc/m2/features.py` hardcodes `AUX_DIM = 21`.
- **Relevant specification requirement:** PROJECT_SPEC §8.2.
- **Audit recommendation:** Standardize on 10 canonical field types and resize `AUX_DIM`.
- **Final disposition:** ACCEPT WITH MODIFICATION.
- **Final architectural resolution:** 
  1. The canonical field vocabulary is locked to exactly the **10 field types** defined in PROJECT_SPEC §8.2:
     `lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`, `lab.test_name`, `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`.
  2. Code identifier `lab.ref_range` is renamed to canonical `lab.reference_range`.
  3. Obsolete types `lab.loinc_code`, `med.dose_unit`, and `med.quantity` are deprecated and removed from the active schema (LOINC codes belong to the terminology normalizer output, not raw claim extraction; units are handled by `lab.unit` and numeric parsing).
  4. Auxiliary feature encoding in `src/clindoc/m2/features.py` is updated:
     $$\text{AUX\_DIM} = 1 \text{ (OCR conf)} + 10 \text{ (field types)} + 7 \text{ (doc templates)} = 18$$
     $$\text{FUSION\_DIM} = 768 + 64 = 832 \text{ (unchanged)}$$
- **Required PROJECT_SPEC change:** Explicitly reaffirm §8.2 as the exhaustive list of 10 field types.
- **Required implementation change:** Update `FieldType` enum in `src/clindoc/m2/schema.py` and `src/clindoc/schema/`. Update `AUX_DIM` in `features.py`.
- **Required tests/experiments:** Auxiliary feature batch encoding shape test (`assert feat.shape[-1] == 18`).
- **Decision record:** `DEC-017-canonical-field-vocabulary.md`.

---

### AUDIT-05 — Missing 3-Way Calibration Split in CV Manifest
- **Observed state:** `src/clindoc/m2/splits.py` creates a 2-way split (`train_doc_ids`, `val_doc_ids`) per CV fold.
- **Relevant specification requirement:** PROJECT_SPEC §17.2, §19.5; DR-9.
- **Audit recommendation:** Update `splits.py` to generate a 3-way split per fold.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** `src/clindoc/m2/splits.py` is updated so each fold within the 71 CV documents generates three mutually disjoint document sets:
  - `train_doc_ids` (60% of CV documents, ~42 docs)
  - `calib_doc_ids` (20% of CV documents, ~14 docs, used strictly for fitting post-hoc calibration)
  - `val_doc_ids` (20% of CV documents, ~15 docs, used strictly for model selection and threshold tuning)
  The 29 OOD documents remain strictly held out and never participate in train, calib, or val.
- **Required PROJECT_SPEC change:** None (§19.5 already mandates this).
- **Required implementation change:** Update `build_splits()` in `src/clindoc/m2/splits.py`.
- **Required tests/experiments:** Split disjointness assertion test:
  $$\text{train} \cap \text{calib} = \emptyset, \quad \text{train} \cap \text{val} = \emptyset, \quad \text{calib} \cap \text{val} = \emptyset, \quad (\text{train} \cup \text{calib} \cup \text{val}) \cap \text{OOD} = \emptyset$$
- **Decision record:** None (Direct compliance with §19.5).

---

### AUDIT-06 — Complete Absence of Calibration Fitting Implementation
- **Observed state:** `src/clindoc/m2/calibration.py` contains only 4 lines importing evaluation metrics (`ece`, `reliability_data`). No fitting algorithm exists.
- **Relevant specification requirement:** PROJECT_SPEC §5 #12, §10, §17; DR-9.
- **Audit recommendation:** Implement `TemperatureScaler`.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Implement a PyTorch-based `TemperatureScaler` in `src/clindoc/m2/calibration.py`. It optimizes a single scalar parameter $T > 0$ via L-BFGS or Adam to minimize cross-entropy loss on the unnormalized logits of the `calib_doc_ids` split:
  $$\min_{T} -\sum_{k} \log \left( \frac{e^{z_k[y_k] / T}}{\sum_j e^{z_k[j] / T}} \right)$$
  The scaler exposes `.fit(logits, labels)`, `.transform(logits) -> probs`, and `.save(path) / .load(path)` to persist calibration parameters alongside model checkpoints.
- **Required PROJECT_SPEC change:** None (Executes DR-9).
- **Required implementation change:** Implement `TemperatureScaler` class and integrate it into `run_training()` / `evaluate_model()`.
- **Required tests/experiments:** Unit test verifying temperature scaling decreases negative log-likelihood and ECE on validation logits without changing classification accuracy (monotonicity preservation).
- **Decision record:** None (Executes DR-9).

---

### AUDIT-07 — Mathematical Ambiguity in LLM Stage-C Confidence Calibration
- **Observed state:** PROJECT_SPEC §12 states Stage C confidence is "passed through the SAME calibration pipeline as Stage B". Stage B uses multi-class logit scaling, whereas an LLM generating JSON outputs a scalar confidence float.
- **Relevant specification requirement:** PROJECT_SPEC §12, §17.1; DR-9.
- **Audit recommendation:** Specify token logprob extraction or 1D post-hoc calibration.
- **Final disposition:** ACCEPT WITH MODIFICATION.
- **Final architectural resolution:** Formally define the dual-mode calibration interface for Stage C:
  - **Primary Mode (Local vLLM Serving):** Stage C prompts the LLM for JSON with grammar constraints (DR-13). For the `label` field token, unnormalized log-probabilities for the three candidate tokens (`SUPPORTED`, `DISCREPANCY`, `INSUFFICIENT_EVIDENCE`) are extracted directly from the engine. These 3 logits are calibrated using an LLM-specific temperature scalar $T_{\text{LLM}}$ fit on calibration split examples.
  - **Fallback Mode (Hosted API / Non-Logit Serving):** If token logprobs are unavailable, the model self-reports confidence $c \in [0, 1]$, which is calibrated using a 1D Isotonic Regression or Beta Calibration model fit on the calibration split.
- **Required PROJECT_SPEC change:** Update §12 and §17.1 to explicitly document Primary (Logit $T_{\text{LLM}}$) and Fallback (1D Isotonic) mechanisms for Stage C.
- **Required implementation change:** Implement `LLMConfidenceCalibrator` supporting both logit temperature scaling and 1D isotonic calibration.
- **Required tests/experiments:** Calibration test on simulated LLM outputs in Phase 0/1.
- **Decision record:** `DEC-018-stage-c-confidence-calibration.md`.

---

### AUDIT-08 — Incomplete Field-Type Coverage in M2 v2 Dataset vs. DR-11
- **Observed state:** M2 v2 dataset covers 6 field types. 4 field types (`lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`) have zero examples. `INSUFFICIENT_EVIDENCE` is present in only 2 of 6 fields.
- **Relevant specification requirement:** PROJECT_SPEC §8.2, §18.4; DR-11.
- **Audit recommendation:** Formally determine whether all 10 fields receive learned verification or if deterministic fields are verified at Stage A.
- **Final disposition:** ACCEPT WITH MODIFICATION.
- **Final architectural resolution:** Establish an explicit two-tier verification architecture:
  1. **Tier 1 (Learned Verification — Stages A, B, C):** Fields with high semantic variability, numeric thresholds, or critical clinical sensitivity (`lab.value`, `lab.flag`, `med.name`, `med.dose`, `med.frequency`, `med.route`). These 6 fields are trained and evaluated in M2 ModernBERT and the full verification cascade.
  2. **Tier 2 (Deterministic Verification — Stage A Only):** Fields governed by exact string matching, standardized unit tables (UCUM), or literal interval tokens (`lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`). These 4 fields are resolved definitively at Stage A using rule-based comparison. They bypass Stage B unless a normalization ambiguity exists.
  3. **INSUFFICIENT_EVIDENCE Policy:** Reaffirm DR-11. For Tier 1 fields, `INSUFFICIENT_EVIDENCE` is required where semantically meaningful (unobserved tests, unprescribed medications, OCR-destroyed values/doses). It is not synthetically forced onto closed categorical flags where "missing" collapses to unobserved values.
- **Required PROJECT_SPEC change:** Amend §12 to formally document Tier 1 (Learned Cascade) and Tier 2 (Stage A Deterministic) field partitioning.
- **Required implementation change:** Update verification routing logic in `src/clindoc/verify/` to route Tier 2 fields directly to Stage A.
- **Required tests/experiments:** Unit tests verifying Stage A correctly evaluates Tier 2 fields without invoking ModernBERT.
- **Decision record:** `DEC-019-verification-field-tiering.md`.

---

### AUDIT-09 — Disconnect Between DR-3 Context Window and Current Evidence Representation
- **Observed state:** M2 v2 dataset stores isolated single-line text strings without character offsets or DR-3 context windows.
- **Relevant specification requirement:** DR-3; PROJECT_SPEC §7.6, §9.
- **Audit recommendation:** In Phase 1 dataset regeneration, generate `Evidence` with character offsets and DR-3 context windows.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** During Phase 1 dataset regeneration, all examples will be generated with full `Evidence` records (§9) containing `char_start`, `char_end`, `page_number`, and `bbox`. The input evidence passed to verification models will be extracted using the canonical DR-3 algorithm: paragraph-bounded window up to 500 characters, falling back to a fixed ±250 character window around `[char_start, char_end]` for dense tabular text.
- **Required PROJECT_SPEC change:** None (Implements DR-3 and §9).
- **Required implementation change:** Update `src/clindoc/m2/generation.py` to extract DR-3 context windows from OCR page text.
- **Required tests/experiments:** Verification context window ablation on negation/temporal subsets prior to Phase 4 training.
- **Decision record:** None (Executes DR-3).

---

### AUDIT-10 — Incompatible FHIR Mapper & Legacy Verification Status Schema
- **Observed state:** `src/clindoc/fhir/mapper.py` uses legacy M0 status codes (`VERIFIED`, etc.) and custom local URIs. It maps only lab reports and lacks medication mapping.
- **Relevant specification requirement:** PROJECT_SPEC §15, §16; DR-8.
- **Audit recommendation:** Refactor FHIR mapper in Phase 5.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** In Phase 5, `src/clindoc/fhir/mapper.py` will be completely refactored to consume the canonical data model: `Claim[] + VerificationResult[] + Evidence[]`. It will implement:
  - Lab mapping to `DiagnosticReport` and `Observation`.
  - Medication mapping to `MedicationStatement` and `MedicationRequest` using DR-8's hybrid rule.
  - Standard FHIR R4B provenance extensions using the canonical URLs defined in Section 11 of this document.
  Legacy M0 schemas in `src/clindoc/verify/schema.py` will be deprecated.
- **Required PROJECT_SPEC change:** None (Phase 5 milestone).
- **Required implementation change:** Rewrite `src/clindoc/fhir/mapper.py` during Phase 5.
- **Required tests/experiments:** 100% FHIR schema validity test in CI across all benchmark documents.
- **Decision record:** None (Executes Phase 5).

---

### AUDIT-11 — Missing Real-Data Access Log & Action Item
- **Observed state:** No `n2c2_access/` directory or `REQUEST_STATUS.md` file exists in the repository.
- **Relevant specification requirement:** PROJECT_SPEC §18.3, §25 Phase 0; DR-10.
- **Audit recommendation:** Create `n2c2_access/REQUEST_STATUS.md` immediately in Phase 0.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Create directory `n2c2_access/` and file `n2c2_access/REQUEST_STATUS.md` in Phase 0. Document the application date, institutional sign-off status, and DUA approval milestones for the n2c2 2018 Track 2 (Medication / ADE) dataset.
- **Required PROJECT_SPEC change:** None (Action item from DR-10).
- **Required implementation change:** Add tracking file to version control.
- **Required tests/experiments:** None.
- **Decision record:** None (Executes DR-10).

---

### AUDIT-12 — Absence of Scaffolding Directories Required by §0.2
- **Observed state:** Directories `schemas/`, `docs/decisions/`, `experiments/`, and `benchmark/` do not exist.
- **Relevant specification requirement:** PROJECT_SPEC §0.2, §25 Phase 0.
- **Audit recommendation:** Create scaffolding directories.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Create the four scaffolding directories as part of Phase 0 setup:
  - `schemas/` (stores JSON Schema definitions for contracts §7–§10)
  - `docs/decisions/` (stores formal decision records `DEC-001` through `DEC-019`)
  - `experiments/` (stores per-experiment configs and `REPORT.md` files)
  - `benchmark/` (stores benchmark configs, split manifests, and evaluation data)
- **Required PROJECT_SPEC change:** None.
- **Required implementation change:** Create directories and `.gitkeep` files.
- **Required tests/experiments:** CI skeleton check verifying directories exist.
- **Decision record:** None (Executes §0.2).

---

### AUDIT-13 — Absence of Extraction Router & GLiNER Pipeline Integration
- **Observed state:** Parsers exist as isolated scripts without routing coordination or region classification.
- **Relevant specification requirement:** PROJECT_SPEC §5 #2, §5 #4, §11; DR-1, DR-4.
- **Audit recommendation:** Implement `ExtractionRouter` in Phase 2.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Planned Phase 2 deliverable. In Phase 2, `src/clindoc/extract/router.py` will implement rule-based region classification (regex triggers for regimens + column alignment for tables per DR-1), coordinate deterministic vs GLiNER vs LLM extraction, and log `RoutingDecision` records (§7.2).
- **Required PROJECT_SPEC change:** None (Phase 2 milestone).
- **Required implementation change:** Implement `ExtractionRouter` in Phase 2.
- **Required tests/experiments:** E1–E4 extraction benchmark suite.
- **Decision record:** None (Executes DR-1, DR-4).

---

### AUDIT-14 — Absence of Terminology Hybrid Retrieval System
- **Observed state:** No retrieval or normalization code exists in `src/clindoc/`.
- **Relevant specification requirement:** PROJECT_SPEC §5 #6, §14; DR-2, DR-7.
- **Audit recommendation:** Implement terminology retrieval in Phase 3.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Planned Phase 3 deliverable. In Phase 3, implement `src/clindoc/normalize/` with SapBERT semantic embeddings + BM25 lexical search over LOINC and RxNorm tables, with deterministic UCUM lookup table.
- **Required PROJECT_SPEC change:** None (Phase 3 milestone).
- **Required implementation change:** Build normalization module in Phase 3.
- **Required tests/experiments:** Terminology retrieval top-1/top-5 benchmark (§21.6).
- **Decision record:** None (Executes DR-2, DR-7).

---

### AUDIT-15 — Absence of Automated CI Balance Check Script
- **Observed state:** No automated script checks DR-11 floors on regenerated datasets in CI.
- **Relevant specification requirement:** PROJECT_SPEC §18.4, §24; DR-11.
- **Audit recommendation:** Implement `assert_class_balance()` in dataset generation.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Implement `assert_class_balance(metadata, config_path)` in `src/clindoc/m2/dataset.py`. The function reads `configs/class_balance_floor.yaml` (DR-11) and asserts that for every active verification field type:
  $$\text{count}(\text{SUPPORTED}) \ge \max(0.10 \times N_{\text{field}}, 30)$$
  $$\text{count}(\text{DISCREPANCY}) \ge \max(0.10 \times N_{\text{field}}, 30)$$
  $$\text{count}(\text{INSUFFICIENT\_EVIDENCE}) \ge 15 \quad (\text{where semantically applicable})$$
  This check will be integrated directly into `step_generate_dataset()` and executed as a mandatory CI test.
- **Required PROJECT_SPEC change:** None.
- **Required implementation change:** Add validation function in `dataset.py` and test in `tests/test_m2_dataset.py`.
- **Required tests/experiments:** CI test asserting balance check passes on regenerated dataset.
- **Decision record:** None (Executes DR-11).

---

### AUDIT-16 — Verification Result Schema Mismatch (`M2EvalResult` vs. `VerificationResult`)
- **Observed state:** Code defines only aggregate eval metrics (`M2EvalResult`). Atomic `VerificationResult` schema (§10) is missing.
- **Relevant specification requirement:** PROJECT_SPEC §10.
- **Audit recommendation:** Implement atomic `VerificationResult` schema.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Implement `VerificationResult` Pydantic model in `src/clindoc/schema/verification.py` strictly matching PROJECT_SPEC §10:
  `verification_id`, `claim_id`, `label`, `confidence` (calibrated), `stage_reached`, `stage_a_label`, `stage_b_label`, `stage_b_raw_confidence`, `contradiction_type`, `normalized_code_agreement`, `rationale`, `evidence_id`, `escalated`.
- **Required PROJECT_SPEC change:** None.
- **Required implementation change:** Add schema file and schema validation in `schemas/verification_result.json`.
- **Required tests/experiments:** Schema validation round-trip test.
- **Decision record:** None (Executes §10).

---

### AUDIT-17 — Evidence Character-Offset to Bounding Box Mapping Utility Missing
- **Observed state:** OCR output has token bboxes, but no utility aggregates character offsets `[char_start, char_end]` to an enclosing bounding box.
- **Relevant specification requirement:** PROJECT_SPEC §6 #4, §9; DR-12.
- **Audit recommendation:** Implement `span_to_bbox()` utility.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** Implement `span_to_bbox(tokens: list[DocumentToken], char_start: int, char_end: int) -> BoundingBox` in `src/clindoc/ocr/geometry.py`. The utility finds all tokens whose character spans overlap `[char_start, char_end]` on the specified page and returns the minimum bounding rectangle:
  $$x_0 = \min_i(x_{0, i}), \quad y_0 = \min_i(y_{0, i}), \quad x_1 = \max_i(x_{1, i}), \quad y_1 = \max_i(y_{1, i})$$
- **Required PROJECT_SPEC change:** None.
- **Required implementation change:** Add geometric utility function.
- **Required tests/experiments:** Unit test with known token coordinates verifying exact bounding box calculation.
- **Decision record:** None.

---

### AUDIT-18 — Preserving M2 v1 Checkpoints vs. Retraining V6 on Corrected V2
- **Observed state:** `data/m2_results/` contains 30 recovered headline runs and 15 checkpoints trained on flawed v1 data.
- **Relevant specification requirement:** PROJECT_SPEC §22.2 (V6), §26 Phase 4.
- **Audit recommendation:** Move legacy v1 results to `data/m2_results_v1_legacy/`.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** To prevent stale v1 checkpoints or headline evaluations from contaminating the official experiment record, `data/m2_results/` will be archived to `data/m2_results_v1_legacy/` before Phase 4 training begins. Official V6/V7 training sweeps will output cleanly to `data/m2_results_v2/` or `experiments/V6/` and `experiments/V7/`.
- **Required PROJECT_SPEC change:** None.
- **Required implementation change:** Directory relocation before Phase 4.
- **Required tests/experiments:** Resume-state tests confirming pipeline starts fresh on v2.
- **Decision record:** None (Operational hygiene).

---

### AUDIT-19 — Unanchored Non-Numerical Discrepancy Generation
- **Observed state:** Synthetic discrepancies in `generation.py` are generated by substitution but not explicitly tagged with `contradiction_type`.
- **Relevant specification requirement:** PROJECT_SPEC §18.5, §20.1.
- **Audit recommendation:** Add explicit `contradiction_type` tagging.
- **Final disposition:** ACCEPT WITH MODIFICATION.
- **Final architectural resolution:** In `src/clindoc/m2/generation.py`, every generated `DISCREPANCY` example will be explicitly assigned a `contradiction_type` from the §20.1 taxonomy:
  - `lab.value` alteration $\rightarrow$ `value_mismatch`
  - `lab.flag` alteration $\rightarrow$ `value_mismatch` (or categorical flag mismatch)
  - `med.dose` numeric change $\rightarrow$ `dose_mismatch`
  - `med.name` substitution $\rightarrow$ `wrong_entity`
  - `med.frequency` substitution $\rightarrow$ `value_mismatch`
  - `med.route` substitution $\rightarrow$ `value_mismatch`
  - `lab.unit` swap $\rightarrow$ `unit_mismatch`
- **Required PROJECT_SPEC change:** None.
- **Required implementation change:** Add `contradiction_type: str | None` field to `M2Example` and populate it in generator.
- **Required tests/experiments:** Assertion verifying 100% of discrepancy examples have non-null `contradiction_type`.
- **Decision record:** None (Executes §20.1).

---

### AUDIT-20 — Missing Narrative Scope Clarification in Controlled Vocabulary
- **Observed state:** Controlled vocabulary contains only lab and medication fields. Scope of narrative extraction is not explicitly bounded.
- **Relevant specification requirement:** PROJECT_SPEC §2.1, §8.2.
- **Audit recommendation:** Add explicit clarification note to §2.1 and §8.2.
- **Final disposition:** ACCEPT.
- **Final architectural resolution:** PROJECT_SPEC §2.1 and §8.2 will be formally amended to clarify:
  *"The LLM narrative extractor's operational scope is strictly bounded by the controlled claim vocabulary (§8.2) and target FHIR resources (§15.1). In narrative clinical notes, it extracts ONLY medication regimens (drug name, dose, route, frequency, duration) and laboratory observations mentioned in clinical prose. Extraction of diagnoses, conditions, surgical procedures, or family history is explicitly out of scope for the core pipeline."*
- **Required PROJECT_SPEC change:** Add explicit boundary note to §2.1, §7.4, and §8.2.
- **Required implementation change:** Prompt template in Phase 2 strictly constrains output schema to `med.*` and `lab.*` fields.
- **Required tests/experiments:** Negative test verifying non-med/non-lab clinical statements (e.g. "Patient has hypertension") are not emitted as claims.
- **Decision record:** None (Documentation amendment).

---

## 5. Canonical Data Model Resolution

The system establishes an unambiguous relational data flow from raw document to FHIR bundle:

```
[Clinical Document (PDF/Image)]
       │ (Ingestion & OCR: PyMuPDF / PaddleOCR)
       ▼
[DocumentText (§7.1)] ── Page[] ── Token[] (text, bbox, char offsets)
       │ (Extraction Router: §11)
       ▼
[Evidence Store (§9)] ◄─────────────────────────┐ (Grounding Reference: evidence_id)
  - evidence_id (UUID)                           │
  - document_id                                  │
  - page_number                                  │
  - text (verbatim snippet)                      │
  - char_start, char_end                         │
  - bbox [x0, y0, x1, y1]                        │
  - ocr_confidence                               │
       │                                         │
       ▼                                         │
[Claim Store (§8)]                               │
  - claim_id (UUID, primary key)                 │
  - document_id                                  │
  - group_id (UUID, links composite entity) ────┼────────┐
  - field_type (canonical 10-type vocabulary)    │        │
  - value (raw string)                           │        │
  - value_numeric (float or null)                │        │
  - unit_raw (string or null)                    │        │
  - evidence_id (foreign key) ───────────────────┘        │
  - normalized_code, code_system, code_conf               │
       │                                                  │
       ▼ (Verification Cascade: Stage A → B → C)          │
[VerificationResult Store (§10)]                          │
  - verification_id (UUID, primary key)                   │
  - claim_id (foreign key)                                │
  - label (SUPPORTED | DISCREPANCY | INSUFFICIENT)        │
  - confidence (calibrated float 0-1)                     │
  - stage_reached (A | B | C)                             │
  - contradiction_type (if DISCREPANCY)                   │
  - rationale (if Stage C)                                │
       │                                                  │
       ▼                                                  │
[FHIR R4B Generation (§15, §16)]                          │
  - Grouped by group_id ──────────────────────────────────┘
  - Primary Entity Claim → Resource-level Provenance
  - Constituent Field Claims → Element-level Provenance Sub-extensions
```

### Identifier Invariants
- `claim_id`: Unique per extracted atomic field claim.
- `group_id`: Identical across all atomic claims originating from the same clinical entity row or order (e.g., test name + value + unit + ref range + flag share one `group_id`; drug name + dose + route + freq share one `group_id`).
- `evidence_id`: Deduplicated by `(document_id, page_number, char_start, char_end)`.
- `verification_id`: Exactly 1-to-1 with `claim_id`.

---

## 6. Canonical Field Vocabulary

The canonical field vocabulary is locked to **exactly 10 field types**. No other field types may be produced by extractors or consumed by downstream verifiers.

| Field Type | Target Clinical Element | Verification Tier | Stage A Rule | Stage B (ModernBERT) | Stage C (LLM Judge) | Target FHIR Mapping |
|---|---|---|---|---|---|---|
| `lab.value` | Quantitative lab test result | Tier 1 (Learned) | Numeric comparison post-UCUM | Active | Active on escalation | `Observation.valueQuantity.value` |
| `lab.unit` | Unit of measure | Tier 2 (Deterministic) | Deterministic UCUM lookup | Bypass (default) | Escalation only | `Observation.valueQuantity.unit` |
| `lab.flag` | Interpretation flag (NORMAL/HIGH/LOW) | Tier 1 (Learned) | Exact token match | Active | Active on escalation | `Observation.interpretation` |
| `lab.reference_range` | Reference interval text/bounds | Tier 2 (Deterministic) | Interval parsing & bounds match | Bypass (default) | Escalation only | `Observation.referenceRange` |
| `lab.test_name` | Analyte or panel name | Tier 2 (Deterministic) | Lexical match / Terminology check | Active (semantic) | Active on escalation | `Observation.code.text` |
| `med.name` | Active ingredient or brand name | Tier 1 (Learned) | Bypass to B | Active (semantic) | Active on escalation | `Medication*.code.text` |
| `med.dose` | Quantity of drug per administration | Tier 1 (Learned) | Numeric comparison | Active | Active on escalation | `Medication*.dosage[0].doseQuantity.value` |
| `med.route` | Route of administration (oral, IV, etc.) | Tier 1 (Learned) | Normalized vocabulary lookup | Active | Active on escalation | `Medication*.dosage[0].route` |
| `med.frequency` | Dosing frequency (BID, daily, etc.) | Tier 1 (Learned) | Normalized vocabulary lookup | Active | Active on escalation | `Medication*.dosage[0].timing` |
| `med.duration` | Length of course (e.g., 10 days) | Tier 2 (Deterministic) | Duration token matching | Bypass (default) | Escalation only | `Medication*.dispenseRequest.expectedSupplyDuration` |

---

## 7. Narrative Extraction Scope

To eliminate ambiguity for AI coding agents implementing Phase 2:

1. **Permitted Concepts:** The LLM narrative extractor (§7.4) is permitted to extract ONLY:
   - Medication regimens: `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`.
   - Laboratory results: `lab.test_name`, `lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`.
2. **Prohibited Concepts:** The LLM narrative extractor MUST NOT extract:
   - Diagnoses, conditions, or clinical impressions (`Condition`).
   - Allergies or adverse reactions (`AllergyIntolerance`).
   - Surgical or diagnostic procedures (`Procedure`).
   - Vital signs (`Observation` for vitals).
   - Social or family history.
3. **Enforcement:** The JSON output schema passed to grammar-constrained decoding (DR-13) will restrict `field_type` strictly to the 10 canonical values above. Any candidate claim outside this list is rejected at the grammar level.

---

## 8. Verification Architecture

```
                       [Claim + Evidence + Context Window]
                                        │
                                        ▼
                      ┌───────────────────────────────────┐
                      │ Stage A: Deterministic Rule Check │
                      └───────────────────────────────────┘
                                        │
                  ┌─────────────────────┴─────────────────────┐
                  │ Tier 2 Field & Exact Match?               │
                  ▼ YES                                       ▼ NO / Tier 1 Field
      ┌─────────────────────────┐                 ┌─────────────────────────────┐
      │ Final Label: Stage A    │                 │ Stage B: ModernBERT/NLI     │
      │ stage_reached = "A"     │                 │ (Aux Features: AUX_DIM=18)  │
      │ confidence = 1.0        │                 └─────────────────────────────┘
      └─────────────────────────┘                                 │
                                                                  ▼
                                                      ┌─────────────────────────┐
                                                      │ Calibrate Logits (T)    │
                                                      │ (TemperatureScaler)     │
                                                      └─────────────────────────┘
                                                                  │
                                           ┌──────────────────────┴──────────────────────┐
                                           │ Escalation Trigger Conditions (§12):        │
                                           │  - Stage A != Stage B (both non-null)       │
                                           │  - Calibrated Confidence < Threshold (DR-5) │
                                           │  - Stage B predicts INSUFFICIENT_EVIDENCE   │
                                           ▼ NO                                          ▼ YES
                               ┌─────────────────────────┐                   ┌─────────────────────────┐
                               │ Final Label: Stage B    │                   │ Stage C: LLM Judge      │
                               │ stage_reached = "B"     │                   │ (Local vLLM / Tool-free)│
                               │ confidence = Calibrated │                   └─────────────────────────┘
                               │ rationale = null        │                                 │
                               └─────────────────────────┘                                 ▼
                                                                             ┌─────────────────────────┐
                                                                             │ Final Label: Stage C    │
                                                                             │ stage_reached = "C"     │
                                                                             │ confidence = Calibrated │
                                                                             │ rationale = Populated   │
                                                                             │ escalated = true        │
                                                                             └─────────────────────────┘
```

---

## 9. Evidence & Context Contract

To prevent train/inference skew, evidence extraction and context window construction follow a single deterministic contract:

1. **Character Coordinates:** Every document page has its text concatenated with newline delimiters. `char_start` and `char_end` represent exact 0-indexed character offsets within that page's text string.
2. **Context Window Generation (DR-3):**
   - Given `[char_start, char_end]` on `page_number`:
   - Search backward for the nearest preceding double-newline `\n\n` (paragraph boundary) up to 250 characters before `char_start`. If found, set `window_start` to that boundary; otherwise set `window_start = max(0, char_start - 250)`.
   - Search forward for the nearest succeeding double-newline `\n\n` up to 250 characters after `char_end`. If found, set `window_end` to that boundary; otherwise set `window_end = min(len(page_text), char_end + 250)`.
   - If `(window_end - window_start) > 500`, symmetrically truncate to 500 characters centered around the evidence span.
3. **Bounding Box Resolution:** Call `span_to_bbox(page_tokens, char_start, char_end)`. The enclosing rectangle `[x0, y0, x1, y1]` is computed as the envelope over all tokens whose text spans intersect the character range.
4. **Duplicate Text Disambiguation:** If identical text appears multiple times on a page (e.g. "NORMAL" appearing in multiple lab rows), the parser binds the evidence span based on line proximity to the parent test name token, never via unindexed global string search.
5. **Multi-Page Spans:** Claims are strictly bounded within single pages. In the rare event of a clinical sentence spanning a page break, each page segment forms an independent evidence record linked via the parent `group_id`.

---

## 10. Calibration Architecture

Confidence calibration is strictly decoupled between raw model outputs, calibration mapping, and thresholding:

### Stage B Calibration
- **Raw Output:** Unnormalized logit vector $\mathbf{z} \in \mathbb{R}^3$ for classes `[SUPPORTED, DISCREPANCY, INSUFFICIENT_EVIDENCE]`.
- **Calibration Method:** Multi-class temperature scaling (DR-9).
- **Fitting:** Parameter $T > 0$ is optimized on the `calib_doc_ids` split by minimizing cross-entropy. $T$ is versioned with the model checkpoint.
- **Calibrated Probability:**
  $$P(\text{label} = i \mid \mathbf{z}) = \frac{e^{z_i / T}}{\sum_{j=1}^3 e^{z_j / T}}$$
- **Confidence Metric:** Maximum calibrated probability $\max_i P(\text{label} = i \mid \mathbf{z})$.

### Stage C Calibration
- **Primary Mode:** Extract unnormalized token logits for the three label tokens from vLLM under grammar constraint. Scale by $T_{\text{LLM}}$ fit on calibration split examples.
- **Fallback Mode:** Fit 1D Isotonic Regression on self-reported scalar confidence.

### Escalation Threshold (DR-5)
- The threshold $\tau_{\text{esc}}$ operates exclusively on **calibrated confidence**.
- If $\max_i P(\text{label} = i \mid \mathbf{z}) < \tau_{\text{esc}}$, escalate to Stage C.
- Provisional dev default: $\tau_{\text{esc}} = 0.60$. Final threshold locked at the risk-coverage knee in Phase 4 (V12).

---

## 11. FHIR Provenance Architecture

Canonical project extension URIs:
- **Base Extension URI:** `http://clindoc.org/fhir/StructureDefinition/claim-provenance`
- **Resource-Level Extension:** Carried on `Observation`, `DiagnosticReport`, `MedicationStatement`, `MedicationRequest`.
- **Element-Level Extension:** Carried on specific clinical elements (`valueQuantity`, `interpretation`, `dosage`).

### Provenance Sub-Extension Fields
1. `claimId` (`valueString`): UUID of the originating claim.
2. `verificationId` (`valueString`): UUID of the verification result.
3. `verificationLabel` (`valueCode`): `SUPPORTED | DISCREPANCY | INSUFFICIENT_EVIDENCE`.
4. `confidence` (`valueDecimal`): Calibrated confidence float rounded to 4 decimals.
5. `stageReached` (`valueCode`): `A | B | C`.
6. `evidenceText` (`valueString`): Verbatim evidence snippet.
7. `sourcePage` (`valueInteger`): 1-indexed page number.
8. `sourceBoundingBox` (`valueString`): `"x0,y0,x1,y1"`.
9. `ocrConfidence` (`valueDecimal`, optional): OCR confidence float.

### Concrete Example 1: Multi-Claim Lab Observation
An `Observation` for Glucose with a supported value but an incorrect reference range in the claim:
```json
{
  "resourceType": "Observation",
  "id": "obs-glucose-001",
  "status": "final",
  "code": {
    "coding": [{"system": "http://loinc.org", "code": "2345-7", "display": "Glucose [Mass/volume] in Serum or Plasma"}],
    "text": "Glucose"
  },
  "valueQuantity": {
    "value": 142.0,
    "unit": "mg/dL",
    "system": "http://unitsofmeasure.org",
    "code": "mg/dL",
    "extension": [
      {
        "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
        "extension": [
          {"url": "claimId", "valueString": "c-val-101"},
          {"url": "verificationId", "valueString": "v-val-101"},
          {"url": "verificationLabel", "valueCode": "SUPPORTED"},
          {"url": "confidence", "valueDecimal": 0.9820},
          {"url": "evidenceText", "valueString": "Glucose: 142 mg/dL"}
        ]
      }
    ]
  },
  "referenceRange": [
    {
      "text": "100 - 150 mg/dL",
      "extension": [
        {
          "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
          "extension": [
            {"url": "claimId", "valueString": "c-ref-102"},
            {"url": "verificationId", "valueString": "v-ref-102"},
            {"url": "verificationLabel", "valueCode": "DISCREPANCY"},
            {"url": "confidence", "valueDecimal": 1.0000},
            {"url": "evidenceText", "valueString": "Reference: 70 - 99 mg/dL"}
          ]
        }
      ]
    }
  ],
  "extension": [
    {
      "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
      "extension": [
        {"url": "claimId", "valueString": "c-val-101"},
        {"url": "verificationId", "valueString": "v-val-101"},
        {"url": "verificationLabel", "valueCode": "SUPPORTED"},
        {"url": "confidence", "valueDecimal": 0.9820},
        {"url": "evidenceText", "valueString": "Glucose: 142 mg/dL (Ref: 70 - 99)"}
      ]
    }
  ]
}
```

### Concrete Example 2: Medication Order with Grouped Claims
A `MedicationRequest` where Metformin dose is verified as supported, but frequency is flagged as discrepancy:
```json
{
  "resourceType": "MedicationRequest",
  "id": "medrx-metformin-001",
  "status": "active",
  "intent": "order",
  "medicationCodeableConcept": {
    "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "6809", "display": "Metformin"}],
    "text": "Metformin"
  },
  "dosageInstruction": [
    {
      "text": "500 mg orally once daily",
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 500,
            "unit": "mg",
            "extension": [
              {
                "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
                "extension": [
                  {"url": "claimId", "valueString": "c-dose-201"},
                  {"url": "verificationId", "valueString": "v-dose-201"},
                  {"url": "verificationLabel", "valueCode": "SUPPORTED"},
                  {"url": "confidence", "valueDecimal": 0.9650},
                  {"url": "evidenceText", "valueString": "Metformin 500 mg"}
                ]
              }
            ]
          }
        }
      ],
      "timing": {
        "code": {
          "text": "once daily",
          "extension": [
            {
              "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
              "extension": [
                {"url": "claimId", "valueString": "c-freq-202"},
                {"url": "verificationId", "valueString": "v-freq-202"},
                {"url": "verificationLabel", "valueCode": "DISCREPANCY"},
                {"url": "confidence", "valueDecimal": 0.9410},
                {"url": "evidenceText", "valueString": "twice daily with meals"}
              ]
            }
          ]
        }
      }
    }
  ],
  "extension": [
    {
      "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
      "extension": [
        {"url": "claimId", "valueString": "c-name-200"},
        {"url": "verificationId", "valueString": "v-name-200"},
        {"url": "verificationLabel", "valueCode": "SUPPORTED"},
        {"url": "confidence", "valueDecimal": 0.9900},
        {"url": "evidenceText", "valueString": "Metformin 500 mg Oral twice daily"}
      ]
    }
  ]
}
```

---

## 12. Dataset Architecture

### Benchmark Composition (120 Documents Total)
1. **Lab Documents (50 total):** 35 CV, 15 OOD (`template_c` held out).
2. **Medication Documents (50 total):** 36 CV, 14 OOD (`mar_order_sheet` held out).
3. **Narrative Notes (20 total):** 15 CV, 5 OOD (new narrative template held out).

### Split Structure
- **OOD Split:** 34 documents total (15 lab, 14 med, 5 narrative). Completely held out from training, calibration, and hyperparameter tuning.
- **CV Pool:** 86 documents total.
- **5-Fold GroupKFold Structure (per fold):**
  - `train_doc_ids`: ~52 documents (60%)
  - `calib_doc_ids`: ~17 documents (20%, dedicated to post-hoc calibration)
  - `val_doc_ids`: ~17 documents (20%, dedicated to model selection and thresholding)

### Class Balance & Hard Negatives
- Enforces DR-11: For every active learned verification field, $\min(\text{SUPPORTED}, \text{DISCREPANCY}) \ge \max(0.10 \times N, 30)$.
- Discrepancies are tagged with explicit §20.1 contradiction types (`value_mismatch`, `dose_mismatch`, `wrong_entity`, `unit_mismatch`).
- `INSUFFICIENT_EVIDENCE` is generated for unobserved clinical tests, unprescribed medications, and OCR-destroyed text tokens (minimum 15 examples per applicable field).

---

## 13. Legacy Artifact Policy

To guarantee zero data contamination between flawed v1 experiments and official v2 results:

1. **Legacy Directory Relocation:** All existing contents of `data/m2_results/` (including `m2_recovered_headline_results.json`, all checkpoints under `data/m2_results/checkpoints/`, and legacy result JSONs) shall be moved to `data/m2_results_v1_legacy/`.
2. **Fresh V6/V7 Results:** All future ModernBERT runs, baselines, and cascade evaluations will write cleanly to `data/m2_results/` or their respective `experiments/V*/` directories.
3. **No Automatic Resume from V1:** Resume and recovery logic in `src/clindoc/m2/recovery.py` will point strictly to the active v2 results directory and will not read from `m2_results_v1_legacy`.
4. **Reporting Isolation:** In all final tables and publications, v1 numbers may only appear in an appendix labeled *"Superseded Baseline (flawed single-class v1 dataset)"*, never in headline comparison tables.

---

## 14. Decision Records Required

The following 19 formal decision record files must be created in `docs/decisions/` before Phase 1 commences:

### Existing Decisions from Decision Package (DR-1 through DR-13)
- `DEC-001-region-classification.md` (DR-1)
- `DEC-002-code-confidence-threshold.md` (DR-2)
- `DEC-003-verification-context-window.md` (DR-3)
- `DEC-004-deterministic-extraction-threshold.md` (DR-4)
- `DEC-005-stage-b-escalation-threshold.md` (DR-5)
- `DEC-006-llm-model-and-serving.md` (DR-6)
- `DEC-007-terminology-retrieval-model.md` (DR-7)
- `DEC-008-medication-resource-mapping-rule.md` (DR-8)
- `DEC-009-confidence-calibration-method.md` (DR-9)
- `DEC-010-real-data-dataset-selection.md` (DR-10)
- `DEC-011-class-balance-floor.md` (DR-11)
- `DEC-012-evidence-linking-metric.md` (DR-12)
- `DEC-013-structured-output-mechanism.md` (DR-13)

### New Decisions Established by this Resolution (DR-14 through DR-19)
- `DEC-014-narrative-benchmark-synthesis.md` (AUDIT-01: Scope, structure, and template design for the 20 narrative clinical documents).
- `DEC-015-claim-entity-grouping.md` (AUDIT-02: Addition of `group_id` to Claim schema to resolve multi-entity document association).
- `DEC-016-fhir-provenance-hierarchy.md` (AUDIT-03: Two-tier resource-level and element-level FHIR provenance extension architecture).
- `DEC-017-canonical-field-vocabulary.md` (AUDIT-04: Standardization of 10 canonical field types, renaming `lab.ref_range`, and updating `AUX_DIM = 18`).
- `DEC-018-stage-c-confidence-calibration.md` (AUDIT-07: Dual-mode LLM confidence calibration via token logprobs or 1D isotonic regression).
- `DEC-019-verification-field-tiering.md` (AUDIT-08: Explicit partitioning into Tier 1 learned verification vs. Tier 2 deterministic Stage A verification).

---

## 15. PROJECT_SPEC.md Amendments

The following amendments must be applied to `PROJECT_SPEC.md`:

| Section | Current Text / Behavior | Required Amended Text / Behavior |
|---|---|---|
| **§2.1 / §8.2** | General mention of discharge summaries / clinical notes without explicit concept bounding. | Add explicit note: Narrative extraction is strictly confined to medication regimens and lab findings in clinical prose. Diagnoses/conditions are out of scope. |
| **§8.1** | `Claim` schema contains 14 fields; lacks grouping key. | Add `"group_id": "string (uuid) or null — links atomic claims belonging to the same clinical entity instance"` to schema. |
| **§8.2** | 10 field types listed; code uses `lab.ref_range`. | Reaffirm exact 10 types; explicitly specify canonical name `lab.reference_range`. |
| **§12** | Stage C confidence described as "passed through the SAME calibration pipeline as Stage B". | Specify dual-mode calibration: token logprob temperature scaling for local vLLM, 1D isotonic regression for scalar outputs. |
| **§12** | All fields implied to run through Stage B ModernBERT. | Formally document Tier 1 (Learned Cascade: `lab.value`, `lab.flag`, `med.*`) and Tier 2 (Stage A Deterministic: `lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`). |
| **§16** | Provenance extension format assumes single `claimId` per resource. | Update to two-tier specification: resource-level extension for primary claim; element-level sub-extensions for constituent fields. |
| **§18.1** | Benchmark specifies lab reports and medication templates without counts for narrative notes. | Explicitly specify benchmark composition: 50 lab reports, 50 medication forms, 20 narrative discharge notes (120 total). |
| **Appendix A** | 12 open decisions listed. | Update table to reflect all 19 decision records (`DEC-001` through `DEC-019`) with statuses and resolutions. |

---

## 16. Phase 0 Implementation Checklist

The following tasks represent the strictly permitted Phase 0 execution work:

- [ ] Create scaffolding directories: `schemas/`, `docs/decisions/`, `experiments/`, `benchmark/`.
- [ ] Create JSON Schema files under `schemas/`:
  - `document_text.json`
  - `routing_decision.json`
  - `claim.json` (with `group_id`)
  - `evidence.json`
  - `verification_result.json`
  - `audit_report.json`
- [ ] Create decision record files `DEC-001.md` through `DEC-019.md` in `docs/decisions/`.
- [ ] Create config files:
  - `configs/class_balance_floor.yaml` (DR-11)
  - `configs/context_window.yaml` (DR-3)
- [ ] Create `n2c2_access/REQUEST_STATUS.md` and record DUA submission details.
- [ ] Apply the 8 formal amendments to `PROJECT_SPEC.md` listed in Section 15.
- [ ] Relocate legacy v1 artifacts from `data/m2_results/` to `data/m2_results_v1_legacy/`.
- [ ] Verify that CI test suite passes on updated schemas and scaffolding.

---

## 17. Remaining Open Decisions

The following eight decisions MUST remain empirically open. They have locked provisional development defaults to prevent blocking implementation, but their final numerical values can only be established after their designated experiments complete:

| Decision ID | Description | Why It Must Remain Open | Experiment Required | Blocking Milestone |
|---|---|---|---|---|
| **DR-2** | Terminology `code_confidence` threshold | Depends on retrieval score distribution of chosen model | Precision/recall sweep on accepted codes | Phase 3 Gate |
| **DR-3** | Verification context window exact bounds | Requires confirmation that paragraph-bounded window does not degrade negation detection | Context window mini-ablation on negation hard-negatives | Phase 1 Regeneration |
| **DR-4** | Deterministic extraction re-routing threshold | Depends on empirical recall curve of deterministic parser | Extraction threshold sweep {0.5–0.9} on Phase 1 benchmark | Phase 2 Gate |
| **DR-5** | Stage B escalation threshold | Property of trained ModernBERT calibrated probability distribution | V12 Risk-coverage knee-point analysis | Phase 4 Gate |
| **DR-6** | LLM model & serving selection (3B vs 7-8B) | Requires empirical VRAM, latency, and JSON schema compliance check on T4 | 100-case narrative extraction & judge mini-benchmark | Phase 2 Start |
| **DR-7** | Terminology embedding model (SapBERT vs BioLORD) | Requires comparative retrieval accuracy on target clinical vocabularies | Top-1/Top-5 retrieval benchmark | Phase 3 Start |
| **DR-9** | Temperature scaling vs Isotonic OOD transfer | Requires measuring whether temperature scaling transfers to OOD template split | ECE and Brier score comparison on CV vs OOD | Phase 4 Gate |
| **DR-13** | Grammar-constrained decoding quality | Requires confirming grammar enforcement does not induce semantic degeneration | Constrained vs Unconstrained extraction quality check | Phase 2 Start |

---

## 18. Phase 1 Entry Criteria

Phase 1 (Dataset Regeneration & Ingestion) may begin ONLY when all of the following objective criteria are satisfied:

1. **Scaffolding Complete:** Directories `schemas/`, `docs/decisions/`, `experiments/`, and `benchmark/` exist in repository.
2. **Schemas Validated:** Machine-readable JSON Schema files for contracts §7–§10 are committed under `schemas/` and pass schema validation tests.
3. **Decisions Committed:** All 19 Decision Records (`DEC-001` through `DEC-019`) are committed to `docs/decisions/`.
4. **Master Spec Amended:** All 8 amendments in Section 15 are committed to `PROJECT_SPEC.md`.
5. **Real-Data Tracking Established:** `n2c2_access/REQUEST_STATUS.md` exists and confirms DUA submission status.
6. **Legacy Isolation Verified:** `data/m2_results/` is cleared and archived to `data/m2_results_v1_legacy/`.
7. **CI Passes:** Repository CI runs and passes on Phase 0 scaffolding.

---

## 19. Final Status

### **NOT READY FOR PHASE 1**

**Remaining Blockers to Complete in Phase 0:**
1. Creation of directory scaffolding (`schemas/`, `docs/decisions/`, `experiments/`, `benchmark/`).
2. Authoring and committing the 19 formal decision records (`DEC-001` through `DEC-019`).
3. Authoring and committing JSON Schema files under `schemas/` (incorporating `group_id` in `claim.json`).
4. Applying the 8 documented amendments to `PROJECT_SPEC.md`.
5. Creating `n2c2_access/REQUEST_STATUS.md`.
6. Archiving legacy M2 v1 results to `data/m2_results_v1_legacy/`.

Once this Phase 0 checklist is executed, Phase 1 implementation can commence immediately with zero unresolved architecture.
