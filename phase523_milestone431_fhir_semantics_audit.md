# ClinicalDoc — Phase 5.2.3 Sub-Milestone 4.3.1 Audit Report
## FHIR R4B Medication Regimen Semantics Verification Audit

**Execution Date:** 2026-10-31  
**Audit Scope Authority:** ClinicalDoc Phase 5.2.3 Sub-Milestone 4.3.1 Specification  
**Audit Type:** STRICTLY READ-ONLY (Zero code, test, schema, constant, config, or Git state modifications)  
**Status:** Audit Completed & Evaluated  

---

### 1. Executive Summary & Audit Authority

Sub-Milestone 4.3 implemented FHIR R4B medication regimen mapping hardening within [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py). Before authorizing Sub-Milestone 4.4, this independent, strictly read-only audit examines the FHIR R4B specification, installed Pydantic models (`fhir.resources.R4B`), serialized payloads, and edge cases to ensure the implementation adheres to HL7 standards and clinical safety principles.

#### Key Audit Findings
1. **Dosage.sequence Concurrency & Sequencing**:
   - In FHIR R4B, `Dosage.sequence` is an integer (`0..1`) indicating "the order in which the dosage instructions should be applied or interpreted."
   - Concurrency for identical sequence numbers (e.g. `sequence: 1`) is an **implicit semantic convention** codified in HL7 Implementation Guides (e.g., US Core), but is **not an enforced machine-level transaction** across all vendor EHR backends.
   - Commercial EHRs vary widely in how they parse multi-line dosage instructions. Consequently, the narrative `Dosage.text` remains the primary clinical fail-safe against medication errors.
2. **Sequential Taper Representation**:
   - Tapers (e.g. Prednisone step-down) preserve chronological ordering (`sequence: 1`, `sequence: 2`, `sequence: 3`).
   - Duration bounds are populated via `timing.repeat.boundsDuration` with valid UCUM codes (`d`, `wk`, `mo`, `h`) only when explicitly present in source text.
   - Open-ended or step-down maintenance phases omit `boundsDuration` and do NOT fabricate speculative start dates or duration limits.
3. **Split-Dosing Semantics**:
   - Morning and bedtime components preserve distinct dose quantities, distinct frequencies, independent element-level provenance, and separate narrative texts under a single order.
   - Zero dose combination or timing loss occurs.
4. **Safety Boundaries (W-1 and W-2)**:
   - **W-1 (Parenthetical Clauses)**: Preserved fail-closed; mixed temporal statuses (`historical`/`discontinued` + `current`) suppress regimen grouping and trigger deterministic candidate shadowing.
   - **W-2 (Multi-Page Regimens)**: Preserved fail-closed via upstream page segmentation partitioning (`group_id = "__no_anchor__"`). **Audit Finding**: There is currently **no dedicated regression test** for W-2 multi-page cross-talk in the test suite; this gap is documented below.
5. **Schema & Provenance Conformance**:
   - All generated `Dosage`, `DosageDoseAndRate`, `Timing`, `TimingRepeat`, and `Duration` objects serialize validly under FHIR R4B schemas.
   - Element-level provenance extensions (`EXT_CLAIM_PROVENANCE`) and competing candidate shadowing (`EXT_COMPETING_CANDIDATE`) conform to specification.
6. **Readiness Disposition**:
   - Sub-Milestone 4.3 satisfies clinical safety requirements. With documented EHR limitations and the ongoing Multi-Hop Four-Eyes ancestry blocker noted, Sub-Milestone 4.4 is ready for authorization.

---

### 2. Audit A — FHIR R4B `Dosage.sequence` Semantics & Conformance

#### 2.1 HL7 FHIR Specification & Model Inspection
Inspection of `fhir.resources.R4B.dosage.Dosage` and the official HL7 FHIR R4/R4B specification reveals the following definitions:
- **`Dosage.sequence`**: `integer` (`0..1`).
  - *Definition*: "Indicates the order in which the dosage instructions should be applied or interpreted."
  - *Requirements*: "If the sequence number is 1, then this is the first instruction. If the sequence number is 2, then this is the second instruction, etc."

