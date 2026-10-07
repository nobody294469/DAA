# PRODUCTIZATION_DECISIONS.md
## Architectural Decisions for the Clinical Document Verification & FHIR Productization (MVP)

**Status:** APPROVED ARCHITECTURAL BASELINE FOR MVP  
**Version:** 1.0  
**Date:** 2026-10-04  
**Authoritative Context:**  
- Extends: [`PROJECT_SPEC.md`](PROJECT_SPEC.md) (Research/ML baseline remains authoritative)  
- Formalizes: [`PRODUCTIZATION_SPEC.md`](PRODUCTIZATION_SPEC.md) (Translates specification into binding decisions)  
- Experimental Grounding: [`experiments/FINAL_REPORT_DATA_SHEET.md`](experiments/FINAL_REPORT_DATA_SHEET.md)  
- Audit Alignment: [`experiments/FINAL_PROJECT_RECONCILIATION_AUDIT.md`](experiments/FINAL_PROJECT_RECONCILIATION_AUDIT.md)  
- Project Narrative: [`reports/FINAL_PROJECT_REPORT.md`](reports/FINAL_PROJECT_REPORT.md)

---

### Non-Claims and Regulatory Boundary (Mandatory Context)

1. **Research Prototype Foundation:** The current codebase is an academic research prototype. It has been evaluated strictly on a synthetic/semi-synthetic clinical document benchmark (120 documents, 5,629 claims). No real clinical patient data (e.g., n2c2) has been processed through the complete cascade.
2. **Evidence Fidelity vs. Clinical Truth:** The system evaluates document-evidence fidelity (whether extracted claims faithfully match the source document text). It does not evaluate clinical correctness, medical judgment, or therapeutic appropriateness.
3. **No Claim of Regulatory Compliance:** Implementing security, privacy, and audit controls does not constitute HIPAA, Indian Digital Personal Data Protection Act (DPDPA), or CDSCO compliance. Compliance requires institutional legal agreements, administrative policies, physical security, and official certification.
4. **No Clinical Decision Support (CDS):** The software is designed exclusively as an administrative digitization and verification tool for medical records staff. It does not provide automated diagnosis, triage, risk scoring, or treatment recommendations.

---

## 1. Core Architectural Decisions

### PD-001: Backend Architecture — Modular Monolith over Microservices
- **Decision:** The production MVP backend will be implemented as a **modular monolith** in Python (FastAPI) paired with a background worker process executing the same codebase, backed by PostgreSQL. LLM inference is decoupled to a dedicated internal vLLM model server.
- **Rationale:** The system is an asynchronous batch-processing pipeline with no high-frequency trading or ultra-low-latency requirements (`PROJECT_SPEC.md` §3). A distributed microservices architecture (e.g., separate services for OCR, extraction, normalization, verification, FHIR) would introduce massive networking overhead, distributed transaction complexity, deployment fragility, and operational burden without any scalability benefit at this stage. A clean modular monolith with strict internal boundaries preserves domain isolation while keeping deployment and local debugging simple and reliable.
- **Implementation Implications:**
  - Single repository and deployment artifact for backend and workers.
  - Asynchronous background tasks managed via a transactional database queue (`FOR UPDATE SKIP LOCKED` in PostgreSQL) or lightweight task runner.
  - Zero external message brokers (no Kafka, RabbitMQ, or Redis required for MVP).
- **Explicitly Deferred:** Distributed microservices decomposition, Kubernetes-based microservice mesh, multi-region distributed transactions.

---

### PD-002: Human-in-the-Loop — Mode 0 Mandatory Review for All Claims
- **Decision:** For the MVP, the system operates exclusively in **Mode 0 (Full Human Review)**. **No claim or document may be exported as final FHIR output without an explicit human review decision (ACCEPT, EDIT, or REJECT).**
- **Rationale:** The verification cascade achieved 97.48% overall accuracy (46 errors across 1,826 OOD claims) and 99.31% accuracy in the autonomous Stage B coverage zone (10 errors in 1,445 claims, risk 0.0069) on the synthetic benchmark. However, synthetic accuracy cannot be conflated with clinical safety on uncurated, messy real-world clinical documents. Until local clinical validation on site-specific documents is conducted, zero autonomous exports can be permitted. Automation serves to prioritize, sort, and highlight evidence for human reviewers, not replace them.
- **Implementation Implications:**
  - All extracted claims enter state `PENDING_REVIEW`.
  - The UI presents claims sorted by risk (flagged/discrepant claims first).
  - The export action is cryptographically blocked by the backend unless 100% of required claims in the document have reached a terminal review state (`ACCEPTED`, `EDITED`, or `REJECTED`).
  - Document approval requires explicit sign-off by an authorized Approver.
- **Explicitly Deferred:**
  - Mode 1 (selective pre-acceptance of high-confidence Stage B claims with random QA sampling).
  - Mode 2 (fully autonomous straight-through export of verified documents).

---

### PD-003: LLM Gateway & Mock Backend Containment
- **Decision:** All interactions with Large Language Models (router narrative extraction and Stage C judge verification) must route through a unified internal **LLM Gateway**. The `MockLLMProvider` and `DefaultMockLLMJudge` are strictly designated as development and unit-testing utilities. In the `production` runtime profile, the system must fail to start if any mock or heuristic backend is active.
- **Rationale:** In the research prototype, two parallel LLM abstractions existed (`extract/llm_extractor.py` and `verify/stage_c.py`), and default invocations silently fell back to mock/heuristic behavior when external endpoints were missing. Silent fallback to mocks in a real environment creates catastrophic hidden failures where regex or keyword rules masquerade as AI verification.
- **Implementation Implications:**
  - Implement gateway adapters wrapping existing interfaces without rewriting ML pipeline code.
  - Every model execution record must persist `provider_kind` (`vllm`, `mock`, `heuristic`).
  - The FHIR export pipeline must verify that no claim was verified using `provider_kind != vllm` (or heuristic Stage B); any mock-derived claim halts export with an unrecoverable validation error.
  - The production profile checks configuration on boot and crashes immediately if mock classes or invalid endpoint URLs are supplied.
- **Explicitly Deferred:** Multi-provider commercial cloud routing (OpenAI, Anthropic, Bedrock), dynamic runtime fallback across different LLM families.

---

