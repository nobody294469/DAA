# Phase 5.2.3 Sub-Milestone 4.4: REQ-44 Remediation Design Review (v3.1)

**Document Authority:** Phase 5.2.3 Sub-Milestone 4.4 Comprehensive Remediation Design Review  
**Revision Date:** 2026-10-31  
**Document Version:** 3.1 (Clarification Release; Supersedes v1.0, v2.0, and v3.0)  
**Operating Mode:** STRICTLY DOCUMENTATION-ONLY REVIEW (Zero production code, test, schema, constant, config, review/export service, or Git modifications)  
**Status:** **REVISED DESIGN REVIEW COMPLETE — AWAITING EXPLICIT AUTHORIZATION PRIOR TO IMPLEMENTATION**  

---

## 1. Executive Summary & Change Summary from v3.0

This specification represents Version 3.1 of the REQ-44 remediation design review. It incorporates five mandatory, narrowly scoped clarifications addressing semantic grounding, clinical-history separation, lifecycle decoupling, completed-course evidence linkage, and regimen scope boundaries prior to any implementation planning.

### Change Summary (What Changed from v3.0):

1. **Source-Span Grounding of Semantic Roles (Section 2.1 & 2.2)**:
   - Formalized that semantic role classification must be grounded in the actual source span, syntactic dependency, and medication context.
   - Regex cues alone **must never** authorize structured duration assignment when meaning is ambiguous, conflicting, or ungrounded.

2. **Correction of Scenario 16 & Clinical History Separation (Section 2.2, 2.5, & 4)**:
   - Corrected Scenario 16: `"symptom duration 6 months"` is formally defined as a **non-medication clinical-history duration**, distinct from an orphan medication duration.
   - Non-medication clinical-history durations are preserved outside medication `Dosage` mapping entirely (routed to condition/history context) and are never treated as unattached medication fragments.

3. **Decoupling Prescription Cancellation Intent from Patient Cessation (Section 3.1, 3.4, & 5)**:
   - Removed any automatic fallback from an unreferenced prescription cancellation directive to `MedicationStatement(status="stopped")`.
   - Established that prescriber cancellation intent and actual patient cessation must remain separate.
   - A stopped `MedicationStatement` strictly requires independent, grounded evidence of actual patient cessation (`"patient stopped taking"`). Unreferenced cancellation directives fail closed without fabricating patient cessation.

4. **Strengthened Grounding for Completed-Course Evidence (Section 3.1, 3.4, & 5)**:
   - Required explicit syntactic and contextual linkage between the completion phrase and the identified medication and its course.
   - Matching completion keywords or durations appearing elsewhere in the document (or detached from the medication clause) are strictly insufficient to authorize `MedicationStatement(status="completed")`.

5. **Explicit Preservation of Regimen Scope Invariants (Section 2.6)**:
   - Explicitly preserved existing production handling for split dosing (`sequence: 1`), competing candidate alternatives (`EXT_COMPETING_CANDIDATE`), unrelated medication mentions, and non-sequential dose claims (e.g. PRN or dose ranges).
   - Constrained the transition-based algorithm to apply **only** when sequential regimen intent is adequately grounded with explicit transition markers.

---

## 2. Section A: Semantic Duration Ownership, Phase Partitioning, & Regimen Scope

### 2.1 The Semantic Role Problem: Source-Span Grounding vs. Pure Regex Matching

In clinical narrative notes, temporal expressions frequently denote non-pharmacological clinical actions rather than medication administration limits.

#### Clinical Hazard Counterexample:
$$\text{"Take 40 mg daily for 2 weeks, then increase to 60 mg daily. Review after 1 week."}$$
- Phase 1: 40 mg daily for 2 weeks.
- Phase 2: 60 mg daily (open-ended maintenance until clinician reassessment).
- Follow-up Action: `"Review after 1 week"`.

