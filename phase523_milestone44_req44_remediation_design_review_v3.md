# Phase 5.2.3 Sub-Milestone 4.4: REQ-44 Remediation Design Review (v3.0)

**Document Authority:** Phase 5.2.3 Sub-Milestone 4.4 Comprehensive Remediation Design Review  
**Revision Date:** 2026-10-31  
**Document Version:** 3.0 (Supersedes v1.0 and v2.0)  
**Operating Mode:** STRICTLY DOCUMENTATION-ONLY REVIEW (Zero production code, test, schema, constant, config, or Git modifications)  
**Status:** **REVISED DESIGN REVIEW COMPLETE — PENDING EXPLICIT AUTHORIZATION PRIOR TO IMPLEMENTATION**  

---

## 1. Executive Summary & Change Summary from v2.0

This specification represents Version 3.0 of the REQ-44 remediation design review. It formalizes semantic role disambiguation for temporal expressions, establishes structural boundaries for OCR paragraphs and line breaks, categorizes extra/orphan durations into three distinct mathematical cases, and tightens evidence requirements for FHIR R4B lifecycle resource generation.

### Change Summary (What Changed from v2.0):
1. **Semantic Role Differentiation for Temporal Expressions (Section 2.1 & 2.4)**:
   - Added explicit semantic categorization to distinguish **Regimen Administration Durations** from **Follow-up/Review Intervals** (`"Review after 1 week"`), **Monitoring/Reassessment Intervals** (`"repeat labs in 1 week"`), and **Clinical History Durations** (`"symptoms for 3 weeks"`).
   - Established strict fail-closed boundary: Non-regimen temporal expressions are never attached as `boundsDuration` to a medication phase.
2. **OCR Paragraph & Double Line-Break Disambiguation (Section 2.3)**:
   - Clarified that neither single line wraps nor double line breaks (paragraph boundaries) independently authorize medication phase transitions.
   - Prescriptions spanning paragraph boundaries without explicit transition cues fail closed for structured duration.
3. **Three-Case Taxonomy for Extra / Conflicting Durations (Section 2.5)**:
   - Resolved the apparent conflict between orphan suppression and multi-duration fail-closed handling by defining three mutually exclusive cases:
     * **Case A (Orphan Duration)**: No local dose anchor $\implies$ Suppressed with `UNATTACHED_EXTRA_DURATION`.
     * **Case B (Multiple Plausible Regimen Durations in Single Phase)**: Ambiguous dosing conflict $\implies$ Phase fails closed (`boundsDuration = None`).
     * **Case C (Non-Regimen Activity Duration)**: Governs follow-up/review/monitoring $\implies$ Excluded from `Dosage.timing`.
4. **Strict Grounded Evidence Requirements for FHIR R4B Lifecycle Mapping (Section 3)**:
   - Eliminated ungrounded `MedicationRequest(status="cancelled")` generation: requires verifiable prior order reference or prescription cancellation directive.
   - Enforced strict evidence thresholds for `MedicationStatement(status="stopped")`, `MedicationStatement(status="unknown")`, and `MedicationStatement(status="completed")`.
5. **Expanded Regression Test Suite (Section 5)**:
   - Added formal test specifications for follow-up review collisions, paragraph boundaries without transition cues, three-case duration taxonomy, and lifecycle evidence boundaries.

---

## 2. Section A: Semantic Duration Ownership & Phase Partitioning

### 2.1 The Semantic Role Problem: Follow-up Timing vs. Regimen Duration

In clinical narrative notes, temporal expressions frequently denote non-pharmacological clinical actions rather than medication administration limits.

#### Clinical Hazard Counterexample:
$$\text{"Take 40 mg daily for 2 weeks, then increase to 60 mg daily. Review after 1 week."}$$
- Phase 1: 40 mg daily for 2 weeks.
- Phase 2: 60 mg daily (open-ended maintenance until clinician reassessment).
- Follow-up Action: `"Review after 1 week"`.

Under purely positional or geometric windowing:
- Phase 2 text span $[\text{pos}(\text{"then"}), \text{EOF})$ encompasses both `"increase to 60 mg daily"` and `"Review after 1 week"`.
- If `"1 week"` is treated merely as a duration candidate in Phase 2's window, the mapper would emit:
  `Dosage(doseAndRate=60 mg, timing.repeat.boundsDuration=1.0 wk)`
