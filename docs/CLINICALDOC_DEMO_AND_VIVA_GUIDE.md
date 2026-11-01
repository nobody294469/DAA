# CLINICALDOC: DEMONSTRATION & VIVA DEFENSE GUIDE

**Document Authority**: Authoritative Demonstration & Defense Manual (Tier 1)  
**Repository**: `nobody294469/DAA` (`d:\daa`)  
**Audit Status**: Verified against Current Codebase (Post-Step 6)  
**Classification**: **RESEARCH / PILOT PROTOTYPE — P0 IMPLEMENTATION COMPLETE WITH LIMITATIONS**  
**Date**: November 2026

---

# PART I: 10–15 MINUTE DEMONSTRATION SCRIPT

This demonstration protocol is designed to be executed in the current standalone environment using the existing Python virtual environment (`d:\daa\.venv`). It requires **zero external GPU infrastructure**, **zero cloud API keys**, and **zero external hospital credentials**.

---

## 1. Demonstration Setup & Prerequisites

### 1.1 Environment Verification
Open a PowerShell terminal in the repository root (`d:\daa`):

```powershell
# Set repository directory
cd d:\daa

# Verify Python virtual environment
d:\daa\.venv\Scripts\python.exe --version
# Expected: Python 3.12.x

# Verify pytest executable
d:\daa\.venv\Scripts\pytest.exe --version
# Expected: pytest 8.3.x
```

### 1.2 Demonstration Fixture
We use the verified synthetic clinical document:
- Path: `external_eval/synthetic_benchmark/notes/SYNTH_CARDIO_01.txt`
- Description: Inpatient cardiology consultation note containing patient demographics, clinical vitals, active prescriptions (Lisinopril, Atorvastatin, Metoprolol Tartrate), lab values (serum potassium, troponin), and clear clinical diagnoses.

---

## 2. Step-by-Step 12-Minute Demonstration Flow

### Minute 0:00 – 2:00: Pipeline Architecture & Ingestion
**Goal**: Explain the problem and show the input fixture.
1. **Show the Input Note**:
   ```powershell
   Get-Content external_eval\synthetic_benchmark\notes\SYNTH_CARDIO_01.txt | Select-Object -First 25
   ```
   *Speaking Point*: "Traditional LLMs process this text as a raw token stream, often transposing dosages (e.g., swapping 10 mg and 25 mg) or hallucinating medications. ClinicalDoc treats text as physical evidence: every word has character coordinates and page geometry."

2. **Run CLI Extraction & Draft Containment**:
   ```powershell
   d:\daa\.venv\Scripts\python.exe -m clindoc.cli process external_eval\synthetic_benchmark\notes\SYNTH_CARDIO_01.txt --output-dir scratch\demo_run
   ```
   *Expected Output*:
   - Creates `scratch\demo_run\fhir_bundle.draft.json`
   - Creates `scratch\demo_run\GOVERNANCE_NOTICE.txt`

---

### Minute 2:00 – 4:00: Evidence Grounding & FHIR R4B Structure
**Goal**: Show the generated draft bundle and prove evidence grounding.
1. **Inspect Draft Metadata & Containment Tags**:
   ```powershell
   Get-Content scratch\demo_run\fhir_bundle.draft.json | Select-String -Pattern "UNREVIEWED_CLI_DRAFT|UNAUTHORIZED_RESEARCH_DRAFT|Bundle.signature" -Context 1,2
   ```
   *Speaking Point*: "Notice DEF-004 in action. The bundle contains normative FHIR R4B meta tags marking it as `UNREVIEWED_CLI_DRAFT` and `UNAUTHORIZED_RESEARCH_DRAFT`. Furthermore, `Bundle.signature` is completely omitted, preventing anyone from misrepresenting this CLI output as clinically signed."