#### Source-Span Grounding Invariant:
Under purely positional windowing or surface regex pattern matching, the token `"1 week"` would match a duration pattern within Phase 2's clause window $[p(\text{"then"}), \text{EOF})$, leading to:
`Dosage(doseAndRate=60 mg, timing.repeat.boundsDuration=1.0 wk)`
- **Clinical Hazard**: The patient's electronic prescription prematurely terminates after 7 days, cutting off chronic therapy because a consultation interval was falsely ingested as the medication therapy duration limit.
- **Rule of Grounding**: Pattern matching on duration units (e.g. `r"\b[0-9]+\s+(?:days?|weeks?|months?)\b"`) alone **does not authorize** structured duration assignment. Every temporal expression must be syntactically grounded to the medication administration verb and dose claim within the source text span.

---

### 2.2 Semantic Role Taxonomy & Grounding Requirements

Every extracted temporal expression is evaluated against structural and syntactic criteria within its local text span:

```
                           Extracted Temporal Expression
                                          │
         ┌────────────────────────────────┼────────────────────────────────┬───────────────────────────────┐
         ▼                                ▼                                ▼                               ▼
[REGIMEN_ADMINISTRATION]       [CLINICAL_FOLLOWUP_REVIEW]       [MONITORING_REASSESSMENT]       [NON_MED_CLINICAL_HISTORY]
Governs drug consumption:      Governs physician visit:         Governs diagnostic test:        Governs disease/symptom:
"for 2 weeks", "x 10 days",    "review after 1 week",           "check BP in 3 days",           "symptom duration 6 mos",
"take for 1 month"             "follow up in 2 months"          "repeat CBC in 1 week"          "history of pain for 2 yrs"
         │                                │                                │                               │
         ▼                                ▼                                ▼                               ▼
Eligible for boundsDuration    EXCLUDED from Dosage             EXCLUDED from Dosage            EXCLUDED from Med Mapping
(Requires syntactic grounding) Preserved in note / SIG          Preserved in note / SIG         Preserved in History/Condition
```

#### Deterministic Role Definitions & Grounding Criteria:

1. **`ROLE_REGIMEN_DURATION`**:
   - **Syntactic Grounding**: The temporal expression must be syntactically attached directly to medication administration verbs or frequency indicators (e.g., `"take [dose] daily for [dur]"`, `"[dose] po bid x [dur]"`).
   - **Exclusion**: Must NOT be syntactically governed by clinical review, monitoring, or history verbs.
   - **Ambiguity Rule**: If regex cues suggest a duration but syntactic linkage to the medication dose is ambiguous, conflicting, or separated by non-medication clauses, the token **fails closed** (`boundsDuration = None`).

2. **`ROLE_FOLLOWUP_REVIEW`**:
   - **Syntactic Grounding**: Governed by consultation, encounter, or clinic review verbs (`"review"`, `"f/u"`, `"follow up"`, `"see clinic"`, `"appointment"`, `"re-evaluate"`).
   - **FHIR Consequence**: **STRICTLY EXCLUDED** from `Dosage.timing.repeat.boundsDuration`. Preserved in `Dosage.text` or routed to `Encounter` / `CarePlan`.

3. **`ROLE_MONITORING_REASSESSMENT`**:
   - **Syntactic Grounding**: Governed by laboratory, diagnostic, or vital sign monitoring verbs (`"check"`, `"repeat"`, `"draw"`, `"monitor"`, `"measure"` applied to `"labs"`, `"inr"`, `"cbc"`, `"bp"`, `"levels"`).
   - **FHIR Consequence**: **STRICTLY EXCLUDED** from `Dosage.timing.repeat.boundsDuration`.

4. **`ROLE_NON_MED_CLINICAL_HISTORY`**:
   - **Syntactic Grounding**: Governed by clinical history, symptom onset, or disease duration expressions (`"symptom duration"`, `"history of [condition] for"`, `"pain for past [dur]"`).
   - **FHIR Consequence**: **PRESERVED OUTSIDE MEDICATION MAPPING**. This is not an orphan medication duration; it represents clinical condition history and must be routed to `Condition` / clinical narrative, completely isolated from medication `Dosage`.