- **Clinical Hazard**: The patient's electronic prescription automatically expires after 7 days, cutting off critical chronic medication (e.g. antihypertensive or steroid taper) because the follow-up consultation interval was falsely ingested as the medication therapy duration limit.

---

### 2.2 Semantic Role Taxonomy for Clinical Temporal Expressions

Every extracted `med.duration` claim is evaluated against a deterministic semantic role classifier prior to phase association:

```
                           Extracted Temporal Expression
                                         │
        ┌────────────────────────────────┼────────────────────────────────┐
        ▼                                ▼                                ▼
[REGIMEN_ADMINISTRATION]       [CLINICAL_FOLLOWUP_REVIEW]       [MONITORING_REASSESSMENT]
Governs drug consumption:      Governs physician visit:         Governs diagnostic test:
"for 2 weeks", "x 10 days",    "review after 1 week",           "check BP in 3 days",
"x 5d", "take for 1 month"     "follow up in 2 months"          "repeat CBC in 1 week"
        │                                │                                │
        ▼                                ▼                                ▼
Eligible for                   EXCLUDED from Dosage             EXCLUDED from Dosage
boundsDuration                 Preserved in note / SIG          Preserved in note / SIG
```

#### Deterministic Role Definitions:
1. **ROLE_REGIMEN_DURATION**:
   - Syntactic Anchor: Directly attached to dose/route/frequency verbs (`"take"`, `"take for"`, `"daily for"`, `"po x"`, `"x [0-9]+ (?:days|weeks|months)"`, `"for [0-9]+ (?:days|weeks|months)"`).
   - Exclusion: Must NOT be governed by review or monitoring verbs.
   - FHIR Consequence: Eligible to populate `timing.repeat.boundsDuration`.
2. **ROLE_FOLLOWUP_REVIEW**:
   - Syntactic Cues: Governed by consultation, appointment, or clinic review keywords:
     `r"\b(?:review|f/u|follow[- ]?up|see|visit|consult|appointment|recheck|re-evaluate)\s+(?:in|after|within)\s+[0-9]+\s+(?:days|weeks|months)\b"`
   - FHIR Consequence: **STRICTLY EXCLUDED** from `Dosage.timing.repeat.boundsDuration`. If present in medication narrative, preserved in `Dosage.text` or routed to `Encounter` / `CarePlan`.
3. **ROLE_MONITORING_REASSESSMENT**:
   - Syntactic Cues: Governed by laboratory or vital sign monitoring:
     `r"\b(?:check|repeat|monitor|draw|measure)\s+(?:labs?|cbc|bmp|inr|creatinine|bp|pulse|levels?)\s+(?:in|after|within)\s+[0-9]+\s+(?:days|weeks|months)\b"`
   - FHIR Consequence: **STRICTLY EXCLUDED** from `Dosage.timing.repeat.boundsDuration`.
4. **ROLE_AMBIGUOUS_TEMPORAL**:
   - Temporal expression lacking explicit governing verbs (e.g. `"1 week"` isolated at the end of a sentence without `"for"`, `"take"`, or `"review"`).
   - FHIR Consequence: **FAILS CLOSED**. Set `boundsDuration = None`.

---

### 2.3 OCR Paragraph and Line-Break Handling

Clinical documents ingested via OCR or digital PDF layout contain two distinct types of formatting breaks:
1. **Soft Line Wraps (`\n`, `\r\n`)**: Artifacts of visual column width or OCR word-wrapping (e.g. `"Prednisone 40 mg daily for 2\nweeks, then 20 mg daily"`).
2. **Double Line Breaks / Paragraph Boundaries (`\n\n`)**: Visual separation between document sections or paragraphs.

#### Structural Rules:
- **Rule 1 (Line-Wrap Protection)**: Ordinary line breaks and whitespace wraps **never** establish medication phase boundaries. They are treated as intra-instruction whitespace.
- **Rule 2 (Paragraph Non-Sufficiency)**: A double line break or paragraph boundary does **not** independently prove a medication phase transition.
  - If a medication order is split across paragraphs:
    ```text
    Prednisone 40 mg daily for 2 weeks
    
    20 mg daily for 2 weeks
    ```
    Although separated by a paragraph break, there is **no explicit transition token** (`"then"`, `"followed by"`).
  - **Invariance Rule**: A paragraph break alone **must not** authorize a sequential taper transition. In the absence of explicit transition cues, the relationship remains ambiguous and the system **fails closed**:
    - Both phases emit `boundsDuration = None`.
    - Both phases retain verbatim text in `Dosage.text`.
    - Audit log records `PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED`.
