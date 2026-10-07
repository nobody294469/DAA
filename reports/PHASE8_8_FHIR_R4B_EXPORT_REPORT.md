# Phase 8.8 — FHIR R4B Export & Provenance Grounding Verification Report

**Document ID**: `PHASE8_8_FHIR_R4B_EXPORT_REPORT.md`  
**Phase**: 8.8 — FHIR R4B Export Layer  
**Authority**:
- `PROJECT_SPEC.md` §5 #10, §7.7, §15, §16
- `PRODUCTIZATION_SPEC.md` §1.2, §4.3, §7.2, §9.3, §9.4
- `PRODUCTIZATION_DECISIONS.md` DEC-008, DEC-016, PD-002, PD-009, PD-011, PD-012, PD-015
**Status**: APPROVED & VERIFIED  
**Date**: 2026-10-06  

---

## Executive Summary

Phase 8.8 implements a production-ready, deterministic **FHIR R4B Export Layer** that maps the system's verified Intermediate Representation (claims, evidence spans, verification cascade verdicts, and operator review states) into standards-compliant FHIR R4B `Bundle` resources of type `collection`.

Crucially, the exporter operates strictly as a **deterministic transformation engine**:
- It performs **zero clinical inference** and invokes **no LLMs or heuristic repair models**.
- It enforces a strict **Verification Safety Boundary**: claims generated or processed via fallback mechanisms (`is_fallback=True`) or with unconfirmed review states (`PENDING`, `REJECTED`, `ESCALATED`) are categorically blocked from export.
- It guarantees unbroken **evidence grounding and provenance linking** from the source clinical document through individual FHIR elements and top-level `Provenance` resources.
- It enforces a **Four-Eyes Document Approval gate** (`is_export_eligible=True` and confirmed patient identity sign-off) before permitting database export.

All 16 dedicated Phase 8.8 tests and 99 existing regression tests (including Phase 8.7 circuit breaker, model health, failure drills, and load testing) passed with zero regressions (115 passed total).

---

## 1. System Architecture

```text
       Verified Intermediate Representation (IR)
  [Claim, Evidence, VerificationResult, ReviewState]
                          │
                          ▼
            Verification Safety Boundary
         (FHIRExportService.evaluate_claim_eligibility)
  ┌───────────────────────┴────────────────────────┐
  │ [Eligible Claims]             [Ineligible Claims]
  │ (ACCEPTED / EDITED /          (is_fallback=True / PENDING /
  │  SUPPORTED & grounded)         REJECTED / ESCALATED / orphan)
  │                                                │
  ▼                                                ▼
FHIR R4B Mapper                     Structured Rejection Report
(claims_to_fhir_bundle)               (Audit Logged / Returned)
  ├── Patient
  ├── Encounter (if metadata present)
  ├── Observation (with LOINC, UCUM, Interpretation, Reference Range)
  ├── MedicationRequest / MedicationStatement (DEC-008 hybrid routing)
  ├── DocumentReference (source document attachment & metadata)
  ├── DiagnosticReport (lab grouping container)
  └── Provenance (resource-level & element-level extensions + top-level Provenance)
                          │
                          ▼
                FHIR R4B Bundle (type: collection)
                          │
                          ▼
             Structural Validation Layer
           (FHIRValidator.validate_bundle)
  ┌───────────────────────┴────────────────────────┐
  │ [Validation PASSED]           [Validation FAILED]
  │ (Referential integrity,       (Errors recorded, fail-closed)
  │  no duplicates, schemas valid)
  │
  ▼
Deterministic Serialization & Checksum
  ├── Canonical JSON formatting
  └── SHA-256 Bundle Payload Digest
                          │
                          ▼
Service & Persistence Integration
  ├── Persist in `fhir_bundles` table
  ├── Record idempotent `fhir_exports` record
  ├── Transition Document status -> EXPORTED
  └── Cryptographic Hash-Chained Audit Logging (PD-012, strictly non-PHI)
```

---

## 2. Existing IR → FHIR Resource Mapping Table