5. **`ROLE_AMBIGUOUS_TEMPORAL`**:
   - Any temporal expression lacking explicit, verifiable governing verbs or displaying conflicting cues.
   - **FHIR Consequence**: **FAILS CLOSED**. Set `boundsDuration = None`.

---

### 2.3 OCR Paragraph and Line-Break Handling

Clinical documents ingested via OCR or digital PDF layout contain two distinct formatting breaks:
1. **Soft Line Wraps (`\n`, `\r\n`)**: Artifacts of visual column width or OCR word-wrapping (e.g. `"Prednisone 40 mg daily for 2\nweeks, then 20 mg daily"`).
2. **Double Line Breaks / Paragraph Boundaries (`\n\n`)**: Visual separation between document sections or paragraphs.

#### Structural Invariants:
- **Rule 1 (Line-Wrap Protection)**: Ordinary line breaks and whitespace wraps **never** establish medication phase boundaries. They are treated as intra-instruction whitespace.
- **Rule 2 (Paragraph Non-Sufficiency)**: A double line break or paragraph boundary does **not** independently prove a medication phase transition.
  - If a medication order is split across paragraphs:
    ```text
    Prednisone 40 mg daily for 2 weeks
    
    20 mg daily for 2 weeks
    ```
    Although separated by a paragraph break, there is **no explicit transition token** (`"then"`, `"followed by"`).
  - **Invariance Rule**: In the absence of explicit transition cues, the relationship remains ambiguous and the system **fails closed**:
    - Both phases emit `boundsDuration = None`.
    - Both phases retain verbatim text in `Dosage.text`.
    - Audit log records `PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED`.
- **Rule 3 (Explicit Transition Primacy)**: A transition cue (`"then"`, `"followed by"`) establishes a phase boundary regardless of whether it appears on the same line, after a soft line break, or across a paragraph boundary.

---

### 2.4 Transition-Grounded Clause Partitioning Algorithm

Let a multi-phase regimen text $T$ contain $N$ ordered dose claims $D_0, D_1, \dots, D_{N-1}$ at offsets $p(D_0) < p(D_1) < \dots < p(D_{N-1})$.

1. **Sequential Intent Verification**:
   The transition-based algorithm **only applies** when sequential regimen intent is grounded by explicit transition markers. Scan text between $p(D_k)$ and $p(D_{k+1})$ for transition tokens using `TAPER_TRANSITION_REGEX`:
   $$\mathcal{T} = \{\text{"then"}, \text{"followed by"}, \text{"afterwards"}, \text{"taper to"}, \text{"step down to"}, \text{"reduce to"}, \text{"decrease to"}\}$$
   - If an explicit transition token is found between $D_k$ and $D_{k+1}$, set boundary $B_k$ to the token's start offset.
   - If no explicit transition token is found, set $B_k = \text{None}$.
2. **Global Regimen Transition Verification**:
   - If any adjacent dose pair has $B_k = \text{None}$, the entire sequence lacks explicit sequential transitions $\implies$ **Fail closed for structured duration** across all phases (`boundsDuration = None`).
3. **Partition Span Construction**:
   When all $B_k$ are established:
   $$[L_0, R_0) = [0, B_0), \quad [L_k, R_k) = [B_{k-1}, B_k), \quad [L_{N-1}, R_{N-1}) = [B_{N-2}, |T|)$$
4. **Phase Duration Candidate Filter & Grounding**:
   For Phase $k \in \{0, \dots, N-1\}$:
   $$\mathcal{C}_k = \{ u \in \mathcal{U} \mid L_k \le p(u) < R_k \ \wedge \ \text{role}(u) == \text{ROLE\_REGIMEN\_DURATION} \}$$
   - If $|\mathcal{C}_k| = 1$: Bind $u \in \mathcal{C}_k \implies$ `boundsDuration = u`.
   - If $|\mathcal{C}_k| = 0$: Open-ended maintenance $\implies$ `boundsDuration = None`.
   - If $|\mathcal{C}_k| > 1$: Multiple competing regimen durations $\implies$ **Fail closed** (`boundsDuration = None`).