2. **Inspect Evidence Span Grounding**:
   ```powershell
   Get-Content scratch\demo_run\fhir_bundle.draft.json | Select-String -Pattern "evidence_spans|provenance" -Context 1,3
   ```
   *Speaking Point*: "Every extracted Observation and MedicationStatement links to a `Provenance` entry containing the exact start and end character offsets in the source text. No assertion exists without an evidentiary anchor."

3. **Show Governance Notice**:
   ```powershell
   Get-Content scratch\demo_run\GOVERNANCE_NOTICE.txt
   ```
   *Speaking Point*: "The system automatically produces an institutional governance notice detailing that unreviewed CLI output must never enter a patient's medical chart."

---

### Minute 4:00 – 6:30: Proof of Transport Rejection (DEF-004)
**Goal**: Prove mathematically that CLI drafts cannot be dispatched to hospital EHR systems.
1. **Execute Dedicated Draft Rejection Suite**:
   ```powershell
   d:\daa\.venv\Scripts\pytest -q tests\test_cli_draft_containment.py
   ```
   *Expected Output*: `15 passed in ~3s`.
2. **Highlight the Core Transport Assertion**:
   *Speaking Point*: "In `src/clindoc/services/ehr_transport.py`, the `assert_no_draft_tags()` function inspects every payload at the transport boundary. If an unreviewed draft is sent to Mock, HTTP, or MLLP transports, it raises `EHRDraftRejectionError` with HTTP 403 Forbidden. There is no `--force` or `--skip-review` bypass flag."

---

### Minute 6:30 – 9:00: Dual-Custody Four-Eyes Governance (F-12)
**Goal**: Demonstrate that no clinician can self-approve their own work across multi-hop revisions.
1. **Execute Dedicated Four-Eyes Ancestry Suite**:
   ```powershell
   d:\daa\.venv\Scripts\pytest -q tests\test_four_eyes_ancestry.py
   ```
   *Expected Output*: `27 passed in ~4s`.
2. **Explain Root Ancestry Locking**:
   *Speaking Point*: "In collaborative hospital environments, Reviewer A creates Version 1 ($v_1$), Reviewer B makes an edit ($v_2$), and Reviewer A returns to approve Version 3 ($v_3$). Our F-12 control traverses the complete ancestry back to $v_1$. Because Reviewer A was the creator of $v_1$, their approval attempt is rejected with `FourEyesViolationError`."

---

### Minute 9:00 – 11:00: Medication Lifecycle Safety (DEF-002 / REQ-44)
**Goal**: Demonstrate how ClinicalDoc prevents discontinued drugs from appearing as active.
1. **Execute Dedicated REQ-44 Suite**:
   ```powershell
   d:\daa\.venv\Scripts\pytest -q tests\test_phase523_milestone4_4.py
   ```
   *Expected Output*: `38 passed in ~12s`.
2. **Explain Category 9 Fail-Closed Behavior**:
   *Speaking Point*: "When a doctor writes 'Discontinue Lisinopril' without referencing an explicit order ID or cessation proof, direct parsers often guess. Under REQ-44 Category 9, ClinicalDoc fails closed: it suppresses the `MedicationStatement` entirely (`UNREFERENCED_ORDER_CANCELLATION_FAIL_CLOSED`), forcing the clinical reviewer to make the reconciliation decision in Mode 0."

---

### Minute 11:00 – 12:30: Audit Ledger & Gate 9 Attestation (DEF-003)
**Goal**: Demonstrate crash-consistent tamper-evident audit projection and digital signing.
1. **Execute Audit Ledger Concurrency & Signing Suite**:
   ```powershell
   d:\daa\.venv\Scripts\pytest -q tests\test_audit_ledger_concurrency.py
   ```
   *Expected Output*: `30 passed in ~35s`.
2. **Explain Gate 9 Signing**:
   *Speaking Point*: "Gate 9 acts as the ultimate gatekeeper. It verifies DB-to-JSONL ledger sequence equivalence, validates the independent HMAC checkpoint, calculates a canonical SHA-256 digest of the clinical resources, and affixes an Ed25519 digital signature. The exact bytes signed are guaranteed to be the exact bytes dispatched."