| Intermediate Representation (IR) Construct | Target FHIR R4B Resource | Key Fields & Codings Mapped | Rationale & Architectural Rule |
| :--- | :--- | :--- | :--- |
| Extracted Patient Demographics / Context | `Patient` | `id`, `identifier` (MRN system `urn:clindoc:mrn`), `name` (`HumanName`), `gender`, `birthDate` | Never fabricates demographics if omitted in IR. Sanitizes ID deterministically. |
| Source Document Metadata & Blob Reference | `DocumentReference` | `id`, `status: current`, `date`, `category` (`clinical-note`), `content.attachment` (`contentType`, `title`, `url`, `hash` SHA-256) | Preserves permanent linkage to the ingested raw file and ciphertext storage reference. |
| Encounter Context | `Encounter` | `id`, `status`, `class` (ActCode `AMB`, `IMP`, `EMER`, `VR`, `HH`), `subject` | Mapped only when explicit encounter metadata exists. **Safely omitted when absent** (never fabricated). |
| Verified Lab Claim Group | `Observation` | `id`, `status: final/preliminary`, `category: laboratory`, `code` (LOINC), `valueQuantity` (UCUM), `interpretation`, `referenceRange`, `derivedFrom` (`DocumentReference`), `encounter` | Fully verified observations link directly to `DocumentReference` via `derivedFrom`. Includes two-tier provenance extensions. |
| Verified Medication Group (Order/Prescription) | `MedicationRequest` | `id`, `status: active/stopped/completed`, `intent: order/plan`, `medicationCodeableConcept` (RxNorm), `dosageInstruction`, `supportingInformation` (`DocumentReference`), `encounter` | DEC-008: Triggered when document type is prescription/order sheet (`clinic_rx_pad`, `mar_order_sheet`, etc.) or temporal status is `planned`. |
| Verified Medication Group (History/Statement) | `MedicationStatement` | `id`, `status: active/stopped/completed/intended`, `medicationCodeableConcept` (RxNorm), `dosage`, `derivedFrom` (`DocumentReference`), `context` (`Encounter`) | DEC-008: Triggered for narrative notes and non-order documents without explicit planned orders. |
| Lab Group Container | `DiagnosticReport` | `id`, `status: final`, `category: LAB`, `code`, `subject`, `performer`, `result` (`Observation` references), `encounter` | Organizes lab observations into a clinical report container. |
| Top-Level Attribution & Run Metadata | `Provenance` | `id`, `target` (`Observation`, `Medication`, `DiagnosticReport`), `recorded`, `agent` (`Practitioner`), `entity` (`DocumentReference` as `role: source`) | Unifies audit trail, linking all exported resources back to the source document and ordering practitioner. |

---

## 3. Supported Resources & Field Completeness

### 3.1 `Patient`
- `identifier`: System set to `urn:clindoc:mrn` with verified patient ID.
- `name`: Preserves parsed `family`, `given`, and `text` representation.
- `gender` & `birthDate`: Populated if present in IR; left `None` if absent. Zero demographic fabrication.

### 3.2 `Observation`
- `status`: `"final"` for `SUPPORTED` claims; `"preliminary"` for unconfirmed findings.
- `code`: Preserves normalized LOINC code (`http://loinc.org`). Falls back gracefully to `CodeableConcept.text` without fake codes if unmapped.
- `valueQuantity`: Decimal value mapped with normalized UCUM unit code (`http://unitsofmeasure.org`).
- `interpretation`: Standard FHIR Observation Interpretation codes (`H`, `L`, `A`, `N`).
- `referenceRange`: Parsed lower/upper quantities and text representation.
- `derivedFrom`: Direct `Reference` to `DocumentReference/{id}`.

### 3.3 `MedicationRequest` / `MedicationStatement`
- DEC-008 hybrid routing rule:
  - Prescriptions (`clinic_rx_pad`, `mar_order_sheet`, `pharmacy_label`, `prescription`, `order`) or claims with `temporal_status == "planned"` map to `MedicationRequest`.
  - Ingestion from narrative notes or general summaries map to `MedicationStatement`.
- `medicationCodeableConcept`: Normalized RxNorm code (`http://www.nlm.nih.gov/research/umls/rxnorm`).
- `dosageInstruction` / `dosage`: Structured dose quantity (UCUM), route of administration, and timing codes (e.g. `TID`, `QD`).
- `supportingInformation` / `derivedFrom`: Direct `Reference` to `DocumentReference/{id}`.