- **Rule 3 (Explicit Transition Primacy)**: A transition cue (`"then"`, `"followed by"`) establishes a phase boundary regardless of whether it appears on the same line, after a soft line break, or across a paragraph boundary.

---

### 2.4 Transition-Grounded Clause Partitioning Algorithm (Formal Specification)

Let a multi-phase regimen text $T$ contain $N$ ordered dose claims $D_0, D_1, \dots, D_{N-1}$ at offsets $p(D_0) < p(D_1) < \dots < p(D_{N-1})$.

1. **Explicit Transition Detection**:
   Scan text between $p(D_k)$ and $p(D_{k+1})$ for explicit transition tokens using `TAPER_TRANSITION_REGEX`:
   $$\mathcal{T} = \{\text{"then"}, \text{"followed by"}, \text{"afterwards"}, \text{"taper to"}, \text{"step down to"}, \text{"reduce to"}, \text{"decrease to"}\}$$
   - If an explicit transition token is found between $D_k$ and $D_{k+1}$, set boundary $B_k$ to the token's start offset.
   - If no explicit transition token is found, set $B_k = \text{None}$.
2. **Global Regimen Transition Verification**:
   - If any adjacent dose pair has $B_k = \text{None}$, the entire sequence lacks explicit sequential transitions $\implies$ **Fail closed for structured duration** across all phases.
3. **Partition Span Construction**:
   When all $B_k$ are established:
   $$[L_0, R_0) = [0, B_0), \quad [L_k, R_k) = [B_{k-1}, B_k), \quad [L_{N-1}, R_{N-1}) = [B_{N-2}, |T|)$$
4. **Phase Duration Candidate Filter**:
   For Phase $k \in \{0, \dots, N-1\}$:
   $$\mathcal{C}_k = \{ u \in \mathcal{U} \mid L_k \le p(u) < R_k \ \wedge \ \text{role}(u) == \text{ROLE\_REGIMEN\_DURATION} \}$$
   - If $|\mathcal{C}_k| = 1$: Bind $u \in \mathcal{C}_k \implies$ `boundsDuration = u`.
   - If $|\mathcal{C}_k| = 0$: Open-ended maintenance $\implies$ `boundsDuration = None`.
   - If $|\mathcal{C}_k| > 1$: Multiple competing regimen durations $\implies$ **Fail closed** (`boundsDuration = None`).

---

### 2.5 Resolution of Orphan Durations vs. Multiple Durations in One Clause

To eliminate ambiguity in audit and error reporting, the mapper categorizes non-standard duration scenarios into three mutually exclusive cases:

| Case | Definition | Structural Criteria | System Action | Audit Ledger Record |
| :--- | :--- | :--- | :---: | :--- |
| **Case A: Orphan Duration** | An isolated duration token with no corresponding dose or medication anchor anywhere in its syntactic vicinity. | Duration claim $u$ where no dose claim exists in clause $[L(u), R(u))$, and $u$ is not bound to any medication group. | Suppress duration from FHIR `Dosage`. Do not emit structured duration. | `UNATTACHED_EXTRA_DURATION` |
| **Case B: Multiple Plausible Regimen Durations** | Two or more duration claims qualifying as regimen durations within a single phase clause. | Phase clause $k$ has $|\mathcal{C}_k| \ge 2$, where all candidates have `role == ROLE_REGIMEN_DURATION`. | Ambiguous dosing conflict: **Fail closed**. Set Phase $k$ `boundsDuration = None`. Narrative retains text. | `AMBIGUOUS_MULTI_DURATION_CONFLICT` |
| **Case C: Non-Regimen Activity Duration** | A duration within the phase interval that semantically belongs to follow-up, review, or monitoring. | Duration claim $u$ within $[L_k, R_k)$ has $\text{role}(u) \in \{\text{FOLLOWUP}, \text{MONITORING}, \text{HISTORY}\}$. | Exclude from `boundsDuration`. Phase $k$ receives `boundsDuration = None` (unless separate regimen duration exists). | `EXCLUDED_NON_REGIMEN_DURATION` |

---

## 3. Section B: Evidence Requirements for FHIR R4B Lifecycle Mapping

### 3.1 Strict Grounding Invariants for FHIR Resources