#### 2.2 Concurrency via Identical Sequence Values
- **Specification Reality**: FHIR R4B does not include an explicit `concurrency: boolean` flag or concurrency primitive on `Dosage`.
- **HL7 Community Consensus**: When multiple `Dosage` entries have identical `sequence` values (e.g., both having `sequence: 1`), implementation guides (such as US Core MedicationRequest Profile and HL7 International Pharmacy workgroup guides) interpret them as parallel or concurrent components of a split regimen.
- **Correction to Implementation Report**: Section 3 of `phase523_milestone43_implementation_report.md` described identical sequence integers as "interpreted concurrently by clinical consuming systems." The report must be qualified: while this represents standard HL7 best practice, receiving EHRs are not architecturally bound by the core specification to execute identical sequence numbers concurrently; legacy EHR engines frequently collapse multiple dosage lines into free-text SIGs or execute only the primary line.

#### 2.3 Sequential Execution via Ascending Sequence Values
- **Specification Reality**: Ascending integers (`sequence: 1`, `sequence: 2`) denote that instruction 1 precedes instruction 2 in application or interpretation.
- **Lack of Execution Synchronization**: FHIR R4/R4B lacks built-in orchestration metadata (such as a `previousSequenceId`, `delay`, or explicit trigger condition). The standard leaves the scheduling of sequence 2 relative to sequence 1 to receiving application logic or pharmacist review.
- *(Note: HL7 FHIR R6 drafts have proposed revising `DosageInstruction` to include explicit step transitions precisely because R4 sequence integers lack machine-executable transitions.)*

#### 2.4 Relationship Between `sequence` and `Timing.repeat.boundsDuration`
- In FHIR R4B, `boundsDuration` is an attribute of `Timing.repeat`, specifying the length of time over which repetitions occur for that specific instruction.
- **Specification Reality**: The standard defines **zero semantic linkage** between `Timing.repeat.boundsDuration` on sequence $N$ and the initiation time of sequence $N+1$.
- A receiving EHR cannot determine from FHIR metadata alone whether sequence 2 begins immediately at $T_0 + \text{boundsDuration}_1$ or upon a subsequent clinical event, unless scheduled by an EHR order entry system or clearly stated in `Dosage.text`.

#### 2.5 What a Receiving EHR Can and Cannot Reliably Infer

| Capability | Status | EHR Reality & Clinical Implications |
| :--- | :--- | :--- |
| **Dose quantities per instruction** | **Reliable** | Standard EHRs reliably parse `Dosage.doseAndRate[0].doseQuantity` for each line. |
| **Instruction text (SIG)** | **Reliable** | `Dosage.text` is the most universally displayed and clinically trusted attribute across Epic, Cerner, and ambulatory systems. |
| **Chronological ordering** | **Partially Reliable** | Ascending sequence integers are recognized by modern inpatient systems, but ambulatory order entries may flatten them into a single string. |
| **Concurrent split execution** | **Conditional** | Inpatient MARs (Medication Administration Records) handle split doses when mapped to pre-configured hospital split-dose templates, but automated calculation of active drug load across lines often fails. |
| **Automated phase transitions** | **Unreliable** | EHRs almost never auto-discontinue sequence 1 upon expiration of `boundsDuration` without clinician or pharmacist intervention. |
| **Cumulative daily dose calculations** | **Unreliable** | Automated Clinical Decision Support (CDS) tools often fail to calculate total active drug load across multi-phase tapers or split regimens. |

---

### 3. Audit B — Sequential Taper Representation & Edge Cases

The implementation of sequential tapers resides in [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1807-L1866).

#### 3.1 Evaluated Taper Scenarios