---

### 2.5 Taxonomy of Extra, Conflicting, & Non-Medication Durations

To maintain audit integrity and eliminate false bindings, the mapper categorizes durations into four mutually exclusive cases:

| Case | Definition | Structural Criteria | System Action | Audit Ledger Record |
| :--- | :--- | :--- | :---: | :--- |
| **Case A: Orphan Medication Duration** | An isolated duration token located within a medication instruction block that lacks any dose anchor. | Duration claim $u$ within medication span where no dose claim exists in clause $[L(u), R(u))$. | Suppress duration from FHIR `Dosage`. Do not emit structured duration. | `UNATTACHED_EXTRA_DURATION` |
| **Case B: Multiple Plausible Regimen Durations** | Two or more duration claims qualifying as regimen durations within a single phase clause. | Phase clause $k$ has $|\mathcal{C}_k| \ge 2$, where all candidates have `role == ROLE_REGIMEN_DURATION`. | Ambiguous dosing conflict: **Fail closed**. Set Phase $k$ `boundsDuration = None`. Narrative retains text. | `AMBIGUOUS_MULTI_DURATION_CONFLICT` |
| **Case C: Non-Regimen Activity Duration** | A duration within the phase interval that semantically belongs to follow-up, review, or monitoring. | Duration claim $u$ within $[L_k, R_k)$ has $\text{role}(u) \in \{\text{FOLLOWUP}, \text{MONITORING}\}$. | Exclude from `boundsDuration`. Phase $k$ receives `boundsDuration = None` (unless separate regimen duration exists). | `EXCLUDED_NON_REGIMEN_DURATION` |
| **Case D: Non-Medication Clinical-History Duration** | A duration expressing symptom duration, disease history, or non-medication clinical timeline. | Duration claim $u$ has $\text{role}(u) == \text{ROLE\_NON\_MED\_CLINICAL\_HISTORY}$ (e.g. `"symptom duration 6 months"`). | **Preserve outside medication Dosage mapping**. Route to Condition/History context. Zero medication binding. | `PRESERVED_CLINICAL_HISTORY_DURATION` |

---

### 2.6 Regimen Scope Invariants: Split Dosing, Competing Alternatives, & Non-Sequential Mentions

The transition-based sequential algorithm must operate strictly within bounded regimen contexts. The following clinical constructs must be explicitly preserved:

1. **Split Dosing Preservation**:
   - Dosing regimens specifying different times of day for the same medication (e.g. `"20 mg in the morning and 10 mg at bedtime"`, or claims flagged with `split_dose=True`) represent **concurrent daily administration**, not sequential phases.
   - **Invariant**: Split dosing instructions are assigned concurrent sequence values (`sequence: 1`) and distinct `timing.code` or `asNeeded` attributes. The transition-based taper algorithm **must not** sequence split doses.
2. **Competing Alternatives Preservation**:
   - In clinical notes, alternative formulations or disputed doses (e.g. `"Amlodipine 5 mg or 10 mg daily"`) represent competing candidates.
   - **Invariant**: The mapper's existing candidate disambiguation, confidence ranking, shadowing (`EXT_COMPETING_CANDIDATE`), and validator logic remain completely intact. Competing options are never chained as sequential taper phases.
3. **Unrelated Medication Mentions Preservation**:
   - Notes frequently list multiple medications in close proximity (e.g. `"Lisinopril 20 mg daily. Metformin 500 mg bid."`).
   - **Invariant**: Distinct medication entities form independent FHIR resources. They must never be merged into a multi-phase sequential regimen, even if separated only by whitespace or punctuation.
4. **Non-Sequential Dose Claims Preservation**:
   - PRN instructions (e.g. `"Morphine 5 mg q4h prn severe pain"`), dose ranges (`"10-20 mg daily"`), or titration notes without explicit phase transitions must not be coerced into sequential phases.
   - **Invariant**: Unless explicit sequential transition cues (`"then"`, `"followed by"`) connect ordered dose phases of the same drug, claims remain single-phase or fail closed.