The mapper must never fabricate or guess lifecycle states based on ungrounded clinical inferences:
1. **Prescription Cancellation (`MedicationRequest(status="cancelled")`)**:
   - **Grounding Requirement**:
     - Must be an outpatient prescription pad or formal order context (`is_rx_doc == True`).
     - Must contain explicit cancellation language (`"cancel prescription"`, `"discontinue order"`).
     - Must reference an existing verifiable order identifier, prescription number, or prior grounded claim in the same document context.
   - **Fail-Closed Rule**: If a progress note narrative merely says `"discontinue Lisinopril"` without a prescription order context or prior order reference, **DO NOT emit a `MedicationRequest(status="cancelled")`**. Emit a `MedicationStatement(status="stopped")` or suppress if unanchored.
2. **Patient Cessation (`MedicationStatement(status="stopped")`)**:
   - **Grounding Requirement**:
     - Must document actual cessation of therapy (`"patient stopped taking"`, `"discontinued by patient"`, `"d/c'd due to allergy"`, `"ceased"`).
     - Must have a grounded medication name anchor.
   - **Fail-Closed Rule**: Ambiguous negative mentions (`"no Lisinopril"`, `"not on beta blocker"`) must NOT be mapped to `status="stopped"`.
3. **Historical Mention (`MedicationStatement(status="unknown")`)**:
   - **Grounding Requirement**:
     - Must document past medication use (`"past history of"`, `"previously took"`, `"prior trial of"`).
     - Must explicitly map to `status="unknown"` with extension `EXT_TEMPORAL_STATUS="historical"`.
   - **Strict Prohibition**: Historical mention **must never** be mapped to `status="completed"`. In medical documentation, historical use does not verify that a prescribed therapy course was completed.
4. **Completed Course (`MedicationStatement(status="completed")`)**:
   - **Grounding Requirement**:
     - Requires explicit, verbatim clinical evidence of course completion:
       `r"\b(?:completed|finished)\s+(?:course|treatment|therapy|prescription|[0-9]+\s+(?:days|weeks|months))\b"`
   - **Fail-Closed Rule**: If evidence of completion is absent, fall back to `status="unknown"`.

---

### 3.4 Temporal Lifecycle Decision Matrix

| Source Context | Evidence Pattern | Target Resource | Valid FHIR R4B Status | Intent / Category | Fail-Closed Fallback |
| :--- | :--- | :---: | :---: | :---: | :--- |
| Rx Pad / Order | `"Lisinopril 20 mg daily"` | `MedicationRequest` | `active` | `order` | Suppress if unanchored |
| Rx Pad / Order | `"Prescribe Amoxicillin 500 mg tid x 10d"` | `MedicationRequest` | `active` | `order` | Suppress if unanchored |
| Rx Pad / Order | `"Discontinue previous Rx #10492 for Lisinopril"` | `MedicationRequest` | `cancelled` | `order` | Fall back to `MedicationStatement(status="stopped")` if order ref missing |
| Progress Note | `"Patient taking Amlodipine 5 mg daily"` | `MedicationStatement` | `active` | N/A | Route to review if dose ambiguous |
| Progress Note | `"Discontinue Prednisone 50 mg"` | `MedicationStatement` | `stopped` | N/A | Suppress if unanchored |
| Progress Note | `"Past medical history: took Prednisone in 2023"` | `MedicationStatement` | `unknown` | `historical` ext | Suppress if drug identity ungrounded |
| Progress Note | `"Completed 10-day course of Amoxicillin"` | `MedicationStatement` | `completed` | N/A | Fall back to `status="unknown"` if completion unproven |
| Progress Note | `"Prednisone mentioned in passing"` | `MedicationStatement` | `unknown` | N/A | Suppress if context unverified |

---

## 4. Section C: Worked Examples & Clinical Boundary Tracing

### Scenario 14: Duration Ownership vs. Follow-up Collision (The Core Problem)
- **Input Text:** `"Take 40 mg daily for 2 weeks, then increase to 60 mg daily. Review after 1 week."`
- **Claims Extracted:**
  - $D_0$: 40 mg (pos 5); $D_1$: 60 mg (pos 52).
  - Transition $B_0$: `", then increase to "` (pos 33).
  - Durations:
    - $\text{Dur}_0$: `"2 weeks"` (pos 21) $\implies \text{role} = \text{ROLE\_REGIMEN\_DURATION}$.
    - $\text{Dur}_1$: `"1 week"` (pos 79) $\implies \text{role} = \text{ROLE\_FOLLOWUP\_REVIEW}$ (governed by `"Review after"`).