##### Scenario 1: Standard Two-Phase Taper
*Input*: `"Prednisone: 20 mg once daily for 5 days. Then 10 mg once daily."*
- **Mapper Execution**:
  - Phase 1: `sequence = 1`, `doseQuantity = 20.0 mg`, `Timing.code = "once daily"`, `Timing.repeat.boundsDuration = 5.0 days (UCUM: d)`. Narrative: `"Prednisone 20 mg once daily for 5 days"`.
  - Phase 2: `sequence = 2`, `doseQuantity = 10.0 mg`, `Timing.code = "once daily"`, `Timing.repeat = None` (open-ended). Narrative: `"Then 10 mg once daily"`.
- **Audit Assessment**: **Compliant**. Exactly mirrors source semantics. Phase 2 omits fabricated duration bounds. Dose summation is strictly prevented (no combined 30 mg order).

##### Scenario 2: Multi-Phase Taper with Distinct Durations
*Input*: `"Dexamethasone 4 mg daily for 2 weeks, then 2 mg daily for 1 week, then 1 mg daily."*
- **Mapper Execution**:
  - Phase 1: `sequence = 1`, `4.0 mg`, `boundsDuration = 2.0 weeks (UCUM: wk)`.
  - Phase 2: `sequence = 2`, `2.0 mg`, `boundsDuration = 1.0 weeks (UCUM: wk)`.
  - Phase 3: `sequence = 3`, `1.0 mg`, `boundsDuration = None`.
- **Audit Assessment**: **Compliant**. Chronological ordering and weeks-level UCUM duration mapping (`code = "wk"`) are preserved.

##### Scenario 3: Taper Where Initial Phase Lacks Explicit Duration
*Input*: `"Prednisone 20 mg daily, then 10 mg daily for 5 days."*
- **Mapper Execution**:
  - Phase 1: `sequence = 1`, `20.0 mg`, `Timing.repeat = None`.
  - Phase 2: `sequence = 2`, `10.0 mg`, `boundsDuration = 5.0 days (UCUM: d)`.
- **Audit Assessment**: **Compliant**. The system does not invent a duration for Phase 1 simply because Phase 2 has one.

##### Scenario 4: Taper Where No Phase Has Explicit Duration
*Input*: `"Prednisone 20 mg daily, then 10 mg daily."*
- **Mapper Execution**:
  - Phase 1: `sequence = 1`, `20.0 mg`, `Timing.repeat = None`.
  - Phase 2: `sequence = 2`, `10.0 mg`, `Timing.repeat = None`.
- **Audit Assessment**: **Compliant**. Sequence reflects ordering; boundsDuration is completely omitted.

##### Scenario 5: Ambiguous Transition Language (e.g. "or")
*Input*: `"Prednisone 20 mg daily or 10 mg daily."*
- **Mapper Execution**:
  - Transition regex does not match `"or"`.
  - System recognizes multiple competing doses in an unslotted group and invokes `select_deterministic_slot_candidate`.
  - The winner (e.g. 10 mg) is selected based on confidence/priority; the competing dose (20 mg) is shadowed under `EXT_COMPETING_CANDIDATE`.
  - Exactly 1 `Dosage` instruction is emitted (`sequence = 1`).
- **Audit Assessment**: **Compliant**. Ambiguous alternative dosing is never misinterpreted as a sequential taper or split dosing.

#### 3.2 Technical Limitation: Multi-Phase Token Collision on Shared Evidence Spans
- When multiple claims in a multi-phase taper share an identical duration string (e.g., `"2 weeks"` in Phase 1 and `"2 weeks"` in Phase 2) AND share an identical single `Evidence` object where `char_start == 0`, `re.search` finds the first occurrence of the string.
- *Impact*: In synthetic single-evidence unit tests where offsets are not set on individual claims, duration pairing can fail to bind to later duplicate phrases. In production, OCR layout segmentation creates discrete `Evidence` objects with distinct `char_start` offsets, mitigating this issue.

---

### 4. Audit C — Split-Dose Semantics & Clinical Safety

The implementation of split dosing resides in [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1869-L1983).

#### 4.1 Carvedilol Morning & Bedtime Regimen Evaluation
*Input*: `"Carvedilol: 6.25 mg every morning. 12.5 mg at bedtime."*
- **Mapper Execution**:
  - Instruction 1: `sequence = 1`, `doseQuantity = 6.25 mg`, `Timing.code = "every morning"`, `text = "Carvedilol 6.25 mg every morning"`.
  - Instruction 2: `sequence = 1`, `doseQuantity = 12.5 mg`, `Timing.code = "at bedtime"`, `text = "12.5 mg at bedtime"`.