---

## 3. Section B: Evidence Requirements for FHIR R4B Lifecycle Mapping

### 3.1 Strict Grounding Invariants for FHIR Resources

The mapper must never fabricate or guess lifecycle states based on ungrounded clinical inferences or cross-category fallbacks:

1. **Prescription Cancellation (`MedicationRequest(status="cancelled")`)**:
   - **Grounding Requirement**:
     - Must be an outpatient prescription pad or formal order context (`is_rx_doc == True`).
     - Must contain explicit order cancellation language (`"cancel prescription"`, `"discontinue order"`, `"void rx"`).
     - Must reference an existing verifiable order identifier, prescription number, or prior grounded claim in the same document context.
   - **Decoupling & Fail-Closed Rule**:
     - Prescriber cancellation intent and actual patient cessation **must remain completely separate**.
     - If a cancellation directive lacks a verifiable prior order reference, **DO NOT automatically fall back to `MedicationStatement(status="stopped")`**.
     - An unreferenced cancellation directive fails closed (suppressed or routed to review with `UNREFERENCED_ORDER_CANCELLATION_FAIL_CLOSED`). It must never fabricate patient cessation.

2. **Patient Cessation (`MedicationStatement(status="stopped")`)**:
   - **Grounding Requirement**:
     - Strictly requires independent, grounded evidence of **actual patient cessation** (`"patient stopped taking"`, `"discontinued by patient"`, `"patient ceased medication"`, `"patient self-discontinued"`).
     - Must have a grounded medication name anchor.
   - **Fail-Closed Rule**: Ambiguous negative mentions (`"no Lisinopril"`, `"not on beta blocker"`) or prescriber order cancellations without patient cessation evidence must NOT be mapped to `status="stopped"`.

3. **Historical Mention (`MedicationStatement(status="unknown")`)**:
   - **Grounding Requirement**:
     - Must document past medication use (`"past history of"`, `"previously took"`, `"prior trial of"`).
     - Must explicitly map to `status="unknown"` with extension `EXT_TEMPORAL_STATUS="historical"`.
   - **Strict Prohibition**: Historical mention **must never** be mapped to `status="completed"`. In medical documentation, past use does not verify that a prescribed therapy course was completed.

4. **Completed Course (`MedicationStatement(status="completed")`)**:
   - **Grounding Requirement**:
     - Requires explicit, verbatim clinical evidence of course completion **syntactically linked directly to the identified medication and its course**:
       `r"\b(?:completed|finished)\s+(?:course|treatment|therapy|prescription|[0-9]+\s+(?:days|weeks|months))\s+of\s+<medication>\b"`
     - Or immediate intra-clause coordination (e.g. `"<medication> [dose] ... completed 10-day course"`).
   - **Non-Sufficiency Rule**: A matching keyword (e.g. `"completed"`) or a duration appearing elsewhere in the same note (detached from the medication entity) is **strictly insufficient** to authorize `status="completed"`.
   - **Fail-Closed Rule**: In the absence of direct syntactic linkage, fall back to `status="unknown"` (with `EXT_TEMPORAL_STATUS="historical"` if past use is established) or fail closed.

---

### 3.4 Temporal Lifecycle Decision Matrix