- **Execution Trace:**
  - Boundary $B_0 = 33$ creates:
    - Clause 0 $[0, 33)$: Contains $D_0$ (40 mg) and $\text{Dur}_0$ (2 weeks).
      - $\mathcal{C}_0 = \{\text{Dur}_0\}$. $|\mathcal{C}_0| = 1 \implies$ **Phase 1: 40 mg, boundsDuration = 2.0 wk**.
    - Clause 1 $[33, \infty)$: Contains $D_1$ (60 mg) and $\text{Dur}_1$ (1 week).
      - Semantic Filter: $\text{role}(\text{Dur}_1) == \text{ROLE\_FOLLOWUP\_REVIEW} \implies \text{Dur}_1 \notin \mathcal{C}_1$!
      - $\mathcal{C}_1 = \emptyset$. $|\mathcal{C}_1| = 0 \implies$ **Phase 2: 60 mg, boundsDuration = None**!
- **FHIR R4B Emission:**
  - Instruction 1: `doseQuantity = 40.0 mg`, `boundsDuration = 2.0 wk`, `text = "Take 40 mg daily for 2 weeks"`.
  - Instruction 2: `doseQuantity = 60.0 mg`, `boundsDuration = None`, `text = "Then increase to 60 mg daily. Review after 1 week"`.
- **Verdict:** **SUCCESSFUL DISAMBIGUATION.** Chronic maintenance phase remains unbounded. Prescription does not prematurely terminate.

---

### Scenario 15: Paragraph Break Without Transition Cue (Fail Closed)
- **Input Text:**
  ```text
  Prednisone 40 mg daily for 2 weeks
  
  20 mg daily for 2 weeks
  ```
- **Claims Extracted:** $D_0 = 40\text{ mg}$ (pos 11), $D_1 = 20\text{ mg}$ (pos 38), $\text{Dur}_0 = \text{"2 weeks"}$, $\text{Dur}_1 = \text{"2 weeks"}$.
- **Execution Trace:**
  - Scan between $D_0$ and $D_1$: Found double line break `\n\n`.
  - Check for explicit transition token: **NONE FOUND** ($B_0 = \text{None}$).
  - Rule 2 (Paragraph Non-Sufficiency) triggers: Paragraph boundary alone does not prove sequential execution.
  - Case D applies: **Fail closed for structured duration**.
- **FHIR R4B Emission:**
  - Instruction 1: `doseQuantity = 40.0 mg`, `boundsDuration = None`, `text = "Prednisone 40 mg daily for 2 weeks"`.
  - Instruction 2: `doseQuantity = 20.0 mg`, `boundsDuration = None`, `text = "20 mg daily for 2 weeks"`.
  - Audit Trail: `PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED`.

---

### Scenario 16: Three-Case Duration Taxonomy Verification
- **Input Text:** `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily. Review after 3 days. Also note symptom duration 6 months."`
- **Execution Trace:**
  - $\text{Dur}_0$ (`"2 weeks"`, pos 24) $\implies$ $\text{role} = \text{REGIMEN\_DURATION}$.
  - $\text{Dur}_1$ (`"3 days"`, pos 68) $\implies$ $\text{role} = \text{FOLLOWUP\_REVIEW}$ (governed by `"Review after"`).
  - $\text{Dur}_2$ (`"6 months"`, pos 103) $\implies$ $\text{role} = \text{CLINICAL\_HISTORY}$ (governed by `"symptom duration"`).
- **Classification:**
  - Phase 1: Binds $\text{Dur}_0 \implies$ **boundsDuration = 2.0 wk**.
  - Phase 2: $\text{Dur}_1$ classified as **Case C (Non-Regimen Activity Duration)** $\implies$ Excluded from `boundsDuration`. Phase 2 receives **boundsDuration = None**.
  - $\text{Dur}_2$: Non-regimen history. Does not map to any medication $\implies$ Handled as **Case A (Orphan Non-Medication Duration)**.
- **Verdict:** Zero false bindings. All three duration roles cleanly isolated.

---

## 5. Section D: Expanded Regression Test Suite Specifications

