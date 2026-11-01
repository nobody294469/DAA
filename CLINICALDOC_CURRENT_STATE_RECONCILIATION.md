# ClinicalDoc — Current State Reconciliation Audit

**Document ID**: `CLINICALDOC_CURRENT_STATE_RECONCILIATION.md`  
**Date**: October 31, 2026  
**Auditor**: Principal Software Architect, Senior Clinical NLP/AI Engineer & Security Specialist  
**Target Repository**: `ClinicalDoc` (`clindoc2fhir`)  
**Audit Mode**: STRICTLY READ-ONLY (Zero modifications to source code, tests, configuration, documentation, benchmark data, or Git state; working tree preserved)

---

## 1. Executive Verdict

### High-Potential Research Prototype — Strictly Unsafe for Clinical Export

ClinicalDoc is a modular multimodal clinical-document-to-FHIR R4B conversion system with sophisticated core engineering (PaddleOCR / PyMuPDF ingestion, deterministic extraction, GLiNER fallback, ModernBERT-Biomedical Stage B verification with temperature calibration $T=1.1465$, Qwen2.5-7B narrative extraction, and HL7 FHIR R4B bundle generation). 

However, an empirical, code-level reconciliation of the current repository against `CLINICALDOC_WHOLE_PROJECT_AUDIT.md`, latest Phase 5.2.3 Sub-Milestone 4.4 integration reports, and runtime test results reveals that:

1. **F-12 is NOT the sole clinical export blocker.** While conversational summaries informally singled out F-12, direct code-level inspection of production call paths confirms **THREE distinct, independent P0 blockers** that bar clinical export, plus **ONE major P1 architectural governance bypass**:
   - **Blocker 1 (P0 — Security / Governance)**: F-12 Multi-Hop Four-Eyes Ancestry Bypass in `src/clindoc/review/service.py:838-852`.
   - **Blocker 2 (P0 — Clinical Safety / FHIR Semantics)**: REQ-44 Lifecycle Enforcement is **completely dormant** in production export (`enforce_req44_lifecycle=False` by default; omitted in `src/clindoc/services/fhir_export.py:743, 1029` and `src/clindoc/cli.py:244`).
   - **Blocker 3 (P0 — Integrity / Concurrency)**: Unlocked concurrent append in `ReviewAuditLedger.append_event` (`src/clindoc/review/service.py:335-385`), corrupting hash chains and sequence counters under multi-user access.
   - **Governance Bypass (P1 — Pipeline Architecture)**: Stateless CLI (`src/clindoc/cli.py`) generates unreviewed, untagged final FHIR bundles directly from raw claims, bypassing the database, human review, Four-Eyes, and the cryptographic audit ledger.
2. **M4.4 Integration Claims are Verified**:
   - The 12 new REQ-44 edge-case tests exist in `tests/test_phase523_milestone4_4.py` (lines 300–720) and pass (19/19 in file).
   - Full test suite execution is verified at **1,466 passed, 5 failed, 1,471 total** in 263.35s.
   - The 5 failures match the established historical baseline with **zero new regressions**.
   - The 38 recovered medication claims across `NARRATIVE_0016`, `0017`, and `0018` are grounded in frozen ground truth and pass coreference tests without cross-medication contamination.
   - Benchmark denominators and metrics across 12-doc, 60-doc, 120-doc, and OOD scopes remain 100% frozen with zero drift.

---

## 2. Reconciliation of the Four Critical Security & Architecture Findings

### Finding 1: F-12 — Multi-Hop Four-Eyes Ancestry Traversal