| Source Context | Evidence Pattern | Target Resource | Valid FHIR R4B Status | Intent / Category | Fail-Closed Fallback |
| :--- | :--- | :---: | :---: | :---: | :--- |
| Rx Pad / Order | `"Lisinopril 20 mg daily"` | `MedicationRequest` | `active` | `order` | Suppress if unanchored |
| Rx Pad / Order | `"Prescribe Amoxicillin 500 mg tid x 10d"` | `MedicationRequest` | `active` | `order` | Suppress if unanchored |
| Rx Pad / Order | `"Discontinue previous Rx #10492 for Lisinopril"` | `MedicationRequest` | `cancelled` | `order` | Suppress if prior order unreferenced; **NEVER** fall back to patient `stopped` |
| Progress Note | `"Patient taking Amlodipine 5 mg daily"` | `MedicationStatement` | `active` | N/A | Route to review if dose ambiguous |
| Progress Note | `"Patient stopped taking Prednisone 50 mg"` | `MedicationStatement` | `stopped` | N/A | Suppress if unanchored |
| Progress Note | `"Past medical history: took Prednisone in 2023"` | `MedicationStatement` | `unknown` | `historical` ext | Suppress if drug identity ungrounded |
| Progress Note | `"Completed 10-day course of Amoxicillin"` | `MedicationStatement` | `completed` | N/A | Fall back to `status="unknown"` if completion unproven or unlinked |
| Progress Note | `"Discontinue Prednisone"` (no order ref, no patient cessation) | Suppressed / Review | N/A | N/A | `UNREFERENCED_ORDER_CANCELLATION_FAIL_CLOSED` (no statement created) |

---

## 4. Section C: Worked Examples & Clinical Boundary Tracing

### Scenario 14: Duration Ownership vs. Follow-up Collision
- **Input Text:** `"Take 40 mg daily for 2 weeks, then increase to 60 mg daily. Review after 1 week."`
- **Claims Extracted:**
  - $D_0$: 40 mg (pos 5); $D_1$: 60 mg (pos 52).
  - Transition $B_0$: `", then increase to "` (pos 33).
  - Durations:
    - $\text{Dur}_0$: `"2 weeks"` (pos 21) $\implies \text{role} = \text{ROLE\_REGIMEN\_DURATION}$ (grounded to `"take ... daily for"`).
    - $\text{Dur}_1$: `"1 week"` (pos 79) $\implies \text{role} = \text{ROLE\_FOLLOWUP\_REVIEW}$ (grounded to `"Review after"`).
- **Execution Trace:**
  - Clause 0 $[0, 33)$: Contains $D_0$ (40 mg) and $\text{Dur}_0$ (2 weeks).
    - $\mathcal{C}_0 = \{\text{Dur}_0\}$. $|\mathcal{C}_0| = 1 \implies$ **Phase 1: 40 mg, boundsDuration = 2.0 wk**.
  - Clause 1 $[33, \infty)$: Contains $D_1$ (60 mg) and $\text{Dur}_1$ (1 week).
    - Semantic Filter: $\text{role}(\text{Dur}_1) == \text{ROLE\_FOLLOWUP\_REVIEW} \implies \text{Dur}_1 \notin \mathcal{C}_1$.
    - $\mathcal{C}_1 = \emptyset$. $|\mathcal{C}_1| = 0 \implies$ **Phase 2: 60 mg, boundsDuration = None**.
- **FHIR R4B Emission:**
  - Instruction 1: `doseQuantity = 40.0 mg`, `boundsDuration = 2.0 wk`, `text = "Take 40 mg daily for 2 weeks"`.
  - Instruction 2: `doseQuantity = 60.0 mg`, `boundsDuration = None`, `text = "Then increase to 60 mg daily. Review after 1 week"`.
- **Verdict:** **SUCCESSFUL DISAMBIGUATION.** Chronic maintenance phase remains unbounded.

---

### Scenario 15: Paragraph Break Without Transition Cue (Fail Closed)
- **Input Text:**
  ```text
  Prednisone 40 mg daily for 2 weeks
  
  20 mg daily for 2 weeks
  ```
- **Execution Trace:**
  - Double line break `\n\n` detected between $D_0$ and $D_1$.
  - Transition token scan: **NONE FOUND** ($B_0 = \text{None}$).
  - Rule 2 (Paragraph Non-Sufficiency) triggers: Paragraph boundary alone does not authorize sequential taper.
  - Fail closed for structured duration.
- **FHIR R4B Emission:**
  - Instruction 1: `doseQuantity = 40.0 mg`, `boundsDuration = None`, `text = "Prednisone 40 mg daily for 2 weeks"`.
  - Instruction 2: `doseQuantity = 20.0 mg`, `boundsDuration = None`, `text = "20 mg daily for 2 weeks"`.
  - Audit Trail: `PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED`.