| Test Case Identifier | Scenario / Clinical Condition | Expected Resource & Structure | Expected `boundsDuration` | Expected Audit & Safety Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `test_req44_duration_vs_followup_review_interval` | `"40 mg daily for 2 weeks, then 60 mg daily. Review after 1 week."` | Single `MedicationRequest` (2 instructions) | Inst 1: 2.0 wk; Inst 2: `None` | `"1 week"` recognized as follow-up review; Phase 2 remains unbounded chronic maintenance. |
| `test_req44_duration_vs_monitoring_reassessment` | `"40 mg daily for 2 weeks, then 20 mg daily. Repeat labs in 5 days."` | Single `MedicationRequest` (2 instructions) | Inst 1: 2.0 wk; Inst 2: `None` | `"5 days"` recognized as lab monitoring interval; excluded from medication `boundsDuration`. |
| `test_req44_paragraph_break_without_transition_fails_closed` | Doses separated by `\n\n` without `"then"` or `"followed by"` | Single `MedicationRequest` (2 instructions) | Inst 1: `None`; Inst 2: `None` | **Fails closed**: structured bounds omitted; audit log `PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED`. |
| `test_req44_ocr_line_wrap_with_explicit_transition` | Text has soft line wraps `\n` mid-clause with explicit `"then"` | Single `MedicationRequest` (2 instructions) | Inst 1: 2.0 wk; Inst 2: 2.0 wk | Line wraps normalized; transition boundary establishes exact 2 wk bounds for both phases. |
| `test_req44_taxonomy_case_a_orphan_duration` | Stray duration fragment with no dose in clinical clause | Single `MedicationRequest` | Matching dose bounds only | Stray duration suppressed; audit log `UNATTACHED_EXTRA_DURATION`. |
| `test_req44_taxonomy_case_b_multiple_regimen_durations` | Clause has `"40 mg daily for 2 weeks for 3 days"` | Single `MedicationRequest` (1 instruction) | Inst 1: `None` | **Fails closed**: conflicting durations within single phase yield `boundsDuration = None`. |
| `test_req44_taxonomy_case_c_non_regimen_activity` | Clause has dose followed by clinic follow-up duration | Single `MedicationRequest` (1 instruction) | Inst 1: `None` | Follow-up duration excluded from `Dosage.timing`. |
| `test_req44_lifecycle_cancelled_requires_order_reference` | Progress note says `"Discontinue Lisinopril 20 mg"` | `MedicationStatement(status="stopped")` | `None` | **No ungrounded `MedicationRequest(status="cancelled")` created** without prior order reference. |
| `test_req44_lifecycle_historical_maps_to_unknown_not_completed` | Note says `"History of Prednisone 20 mg daily in 2023"` | `MedicationStatement(status="unknown")` | `None` | Mapped to `status="unknown"` with temporal extension; **NOT** mapped to `"completed"`. |
| `test_req44_lifecycle_completed_requires_explicit_evidence` | Note says `"Completed 10-day course of Amoxicillin 500 mg"` | `MedicationStatement(status="completed")` | `10.0 d` | Verified explicit evidence of completed course satisfies threshold for `status="completed"`. |

---

## 6. Section E: Compliance, Security, & Governance Declarations

1. **Four-Eyes Security Export Blocker Inviolable**:
   The multi-hop Four-Eyes ancestry vulnerability in `src/clindoc/review/service.py` remains an **active, absolute clinical export blocker**. Gate 9 audit ledger verification is preserved with zero bypasses.
2. **Review and Export Code Freeze**:
   Neither [src/clindoc/review/service.py](file:///d:/daa/src/clindoc/review/service.py) nor [src/clindoc/services/fhir_export.py](file:///d:/daa/src/clindoc/services/fhir_export.py) were modified or will be modified under this design.
3. **Strictly Documentation-Only Status**:
   This deliverable is a comprehensive architectural design review (v3.0). Zero production code, test code, or schemas were altered.
4. **FHIR R4B Base Scope Adherence**:
   All specifications strictly target core FHIR R4B Base. No US Core conformance claims are made.
5. **Benchmark Scope Preservation**:
   All benchmark definitions (Synthetic 12-doc/60-doc cascade testbeds, GLiNER-120, E3-20, OOD-5/34, Scope 4 adversarial fixtures) remain completely frozen and separate.
6. **Workspace & Git Invariants**:
   All pre-existing workspace modifications remain completely intact. Zero git operations performed.
7. **Execution Protocol**:
   Implementation will commence **only upon separate, explicit authorization** following review of this v3.0 specification.