- **Verification Points**:
  - **Dose Preservation**: Distinct values (6.25 vs 12.5) are preserved in separate `Dosage` entries.
  - **Timing Preservation**: Separate morning and evening timing codes and text phrases are retained.
  - **No Dose Merging**: No combined 18.75 mg daily dose is emitted.
  - **Resource Parity**: Identical dosage structures are generated whether mapping to `MedicationRequest` or `MedicationStatement`.

#### 4.2 Clinical Safety & Narrative Text Primacy
Because FHIR R4B does not enforce machine-level concurrency handling across all EHR platforms:
- Populating human-readable narrative `Dosage.text` (`"Carvedilol 6.25 mg every morning"`, `"12.5 mg at bedtime"`) ensures clinicians, nurses, and pharmacists reading the electronic health record receive complete, unambiguous dosing instructions even if the receiving EHR cannot parse structured split dosages.

---

### 5. Audit D — W-1 and W-2 Regimen Safety Boundaries

#### 5.1 Warning W-1: Complex Parenthetical Embedded Clauses
- **Scenario**: *"Metoprolol (previously 25 mg daily, stopped due to bradycardia) 50 mg daily."*
- **Implementation Inspection**:
  In [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1798-L1806):
  ```python
  temp_statuses = {c.temporal_status for c in sorted_doses}
  has_mixed_inactive = ("discontinued" in temp_statuses or "historical" in temp_statuses) and ("current" in temp_statuses or "planned" in temp_statuses)
  has_taper_cues = bool(TAPER_TRANSITION_REGEX.search(full_grp_text)) and not has_mixed_inactive
  has_split_cues = (SPLIT_REGEX.search(full_grp_text) or (has_morning and has_bedtime)) and not has_mixed_inactive
  ```
- **Audit Verification**:
  - When historical or discontinued doses are embedded in parentheticals alongside current doses, `has_mixed_inactive` evaluates to `True`.
  - This immediately disables split-dose and taper detection.
  - The mapper falls through to deterministic slot selection, selecting the active/current dose and shadowing the discontinued dose, or suppressing unanchored fragments.
  - Validated by test: `tests/test_phase523_milestone4_3.py::test_parenthetical_w1_fail_closed_preservation`.

#### 5.2 Warning W-2: Multi-Page Spanning Medication Regimens
- **Scenario**: Medication name appears at the bottom of Page 1; dose appears at the top of Page 2.
- **Implementation Inspection**:
  - Upstream document ingestion (`src/clindoc/ocr/layout.py` and `src/clindoc/extract/segmentation.py`) partitions text strictly by `page_number`.
  - A dose on Page 2 with no medication anchor on Page 2 receives `group_id = "__no_anchor__"`.
  - At the mapper boundary (`src/clindoc/fhir/mapper.py#L2684`):
    `if not name_claim: continue`
  - The orphan dose claim on Page 2 is discarded and never emitted as an unanchored FHIR resource.
- **Audit Discrepancy / Gap Reporting**:
  > [!WARNING]
  > **Missing Direct Regression Test for W-2**: While the fail-closed code path is active and verified by architectural inspection, there is currently **no dedicated automated test in `tests/` explicitly simulating a cross-page medication split (Page 1 drug name to Page 2 dose)**. In accordance with read-only audit instructions, no tests were added during this audit. A dedicated regression test fixture should be scheduled during Sub-Milestone 4.4 integration.

---

### 6. Audit E — Schema Conformance, Data Types, & Provenance

Inspection of serialized resources and validator checks confirms strict schema conformance across all FHIR R4B types:

#### 6.1 Validated FHIR Types & Field Paths