---

### Scenario 16: Non-Medication Clinical History vs. Medication Durations
- **Input Text:** `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily. Review after 3 days. Also note symptom duration 6 months."`
- **Claims & Roles:**
  - $\text{Dur}_0$ (`"2 weeks"`, pos 24) $\implies$ $\text{role} = \text{ROLE\_REGIMEN\_DURATION}$ (grounded to `"daily for"`).
  - $\text{Dur}_1$ (`"3 days"`, pos 68) $\implies$ $\text{role} = \text{ROLE\_FOLLOWUP\_REVIEW}$ (governed by `"Review after"`).
  - $\text{Dur}_2$ (`"6 months"`, pos 103) $\implies$ $\text{role} = \text{ROLE\_NON\_MED\_CLINICAL\_HISTORY}$ (governed by `"symptom duration"`).
- **Classification & Association:**
  - Phase 1: Binds $\text{Dur}_0 \implies$ **boundsDuration = 2.0 wk**.
  - Phase 2: $\text{Dur}_1$ classified as **Case C (Non-Regimen Activity Duration)** $\implies$ Excluded from `boundsDuration`. Phase 2 receives **boundsDuration = None**.
  - $\text{Dur}_2$: Evaluated under **Case D (Non-Medication Clinical-History Duration)**:
    * Not an orphan medication token.
    * Completely preserved outside medication Dosage mapping (routed to `Condition` / clinical history).
    * Zero association with Prednisone.
- **Verdict:** Clean separation of regimen duration, follow-up timing, and non-medication clinical timeline.

---

### Scenario 17: Detached Completed Keyword vs. Syntactic Linkage
- **Input Text:** `"Amoxicillin 500 mg tid for 10 days. Patient completed physical therapy course 2 weeks ago."`
- **Execution Trace:**
  - Extraction contains medication `"Amoxicillin"`, duration `"10 days"`, and elsewhere in text the completion phrase `"completed ... course"`.
  - Linkage Check: Completion phrase is syntactically bound to `"physical therapy"`, not `"Amoxicillin"`.
  - Rule of Grounding: Detached completion keyword elsewhere in document does **not** authorize `MedicationStatement(status="completed")`.
- **FHIR R4B Emission:**
  - `MedicationStatement(status="active")` (or `unknown` if past use context) with `boundsDuration = 10.0 d`.
  - Amoxicillin is **NOT** marked as `status="completed"`.

---

## 5. Section D: Expanded Regression Test Suite Specifications