### PD-004: Learned Verifier Integrity & Fallback Containment
- **Decision:** ModernBERT-Biomedical remains the primary learned verifier (Stage B). In the production profile, the system must fail-closed if the trained model checkpoint (`best_model.pt`) or calibrated temperature parameters are missing or corrupted. The silent heuristic fallback (`_heuristic_logits`) is prohibited in production.
- **Rationale:** Stage B is the core research contribution, autonomously resolving 79.13% of claims with 99.31% precision at threshold 0.85 (data sheet §3.4). In the research code, `LearnedVerifier` silently fell back to heuristic logits when no model path was passed. In production, such silent degradation invalidates the entire verification foundation.
- **Implementation Implications:**
  - Checkpoint loading must enforce secure deserialization (`weights_only=True` or converted safetensors), eliminating the arbitrary code execution vulnerability of `torch.load(..., weights_only=False)`.
  - Startup health checks verify the presence and SHA-256 hash of the ModernBERT checkpoint.
  - If ModernBERT fails at runtime, the processing job halts with `STAGE_B_UNAVAILABLE` and is scheduled for retry; it never degrades to heuristic guessing.
- **Explicitly Deferred:** Online continuous fine-tuning, dynamic active-learning retraining loops.

---

### PD-005: Qualified Configuration Manifest & Replay Invariance
- **Decision:** The production deployment must pin all pipeline parameters inside a cryptographically verified **Qualified Configuration Manifest**. Any run in production must verify against this manifest or visibly flag its outputs as `UNQUALIFIED`.
- **Rationale:** Experimental claims (V8 cascade accuracy 97.48%, Stage B risk 0.0069) were established under exact configurations: ModernBERT V3 checkpoint, temperature $T = 1.146522$, escalation threshold $0.60$, Qwen2.5-7B-Instruct-AWQ, and Prompt V1. Modifying any threshold, prompt wording, or checkpoint changes system behavior unpredictably.
- **Implementation Implications:**
  - Pinned parameters: ModernBERT checkpoint SHA-256, Qwen revision hash, Prompt V1 SHA-256, calibrator parameters, Stage A deterministic tolerance, Stage B threshold (0.60 / 0.85).
  - Introduce replay-invariance tests in CI: running recorded frozen inputs through the pipeline must reproduce audited decisions bitwise or within tight statistical tolerance.
  - The UI and exported audit logs must prominently indicate whether the output was generated under a `QUALIFIED` or `UNQUALIFIED` configuration.
- **Explicitly Deferred:** Automated dynamic hyperparameter re-tuning in production.

---

### PD-006: User Authentication & Identity Federation
- **Decision:** User authentication will be delegated entirely to an organizational Identity Provider (IdP) using standard OpenID Connect (OIDC) / OAuth 2.0 with PKCE. The application will not manage or store user passwords. For local development and standalone testing, a standard local OIDC container (e.g., Keycloak or Authelia) will be provided.
- **Rationale:** Storing passwords creates severe compliance risks, security vulnerabilities, and credential management overhead. Enterprise healthcare environments already mandate centralized directory management (Active Directory, Azure AD, Okta, PingIdentity) with Multi-Factor Authentication (MFA).
- **Implementation Implications:**
  - Backend validates signed JWT tokens issued by the IdP.
  - Application maintains a local `users` table storing external `idp_subject`, name, and email for reference, but zero password hashes.
  - Roles (Operator, Reviewer, Approver, Admin, Auditor) are mapped from IdP group claims or assigned by the System Administrator in the application database.
- **Explicitly Deferred:** Custom username/password database, social login providers, biometric authentication integrations.

---