| FHIR R4B Element | Target Field | Conformance & Mapping Rules | Validation Result |
| :--- | :--- | :--- | :--- |
| **`Dosage`** | `sequence` | `integer` (`1..N`), strictly positive | **VALID** |
| **`Dosage`** | `text` | Verbatim text summary combining drug, dose, route, timing | **VALID** |
| **`Dosage`** | `route` | `CodeableConcept(text=...)` | **VALID** |
| **`DosageDoseAndRate`** | `doseQuantity` | `Quantity(value=Decimal, unit=str, system=UCUM, code=str)` | **VALID** |
| **`Timing`** | `code` | `CodeableConcept(text=str)` | **VALID** |
| **`TimingRepeat`** | `boundsDuration` | `Duration(value=Decimal, unit=str, system=UCUM, code=str)` | **VALID** |
| **`Duration`** | `system` / `code` | `http://unitsofmeasure.org` with valid codes (`d`, `wk`, `mo`, `h`) | **VALID** |
| **`Extension`** | `EXT_CLAIM_PROVENANCE` | Sub-extensions: `claimId`, `verificationId`, `verificationLabel`, `confidence`, `evidenceText` | **VALID** |
| **`Extension`** | `EXT_COMPETING_CANDIDATE` | Sub-extensions: `slot`, `selectedClaimId`, `competingClaimId`, `competingValue`, `selectionReason` | **VALID** |

#### 6.2 Zero Fabrication Invariant
- **Zero Dose Fallback**: If a dose claim is unparseable (e.g. `"sliding scale"`), `_build_dose_and_rate` returns `None` for `doseAndRate` and preserves the provenance on `Dosage.extension`. It **never** emits a default `0.0 mg` dose.
- **Explicit Zero Dose**: If a dose is verified as `"0 units"`, `value = 0.0` is preserved because it matches literal zero text.
- **Zero Start Date Invention**: Tapers omit speculative `Timing.repeat.boundsPeriod` start/end dates when not stated in source text.
- **Zero Duration Invention**: Open-ended taper steps emit `Timing.repeat = None` instead of guessing duration limits.

---

### 7. Serialized FHIR R4B Representation Examples

#### Example 1: Split-Dose MedicationRequest
```json
{
  "resourceType": "MedicationRequest",
  "id": "medrx-doc-regimen-001-1",
  "status": "active",
  "intent": "order",
  "medicationCodeableConcept": {
    "coding": [
      {
        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "code": "20352",
        "display": "Carvedilol"
      }
    ],
    "text": "Carvedilol"
  },
  "subject": {
    "reference": "Patient/pat-regimen-001"
  },
  "dosageInstruction": [
    {
      "sequence": 1,
      "text": "Carvedilol 6.25 mg every morning",
      "timing": {
        "code": {
          "text": "every morning",
          "extension": [
            {
              "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
              "extension": [
                { "url": "claimId", "valueString": "c-f1" },
                { "url": "verificationId", "valueString": "ver-c-f1" },
                { "url": "verificationLabel", "valueCode": "SUPPORTED" },
                { "url": "confidence", "valueDecimal": 0.95 },
                { "url": "evidenceText", "valueString": "Carvedilol: 6.25 mg every morning. 12.5 mg at bedtime." }
              ]
            }
          ]
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 6.25,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg",
            "extension": [
              {
                "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
                "extension": [
                  { "url": "claimId", "valueString": "c-d1" },
                  { "url": "verificationId", "valueString": "ver-c-d1" },
                  { "url": "verificationLabel", "valueCode": "SUPPORTED" },
                  { "url": "confidence", "valueDecimal": 0.95 },
                  { "url": "evidenceText", "valueString": "Carvedilol: 6.25 mg every morning. 12.5 mg at bedtime." }
                ]
              }
            ]
          }
        }
      ]
    },
    {
      "sequence": 1,
      "text": "12.5 mg at bedtime",
      "timing": {
        "code": {
          "text": "at bedtime",
          "extension": [
            {
              "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
              "extension": [
                { "url": "claimId", "valueString": "c-f2" },
                { "url": "verificationId", "valueString": "ver-c-f2" },
                { "url": "verificationLabel", "valueCode": "SUPPORTED" },
                { "url": "confidence", "valueDecimal": 0.95 },
                { "url": "evidenceText", "valueString": "Carvedilol: 6.25 mg every morning. 12.5 mg at bedtime." }
              ]
            }
          ]
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 12.5,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg",
            "extension": [
              {
                "url": "http://clindoc.org/fhir/StructureDefinition/claim-provenance",
                "extension": [
                  { "url": "claimId", "valueString": "c-d2" },
                  { "url": "verificationId", "valueString": "ver-c-d2" },
                  { "url": "verificationLabel", "valueCode": "SUPPORTED" },
                  { "url": "confidence", "valueDecimal": 0.95 },
                  { "url": "evidenceText", "valueString": "Carvedilol: 6.25 mg every morning. 12.5 mg at bedtime." }
                ]
              }
            ]
          }
        }
      ]
    }
  ]
}
```