| Test Case Identifier | Scenario / Clinical Condition | Expected Resource & Structure | Expected `boundsDuration` | Expected Audit & Safety Outcome |
| :--- | :--- | :--- | :--- | :--- |
| `test_req44_duration_vs_followup_review_interval` | `"40 mg daily for 2 weeks, then 60 mg daily. Review after 1 week."` | Single `MedicationRequest` (2 instructions) | Inst 1: 2.0 wk; Inst 2: `None` | `"1 week"` recognized as follow-up review; Phase 2 remains unbounded chronic maintenance. |
| `test_req44_duration_vs_monitoring_reassessment` | `"40 mg daily for 2 weeks, then 20 mg daily. Repeat labs in 5 days."` | Single `MedicationRequest` (2 instructions) | Inst 1: 2.0 wk; Inst 2: `None` | `"5 days"` recognized as lab monitoring interval; excluded from medication `boundsDuration`. |
| `test_req44_paragraph_break_without_transition_fails_closed` | Doses separated by `\n\n` without `"then"` or `"followed by"` | Single `MedicationRequest` (2 instructions) | Inst 1: `None`; Inst 2: `None` | **Fails closed**: structured bounds omitted; audit log `PARAGRAPH_BREAK_WITHOUT_TRANSITION_FAIL_CLOSED`. |
| `test_req44_ocr_line_wrap_with_explicit_transition` | Text has soft line wraps `\n` mid-clause with explicit `"then"` | Single `MedicationRequest` (2 instructions) | Inst 1: 2.0 wk; Inst 2: 2.0 wk | Line wraps normalized; transition boundary establishes exact 2 wk bounds for both phases. |
| `test_req44_taxonomy_case_a_orphan_medication_duration` | Stray duration fragment within medication text with no dose | Single `MedicationRequest` | Matching dose bounds only | Stray duration suppressed; audit log `UNATTACHED_EXTRA_DURATION`. |
| `test_req44_taxonomy_case_b_multiple_regimen_durations` | Clause has `"40 mg daily for 2 weeks for 3 days"` | Single `MedicationRequest` (1 instruction) | Inst 1: `None` | **Fails closed**: conflicting durations within single phase yield `boundsDuration = None`. |
| `test_req44_taxonomy_case_c_non_regimen_activity` | Clause has dose followed by clinic follow-up duration | Single `MedicationRequest` (1 instruction) | Inst 1: `None` | Follow-up duration excluded from `Dosage.timing`. |
| `test_req44_taxonomy_case_d_symptom_history_duration` | Note has medication order and `"symptom duration 6 months"` | `MedicationRequest` + `Condition` history | Med: normal; Condition: 6 mos | History duration preserved outside medication Dosage; zero medication contamination. |
| `test_req44_lifecycle_unreferenced_cancellation_fails_closed` | Progress note says `"Discontinue Lisinopril 20 mg"` | Suppressed from Rx / Review Route | `None` | **No ungrounded `MedicationStatement(status="stopped")` created** without patient cessation evidence. |
| `test_req44_lifecycle_patient_cessation_emits_stopped` | Note says `"Patient stopped taking Lisinopril 20 mg"` | `MedicationStatement(status="stopped")` | `None` | Grounded evidence of patient cessation authorizes `status="stopped"`. |
| `test_req44_lifecycle_historical_maps_to_unknown_not_completed` | Note says `"History of Prednisone 20 mg daily in 2023"` | `MedicationStatement(status="unknown")` | `None` | Mapped to `status="unknown"` with temporal extension; **NOT** mapped to `"completed"`. |
| `test_req44_lifecycle_completed_requires_syntactic_linkage` | Note says `"Amoxicillin 500 mg. Completed PT course."` | `MedicationStatement(status="active")` | `None` | Detached `"completed"` does not contaminate medication; Amoxicillin remains non-completed. |
| `test_req44_split_dosing_preserved_concurrent` | `"20 mg in morning and 10 mg at bedtime"` | Single `MedicationRequest` (2 instructions) | Both `None` (or explicit) | Instructions remain concurrent (`sequence: 1`), not sequenced by taper algorithm. |

---

## 6. Section E: Compliance, Security, & Governance Declarations

1. **Four-Eyes Security Export Blocker Inviolable**:
   The multi-hop Four-Eyes ancestry vulnerability in `src/clindoc/review/service.py` remains an **active, absolute clinical export blocker**. Gate 9 audit ledger verification is preserved with zero bypasses.
2. **Review and Export Code Freeze**:
   Neither [src/clindoc/review/service.py](file:///d:/daa/src/clindoc/review/service.py) nor [src/clindoc/services/fhir_export.py](file:///d:/daa/src/clindoc/services/fhir_export.py) were modified or will be modified under this design.
3. **Strictly Documentation-Only Status**:
   This deliverable is a comprehensive architectural design review (v3.1). Zero production code, test code, or schemas were altered.
4. **FHIR R4B Base Scope Adherence**:
   All specifications strictly target core FHIR R4B Base. No US Core conformance claims are made.
5. **Benchmark Scope Preservation**:
   All benchmark definitions (Synthetic 12-doc/60-doc cascade testbeds, GLiNER-120, E3-20, OOD-5/34, Scope 4 adversarial fixtures) remain completely frozen and separate.
6. **Workspace & Git Invariants**:
   All pre-existing workspace modifications remain completely intact. Zero git operations performed.
7. **Execution Protocol**:
   Implementation will commence **only upon separate, explicit authorization** following review of this v3.1 specification.