### PD-007: Database Architecture & Storage Separation
- **Decision:** Relational data, operational state, review workflows, and audit trails will be stored in **PostgreSQL**. Document binary files (PDFs, TIFFs, page images) will be stored in an **encrypted object/blob store** (filesystem with LUKS/BitLocker or self-hosted MinIO with SSE). The database stores only file references and SHA-256 hashes, never raw binary documents.
- **Rationale:** The relational model cleanly supports ACID state transitions, complex review queries, and immutable audit logs. Relational integrity guarantees that claims, evidence, and verification records remain permanently linked. Storing large binary blobs directly in PostgreSQL causes database bloat and degrades backup performance.
- **Implementation Implications:**
  - PostgreSQL schema covering: `users`, `roles`, `user_roles`, `documents`, `processing_jobs`, `claims`, `evidence`, `verification_results`, `review_actions`, `document_approvals`, `fhir_bundles`, `fhir_exports`, `audit_events`, and `model_runs`.
  - Blob store requires client-side or server-side encryption-at-rest (AES-256).
  - Background worker queue implemented via PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`.
- **Explicitly Deferred:** Separate NoSQL document databases, distributed search clusters (Elasticsearch), specialized vector databases for review state.

---

### PD-008: Tenancy Model — Single-Tenant per Deployment
- **Decision:** The software will be deployed as a **single-tenant** system per healthcare organization or facility instance.
- **Rationale:** Multi-tenancy in healthcare systems introduces severe data isolation risks, database row-level security leak potentials, and cross-organization cross-contamination. Single-tenant architecture guarantees physical and logical database separation per hospital or client, simplifying compliance with data residency and privacy mandates.
- **Implementation Implications:**
  - Database schemas and storage volumes are isolated to a single customer instance.
  - Zero multi-tenant routing logic or tenant identifier filtering in application queries.
  - Sizing and scaling are calculated per facility.
- **Explicitly Deferred:** Multi-tenant SaaS architecture, shared multi-customer databases, tenant-level metering and billing.

---

### PD-009: RBAC, Separation of Duties, and Four-Eyes Policy
- **Decision:** Role-Based Access Control (RBAC) will strictly separate administrative control from clinical data access. System Administrators shall have configuration and operational rights but **no default access to document content or clinical claims (PHI)**. Furthermore, a configurable **Four-Eyes Policy** (`require_distinct_approver`) will allow organizations to mandate that the Document Operator who uploaded a document cannot be the sole approver for export.
- **Rationale:** Insider threats and unauthorized clinical record browsing represent significant healthcare security violations. Administrative accounts are prime targets for credential compromise; preventing admins from accessing PHI mitigates data breach blast radius. Four-eyes verification prevents transcription fraud and accidental single-operator error.
- **Implementation Implications:**
  - Roles defined: `Document Operator`, `Clinical Data Reviewer`, `Approver`, `System Administrator`, `Auditor/Quality Officer`, `Integration Service Account`.
  - Backend API enforces object-level access control on every endpoint; admin roles requesting document contents are rejected with 403 Forbidden.
  - If `require_distinct_approver` is enabled in tenant settings, the approval endpoint rejects approval requests where `reviewer_id == uploader_id`.
- **Explicitly Deferred:** Attribute-Based Access Control (ABAC), dynamic policy engines (OPA) running in sidecars.

---

### PD-010: Reviewer Edits Data Model & Re-Verification
- **Decision:** When a human reviewer edits an extracted claim in the review workbench, the original claim record remains untouched. A new claim version is created with `origin = reviewer`. The edited claim is immediately submitted to **Stage A and Stage B verification** against the source evidence, presenting the updated verification verdict to the reviewer before final confirmation.
- **Rationale:** Human reviewers can make transcription errors or introduce typos. Re-verifying edited claims against the highlighted evidence ensures that human corrections maintain evidence fidelity and provides instant feedback if an edit creates a new contradiction with the text. Maintaining immutable before/after snapshots is a fundamental requirement for regulatory auditability.
- **Implementation Implications:**
  - Schema extension: Claim metadata records `origin` (`pipeline` vs. `reviewer`) and links to `supersedes_claim_id`.
  - A formal DEC record (`docs/decisions/DEC-020`) will document the schema extension to `schemas/claim.json` prior to implementation.
  - UI workflow: Reviewer clicks "Save Edit" -> backend runs Stage A/B -> UI displays "Re-verification: SUPPORTED (0.94)" -> Reviewer confirms edit.
- **Explicitly Deferred:** Free-form manual creation of arbitrary claims without associated source evidence anchors.

---

### PD-011: FHIR Provenance Minimization
- **Decision:** The FHIR generation service must provide a configurable setting (`minimize_provenance_phi: boolean`, default `true`). When enabled, the exported FHIR Provenance extension includes target element paths, source bounding boxes, page numbers, claim IDs, and verification confidence, but **strips the verbatim `evidenceText` string**.
- **Rationale:** The research project's DEC-016 extension embedded verbatim source text into the FHIR Provenance resource. While useful for academic debugging, copying verbatim narrative snippets into external EHRs duplicates PHI across institutional boundaries, increases payload sizes, and complicates patient data deletion requests under privacy laws. The full evidence text remains securely stored and queryable in this system's internal audit log.
- **Implementation Implications:**
  - FHIR mapper inspects `minimize_provenance_phi`. If true, `evidenceText` is omitted from the extension or replaced with a cryptographic hash of the evidence snippet.
  - Internal audit report retains full evidence text, bounding boxes, and image coordinates for regulatory inspections.
- **Explicitly Deferred:** Redaction of clinical concepts from the primary FHIR resource bodies (resources must retain full clinical fidelity).

---

### PD-012: Audit Integrity & Fail-Closed Logging
- **Decision:** Operational and data access events must be written to an **append-only audit log** secured with cryptographic hash chaining (each log entry contains the SHA-256 hash of the preceding entry). If the audit service fails to record an event, **the triggering business transaction must immediately abort and fail closed**.
- **Rationale:** In healthcare compliance, an unrecorded action is considered an unauditable action. If a database glitch allows a user to approve a document or export PHI without recording who performed the action, the integrity of the medical record is legally compromised.
- **Implementation Implications:**
  - Audit table uses database-level permissions: the application database user has `INSERT` and `SELECT` privileges, but `UPDATE` and `DELETE` are revoked at the PostgreSQL engine level.
  - Audit writes occur within the same database transaction as the state change; a transaction failure rolls back both the state change and the audit entry.
  - Periodic background integrity checker validates the cryptographic hash chain.
- **Explicitly Deferred:** Blockchain/distributed ledger audit networks, external write-once-read-many (WORM) optical storage drivers.

---

### PD-013: User Interface Architecture — Browser-Based Review Workbench
- **Decision:** The prototype Streamlit UI (`ui/app.py`) is designated strictly as a research demo and **will be replaced in the MVP by a modern, secure, browser-based Single-Page Application (SPA)** (React/TypeScript or lightweight modern web framework) communicating with the FastAPI backend over authenticated REST APIs.
- **Rationale:** Streamlit executes server-side Python scripts per user interaction, stores session state in server memory, cannot handle fine-grained DOM-level bounding-box drawing over high-resolution multi-page PDFs, lacks native support for enterprise RBAC routing, and relies on insecure inline HTML rendering (`unsafe_allow_html=True`). A dedicated SPA decouples UI presentation from backend logic, supports client-side document pan/zoom with SVG bounding box overlays, and enables enterprise security headers (CSP, CORS).
- **Implementation Implications:**
  - Frontend: Responsive web client supporting high-DPI document rendering (PDF.js / SVG overlay), split-screen evidence viewing, keyboard navigation for rapid review, and plain-language verification badges.
  - API communication via secure HTTP-only cookies and CSRF protection.
  - Streamlit code retained in repository for research replication only, isolated from production build scripts.
- **Explicitly Deferred:** Mobile applications (iOS/Android), desktop native Electron apps, offline-first progressive web apps (PWAs).

---

### PD-014: Primary Customer & Operational Setting
- **Decision:** The primary target customer is a **hospital, clinical network, or medical records digitization contractor's Health Information Management (HIM) department**.
- **Rationale:** HIM departments process high volumes of inbound unstructured documents (scanned referral notes, external lab reports, paper discharge summaries) that require transcription and indexing into the hospital's central electronic system. They possess trained medical records staff capable of performing clinical verification, but suffer from severe manual transcription bottlenecks.
- **Implementation Implications:**
  - Product positioning focuses on transcription acceleration, error reduction, and auditability.
  - Document intake models designed for batches of scanned PDFs and TIFFs rather than individual interactive doctor-patient encounters.
- **Explicitly Deferred:** Direct-to-consumer/patient portal document ingestion, outpatient bedside point-of-care capture.

---

### PD-015: Patient Identity Management — Explicit Confirmation Only
- **Decision:** In the MVP, patient identification relies on **explicit manual selection or operator confirmation**. The system extracts patient demographic claims (Name, MRN, DOB, Gender) from the document header, matches them against the operator-selected patient context, and flags discrepancies. **The system will NOT attempt automated Master Patient Index (MPI) searching or probabilistic record linkage.**
- **Rationale:** Associating a clinical document with the wrong patient record is a catastrophic clinical safety event ("wrong-patient error"). Probabilistic patient matching is notoriously error-prone across Indian healthcare systems due to naming variations, missing demographic fields, and non-standard identifiers. Requiring human confirmation eliminates automated misattribution risk.
- **Implementation Implications:**
  - During document upload, the Document Operator selects or enters the target Patient Identifier / MRN.
  - The extraction pipeline parses header claims (`patient_name`, `mrn`, `dob`, `gender`).
  - If extracted header claims disagree with the selected patient context, the document is flagged with a high-priority warning: `PATIENT_IDENTITY_MISMATCH`.
  - The Approver must explicitly resolve the identity check before the document can be approved.
- **Explicitly Deferred:** Automated probabilistic MPI matching algorithms, biometric patient lookup, national ID (e.g., Aadhaar) biometric verification.

---

### PD-016: FHIR R4B Scope & Export Boundary — File Export Only
- **Decision:** The MVP FHIR export capability is restricted to **FHIR R4B JSON Bundle file generation and download**. Direct live writes to external EHR servers (via FHIR REST API transactions or SMART Backend Services) are deferred to a subsequent phase.
- **Rationale:** Direct EHR write-back requires deep coordination with hospital IT, firewall exceptions, custom EHR vendor approval (Epic, Cerner, or local Indian hospital HIS), and risk of corrupting live clinical records during early pilots. Generating validated, downloadable FHIR R4B Bundles allows hospital IT to inspect, validate, and test-import records through their existing integration engines (e.g., Mirth Connect / NextGen Connect) without risk to live databases.
- **Implementation Implications:**
  - Approved documents generate an R4B `Bundle` containing `Patient`, `Observation`, `DiagnosticReport`, `MedicationStatement`, `Condition`, and `Provenance` resources.
  - Bundles are validated against HL7 FHIR R4B schema definitions using `fhir.resources`.
  - Authorized users or integration accounts can download the bundle via API or UI.
- **Explicitly Deferred:** Direct FHIR REST API `POST`/`PUT` to remote servers, SMART-on-FHIR user launch context, HL7 v2 (ORU/MDM) message encoding, CDA/C-CDA document generation.

---

### PD-017: Deployment Topology & LLM Hosting Boundary
- **Decision:** The entire application and model serving infrastructure will be deployed **on-premises or inside a dedicated private cloud Virtual Private Cloud (VPC)** located within Indian national jurisdiction. The Qwen2.5-7B-Instruct-AWQ model must be served internally via vLLM on a dedicated GPU host on the local network. **No clinical documents, claims, evidence, or prompts may ever be sent to public commercial cloud LLM APIs.**
- **Rationale:** Under Indian healthcare data regulations (DPDPA 2023, DISHA guidelines) and international standards, clinical records constitute sensitive personal data that cannot be routed through unvetted third-party commercial APIs. Self-hosting vLLM guarantees complete data sovereignty, eliminates third-party data retention risks, and protects against external vendor API outages.
- **Implementation Implications:**
  - Colab and ngrok tunnels are completely prohibited in production.
  - GPU node requires an NVIDIA GPU with at least 16 GB VRAM (e.g., T4, L4, A10G, or RTX 4090).
  - Network firewall configuration enforces zero outbound internet egress from the vLLM GPU host.
- **Explicitly Deferred:** Public multi-tenant cloud deployments outside India, commercial LLM API integrations (OpenAI, Anthropic).

---

### PD-018: Verification Cascade Operational Profile
- **Decision:** The operational verification cascade strictly preserves the two-stage learned architecture: **Stage A (deterministic rule-based verifier) and Stage B (calibrated ModernBERT) execute locally for 100% of claims.** Stage C (Qwen2.5-7B LLM judge) is invoked **exclusively for claims that are escalated** (Stage B confidence $< 0.60$ or Stage A contradiction detected).
- **Rationale:** In the audited OOD benchmark, Stage B autonomously resolved 79.13% of claims with 99.31% accuracy, leaving only 20.87% (381 claims) requiring Stage C escalation. ModernBERT executes in milliseconds on standard CPU/GPU hardware, whereas LLM generation requires several seconds per claim. Preserving selective escalation minimizes GPU compute costs, keeps overall processing latency low, and relies on the LLM only where complex semantic reasoning is required.
- **Implementation Implications:**
  - Stage B executes in-process on CPU or shared GPU.
  - LLM Gateway receives requests only when `stage_b_confidence < 0.60` or Stage A flags a contradiction.
  - Under no circumstances is Qwen used as the default or sole verifier.
- **Explicitly Deferred:** End-to-end single-pass LLM document processing, multi-LLM ensemble voting cascades.

---

### PD-019: Fail-Closed Security & Observability Stance
- **Decision:** The system adheres to a strict **fail-closed on export and fail-visible for review** philosophy. If any pipeline component fails, times out, or produces unparseable output, the claim is flagged as unverified and routed to human review with the exact failure reason visible. Furthermore, **application logs, metrics, and trace telemetry must be scrubbed of all PHI/PII**.
- **Rationale:** In clinical systems, a silent failure that allows unverified data to slip through into an export can lead to clinical harm. Conversely, logging raw prompts, OCR text, or clinical values into monitoring systems (Datadog, Prometheus, CloudWatch) causes widespread secondary PHI leakage.
- **Implementation Implications:**
  - Logging framework enforces strict redaction: only document UUIDs, claim IDs, latency, token counts, and error codes are logged; zero clinical text or patient names.
  - Malformed LLM output triggers bounded retries (up to 2), followed by a fallback `INSUFFICIENT_EVIDENCE` verdict with `is_fallback = true` and `error_code = LLM_MALFORMED_OUTPUT`.
  - The UI displays a warning banner: *"Automated verification failed due to model timeout; manual review required."*
- **Explicitly Deferred:** Automated dynamic log anonymization via LLM-based de-identification in the logging path.

---

### PD-020: Data Governance — Synthetic/Semi-Synthetic Documents Only in MVP
- **Decision:** The initial MVP development, testing, staging, and initial demonstration will operate **exclusively on synthetic and semi-synthetic clinical document datasets**. Processing of real-world patient records (PHI) is explicitly prohibited until formal security auditing, institutional ethics review, and Data Protection Impact Assessments (DPIA) are completed.
- **Rationale:** Operating on synthetic data allows engineering teams to build, harden, and test the entire productization stack (APIs, UI, database, queues, RBAC, FHIR export) without incurring severe legal liabilities or exposing patients to data breach risks during the pre-release development cycle.
- **Implementation Implications:**
  - Seed database with synthetic patient profiles and generated clinical documents.
  - Test suites run exclusively on M2 V3 benchmark data and synthetic test suites.
  - Phase 8.12 defines the mandatory gateway before any real clinical document may be loaded into the system.
- **Explicitly Deferred:** Ingestion of live un-anonymized hospital patient records during MVP development.

---

### PD-021: Strict Out-of-Scope Product Boundaries
- **Decision:** To prevent scope creep and maintain absolute architectural integrity, the following capabilities are declared **strictly out of scope for the MVP**:
  1. Clinical Decision Support (CDS) or diagnostic suggestions.
  2. Patient treatment or medication recommendation algorithms.
  3. Longitudinal patient trajectory reasoning or cross-document clinical synthesis.
  4. SNOMED CT terminology integration (MVP retains LOINC, RxNorm, UCUM).
  5. Master Patient Index (MPI) or automated record linkage engines.
  6. SMART-on-FHIR embedded EHR launch workflows.
  7. HL7 v2 (ADT/ORU/MDM) or CDA/C-CDA ingestion and conversion.
  8. Multi-tenant SaaS billing, metering, or tenant-isolation architectures.
  9. Mobile applications (iOS/Android).
  10. Autonomous, unreviewed straight-through EHR database writes.
- **Rationale:** The core value proposition of this project is evidence-grounded document verification and high-fidelity FHIR conversion. Expanding into diagnostic AI or complex legacy integration protocols before perfecting the core verification and human review workbench would compromise quality and jeopardize regulatory positioning.
- **Implementation Implications:** Any feature request touching the above list is immediately rejected for MVP and logged in the product backlog.
- **Explicitly Deferred:** All items 1 through 10 listed above.

---

### PD-022: Representation of Qualitative Laboratory Determinations as lab.value and Export via FHIR R4B Observation.valueString
- **Decision:**
  1. **Dual Representation for `lab.value`:**
     - **Quantitative Findings:** Continue to use `lab.value` for numeric measurement strings (e.g., `"14.2"`, `"95"`), populating `value_numeric` (`14.2`, `95.0`) and `unit_raw` / UCUM codes where applicable.
     - **Qualitative Findings:** Grounded qualitative determinations shall be represented as `lab.value` (e.g., `"Negative"`, `"Positive"`, `"Non-Reactive"`, `"Reactive"`, `"Detected"`, `"Not Detected"`, `"Equivocal"`). In such claims, `value_numeric`, `unit_raw`, and unit codes MUST be explicitly `null`.
  2. **Canonical Qualitative Normalization:**
     - Source evidence spans must always preserve verbatim text via existing `Evidence` grounding (§9).
     - In normalization, recognized qualitative determinations shall map to a canonical controlled vocabulary: `NEGATIVE`, `POSITIVE`, `REACTIVE`, `NON_REACTIVE`, `DETECTED`, `NOT_DETECTED`, `EQUIVOCAL`.
     - Extraction is NOT required to reject grounded qualitative phrases solely because they fall outside this list, but arbitrary commentary (e.g., `"specimen hemolyzed"`) must not silently become normalized clinical findings.
  3. **Strict Separation of `lab.flag` Semantics:**
     - `lab.flag` remains strictly reserved for clinical interpretation / abnormality assessments (`NORMAL`, `HIGH`, `LOW`, `ABNORMAL`, `CRITICAL`).
     - The system MUST NOT automatically manufacture or synthesize `lab.flag` values from qualitative findings (e.g., do not automatically synthesize `NORMAL` from `Negative`). `lab.flag` may only be emitted if explicitly and independently grounded in source text.
  4. **Coexistence of `lab.value` and `lab.flag`:**
     - `lab.value` and `lab.flag` MAY coexist on the same observation entity when independently supported by the source text (e.g., `"HBsAg: Negative (Normal)"` yields `lab.value = "Negative"` and `lab.flag = "NORMAL"`).
  5. **FHIR R4B Export Contract:**
     - Numeric `lab.value` claims map to `Observation.valueQuantity`.
     - Qualitative `lab.value` claims map to `Observation.valueString = claim.value`.
     - Qualitative determinations MUST NEVER default to or be coerced into `Observation.valueQuantity = 0.0`.
     - Terminology mapping to `Observation.valueCodeableConcept` (e.g., SNOMED CT) remains deferred per PD-021.
  6. **Backward Compatibility & Persistence:**
     - Existing numeric laboratory claims remain unchanged.
     - No database migration is required: PostgreSQL `claims.value` is `Text` and full payloads persist in `JSONB`.
- **Rationale:** Diagnostic determinations (e.g., "Negative", "Non-Reactive") represent the primary finding (the *value*) of the laboratory test, not an interpretation of severity. Representing them as `lab.value` complies with HL7 FHIR R4B `Observation.value[x]` (`valueString`), aligns with the existing Stage A deterministic string verifier (`stage_a.py` lines 220–230), and preserves the primary value display in the review UI.
- **Implementation Implications:**
  - `src/clindoc/fhir/mapper.py`: Replace line 829 `0.0` fallback with branching (`val_claim.value_numeric is not None` -> `valueQuantity`; else -> `valueString`).
  - `src/clindoc/extract/llm_extractor.py`: Update `lab.value` prompt definition to permit grounded qualitative determinations alongside quantitative measurements.
  - `src/clindoc/normalize/normalizer.py`: Add regex guard so qualitative strings containing numbers (e.g., `"HIV-1 Negative"`) do not parse `"1.0"`.
  - `PROJECT_SPEC.md` and `PRODUCTIZATION_SPEC.md`: Amend relevant sections to formally reflect dual `lab.value` semantics.
- **Explicitly Deferred:** SNOMED CT terminology normalization (`Observation.valueCodeableConcept`) remains deferred to post-MVP terminology enhancements per PD-021.

---

## 2. Resolution of Open Questions (from PRODUCTIZATION_SPEC.md §17.2)

| Spec ID | Question | MVP Resolution | Status |
|---|---|---|---|
| **OPEN-01** | Target customer type and first pilot site | **Resolved for MVP:** Target customer is an enterprise Hospital / Clinical Network HIM department (PD-014). Specific pilot site selection is an external commercial negotiation. | **DECIDED (MVP) / OPEN (Site)** |
| **OPEN-02** | Deployment model: on-prem vs private cloud; data residency | **Resolved:** Self-hosted on-premises or private cloud VPC located strictly within India (PD-017). | **DECIDED** |
| **OPEN-03** | Is an OIDC IdP available at pilot site; MFA requirements | **Resolved for MVP:** System standardizes on OIDC/OAuth2 with PKCE (PD-006). MVP provides a self-hosted local IdP container (e.g., Keycloak) for standalone testing; specific customer enterprise IdP endpoints will be configured during site deployment. | **DECIDED (MVP) / OPEN (Site IdP)** |
| **OPEN-04** | Target FHIR version and profiles (R4 vs R4B; ABDM profiles) | **Resolved for MVP:** Target format is standard **FHIR R4B JSON Bundle** (PD-016). Alignment with Indian Ayushman Bharat Digital Mission (ABDM) profiles (e.g., NRCeS DiagnosticReport) is deferred to the EHR integration phase. | **DECIDED (MVP) / OPEN (ABDM)** |
| **OPEN-05** | Terminology needs: Indian drug/brand coding, SNOMED CT | **Resolved for MVP:** MVP relies on existing deterministic dictionaries: **LOINC** (labs), **RxNorm** (generic medications), and **UCUM** (units). Indian commercial brand names and SNOMED CT are deferred (PD-021). Commercial licensing of LOINC/RxNorm for commercial software distribution flagged for legal review. | **DECIDED (MVP) / OPEN (Legal)** |
| **OPEN-06** | Patient identity source and matching approach | **Resolved for MVP:** Explicit manual selection/confirmation by Document Operator with automated mismatch detection (PD-015). No automated MPI. | **DECIDED** |
| **OPEN-07** | FHIR `status` policy for approved data; provenance detail | **Resolved:** Approved resources are exported with `status = "final"`. Provenance minimization is enabled by default (`minimize_provenance_phi = true`), omitting verbatim `evidenceText` from the export (PD-011). | **DECIDED** |
| **OPEN-08** | GPU hardware availability and budget | **Resolved for MVP:** Minimum hardware requirement specified: 1x dedicated NVIDIA GPU with $\ge 16$ GB VRAM (T4, L4, or RTX 4090) dedicated to vLLM (PD-017). Specific server procurement depends on host organization. | **DECIDED (MVP) / OPEN (Procurement)** |
| **OPEN-09** | Review staffing model: who may review/approve; four-eyes | **Resolved:** System implements RBAC separating Document Operator, Reviewer, and Approver roles, with a configurable `require_distinct_approver` toggle to enforce four-eyes verification where mandated by hospital policy (PD-009). | **DECIDED** |
| **OPEN-10** | Commercial license review of open-weight models | **OPEN (Legal):** Qwen2.5 (Alibaba open license), BioClinical-ModernBERT (Apache 2.0), GLiNER-BioMed (Apache 2.0), and PaddleOCR (Apache 2.0) are fully cleared for MVP research and internal development. Commercial distribution rights under specific hospital contracts must be vetted by enterprise legal counsel prior to commercial launch. | **OPEN (Legal Review)** |
| **OPEN-11** | Retention periods and legal-hold rules | **Resolved for MVP:** System implements configurable retention policies (e.g., purge unapproved drafts after $N$ days, retain audit logs indefinitely) and soft-delete tombstoning. Exact statutory retention durations (e.g., 3 years vs 7 years under state medical council rules) are configurable tenant settings determined by customer legal counsel. | **DECIDED (Engine) / OPEN (Policy)** |
| **OPEN-12** | Regulatory classification (CDSCO / SaMD vs Admin Tool) | **Resolved:** System is explicitly positioned, architected, and labeled as an **Administrative Transcription & Verification Workbench**, NOT Software as a Medical Device (SaMD) or Clinical Decision Support (CDS) (PD-021). Formal legal classification review remains an organizational milestone. | **DECIDED (Architecture) / OPEN (Regulatory Filing)** |
| **OPEN-13** | UI technology selection | **Resolved for MVP:** Replace prototype Streamlit with a modern, secure **React/TypeScript Single-Page Application** integrated with PDF.js for high-DPI document rendering and SVG bounding-box overlays (PD-013). | **DECIDED** |

---

## 3. Decision Summary Table

| Decision ID | Focus Area | Architectural Decision | Status |
|---|---|---|---|
| **PD-001** | Architecture | Modular monolith (FastAPI) + PostgreSQL queue + dedicated internal vLLM node | **APPROVED** |
| **PD-002** | Human-in-the-Loop | Mode 0 mandatory: 100% of claims require human decision before FHIR export | **APPROVED** |
| **PD-003** | LLM Gateway | Unified internal gateway; fail-closed on mock/heuristic backends in production | **APPROVED** |
| **PD-004** | Verifier Integrity | ModernBERT required; silent heuristic fallback banned; safe checkpoint loading | **APPROVED** |
| **PD-005** | Model Governance | Cryptographic Qualified Configuration Manifest; replay invariance testing | **APPROVED** |
| **PD-006** | Authentication | Centralized OIDC/OAuth2 with PKCE; zero local password storage; RBAC mapping | **APPROVED** |
| **PD-007** | Persistence | PostgreSQL for relational state, jobs, audit; encrypted object store for files | **APPROVED** |
| **PD-008** | Tenancy | Single-tenant deployment per customer instance | **APPROVED** |
| **PD-009** | Access Control | Role separation; Admins denied PHI access; configurable Four-Eyes approval | **APPROVED** |
| **PD-010** | Reviewer Edits | Immutable claim history; edits create `origin=reviewer` claim and trigger re-verification | **APPROVED** |
| **PD-011** | Privacy | FHIR provenance minimization toggle (strip verbatim `evidenceText` from exports) | **APPROVED** |
| **PD-012** | Audit Trail | Append-only audit table with hash chaining; audit write failure halts transaction | **APPROVED** |
| **PD-013** | Frontend UI | React/TypeScript SPA with PDF.js evidence viewer replacing Streamlit prototype | **APPROVED** |
| **PD-014** | Customer Setting | Hospital/Clinic Health Information Management (HIM) digitization departments | **APPROVED** |
| **PD-015** | Patient Identity | Explicit manual confirmation only; header mismatch detection; zero automated MPI | **APPROVED** |
| **PD-016** | Interoperability | FHIR R4B JSON Bundle file export only; direct EHR database writes deferred | **APPROVED** |
| **PD-017** | Deployment | On-prem / private VPC in India; self-hosted vLLM; zero public LLM APIs | **APPROVED** |
| **PD-018** | Verification Cascade | ModernBERT handles primary load; Qwen invoked strictly upon escalation ($<0.60$) | **APPROVED** |
| **PD-019** | Reliability & Safety | Fail-closed export; fail-visible review; zero PHI in telemetry and application logs | **APPROVED** |
| **PD-020** | Data Governance | MVP operates exclusively on synthetic/semi-synthetic data; zero live PHI in MVP | **APPROVED** |
| **PD-021** | Product Scope | Strict exclusion of CDS, diagnosis, treatment, SNOMED, MPI, SMART, HL7 v2, mobile | **APPROVED** |
| **PD-022** | Lab Findings | Qualitative determinations as lab.value; export via FHIR Observation.valueString | **APPROVED** |

---

## 4. MVP Scope (What is IN)

The production-oriented MVP is strictly defined as the **Clinical Document-to-Verified FHIR Workbench**. It contains:

1. **Document Ingestion:**
   - Authenticated upload of PDF and image clinical documents (single or batch).
   - Document screening (file type sniffing, size limit enforcement, SHA-256 deduplication).
   - Secure encrypted storage of raw document binaries in local/MinIO blob storage.
2. **Core AI Processing Pipeline (Preserving Audited Research Code):**
   - Text extraction via born-digital PDF parser or PaddleOCR.
   - Region classification and hybrid routing (deterministic + GLiNER extraction; Qwen narrative extraction via gateway).
   - Deterministic terminology normalization (LOINC, RxNorm, UCUM).
   - Two-stage verification cascade: Stage A deterministic check $\rightarrow$ Stage B calibrated ModernBERT ($T=1.146522$) $\rightarrow$ Stage C Qwen2.5-7B-Instruct-AWQ invoked selectively on escalation ($<0.60$).
3. **Internal Model Serving & Gateway:**
   - Dedicated internal vLLM inference server hosting Qwen2.5-7B-Instruct-AWQ.
   - LLM Gateway enforcing schema validation, timeouts, bounded retries, and fail-closed mock detection.
4. **Data Management & Persistence:**
   - PostgreSQL database managing users, roles, jobs, claims, evidence, verification verdicts, review actions, and audit logs.
   - Database-backed task queue (`FOR UPDATE SKIP LOCKED`) managing worker execution.
5. **Human Review Workbench (Web UI):**
   - Split-screen interface: high-resolution document viewer with bounding-box evidence highlights alongside extracted structured claims.
   - Risk-prioritized claim review list (discrepancies and escalations surfaced first).
   - Reviewer actions: Accept, Edit (with automatic inline re-verification), Reject, Escalate.
   - Header patient demographic validation banner requiring operator confirmation.
   - Plain-language verification badges with on-demand technical explainability.
6. **Governance, Security & Audit:**
   - OIDC integration for single sign-on authentication.
   - RBAC with separation of duties (Operator, Reviewer, Approver, Admin, Auditor).
   - Configurable Four-Eyes approval policy (`require_distinct_approver`).
   - Append-only, hash-chained audit logging failing closed on write failure.
   - Scrubbed telemetry and logging with zero PHI.
7. **FHIR Output Generation:**
   - Export of validated FHIR R4B JSON Bundles (`Patient`, `Observation`, `DiagnosticReport`, `MedicationStatement`, `Condition`, `Provenance`).
   - Bundles compiled strictly from human-approved claims.
   - Configurable provenance minimization (stripping verbatim text).
   - Manual or authenticated API download of generated bundles.

---

## 5. Deferred Scope (What is OUT)

The following capabilities are deliberately and explicitly excluded from the MVP:

- **Direct Live EHR Integration:** Direct FHIR REST API pushes, SMART Backend Services, or direct database insertions into hospital EHRs (e.g., Epic, Cerner, local HIS).
- **Legacy Healthcare Protocols:** Ingestion or generation of HL7 v2.x messages (ADT, ORU, MDM) or CDA / C-CDA documents.
- **Automated Master Patient Index (MPI):** Probabilistic demographic record linkage or automated patient creation in external registries.
- **Autonomous Straight-Through Processing:** Auto-acceptance or export of claims without human verification (Mode 1 and Mode 2 are deferred).
- **Clinical Decision Support (CDS):** Medical diagnostics, clinical risk scores, drug-drug interaction warnings, or treatment guidance.
- **Advanced Terminology Services:** Integration with SNOMED CT, Indian pharmacopoeia commercial brand-name catalogs, or dynamic external terminology servers.
- **Multi-Tenant SaaS:** Multi-customer database sharing, tenant routing, and subscription/billing management.
- **Public Cloud LLM APIs:** Integration with commercial APIs (OpenAI, Anthropic, Google Cloud Vertex AI) for clinical text processing.
- **Client Platforms:** Native mobile apps (iOS/Android) and desktop native wrappers (Electron).
- **Real Patient Data Execution:** Processing of live un-anonymized clinical records prior to Phase 8.12 sign-off.

---

## 6. Remaining Open Questions (Requiring Future External Stakeholder Input)

These items cannot be resolved internally by engineering decisions and require external commercial, legal, or customer stakeholder resolution during pre-pilot engagements:

1. **OPEN-01-B (Specific Pilot Facility Selection):** Identification of the initial hospital or laboratory site for on-site pilot testing, execution of Memorandum of Association (MoA), and agreement on pilot workflow parameters.
2. **OPEN-03-B (Customer Enterprise IdP Integration):** Specific identity provider configuration at the pilot site (Active Directory Federation Services, Azure AD, Okta), including exact SAML/OIDC claim-to-role mappings.
3. **OPEN-05-B (Commercial Terminology Licensing):** Formal legal review of commercial redistribution licenses for LOINC (Regenstrief Institute) and RxNorm (NLM) within an enterprise software product.
4. **OPEN-08-B (Customer Hardware Procurement):** Verification of physical server procurement and NVIDIA GPU availability (minimum 16 GB VRAM) within the customer's on-premises data center or private VPC.
5. **OPEN-10 (Open-Weight Model Commercial Licensing):** Enterprise legal review of commercial deployment terms for Qwen2.5 (Alibaba license terms), BioClinical-ModernBERT, GLiNER-BioMed, and PaddleOCR under the customer's jurisdiction.
6. **OPEN-11-B (Statutory Data Retention Schedule):** Determination by customer legal counsel of mandatory record retention durations (e.g., 3 years under Indian National Medical Commission regulations vs. 7 years for pediatric records) to populate system retention rules.
7. **OPEN-12-B (Formal Regulatory Filing Determination):** Acquisition of a formal regulatory legal opinion confirming the tool's classification as non-medical-device administrative software under Indian CDSCO Medical Device Rules and relevant international equivalents.

---

## 7. Implementation Order (Phased Roadmap)

Aligned with Section 16 of [`PRODUCTIZATION_SPEC.md`](PRODUCTIZATION_SPEC.md), implementation will proceed in strictly gated sequential phases. **No step may proceed until its exit gate is verified.**

```
Phase 8.1: Architectural Approval & DEC Formalization (Current Step)
    │
    ▼