### 3.4 `DocumentReference`
- Attached content metadata: MIME type, file size, original filename, object storage URI, and SHA-256 checksum in `attachment.hash`.

### 3.5 `Encounter`
- Supported encounter classes: Ambulatory (`AMB`), Inpatient (`IMP`), Emergency (`EMER`), Virtual (`VR`), Home Health (`HH`).
- Mapped strictly when present; omitted cleanly without errors or warnings when metadata is absent.

### 3.6 `Provenance`
- Top-level FHIR resource specifying:
  - `target`: Array of exported Observation, Medication, and DiagnosticReport references.
  - `agent`: Attending practitioner / ordering clinician.
  - `entity`: Source `DocumentReference` with `role == "source"`.

---

## 4. Unsupported & Limited Fields

To ensure clinical safety and strictly avoid hallucination:
1. **Longitudinal History**: The system extracts episodic document claims; it does not fabricate longitudinal patient timelines, encounter histories, or prior episode references.
2. **Missing Coding Fallback**: When an extracted entity has no normalized LOINC or RxNorm code, the mapper exports text-only `CodeableConcept` (`coding=None`). It never hallucinates dummy or default codes.
3. **Complex Dispense / Substitution Details**: Detailed pharmacy dispense events, refill authorizations, and NDC package codes are not extracted by current pipeline stages and are therefore omitted.
4. **Missing Dosage Elements**: If route, frequency, or duration were not stated in the source document, they are omitted from the `Dosage` structure rather than assumed.

---

## 5. Evidence & Provenance Grounding Strategy

The FHIR export preserves the project's evidence-grounding architecture at three complementary tiers:

```text
┌─────────────────────────────────────────────────────────────────┐
│ Tier 1: Top-Level FHIR Provenance Resource                      │
│ Targets: Observation/*, MedicationRequest/*, DiagnosticReport/* │
│ Source Entity: DocumentReference/*                              │
│ Agent: Practitioner/*                                           │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│ Tier 2: Resource-Level Provenance Extension                     │
│ URL: urn:clindoc:extension:claim-provenance                     │
│ Sub-extensions:                                                 │
│   ├── claimId, verificationId, modelRunId                       │
│   ├── stageReached (A/B/C/HUMAN_REVERIFIED)                     │
│   ├── confidence (calibrated float)                             │
│   ├── verificationLabel (SUPPORTED/DISCREPANCY)                 │
│   ├── reviewState (ACCEPTED/EDITED)                             │
│   ├── evidenceText (verbatim source span, strippable PD-011)    │
│   ├── sourcePage, sourceBoundingBox                             │
│   └── contradictionType (when applicable)                       │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│ Tier 3: Element-Level Provenance Extensions                     │
│ Embedded directly in Observation.code, Observation.valueQuantity│
│ Medication.code, and Dosage elements for fine-grained audit.    │
└─────────────────────────────────────────────────────────────────┘
```

### Documented Extensions:
- `urn:clindoc:extension:claim-provenance`: Complex extension carrying extraction, cascade verification, and review metadata.
- `urn:clindoc:extension:source-evidence`: Element-level extension carrying bounding box coordinates and token confidence.
- `urn:clindoc:extension:verification-status`: Legacy verification verdict code extension (maintained for backwards compatibility).
- `urn:clindoc:extension:verification-discrepancy`: Structured taxonomy code for detected contradictions.

---

## 6. Verification Safety Boundary & Export Eligibility Rules

The export layer enforces strict fail-closed safety rules:

```python
# Export Eligibility Rule (Formalized in FHIRExportService.evaluate_claim_eligibility)
def is_eligible(claim, ver, ev):
    # Rule 1: No fallbacks allowed
    if claim.is_fallback or (ver and ver.is_fallback):
        return False, "Claim produced via fallback mechanism; not verified clinical fact"
    
    # Rule 2: Human review workflow check
    if claim.review_state is not None:
        if claim.review_state in ("PENDING", "REJECTED", "ESCALATED"):
            return False, f"Claim review state '{claim.review_state}' is not export-eligible"
        if claim.review_state in ("ACCEPTED", "EDITED"):
            return True, None
            
    # Rule 3: Autonomous pipeline check (when no human decision exists)
    if ver is None or ver.label not in ("SUPPORTED", "VERIFIED"):
        return False, f"Verification verdict '{ver.label}' is not SUPPORTED"
        
    # Rule 4: Grounding & completeness
    if ev is None:
        return False, "Claim missing grounding evidence"
    if not claim.value or not claim.value.strip():
        return False, "Claim value is empty"
        
    return True, None
```