- **Current Status**: **OPEN** (Active Security Vulnerability).
- **Exact File & Line Numbers**: [`src/clindoc/review/service.py:838-852`](file:///d:/daa/src/clindoc/review/service.py#L838-L852) in `ClaimReviewService._check_four_eyes`.
- **Call Graph Context**:
  - Invoked exclusively within `accept_claim` ([`src/clindoc/review/service.py:990`](file:///d:/daa/src/clindoc/review/service.py#L990)) after actor authorization.
- **Code Inspection & Failure Mechanism**:
  ```python
  # src/clindoc/review/service.py:837-852
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
  - **Empirical Trace**: The algorithm inspects *only* the immediate predecessor `prev_claim = session.get(Claim, claim.supersedes_claim_id)`.
  - **Multi-Hop Exploit Scenario**:
    1. System/Operator creates Claim $v_1$.
    2. Actor A edits Claim $v_1 \to v_2$ (`created_by = A`, `supersedes_claim_id = v1`).
    3. Actor B edits Claim $v_2 \to v_3$ (`created_by = B`, `supersedes_claim_id = v2`).
    4. Actor C edits Claim $v_3 \to v_4$ (`created_by = C`, `supersedes_claim_id = v3`).
    5. Actor A calls `accept_claim` on Version $v_4$:
       - `claim.created_by == actor_str` evaluates to `False` ($v_4$ was created by C).
       - `claim.supersedes_claim_id` points to $v_3$. `prev_claim` is $v_3$, created/edited by B. The check evaluates to `False`.
       - Traversal stops immediately. It **does not** walk back to $v_2$ where Actor A modified the claim.
       - Actor A successfully accepts $v_4$, directly violating the Mode 0 Four-Eyes separation of duties by approving their own ancestral modification.
- **Existing Automated Test Coverage**:
  - [`tests/test_phase523_milestone4_2.py:483-490`](file:///d:/daa/tests/test_phase523_milestone4_2.py#L483-L490) tests only 1-hop self-acceptance (Actor A edits $v_1 \to v_2$, then Actor A attempts to accept $v_2$).
  - [`tests/test_phase523_milestone4_4.py:270-297`](file:///d:/daa/tests/test_phase523_milestone4_4.py#L270-L297) (`test_four_eyes_ancestry_blocker_remains_active`) asserts that the blocker is recognized, but does not fix the multi-hop traversal.
- **Remaining Failure Mode**: Any edit chain with $\ge 2$ edits permits an early modifier to act as final approver.
- **Independently Blocks Clinical Export**: **YES**. Mode 0 compliance (PD-002, PD-009, PD-015) requires full separation between claim modifier and claim approver across the complete lineage.

---

### Finding 2: REQ-44 Lifecycle Enforcement in Production Export Paths

- **Current Status**: **OPEN / DORMANT IN PRODUCTION** (Verified in unit tests; dormant in production callers).
- **Exact File & Line Numbers**:
  - Definition: [`src/clindoc/fhir/mapper.py:2397`](file:///d:/daa/src/clindoc/fhir/mapper.py#L2397) (`enforce_req44_lifecycle: bool = False`).
  - Logic Gate: [`src/clindoc/fhir/mapper.py:3005-3045`](file:///d:/daa/src/clindoc/fhir/mapper.py#L3005-L3045) (`if enforce_req44_lifecycle:`).
  - Production Caller 1: [`src/clindoc/services/fhir_export.py:743-759`](file:///d:/daa/src/clindoc/services/fhir_export.py#L743-L759) in `export_document_as_fhir`.
  - Production Caller 2: [`src/clindoc/services/fhir_export.py:1029-1040`](file:///d:/daa/src/clindoc/services/fhir_export.py#L1029-L1040) in `export_batch_as_fhir`.
  - CLI Caller: [`src/clindoc/cli.py:244-249`](file:///d:/daa/src/clindoc/cli.py#L244-L249) in `process_document`.
  - Test Helper Caller: [`src/clindoc/testing/synthetic_export.py:80-96`](file:///d:/daa/src/clindoc/testing/synthetic_export.py#L80-L96).
- **Call Graph Evidence**:
  - In `src/clindoc/fhir/mapper.py:2397`, the parameter defaults to `False`:
    ```python
    def claims_to_fhir_bundle(
        claims: Sequence[Claim],
        evidence_map: dict[str, Evidence],
        verification_map: dict[str, VerificationResult],
        ...
        enforce_req44_lifecycle: bool = False,
    ) -> Bundle:
    ```
  - In every single production call site in `services/fhir_export.py` and `cli.py`, `enforce_req44_lifecycle` is **omitted entirely**.
  - Only dedicated test assertions in [`tests/test_phase523_milestone4_4.py:624, 651, 677, 711`](file:///d:/daa/tests/test_phase523_milestone4_4.py#L624) explicitly pass `enforce_req44_lifecycle=True`.
- **Failure Mechanism & Clinical Impact**:
  When `enforce_req44_lifecycle` is `False` in production runtime:
  1. Historical medication mentions on Rx pad layouts map to `MedicationRequest(status="completed")`, **fabricating an active prescription order** where none exists.
  2. Unreferenced prescription cancellation directives in progress notes map to `MedicationStatement(status="stopped")` without evidence of actual patient cessation.
  3. Patient cessation and prescriber cancellation remain inappropriately coupled.
- **Existing Automated Test Coverage**:
  - 100% passing when explicitly passed as `True` in `tests/test_phase523_milestone4_4.py` (4 dedicated lifecycle tests). Zero tests assert that production callers enable it.
- **Remaining Failure Mode**: At runtime, every document exported via the API or CLI runs the legacy, ungrounded medication lifecycle mapping.
- **Independently Blocks Clinical Export**: **YES**. Emitting fabricated `MedicationRequest` orders or ungrounded `stopped` statements from clinical notes violates FHIR R4B clinical semantics and patient safety requirements.

---

### Finding 3: Audit Ledger Concurrency Flaw

- **Current Status**: **OPEN** (Active Data Integrity Vulnerability).
- **Exact File & Line Numbers**: [`src/clindoc/review/service.py:335-385`](file:///d:/daa/src/clindoc/review/service.py#L335-L385) in `ReviewAuditLedger.append_event`.
- **Call Graph Context**:
  - Called from `ClaimReviewService.accept_claim` (line 1032), `edit_claim` (line 1172), `reject_claim` (line 1238), `escalate_claim` (line 1294), `reopen_claim` (line 1357), and `DocumentApprovalEngine.approve_document` (line 1445).
- **Code Inspection & Failure Mechanism**:
  ```python
  # src/clindoc/review/service.py:351-385
  # Step 1: Open and read file to find trailing line and previous hash
  if self.ledger_path.exists() and self.ledger_path.stat().st_size > 0:
      with open(self.ledger_path, "r", encoding="utf-8") as f:
          last_line = None
          for line in f:
              line_s = line.strip()
              if line_s:
                  last_line = line_s
          if last_line:
              parsed = json.loads(last_line)
              seq_num = parsed.get("sequence_number", 0) + 1
              prev_hash = parsed.get("current_event_hash", "")

  # Step 2: Compute envelope and hash
  envelope = build_canonical_event_envelope(...)
  curr_hash = compute_canonical_envelope_hash(envelope)
  ...
  # Step 3: Re-open in append mode and write
  with open(self.ledger_path, "a", encoding="utf-8") as f:
      f.write(line_json + "\n")
  ```
  - **Empirical Trace**: The method executes an uncoordinated Read-Modify-Write cycle on the filesystem.
  - **Absence of Locking**: There is zero process locking (`portalocker`, `fcntl.flock`, `msvcrt.locking`, or file mutex) and zero threading lock.
  - **Failure Under Concurrency**: When two reviewers submit decisions simultaneously via FastAPI:
    1. Worker Process 1 reads trailing line $L_k$ with hash $H_k$, derives `seq_num = k + 1`.
    2. Worker Process 2 reads trailing line $L_k$ before Process 1 writes, derives `seq_num = k + 1` and `previous_event_hash = H_k`.
    3. Both write their records sequentially:
       - Record $k+1$: `seq_num: k+1`, `prev_hash: H_k`, `curr_hash: H_{k+1A}`
       - Record $k+2$: `seq_num: k+1`, `prev_hash: H_k`, `curr_hash: H_{k+1B}`
    4. **Corruption**: Sequence numbers collide, and Record $k+2$'s `previous_event_hash` points to $H_k$ instead of $H_{k+1A}$.
    5. **Consequence**: The ledger permanently fails subsequent cryptographic verification (`AuditLedgerVerificationError: SequenceGapError` or `HashMismatchError`), which in turn permanently trips Gate 9 in `FHIRExportService`, freezing all further exports.
- **Existing Automated Test Coverage**:
  - `tests/test_phase523_milestone4_2.py` tests hash calculation, HMAC verification, and tampering detection in a sequential test runner. Zero concurrent append tests exist.
- **Remaining Failure Mode**: Any concurrent review actions on multi-core / multi-worker deployments corrupt the ledger.
- **Independently Blocks Clinical Export**: **YES**. Under Gate 9 (`FHIRExportService:560-610`), FHIR export strictly requires a cryptographically verified audit ledger proof. Ledger corruption permanently halts clinical export.

---

### Finding 4: CLI Governance Bypass (Stateless vs Stateful Divergence)

- **Current Status**: **OPEN** (Active Architectural & Governance Disconnection).
- **Exact File & Line Numbers**: [`src/clindoc/cli.py:244-270`](file:///d:/daa/src/clindoc/cli.py#L244-L270) in `process_document`.
- **Call Graph Context**:
  - Invoked directly from the terminal via `python -m clindoc.cli process <input_file> -o <out_dir>` or `clindoc batch <input_dir> -o <out_dir>`.
- **Code Inspection & Failure Mechanism**:
  ```python
  # src/clindoc/cli.py:243-267
  # 5. FHIR R4B Bundle Generation (§15, §16)
  bundle = claims_to_fhir_bundle(
      claims=claims,
      evidence_map=evidence_map,
      verification_map=verification_map,
      document_id=doc_text.document_id,
  )

  # 6. Audit Report Generation (§7.8)
  audit_report = generate_audit_report(...)

  # 7. Output Persistence
  with open(fhir_bundle_path, "w", encoding="utf-8") as f:
      f.write(bundle.json(indent=2))
  ```
  - **Empirical Trace**:
    - Operates completely statelessly without connecting to PostgreSQL or SQLite.
    - Claims are never set to `PENDING_REVIEW`.
    - Bypasses `ReviewService` and the React/Streamlit review interfaces entirely.
    - Zero Four-Eyes verification is performed.
    - Zero events are appended to the cryptographic `ReviewAuditLedger`.
    - Generates a full `Bundle(type="collection")` containing `MedicationRequest(status="active")`, `Observation(status="final")`, etc.
    - **No Draft Metadata**: The bundle carries no tag, extension, or status indicating it was generated headlessly without human review.
- **Intentional Draft vs Genuine Bypass**:
  - In Phase 0 through Phase 5.0 (`PROJECT_SPEC §5, §6`), the CLI served as the primary automated evaluation and research pipeline driver.
  - In Phase 8 Productization (`PRODUCTIZATION_DECISIONS.md: PD-002`), Mode 0 mandatory review was established: *"No claim or document may be exported as final FHIR output without an explicit human review decision."*
  - Because `cli.py` was never updated to conform to PD-002, any operator or script with access to the CLI can produce output that is indistinguishable from an approved clinical export, bypassing all institutional safety controls.
  - **Verdict**: In a clinical or productized environment, this is a **genuine governance bypass**.
- **Existing Automated Test Coverage**:
  - `tests/test_cli.py` verifies CLI argument parsing and artifact writing. No test checks governance containment.
- **Remaining Failure Mode**: Unauthenticated, unreviewed clinical export directly from raw clinical documents.
- **Independently Blocks Clinical Export**: **NO for the API path** (the API path enforces Gate 9 and fails closed). **YES for overall system certification**, because the CLI exposes an ungoverned backdoor to production FHIR generation.

---

## 3. Verification of Sub-Milestone 4.4 Claims

### 3.1 Verification of the 12 New Tests

| Test Function Name | Location in File | Purpose / Scope | Status |
| :--- | :--- | :--- | :---: |
| `test_req44_leading_duration` | `tests/test_phase523_milestone4_4.py:302-334` | Verifies leading duration handling | **PASS** |
| `test_req44_mixed_duration_placement` | `tests/test_phase523_milestone4_4.py:337-374` | Verifies mixed preceding/trailing durations | **PASS** |
| `test_req44_duration_vs_followup_review_interval` | `tests/test_phase523_milestone4_4.py:377-414` | Disambiguates review interval from drug duration | **PASS** |
| `test_req44_duration_vs_monitoring_reassessment` | `tests/test_phase523_milestone4_4.py:417-454` | Excludes diagnostic repeat intervals from Dosage | **PASS** |
| `test_req44_paragraph_break_without_transition_fails_closed` | `tests/test_phase523_milestone4_4.py:457-495` | Fails closed on untransitioned paragraph splits | **PASS** |
| `test_req44_ocr_line_wrap_with_explicit_transition` | `tests/test_phase523_milestone4_4.py:498-537` | Handles OCR newline wraps within transition clauses | **PASS** |
| `test_req44_taxonomy_case_b_multiple_regimen_durations` | `tests/test_phase523_milestone4_4.py:540-578` | Resolves multi-phase sequential durations | **PASS** |
| `test_req44_taxonomy_case_d_symptom_history_duration` | `tests/test_phase523_milestone4_4.py:581-618` | Isolates non-medication symptom durations | **PASS** |
| `test_req44_lifecycle_unreferenced_cancellation_fails_closed` | `tests/test_phase523_milestone4_4.py:621-648` | Fails closed on unreferenced Rx cancellations | **PASS** |
| `test_req44_lifecycle_patient_cessation_emits_stopped` | `tests/test_phase523_milestone4_4.py:650-674` | Emits `stopped` only with patient cessation | **PASS** |
| `test_req44_lifecycle_historical_maps_to_unknown_not_completed`| `tests/test_phase523_milestone4_4.py:676-708` | Maps historical meds to `unknown` | **PASS** |
| `test_req44_lifecycle_completed_requires_syntactic_linkage` | `tests/test_phase523_milestone4_4.py:710-720` | Requires syntactic link for `completed` | **PASS** |

- **Empirical Execution**: All 19 tests in `tests/test_phase523_milestone4_4.py` executed in `0.15s` with **100% pass rate** (task-14258).

---

### 3.2 Full Repository Pytest Execution & Failure Comparison

- **Current Full Test Suite Result**:
  ```
  FAILED tests/test_cli.py::test_cli_unsupported_extension_fails - assert 0 == 1
  FAILED tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling
  FAILED tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation - AssertionError: Expected equal SUPPORTED (335) and DISCREPANCY (705)
  FAILED tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline
  FAILED tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification
  ================ 5 failed, 1466 passed, 11 warnings in 263.35s ================
  ```
- **Total Test Count**: $1,466 + 5 = \mathbf{1,471\text{ tests}}$ (matching the reported figure exactly).
- **Exact Historical Failure Reconciliation**:

| # | Failed Test Identifier | Baseline Root Cause | Current Status | Regressed? |
| :---: | :--- | :--- | :--- | :---: |
| 1 | `tests/test_cli.py::test_cli_unsupported_extension_fails` | `.txt` added to `SUPPORTED_EXTENSIONS` in `ocr/ingest.py`, causing CLI to exit `0` instead of `1`. | Identical (`assert 0 == 1`) | **NO** |
| 2 | `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling` | `notes.txt` ingested without raising `UnsupportedFileTypeError` because `.txt` is supported. | Identical (`DID NOT RAISE`) | **NO** |
| 3 | `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation` | Multi-perturbation generator yields 705 discrepancies vs 335 supported, failing 1:1 parity assertion. | Identical (`assert 335 == 705`) | **NO** |
| 4 | `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline` | Regex hardened to require test names, stopping demographic number leakage even when sections disabled. | Identical (`assert '987654' in ['105']`) | **NO** |
| 5 | `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification` | Substring search matches error string forbidding `ssl_verify=False` in `ehr_transport.py`. | Identical (matches forbidden string) | **NO** |

**Conclusion**: Zero new regressions have been introduced. The failure signature is identical to the baseline recorded in Phase 5.2.3 Milestones 1 through 4.

---

### 3.3 Recovery of 38 Medication Claims

- **Target Documents**: `NARRATIVE_0016`, `NARRATIVE_0017`, `NARRATIVE_0018` in `data/narrative_ground_truth/` and `data/narrative_ocr_cache/`.
- **Claim Breakdown**:
  - `NARRATIVE_0016`: 12 claims (Metformin: 4 claims, Metoprolol: 4 claims, Omeprazole: 4 claims).
  - `NARRATIVE_0017`: 13 claims (Cephalexin: 5 claims, Metoprolol: 4 claims, Lisinopril: 4 claims).
  - `NARRATIVE_0018`: 13 claims (Amlodipine: 4 claims, Metoprolol: 4 claims, Amoxicillin: 5 claims).
  - **Total**: $12 + 13 + 13 = \mathbf{38\text{ claims}}$ across 9 distinct medication entities.
- **Root Cause & Recovery Verification**:
  - In Phase 4.4, single-UUID group assignment in `_build_claims` caused all 3 medications in a document to share a single `group_id`. Downstream cross-medication contamination safety logic in `mapper.py:2086-2104` correctly suppressed all 38 claims (0% exported).
  - With per-anchor UUID assignment implemented in `llm_extractor.py`, each medication receives an isolated `group_id`.
  - Verified via `tests/test_phase52_medication_coreference.py` (26/26 passed) and `scratch_claim_table.json`: all 38 claims export into compliant `MedicationStatement` resources with 100% attribute isolation and zero attribute cross-talk.

---

### 3.4 Benchmark Landscape & Denominator Reconciliation

| Benchmark Scope | Document Count | Certified Denominator | Precision | Recall | Micro-F1 | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Synthetic Cascade Frozen (12-Doc)** | 12 | 269 GT Claims | 0.8872 | 0.8736 | 0.8803 | **Frozen, 0 drift** |
| **Synthetic Cascade Expanded (60-Doc)**| 60 | 974 Eval Claims (999 total) | 0.8540 | 0.9415 | 0.8956 | **Frozen, 0 drift** |
| **Scope 1: Primary Narrative (E3-20)** | 20 | 540 GT Claims | 0.6300 | 0.1167 | 0.1969 | **Frozen, 0 drift** |
| **Scope 2: Multi-Extractor (GLiNER-120)**| 120 | 4,456 GT Claims | 0.8814 | 0.8920 | 0.8867 | **Frozen, 0 drift** |
| **Scope 3: Out-of-Distribution (OOD-5)** | 5 | 135 GT Claims | 0.6120 | 0.1185 | 0.1985 | **Frozen, 0 drift** |
| **Full V3 Verification Holdout (V8)** | 34 | 1,826 Claims | Acc: 0.9748 | Rec: 0.9826 | Macro: 0.9538 | **Frozen, 0 drift** |

**Conclusion**: Denominators, manifest splits, and canonical data sheets in `experiments/FINAL_REPORT_DATA_SHEET.md` remain strictly frozen.

---

## 4. Consolidated Findings & Defect Register

### Consolidated Inventory (Resolved Discrepancies)

The discrepancy between previous reports—where conversational summaries referred to F-12 as the "sole blocker" while `CLINICALDOC_WHOLE_PROJECT_AUDIT.md` listed multiple P0 defects—is hereby resolved. The four critical findings have been reconciled through direct code inspection and classified under standard engineering priority definitions:

| Finding ID | Severity | Category | File Path / Location | Summary of Defect | Current Status | Blocks Clinical Export? |
| :--- | :---: | :--- | :--- | :--- | :---: | :---: |
| **F-12 / DEF-001** | **P0** | Security / Four-Eyes | `src/clindoc/review/service.py:838-852` | Multi-hop Four-Eyes ancestry bypass: checks only single-hop predecessor (`supersedes_claim_id`). | **OPEN** | **YES** |
| **DEF-002** | **P0** | Clinical Safety / FHIR | `src/clindoc/services/fhir_export.py:743, 1029` | REQ-44 lifecycle enforcement is dormant in production export (`enforce_req44_lifecycle=False`). | **OPEN** | **YES** |
| **DEF-003** | **P0** | Security / Integrity | `src/clindoc/review/service.py:335-385` | Concurrent JSONL audit ledger appends lack file locking, corrupting sequence numbers and hash chains. | **OPEN** | **YES** |
| **DEF-004** | **P1** | Pipeline Architecture | `src/clindoc/cli.py:244-270` | Unified CLI completely bypasses Mode 0 review, DB, and audit ledger, directly exporting untagged FHIR. | **OPEN** | **YES (for CLI)** |
| **DEF-005** | **P1** | Clinical Safety / FHIR | `src/clindoc/cli.py:244` | Unified CLI omits `enforce_req44_lifecycle=True`, running ungrounded legacy medication mapping. | **OPEN** | **YES (for CLI)** |
| **DEF-006** | **P1** | Test Suite Integrity | `tests/test_cli.py:147`, `test_document_ingestion.py:238` | Tests assert `.txt` raises `UnsupportedFileTypeError`, but `.txt` was added to `SUPPORTED_EXTENSIONS`. | **BASELINE** | NO |
| **DEF-007** | **P1** | Test Suite Integrity | `tests/test_m2_v2_corrections.py:32` | Assertion `len(supported) == len(discrepancy)` fails on unbalanced perturbation generator. | **BASELINE** | NO |
| **DEF-008** | **P1** | Test Suite Integrity | `tests/test_section_detection_production.py:92` | Test asserts obsolete bug (demographic number leakage) fixed during Phase 5.2.3. | **BASELINE** | NO |
| **DEF-009** | **P1** | Test Suite Integrity | `tests/test_vllm_deployment.py:153` | Substring search matches error string forbidding `ssl_verify=False` in `ehr_transport.py`. | **BASELINE** | NO |
| **F-03** | **P2** | Architectural Boundary | `src/clindoc/extract/deterministic.py:1623` | `DeterministicExtractor.extract_med_line` assigns line UUIDs rather than `__no_anchor__` sentinel. | **OPEN** | NO (Router mitigates) |
| **F-07** | **P2** | Extraction / Regimen | `src/clindoc/fhir/mapper.py:1735` | Forward-looking phase windowing misattributes duration when phrased as `"For 2 weeks take [Drug]"`. | **OPEN** | NO (Non-canonical syntax) |
| **DEF-010** | **P2** | Clinical NLP / ML | `src/clindoc/extract/router.py:176-187` | In DEV profile, narrative extraction silently defaults to regex heuristic (`MockLLMProvider`). | **OPEN** | NO (DEV profile only) |
| **DEF-011** | **P2** | Verification / ML | `src/clindoc/verify/stage_c.py:288-295` | In DEV profile, Stage C judge silently defaults to heuristic `DefaultMockLLMJudge`. | **OPEN** | NO (DEV profile only) |
| **DEF-012** | **P2** | Terminology | `src/clindoc/normalize/normalizer.py` | Local dictionary subsets only (40 LOINC codes); no dynamic or comprehensive terminology lookup. | **OPEN** | NO (By design for MVP) |
| **DEF-013** | **P3** | User Interface | `src/clindoc/ui/app.py` | Legacy Streamlit UI calls backend services directly in-process rather than via REST API. | **OPEN** | NO (Legacy prototype) |
| **DEF-014** | **P3** | Operational / Deployment| `deploy/docker-compose.yml` | Requires separate multi-container orchestration for GPU vLLM server, not turnkey on desktop. | **OPEN** | NO (Hardware bound) |

---

## 5. Confirmed Clinical Export Blockers vs Non-Blocking Defects

### Confirmed Clinical Export Blockers (Must Be Fixed Before Clinical Export)

1. **F-12 (Multi-Hop Four-Eyes Ancestry Bypass)**:  
   Mode 0 governance is the core safety barrier separating ClinicalDoc from unvetted AI generation. Permitting a clinician to approve a claim whose ancestral version they modified invalidates the institutional review guarantee.
2. **DEF-002 (REQ-44 Lifecycle Enforcement Dormancy in Production Export)**:  
   The mapper code contains hardened lifecycle rules, but `services/fhir_export.py` never enables them. Real exported bundles will continue to fabricate active `MedicationRequest` orders from historical mentions and generate invalid `stopped` statements.
3. **DEF-003 (Audit Ledger Concurrency Flaw)**:  
   Multi-user review in production will corrupt the JSONL hash chain, causing Gate 9 verification to trip and bricking subsequent exports.

### Confirmed Non-Blocking Defects (Safe to Defer or Remediate in P1/P2)

- **The 5 Baseline Test Failures (DEF-006 through DEF-009)**: Known test assertion and fixture bugs; none represent a regression or defect in core extraction, verification, or mapping logic.
- **CLI Pipeline Divergence (DEF-004)**: Non-blocking for the API export path because `services/fhir_export.py` enforces Gate 9. (It is an operational defect to be addressed before deploying the CLI).
- **F-03 (`DeterministicExtractor` line UUIDs)**: Non-blocking because `ExtractionRouter` routes narrative medication text through the segmentation and `medication_association.py` pipeline.
- **F-07 (Preceding Duration Windowing)**: Non-blocking because standard clinical prescriptions write durations after doses (`[Dose] [Freq] for [Duration]`), and verbatim narrative `Dosage.text` preserves the original clause.
- **Mock Fallbacks in DEV Profile (DEF-010, DEF-011)**: Non-blocking because the `production` profile explicitly forbids mock fallbacks and crashes on boot (PD-003, PD-004).

---

## 6. Minimal Dependency-Ordered Remediation Plan

To bring ClinicalDoc from its current state to a certified pilot prototype with zero export blockers, the following dependency-ordered remediation plan must be executed:

```
                        REMEDIATION DEPENDENCY GRAPH

   [ACT-P0-1: Four-Eyes Ancestry Traversal]
                    │
                    ▼
   [ACT-P0-2: Enable REQ-44 in Production Export & CLI]
                    │
                    ▼
   [ACT-P0-3: File Locking in ReviewAuditLedger]
                    │
   ═════════════════╪══════════════════════════════════════════════
   GATE: Clinical Export Safety Unlocked (All P0 Blockers Resolved)
   ═════════════════╪══════════════════════════════════════════════
                    │
                    ▼
   [ACT-P1-1: Resolve 5 Baseline Test Failures (100% Green Build)]
                    │
                    ▼
   [ACT-P1-2: Contain CLI Pipeline with Mandatory Draft Metadata]
                    │
                    ▼
   [ACT-P2-1: Align DeterministicExtractor Unanchored Sentinels (F-03)]
```

### Phase 1: Security & Core Safety Hardening (P0 Blockers — Estimated Effort: 1 Day)

1. **`ACT-P0-1` — Remediate Multi-Hop Four-Eyes Ancestry in `src/clindoc/review/service.py`**:
   - Replace single-hop `supersedes_claim_id` lookup in `_check_four_eyes` with an iterative traversal:
     ```python
     curr_claim = claim
     ancestor_actors = set()
     while curr_claim.supersedes_claim_id is not None:
         prev = session.get(Claim, curr_claim.supersedes_claim_id)
         if prev is None:
             break
         if prev.created_by:
             ancestor_actors.add(str(prev.created_by))
         if prev.payload and prev.payload.get("edited_by"):
             ancestor_actors.add(str(prev.payload["edited_by"]))
         curr_claim = prev
     if actor_str in ancestor_actors:
         raise FourEyesViolationError(...)
     ```
   - Add multi-hop unit tests ($v_1 \to v_2 \to v_3 \to v_4$).
2. **`ACT-P0-2` — Activate `enforce_req44_lifecycle=True` in Production Callers**:
   - Update `src/clindoc/services/fhir_export.py` lines 743 and 1029 to pass `enforce_req44_lifecycle=True`.
   - Update `src/clindoc/cli.py` line 244 to pass `enforce_req44_lifecycle=True`.
   - Alternatively, change default in `src/clindoc/fhir/mapper.py:2397` to `enforce_req44_lifecycle: bool = True`.
3. **`ACT-P0-3` — Implement File Locking in `ReviewAuditLedger.append_event`**:
   - Wrap the Read-Modify-Write cycle in `src/clindoc/review/service.py:350-385` using an exclusive cross-platform file lock (`portalocker.Lock` or `msvcrt`/`fcntl` fallback).

### Phase 2: Test Baseline Clean-Up & Governance Alignment (P1 Requirements — Estimated Effort: 1 Day)

1. **`ACT-P1-1` — Fix the 5 Baseline Test Failures**:
   - `test_cli.py`: Change `document.txt` to `document.unsupported_ext`.
   - `test_document_ingestion.py`: Change `notes.txt` to `notes.badext`.
   - `test_m2_v2_corrections.py`: Update assertion to check non-empty counts and label validity rather than strict 1:1 equality.
   - `test_section_detection_production.py`: Update assertion to reflect hardened demographic leakage suppression.
   - `test_vllm_deployment.py`: Update regex to match assignments (`re.search(r"verify\s*=\s*False", ...)`).
   - **Target**: 1,471 / 1,471 tests passing (100% green).
2. **`ACT-P1-2` — Contain Stateless CLI Export**:
   - In `src/clindoc/cli.py`, add a mandatory `--draft` flag for CLI export.
   - Attach an explicit extension to CLI bundles: `http://clindoc.org/fhir/StructureDefinition/export-mode: unreviewed-cli-draft`.
   - Block CLI export of final clinical status without explicit `--allow-unreviewed` override.

### Phase 3: Architectural Unification (P2 — Estimated Effort: 1 Day)

1. **`ACT-P2-1` — Unify Deterministic Extractor Unanchored Sentinels (F-03)**:
   - In `src/clindoc/extract/deterministic.py:1623`, emit `__no_anchor__` sentinel instead of fresh line UUIDs when a line lacks a drug anchor.

---

## 7. Frozen Components Inventory (Zero Modification Without Prior Authorization)

The following components and files are under **strict architectural freeze** and must **NOT** be modified, refactored, or edited until explicit authorization is granted:

| Component / Subsystem | Files Under Strict Freeze | Rationale for Freeze |
| :--- | :--- | :--- |
| **Stage B Learned Verifier** | `src/clindoc/verify/stage_b.py`, `models/modernbert_v3/` | Pinned checkpoint SHA-256 and temperature calibration ($T=1.146522$) under PD-004 / PD-005. |
| **Stage A Deterministic Verifier**| `src/clindoc/verify/stage_a.py` | Core tolerance parameters ($0.05$ float, exact string) frozen across benchmarks. |
| **Evaluation Testbeds & Denominators**| `external_eval/`, `data/m2_verification_v3/`, `experiments/` | Certified benchmarks (12-doc, 60-doc, 120-doc, OOD holdouts) must remain bitwise invariant. |
| **Clause Segmentation Core** | `src/clindoc/extract/segmentation.py`, `medication_association.py` | 26 adversarial rules and $[L_k, R_k)$ boundary logic verified under Phase 5.2.3 M2/M3. |
| **Terminology Code Subsets** | `configs/loinc_subset.json`, `configs/ucum_subset.json` | Pinned mappings for 40 laboratory analytes and UCUM units. |
| **Database Schemas & Migrations**| `src/clindoc/db/models.py`, `alembic/` | Schema modifications require formal migration scripts and database versioning approval. |
| **Existing Phase 4 / Phase 5.2 Tests**| `tests/test_phase4*.py`, `tests/test_phase5*.py` | Regression harness protecting historical milestones against silent drift. |

---

## 8. Summary & Next Steps

1. **Reconciliation Complete**: The codebase state is fully mapped. The 1,466/5 test result is reproducible and verified.
2. **Blocker Clarification**: There are three concrete P0 blockers (F-12 ancestry bypass, dormant REQ-44 lifecycle enforcement, and unlocked audit ledger append) preventing clinical export.
3. **Execution Ready**: When authorized, Phase 1 (`ACT-P0-1`, `ACT-P0-2`, `ACT-P0-3`) can be implemented in a single focused session, removing all clinical export blockers while maintaining zero regressions across the 1,471-test suite.