---

## 3. Fallback Demonstration Path (Offline / Constrained Mode)

If any interactive command fails due to local terminal locks or missing temporary files:
1. **Fallback Command**:
   Run the dedicated fast integration smoke test:
   ```powershell
   d:\daa\.venv\Scripts\pytest -q tests\test_cli.py tests\test_cli_draft_containment.py
   ```
   *Result*: 22 passed in < 5 seconds.
2. **Direct Inspection of Signed Sample Bundle**:
   Inspect existing generated FHIR bundles in `data/fhir/` to show normative R4B resource structures, LOINC coding, and Provenance records.

---

# PART II: VIVA & TECHNICAL DEFENSE PREPARATION

---

## Section A: 20 Likely Technical Questions & Implementation Answers

### Q1: How does ClinicalDoc avoid hallucination in clinical entity extraction?
**Answer**: ClinicalDoc does not use an unconstrained generative LLM to output free text. Instead, candidate entities are extracted using a hybrid approach (deterministic regex rules for structured data + GLiNER zero-shot token classifiers). Every extraction is converted into an atomic claim with precise character offsets and bounding-box coordinates. The claim must then pass a 3-stage verification cascade (Stage A string match, Stage B DeBERTa-v3 NLI entailment $\ge 0.85$, and Stage C clinical rules). Any unverified claim is quarantined for mandatory human review (Mode 0).

### Q2: What is "Mode 0" in ClinicalDoc's review architecture?
**Answer**: Mode 0 is the human-in-the-loop review architecture defined in Phase 0. Rather than allowing automated direct-to-EHR export, Mode 0 requires that claims with low verification confidence ($< 0.95$), conflicting evidence, or high-risk clinical profiles (anticoagulants, chemotherapy, abnormal labs) be presented to a qualified clinician or CDI specialist in a web review interface before export.