Phase 8.2: Pipeline Hardening & Containment (Fail-closed profiles, LLM gateway, safe weights)
    │
    ▼
Phase 8.3: Persistence & Job Queue (PostgreSQL schema, blob store, DB queue)
    │
    ▼
Phase 8.4: Modular Monolith API & OIDC RBAC (FastAPI, auth, hash-chained audit)
    │
    ▼
Phase 8.5: Review Workflow Engine (Claim state machine, re-verification, approval gates)
    │
    ▼
Phase 8.6: Browser Review Workbench UI (React/TypeScript, PDF.js, bounding box viewer)
    │
    ▼
Phase 8.7: Secure vLLM Serving Node (Private network deployment, load testing, circuit breakers)
    │
    ▼
Phase 8.8: FHIR R4B Bundle Export (Schema validation, provenance minimization, file export)
    │
    ▼
Phase 8.9: Security Hardening & Penetration Testing (Threat model validation, fuzzing, secret scans)
    │
    ▼
Phase 8.10: Operational Observability (Metrics, alerts, PHI-free logging)
    │
    ▼
Phase 8.11: Packaging & Air-Gapped Deployment (Docker Compose / Helm, runbooks, backup tests)
    │
    ▼
Phase 8.12: Clinical Pilot Readiness Gate (Formal compliance sign-off; NO real PHI before this gate)
```

### Detailed Phasing Details

1. **Phase 8.1: Architectural Approval & DEC Formalization**
   - Review and baseline `PRODUCTIZATION_SPEC.md` and `PRODUCTIZATION_DECISIONS.md`.
   - File DEC records (e.g., `DEC-020` for human-edited claim origin schema).
   - *Exit Gate:* Formal stakeholder approval; DEC records merged.

2. **Phase 8.2: Pipeline Hardening for Service Use**
   - Implement production vs development configuration profiles.
   - Enforce fail-closed behavior: remove silent mock/heuristic fallbacks when running in production mode.
   - Wrap existing router and stage C interfaces with LLM Gateway adapters (no ML pipeline code redesign).
   - Fix checkpoint loading to use safe deserialization (`weights_only=True` / safetensors).
   - Pin Qualified Configuration Manifest (checkpoint hashes, prompt hashes, temperature $T=1.146522$, threshold $0.60$).
   - Run replay-invariance tests against frozen diagnostic datasets to ensure 100% fidelity to audited results.
   - *Exit Gate:* Replay tests match frozen benchmark results exactly; production profile crashes if mocks are configured; existing test suite 100% green.

3. **Phase 8.3: Persistence & Asynchronous Processing Queue**
   - Implement PostgreSQL migrations for all core entities.
   - Deploy local encrypted blob storage with SHA-256 deduplication.
   - Build database-backed asynchronous job worker using `FOR UPDATE SKIP LOCKED`.
   - Implement file screening and quarantine worker for inbound PDFs.
   - *Exit Gate:* End-to-end processing of synthetic document batches through the background queue with simulated worker crash recovery.

4. **Phase 8.4: Backend API & OIDC Authentication**
   - Build modular FastAPI application with strict route grouping.
   - Integrate OIDC JWT authentication middleware with local Keycloak test container.
   - Implement RBAC authorization checks and object-level permissions.
   - Build append-only, hash-chained audit service; verify that audit failures fail closed.
   - *Exit Gate:* Negative authorization tests pass (admins cannot view PHI; unauthenticated calls rejected); audit tampering detected.

5. **Phase 8.5: Review Workflow Engine**
   - Implement claim review state transitions (`PENDING_REVIEW` $\rightarrow$ `ACCEPTED` / `EDITED` / `REJECTED`).
   - Implement inline re-verification for edited claims via Stage A/B.
   - Build header patient demographic verification and mismatch enforcement.
   - Build document approval state machine enforcing the Four-Eyes policy toggle.
   - *Exit Gate:* State machine blocks export if any claim is unreviewed or if patient identity is unconfirmed.

6. **Phase 8.6: Modern Review Workbench UI**
   - Develop React/TypeScript Single-Page Application.
   - Embed high-resolution PDF viewer with interactive SVG bounding-box highlights matching evidence spans.
   - Build split-screen review workspace with keyboard shortcuts for rapid review.
   - Display plain-language verification badges with expandable technical rationale.
   - *Exit Gate:* Usability walkthrough with synthetic document batches; zero XSS vulnerabilities in evidence rendering.

7. **Phase 8.7: Secure Model Serving Infrastructure**
   - Deploy containerized vLLM instance serving Qwen2.5-7B-Instruct-AWQ on dedicated GPU hardware.
   - Configure gateway circuit breakers, connection pooling, and exponential backoff.
   - Conduct load testing and benchmark escalation latency under batch workloads.
   - Verify zero egress firewall configuration on GPU host.
   - *Exit Gate:* vLLM failover and outage drills demonstrate graceful degradation (claims routed to manual review; zero crashes).

8. **Phase 8.8: FHIR R4B File Export Service**
   - Generate FHIR R4B Bundles strictly from approved claims.
   - Implement configurable provenance minimization (`minimize_provenance_phi`).
   - Validate bundles against official HL7 FHIR R4B schema validators.
   - Implement authenticated bundle download endpoint and export tracking.
   - *Exit Gate:* 100% schema validation pass rate across all synthetic document types; export blocked for unapproved files.

9. **Phase 8.9: Security Hardening & Penetration Testing**
   - Conduct comprehensive threat-model walkthrough (validating T-01 through T-23 from `PRODUCTIZATION_SPEC.md`).
   - Run dependency vulnerability scanning, container image scanning, and upload fuzzing.
   - Test prompt-injection defenses using adversarial document text.
   - Verify that all application logs and telemetry are 100% free of PHI.
   - *Exit Gate:* Zero critical or high-severity vulnerabilities; penetration test sign-off.

10. **Phase 8.10: Operational Observability**
    - Instrument Prometheus metrics (document throughput, stage latencies, escalation rates, override rates).
    - Build Grafana operational dashboards.
    - Configure alerts for pipeline errors, queue backlog spikes, and model timeouts.
    - *Exit Gate:* Alerting drill validates notification of simulated GPU outage and queue backup.

11. **Phase 8.11: Packaging & Air-Gapped Deployment**
    - Author Docker Compose and Helm deployment packages for on-premises / private VPC deployment.
    - Write administrative runbooks (installation, backup/recovery, disaster recovery, incident response).
    - Validate clean-room deployment from documentation on an isolated test machine.
    - *Exit Gate:* Complete installation and end-to-end execution on an isolated, air-gapped server.

12. **Phase 8.12: Clinical Pilot Readiness Gate**
    - Complete Data Protection Impact Assessment (DPIA) and institutional ethics committee review.
    - Execute formal pilot agreement and Data Processing Agreement with pilot healthcare provider.
    - Define local clinical validation protocol (running shadow-mode verification on pilot site documents).
    - *Exit Gate:* Written authorization from pilot site Clinical Director and Data Protection Officer. **STRICT RULE: NO REAL PATIENT PHI MAY ENTER THE SYSTEM BEFORE THIS GATE IS FORMALLY SIGNED.**