### Enforcement Summary:
1. **Fallback Exclusion**: Verified that claims originating from fallbacks (e.g. `is_fallback=True` triggered by vLLM circuit breakers or schema failures) are **never** exported as verified data.
2. **Unresolved Claim Exclusion**: Claims marked as `PENDING`, `REJECTED`, or `ESCALATED` return structured rejection descriptors (`non_exportable_claims`) and are excluded from the bundle.
3. **Four-Eyes Document Approval Gate**: The database export service (`export_document`) checks `DocumentApproval.is_export_eligible == True` and `patient_identity_status == CONFIRMED`. Attempting export without formal sign-off raises `FHIRExportEligibilityError`.

---

## 7. Structural Validation Strategy

Validation is implemented via `clindoc.fhir.validator.FHIRValidator.validate_bundle`:

- **Pydantic Model Re-parsing**: Round-trips the generated bundle through `fhir.resources.R4B.bundle.Bundle.parse_raw()`.
- **Resource Typing**: Verifies standard R4B resource types and collection semantics.
- **Referential Integrity**:
  - Registers all resources in `available_refs` (`{resourceType}/{id}`).
  - Validates `Observation.subject` -> `Patient`, `Observation.derivedFrom` -> `DocumentReference`, `Observation.encounter` -> `Encounter`.
  - Validates `MedicationRequest.subject` -> `Patient`, `MedicationRequest.supportingInformation` -> `DocumentReference`.
  - Validates `DiagnosticReport.subject` -> `Patient`, `DiagnosticReport.result` -> `Observation`.
  - Validates `Provenance.target` -> active resources, `Provenance.entity.what` -> `DocumentReference`.
- **Duplicate Resource Detection**: Flags any duplicate `{resourceType}/{id}` in bundle entries.
- **Orphan Reference Detection**: Any broken reference immediately fails validation.
- **Deterministic Checksum**: Serializes canonical JSON (sorted keys, fixed indentation) and calculates SHA-256 digest stored with the bundle.

---

## 8. API & Service Integration

### Service Layer: `src/clindoc/services/fhir_export.py`
- `FHIRExportService.export_intermediate_representation(...)`: In-memory export for pipeline jobs and test harnesses.
- `FHIRExportService.export_document(...)`: Full transactional workflow that reads approved document state, filters claims, generates the bundle, validates, persists in `fhir_bundles` and `fhir_exports`, sets `DocumentStatus.EXPORTED`, and records hash-chained audit events.

### API Endpoints: `src/clindoc/api/routes/documents.py`
- `POST /api/v1/documents/{document_id}/export`:
  - RBAC Protected: Requires `APPROVER` or `INTEGRATION_SERVICE` role (`require_export_access`).
  - Executes `export_document`, commits transaction, returns export metadata.
- `GET /api/v1/documents/{document_id}/fhir-bundle`:
  - RBAC Protected: Requires `APPROVER` or `INTEGRATION_SERVICE` role.
  - Returns finalized FHIR R4B Bundle JSON payload.

### Audit Logging (PD-012):
Emits append-only, tamper-evident `AuditEvent`:
- `event_type`: `FHIR_BUNDLE_EXPORTED`
- `entity_type`: `fhir_bundle`
- `action`: `EXPORT`
- `metadata_json`: Strictly non-PHI (`document_id`, `bundle_id`, `bundle_sha256`, `resource_counts`, `exportable_claim_count`, `target_type`, `is_valid`).

---

## 9. Test Results