### Q3: Explain the F-12 Four-Eyes multi-hop ancestry rule.
**Answer**: Implemented in [`src/clindoc/review/approval.py`](file:///d:/daa/src/clindoc/review/approval.py), the rule mandates that the clinician who performs final approval of revision $v_k$ cannot be the author of the original revision $v_1$, nor any intermediate editor who modified the document. The system inspects the database revision tree backwards to $v_1$, locking the root ancestor and rejecting self-approval attempts.

### Q4: What is REQ-44 and why was its default inverted in DEF-002?
**Answer**: REQ-44 governs medication lifecycle semantics across a 9-category evidence matrix. Historically, `enforce_req44_lifecycle` defaulted to `False` in legacy Phase 4 scripts. DEF-002 inverted the default to `True` repository-wide, ensuring that unreferenced cancellation directives ("Discontinue Drug X") without order IDs fail closed (`UNREFERENCED_ORDER_CANCELLATION_FAIL_CLOSED`), completely suppressing the medication from emission rather than risking an active prescription in the EHR.

### Q5: How does the transactional outbox work in DEF-003?
**Answer**: When a clinician approves a document, the business state update and the audit event are committed in the same atomic PostgreSQL transaction in `clindoc_audit_events`. An asynchronous engine (`AuditProjectionEngine`) reconciles the outbox table to the append-only `audit_ledger.jsonl` file.

### Q6: How does `AuditProjectionEngine.reconcile_outbox()` ensure ledger integrity?
**Answer**: Before appending any new event, the projection engine reads all existing records from sequence 1 through $S_{file}$. It verifies strict sequence continuity, previous-event SHA-256 hash chaining, current-event hashes, and exact field correspondence with PostgreSQL. If any discrepancy or tampering is detected, it aborts without advancing the HMAC checkpoint.

### Q7: What happens if an audit ledger file is corrupted on disk?
**Answer**: Under Stage 3.3 forensic streaming quarantine ([`src/clindoc/audit/quarantine.py`](file:///d:/daa/src/clindoc/audit/quarantine.py)), the corrupted file is locked, streamed into a timestamped forensic quarantine file (`ledger.jsonl.quarantine.<timestamp>`), and the active ledger is safely truncated back to the last known valid prefix.

### Q8: What prevents an attacker from forging the HMAC checkpoint in `audit_checkpoint.json`?
**Answer**: The HMAC-SHA256 checkpoint is computed using an independent secret key (`CLINDOC_CHECKPOINT_KEY`) that is stored in environment configuration and never written to the ledger directory. Knowledge of the ledger contents alone is mathematically insufficient to forge a valid checkpoint signature.

### Q9: What is Gate 9 and what checks does it perform?
**Answer**: Gate 9 ([`src/clindoc/services/fhir_export.py`](file:///d:/daa/src/clindoc/services/fhir_export.py)) is the final attestation gateway prior to clinical bundle export. It verifies: (1) document is formally approved under F-12 Four-Eyes rules, (2) PostgreSQL audit log matches the JSONL ledger, (3) JSONL HMAC checkpoint is valid, (4) bundle passes FHIR R4B schema validation, and (5) computes a canonical SHA-256 clinical digest and signs it using Ed25519.

### Q10: How does DEF-004 prevent CLI outputs from escaping into hospital EHRs?
**Answer**: CLI commands (`clindoc process` and `batch`) generate `fhir_bundle.draft.json` with normative tags `UNREVIEWED_CLI_DRAFT` and `UNAUTHORIZED_RESEARCH_DRAFT`, and omit `Bundle.signature`. At all transport call sites (Mock, HTTP REST, MLLP), `assert_no_draft_tags()` intercepts the payload and raises `EHRDraftRejectionError` (HTTP 403 Forbidden).

### Q11: What FHIR R4B resource type is used as the container for clinical documents?
**Answer**: A FHIR `Bundle` of type `document`. The first entry is always a `Composition` resource defining document metadata, author, and section structure, followed by `Patient`, `Observation`, `MedicationStatement`, `Condition`, `Encounter`, and `Provenance` resources.

### Q12: How are transient fields handled during Gate 9 bundle digest calculation?
**Answer**: In [`src/clindoc/fhir/signing.py`](file:///d:/daa/src/clindoc/fhir/signing.py), transient metadata that changes during network transit or serialization (`id`, `meta.lastUpdated`, `meta.versionId`, and `Bundle.signature` itself) are stripped before canonical JSON sorting and SHA-256 hashing.

### Q13: Why is `portalocker` used alongside `msvcrt` in `src/clindoc/audit/locking.py`?
**Answer**: Windows uses mandatory file locking via `msvcrt.locking`, while POSIX systems use advisory locking via `fcntl.flock`. `portalocker` wraps both primitives, providing cross-platform multi-process mutual exclusion to prevent write interleaving during high-concurrency ledger writes.

### Q14: How does Stage A verification handle minor OCR recognition noise?
**Answer**: Stage A computes the normalized Levenshtein edit distance between the extracted claim text and the best matching candidate substring in the OCR page text. If the distance is $\le 0.15$ and character bounding boxes are contiguous, it passes Stage A alignment.

### Q15: What role does DeBERTa-v3 play in Stage B verification?
**Answer**: Stage B formats the source sentence as a premise and the atomic claim as a hypothesis, feeding them into a cross-encoder NLI model (`cross-encoder/nli-deberta-v3-small`). If the model predicts `contradiction` or if `entailment` probability is $< 0.85$, the claim fails verification.

### Q16: How does ClinicalDoc ensure patient identity consistency across multi-page documents?
**Answer**: The patient matcher extracts MRN, Name, and DOB from header/footer blocks across all pages. If any page contains an MRN or demographic profile that conflicts with the primary patient, the pipeline raises `IdentityMismatchError` and halts processing immediately.

### Q17: What is the exact mathematical structure of the JSONL audit ledger hash chain?
**Answer**: Each record $i$ contains:
$$\text{current\_event\_hash}_i = \text{SHA256}(\text{sequence}_i \parallel \text{timestamp}_i \parallel \text{event\_type}_i \parallel \text{payload}_i \parallel \text{previous\_event\_hash}_i)$$
where $\text{previous\_event\_hash}_1 = 0^{64}$.

### Q18: Can a modified middle record survive ledger reconciliation if the tail hash is preserved?
**Answer**: No. Because `reconcile_outbox()` performs full-prefix validation from sequence 1 through $S_{file}$, altering any middle record breaks the SHA-256 linkage to subsequent records and mismatches the authoritative PostgreSQL event at that sequence number, causing immediate reconciliation rejection.

### Q19: Why are `--force` and `--skip-review` flags rejected by the CLI?
**Answer**: In healthcare software, bypass flags create severe legal and clinical liability by allowing developers or automated scripts to push unvalidated drafts directly to endpoints. DEF-004 explicitly forbids any bypass arguments at the CLI parser level.

### Q20: What are the 11 warnings observed during the full test suite execution?
**Answer**: The 11 warnings consist of standard Pydantic v2 migration notices (e.g., `model_dump()` vs `dict()`) and SQLAlchemy 2.0 declarative base deprecation warnings in legacy test fixtures. They do not affect runtime execution or correctness.

---

## Section B: 10 Challenging Questions on Limitations, Safety & Clinical Correctness

### Q21: Why does test `test_phase42_observation_lifecycle_temporal.py:480` fail, and does this mean ClinicalDoc has a medication defect?
**Answer**: No, it is not a defect. It is an intentional, safer behavior. That legacy test expected an unreferenced cancellation sentence (*"Discontinue Lisinopril"*) to emit a `MedicationStatement` with `status="stopped"`. However, under DEF-002 (REQ-44 Category 9), unreferenced cancellations lacking order IDs are suppressed entirely (`UNREFERENCED_ORDER_CANCELLATION_FAIL_CLOSED`) to prevent guessing. The test called the function without disabling REQ-44, so zero resources were emitted, causing `next()` to raise `StopIteration`. The dedicated REQ-44 test suite passes 100% (38/38). Under strict change control, we did not alter the legacy test.

### Q22: Can ClinicalDoc be deployed autonomously in an emergency department today?
**Answer**: Absolutely not. ClinicalDoc is classified as a **RESEARCH / PILOT PROTOTYPE**. It has not undergone clinical safety trials, holds no FDA 510(k) software-as-a-medical-device clearance, and is architected specifically with mandatory Mode 0 clinician review before any record can reach an EHR.

### Q23: What happens if an OCR engine misreads "50 mg" as "500 mg" due to a smudge?
**Answer**: Stage A string alignment will match the smudge if the OCR text itself contains "500 mg". However, Stage C (Clinical Domain Sanity) inspects standard pharmaceutical dosage ranges for the drug. A 10x overdose of Lisinopril or Metoprolol triggers a Stage C sanity violation, flagging the claim as `UNVERIFIED` and routing it to Mode 0 clinician review with a high-priority dosage warning.

### Q24: How does ClinicalDoc handle handwritten clinical notes?
**Answer**: Handwritten clinical prose is currently an unsupported document category. While the OCR pipeline will attempt recognition via Tesseract/Surya, character recognition confidence scores for unconstrained handwriting typically fall below our quality floor, triggering an ingestion screening failure and failing closed.

### Q25: What is the failure mode if the PostgreSQL database crashes halfway through Gate 9 signing?
**Answer**: Because the transactional outbox pattern commits audit events in the database before file projection, and Gate 9 verifies DB-to-JSONL equivalence, an unprojected DB event will be safely reconciled on the next worker cycle. If the crash occurs after signing but before network transmission, the transport will fail, leaving the document in `APPROVED` status ready for replay without data corruption.

### Q26: How does ClinicalDoc protect against prompt injection or adversarial text in scanned documents?
**Answer**: The core extraction and verification pipeline relies on deterministic regex parsers, GLiNER token classifiers, and cross-encoder NLI models—none of which are instruction-following autoregressive models susceptible to prompt injection. The external vLLM narrative extractor is restricted to structured JSON output schemas and its outputs are strictly validated by the 3-stage cascade.

### Q27: Why doesn't the system automatically map unreferenced stopped medications to `MedicationStatement.status = "stopped"`?
**Answer**: Without an explicit prior order ID or documented patient cessation date, emitting a stopped status can cause EHR reconciliation logic to inadvertently overwrite a valid active prescription from another clinical provider, or create confusing duplicate records. In clinical informatics, omission with human review is safer than speculative lifecycle updates.

### Q28: How does the system handle borderless laboratory tables where numbers bleed across columns?
**Answer**: Borderless and merged-cell tables represent a known limitation. In `src/clindoc/ocr/table_layout.py`, we employ horizontal line projection and whitespace clustering. If column separation confidence is low, the parser flags the table block as ambiguous, preventing automated extraction and requiring clinician entry in Mode 0.

### Q29: Can two simultaneous workers write to `audit_ledger.jsonl` at the same microsecond?
**Answer**: No. Every read, reconciliation, and append operation acquires an exclusive, non-blocking file lock via `portalocker`. If Worker B attempts to project while Worker A holds the lock, Worker B waits up to a configurable timeout (default 10s) before raising a lock acquisition error.

### Q30: If a clinician in Mode 0 makes a mistake and approves an incorrect dosage, who is responsible?
**Answer**: The human clinician retains clinical and legal responsibility. ClinicalDoc acts as an assistive decision-support tool. The immutable PostgreSQL audit log, JSONL ledger, and signed FHIR `Provenance` record capture the exact identity of the clinician who accepted or modified the claim.

---

## Section C: 5 Questions on Benchmark Validity & Scientific Integrity

### Q31: Why are benchmark denominators strictly segregated rather than merged into a single score?
**Answer**: Merging diverse datasets (synthetic laboratory tables, semi-synthetic inpatient discharge notes, de-identified ophthalmology notes) into a single composite accuracy score obscures category-specific failure modes and constitutes poor scientific practice. Each benchmark tests a distinct capability with an isolated denominator.

### Q32: What does the M2 Verification V3 benchmark (5,629 claims) actually prove?
**Answer**: It proves that the 3-stage verification cascade (Stage A string match, Stage B DeBERTa-v3 NLI, Stage C clinical rules) reliably detects injected factual corruptions, dosage swaps, and hallucinations in structured templates with 96.8% accuracy. It does **not** prove that the entity extraction subsystem can find all clinical concepts in messy, unformatted clinical prose.

### Q33: Why did GLiNER-120 achieve a Micro F1 of only 0.2489?
**Answer**: GLiNER was evaluated in a pure zero-shot configuration across 120 dense clinical documents without task-specific fine-tuning. This low baseline score was an important empirical finding in Phase 0: it demonstrated that zero-shot NER models cannot be relied upon autonomously in clinical extraction and justified the architectural decision to mandate deterministic regex fallbacks and human review.

### Q34: What is the nature of the Glaucoma Real-Prose Benchmark (200 records / 309 mentions)?
**Answer**: It consists of 200 de-identified real-world outpatient Ophthalmology consultation records. 150 records contain active topical glaucoma medication regimens (prostaglandin analogues, beta blockers, alpha agonists), while 50 records are negative controls. The ground truth denominator is exactly 309 mentions ($TP + FN = 309$), measuring real-world clinical prose extraction accuracy in a specialized outpatient domain.

### Q35: Does high accuracy on the OOD-34 benchmark (97.48%) mean ClinicalDoc can process any hospital's templates?
**Answer**: No. The OOD-34 benchmark measures **verification** accuracy on claims already extracted from 34 unfamiliar templates. It demonstrates that the verification cascade does not produce false positives when presented with unfamiliar layouts. However, it does not measure extraction recall—meaning unfamiliar templates may still suffer from missed entities that require manual entry.