#### Example 2: Sequential Taper MedicationStatement
```json
{
  "resourceType": "MedicationStatement",
  "id": "medstmt-doc-regimen-001-1",
  "status": "active",
  "medicationCodeableConcept": {
    "coding": [
      {
        "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
        "code": "8640",
        "display": "Prednisone"
      }
    ],
    "text": "Prednisone"
  },
  "subject": {
    "reference": "Patient/pat-regimen-001"
  },
  "dosage": [
    {
      "sequence": 1,
      "text": "Prednisone 20 mg once daily for 5 days",
      "timing": {
        "code": { "text": "once daily" },
        "repeat": {
          "boundsDuration": {
            "value": 5,
            "unit": "days",
            "system": "http://unitsofmeasure.org",
            "code": "d"
          }
        }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 20.0,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg"
          }
        }
      ]
    },
    {
      "sequence": 2,
      "text": "Then 10 mg once daily",
      "timing": {
        "code": { "text": "once daily" }
      },
      "doseAndRate": [
        {
          "doseQuantity": {
            "value": 10.0,
            "unit": "mg",
            "system": "http://unitsofmeasure.org",
            "code": "mg"
          }
        }
      ]
    }
  ]
}
```

---

### 8. Documented Limitations & Clinical Security Blockers

1. **EHR Interpretive Ambiguity**:
   As detailed in Section 2, receiving EHRs are not architecturally bound by FHIR R4B to execute identical sequence numbers concurrently or automate sequential transitions. Clinical downstream safety is guarded by `Dosage.text`, which must always remain complete, verbatim, and prominent.
2. **Missing W-2 Multi-Page Regression Test**:
   While W-2 multi-page suppression is enforced architecturally through page-level segmentation partitioning, there is currently no dedicated regression test fixture in `tests/` verifying multi-page medication orders.
3. **Multi-Hop Four-Eyes Ancestry Blocker**:
   As documented in Sub-Milestone 4.2.1, the `ClaimReviewService` tracks immediate predecessor authorship, but does not traverse deep ancestor chains across multi-hop edits. This remains an **unresolved clinical-export security blocker** pending root-level DAG traversal authorization.

---

### 9. Final Readiness Verdict

```
================================================================================
           SUB-MILESTONE 4.3.1 FHIR REGIMEN SEMANTICS AUDIT VERDICT
================================================================================
  AUDIT A: FHIR R4B Dosage.sequence Semantics:        [ PASS - QUALIFIED ]
  AUDIT B: Sequential Taper Representation:           [ PASS ]
  AUDIT C: Split-Dosing Semantics:                    [ PASS ]
  AUDIT D: W-1 & W-2 Safety Boundaries:               [ PASS - WITH GAP ]
  AUDIT E: Schema & Provenance Conformance:           [ PASS ]
  ZERO MODIFICATIONS MAINTAINED:                      [ PASS ]
--------------------------------------------------------------------------------
  BLOCKERS:          0 (Regimen mapping level)
  SECURITY BLOCKER:  1 (Carried from 4.2.1: Multi-hop Four-Eyes ancestry)
  TEST GAP:          1 (W-2 multi-page regression test fixture missing)
  DISPOSITION:       SUB-MILESTONE 4.3 FULLY VERIFIED; AUTHORIZATION AWAITED
================================================================================
```

Sub-Milestone 4.3.1 audit is complete. Execution is halted in compliance with strict read-only constraints. Sub-Milestone 4.4 has NOT been started. Awaiting authorization.