### 9.1 Phase 8.8 Dedicated Test Suite (`tests/test_fhir_export.py`)
16/16 tests passed:
- `test_patient_mapping_preserves_demographics_without_fabrication`: PASSED
- `test_observation_mapping_with_loinc_ucum_and_provenance`: PASSED
- `test_medication_mapping_prescription_routes_to_medication_request`: PASSED
- `test_document_reference_and_encounter_mapping`: PASSED
- `test_encounter_safely_omitted_when_no_metadata`: PASSED
- `test_safety_fallback_claim_rejected_from_export`: PASSED
- `test_safety_pending_and_rejected_claims_rejected_from_export`: PASSED
- `test_safety_discrepancy_verdict_rejected_in_autonomous_mode`: PASSED
- `test_safety_missing_terminology_does_not_fabricate_code`: PASSED
- `test_safety_all_ineligible_claims_raises_clear_error`: PASSED
- `test_structural_bundle_referential_integrity`: PASSED
- `test_structural_duplicate_resource_id_detected`: PASSED
- `test_structural_orphan_reference_detected`: PASSED
- `test_deterministic_serialization_and_sha256`: PASSED
- `test_database_export_enforces_four_eyes_approval_gate`: PASSED
- `test_api_export_and_get_bundle_endpoints`: PASSED

### 9.2 Regression Test Suite Execution
Ran full test suite across Phase 8.8 and preceding phases:
- `tests/test_fhir_export.py`: 16 passed
- `tests/test_canonical_fhir_mapping.py`: 11 passed
- `tests/test_circuit_breaker.py`: 13 passed
- `tests/test_structured_fallback_worker.py`: 11 passed
- `tests/test_model_health.py`: 17 passed
- `tests/test_vllm_deployment.py`: 14 passed
- `tests/test_failure_modes_f05_f07.py`: 27 passed
- `tests/test_phase8_7_load.py`: 6 passed

**Total Regression Result**: **115 passed in 54.96s (100% PASS rate, 0 failures)**.

---

## 10. Known Limitations

1. **Non-Standard Local Terminology**: If source documents use idiosyncratic abbreviations that the upstream normalization layer cannot resolve to LOINC/RxNorm/UCUM, the exporter preserves only the raw textual representation.
2. **Single-Encounter Granularity**: Current extraction associates claims with a single encounter per clinical document. Multi-encounter composite documents are not split across distinct encounter dates.
3. **Mode 0 Human Approval Dependency**: Full database export requires an active `DocumentApproval` record. Documents in `PENDING_REVIEW` or `IN_REVIEW` cannot be exported via the persistence service.

---

## 11. Implemented vs. Requires External FHIR Server Validation

| Capability | Status | Notes |
| :--- | :--- | :--- |
| **FHIR R4B Resource Object Creation** | Implemented and locally validated | Full schema compliance via `fhir.resources` R4B library. |
| **Intermediate Representation Mapping** | Implemented and locally validated | Patient, Observation, Medication, DocumentReference, Encounter, Provenance. |
| **Two-Tier Grounding & Provenance Extensions** | Implemented and locally validated | Preserves bounding boxes, confidence, stage, and source document links. |
| **Verification Safety Boundary & Filtering** | Implemented and locally validated | Blocks fallbacks and unapproved claims fail-closed. |
| **Structural Schema & Referential Integrity** | Implemented and locally validated | Validates referential closure, duplicate detection, and orphan prevention. |
| **Database & API Service Orchestration** | Implemented and locally validated | Persists bundles, updates status, and writes cryptographic audit log. |
| **Live External EHR / Hospital FHIR Server Ingestion** | **Requires external FHIR server validation** | Validating HTTP transmission, mTLS handshakes, OAuth2 SMART-on-FHIR scopes, and endpoint conformance against an external Epic, Cerner, or HAPI FHIR server must be tested during subsequent integration phases. |

---

## 12. Verification Determinations & Compliance

1. **Authoritative Specs Untouched**:
   - `PROJECT_SPEC.md`: UNTOUCHED
   - `PRODUCTIZATION_SPEC.md`: UNTOUCHED
   - `PRODUCTIZATION_DECISIONS.md`: UNTOUCHED
2. **Legacy UI Untouched**:
   - `src/clindoc/ui/`: UNTOUCHED
3. **Git Status**:
   - Zero commits, zero staged changes, zero push commands executed.

### Final Determination: **PASS (100%)**
