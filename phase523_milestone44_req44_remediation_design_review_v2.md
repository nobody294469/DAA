# Phase 5.2.3 Sub-Milestone 4.4: REQ-44 Remediation Design Review (v2.0)

**Document Authority:** Phase 5.2.3 Sub-Milestone 4.4 Revised Remediation Design Review  
**Revision Date:** 2026-10-31  
**Document Version:** 2.0 (Supersedes v1.0)  
**Operating Mode:** STRICTLY DOCUMENTATION-ONLY REVIEW (Zero production code, test, schema, constant, config, or Git modifications)  
**Status:** **REVISED DESIGN REVIEW COMPLETE — PENDING EXPLICIT AUTHORIZATION PRIOR TO IMPLEMENTATION**  

---

## 1. Executive Summary & Review Scope

This revised design specification addresses the corrective feedback on the initial remediation review. It formalizes a rigorous, syntax-grounded model for multi-phase duration association and establishes an evidence-based FHIR R4B lifecycle mapping protocol.

### Key Corrections in Version 2.0:
1. **Explicit Transition Priority for Clause Boundaries**:
   Explicit transition cues (`"then"`, `"followed by"`, `"thereafter"`, `"step down to"`, etc.) strictly establish phase boundaries. Commas (`,`), administration phrases (`"with meals"`), and OCR line breaks (`\n`) are protected and **never** split medication instructions.
2. **Elimination of Midpoint Fallback for Structured Assignment**:
   When explicit transition cues are absent (compressed shorthand), the system **fails closed**: structured `boundsDuration` is set to `None`, preventing ungrounded duration fabrication.
3. **Evidence-Based FHIR R4B Lifecycle Mapping**:
   Corrected the mapping of discontinued and historical mentions:
   - Discontinued narrative mentions map to `MedicationStatement(status="stopped")` or document cancellation, not ungrounded `MedicationRequest(status="stopped")`.
   - Historical mentions map to `MedicationStatement(status="unknown")`, explicitly preventing the false equivalence of historical mention with completed therapy.
4. **Expanded Edge-Case Specifications**:
   Added formal models and test specifications for internal commas, administration phrases, OCR line wraps, absent transitions, orphan durations, unknown temporal status, historical/current segregation, and medication strength annotations.

---

## 2. Section A: REQ-44-03 — Phase-Aware Duration Association Design (Corrected)

### 2.1 Rejection of Flawed Heuristics
- **Rejection of `[prev_d_pos, next_d_pos)`**: As established, expanding window starts to the preceding dose causes trailing durations belonging to Phase $k$ to fall into the candidate window of Phase $k+1$.
- **Rejection of Midpoint Fallback**: If two doses appear without an explicit transition cue (e.g. `"Prednisone 40 mg 2 weeks, 20 mg 1 week"`), guessing a midpoint coordinate boundary ($B = \lfloor(p_1 + p_2)/2\rfloor$) to assign structured duration is clinically unsafe. Positional proximity alone does not prove clinical intent. In the absence of explicit transition markers, structured duration assignment **must fail closed**.

---

### 2.2 Transition-Grounded Phase Partitioning

Let a multi-phase regimen text $T$ contain $N$ ordered dose claims $D_0, D_1, \dots, D_{N-1}$ located at character offsets $p(D_0) < p(D_1) < \dots < p(D_{N-1})$.

```
  Phase 0 Clause [0, B_0)              Phase 1 Clause [B_0, B_1)          Phase 2 Clause [B_1, |T|)
┌───────────────────────────────┐  ┌───────────────────────────────┐  ┌──────────────────────────────┐
│ [Leading/Trailing Dur_0] D_0  │  │ [Leading/Trailing Dur_1] D_1  │  │ D_2 (Unbounded Maintenance)  │
└───────────────────────────────┘  └───────────────────────────────┘  └──────────────────────────────┘
                                ▲                                  ▲
                       Transition B_0                     Transition B_1
                     (", then ", pos 32)                (", then ", pos 63)
```

#### 1. Transition Boundary Identification ($B_k$)
Between each adjacent dose pair $(D_k, D_{k+1})$, a transition boundary $B_k \in (p(D_k), p(D_{k+1}))$ is established **exclusively** by the earliest occurrence of an explicit transition marker from `TAPER_TRANSITION_REGEX`:
$$\text{Transition Tokens} = \{\text{"then"}, \text{"followed by"}, \text{"afterwards"}, \text{"taper to"}, \text{"tapering to"}, \text{"step down to"}, \text{"reduce to"}, \text{"decrease to"}, \text{"thereafter"}\}$$
- The boundary coordinate $B_k$ is set to the exact start offset of the transition token (or preceding whitespace/comma immediately prefixing the transition token, e.g. the comma in `", then"`).
- **Invariance Rule 1**: If no explicit transition token exists between $D_k$ and $D_{k+1}$, $B_k = \text{None}$. The regimen is flagged as lacking explicit sequential transitions.

#### 2. Protection of Commas, Administration Instructions, and OCR Line Breaks
The following syntactic elements **must never** create a clause boundary:
- **Internal Commas**: Administration routes, qualifiers, and frequencies (`"40 mg daily, by mouth, with breakfast, for 2 weeks"`). Commas are treated as intra-instruction tokens.
- **Administration Phrases**: Timing and food qualifiers (`"with food"`, `"in the morning"`, `"at bedtime"`, `"before meals"`).
- **OCR Line Breaks**: Line break characters (`\n`, `\r\n`) within narrative text often break lines mid-clause (e.g. `"40 mg daily for 2\nweeks"`). OCR line breaks are treated as standard whitespace and do not split clauses unless accompanied by an explicit transition cue or double line break separating distinct paragraphs.

#### 3. Partition Intervals $[L_k, R_k)$
When all transition boundaries $B_0, B_1, \dots, B_{N-2}$ are successfully grounded:
$$\begin{aligned}
[L_0, R_0) &= [0, B_0) \\
[L_k, R_k) &= [B_{k-1}, B_k) \quad \text{for } 0 < k < N-1 \\
[L_{N-1}, R_{N-1}) &= [B_{N-2}, |T|)
\end{aligned}$$
Every interval $[L_k, R_k)$ is pairwise disjoint:
$$[L_j, R_j) \cap [L_k, R_k) = \emptyset \quad \forall j \neq k$$

#### 4. Candidate Assignment & Fail-Closed Rules
For each phase $k \in \{0, \dots, N-1\}$ with duration candidate set $\mathcal{C}_k = \{ u \in \mathcal{U} \mid L_k \le p(u) < R_k \}$:
- **Case A (Explicit Transition Present, $|\mathcal{C}_k| = 1$)**: Bind candidate $u$ to Phase $k$. Set `boundsDuration` to $u$.
- **Case B (Explicit Transition Present, $|\mathcal{C}_k| = 0$)**: Phase $k$ is open-ended maintenance. Set `boundsDuration = None`. No duration is borrowed or inherited.
- **Case C (Explicit Transition Present, $|\mathcal{C}_k| > 1$)**: Multiple durations in a single clause (e.g. `"for 2 weeks for 3 days"`). Ambiguous conflict: **fail closed** (`boundsDuration = None`).
- **Case D (Explicit Transition Absent)**: If any adjacent dose pair lacks an explicit transition marker ($B_k = \text{None}$), the entire regimen **fails closed for structured duration**:
  - `boundsDuration = None` for all phases.
  - Verbatim text is preserved in `Dosage.text`.
  - Audit warning `AMBIGUOUS_TRANSITION_FAIL_CLOSED` is recorded.

---

## 3. Section B: Evidence-Based FHIR R4B Lifecycle & Temporal Status Mapping (Corrected)

### 3.1 Clarification of FHIR R4B Resource Types & Status Semantics

Clinical documentation reflects two fundamentally distinct clinical perspectives governed by project authority DEC-008:
1. **Prescription Intent (`MedicationRequest`)**: Emitted for outpatient prescription pads, physician orders, or prospective orders (`is_rx_doc == True` or `temporal_status == "planned"`).
2. **Clinical Statement / History (`MedicationStatement`)**: Emitted for progress notes, consultation reports, patient history, or reconciliation of medications reported by patient or external records.

---

### 3.2 Evidence-Based Temporal Mapping Matrix

| Internal Temporal Category | Source Evidence Pattern | Document Scope | Valid FHIR R4B Resource & Status | Clinical Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **`current`** | Active prescription; `"take daily"`; current med list | Rx Pad / Order | `MedicationRequest(status="active", intent="order")` | Active executable prescription order. |
| **`current`** | Active medication reported in clinic note | Progress Note | `MedicationStatement(status="active")` | Confirmed currently active therapy. |
| **`planned`** | Prospective order; `"will start"`; `"prescribe"` | Any | `MedicationRequest(status="active", intent="plan")` (or `MedicationStatement(status="intended")`) | Prospective therapy not yet dispensed/executed. |
| **`discontinued`** | Physician order: `"Discontinue Lisinopril 20 mg"` | Rx Pad / Order | Prior order cancelled; if new resource required: `MedicationRequest(status="cancelled", intent="order")` | Halts or cancels order execution. |
| **`discontinued`** | Narrative statement: `"Patient stopped taking Metformin"` | Progress Note | `MedicationStatement(status="stopped")` | Documents patient cessation of medication. |
| **`historical`** | Historical mention: `"Past medical history: took Prednisone in 2023"` | Progress Note | `MedicationStatement(status="unknown")` with `EXT_TEMPORAL_STATUS="historical"` | **Do NOT map to `"completed"`**. Historical narrative rarely documents completed therapy courses. |
| **`historical`** | Explicitly completed course: `"Completed 10-day course of Amoxicillin"` | Progress Note | `MedicationStatement(status="completed")` | Requires explicit evidence of completed therapy course. |
| **`unknown` / `unspecified`** | Medication mentioned without temporal modifier | Progress Note | `MedicationStatement(status="unknown")` | Preserves safety; avoids assuming active or stopped. |

---

### 3.3 Mapper-Boundary Temporal Segregation Protocol

When a raw group arrives containing claims with mixed temporal categories:
1. **Active + Planned**:
   - Sequential taper phases or split doses.
   - **Safe to combine**: Emitted as a single multi-phase `MedicationRequest` or `MedicationStatement`. Individual phases retain element provenance.
2. **Active/Planned + Discontinued (Separate Clauses & Anchors)**:
   - Example: `"Discontinue Prednisone 50 mg. Prescribe Prednisone 10 mg daily for 5 days."`
   - **Safe to partition**:
     - Partition 1 (`discontinued`): Emits `MedicationStatement(status="stopped")` (or `MedicationRequest(status="cancelled")` if Rx context) with 50 mg dose and `boundsDuration = None`.
     - Partition 2 (`planned`): Emits `MedicationRequest(status="active", intent="order")` with 10 mg dose and `boundsDuration = 5.0 d`.
     - Zero attribute transfer: 50 mg never receives 5 days duration.
3. **Active/Planned + Historical (Separate Clauses)**:
   - Example: `"History of Lisinopril 10 mg. Starting Lisinopril 20 mg daily."`
   - **Safe to partition**:
     - Partition 1 (`historical`): Emits `MedicationStatement(status="unknown")` for 10 mg.
     - Partition 2 (`planned`): Emits `MedicationRequest(status="active")` for 20 mg.
4. **Contradictory / Unresolvable Temporal Status**:
   - Conflicting annotations on the same dose claim, or mixing `discontinued` with `unspecified` within a single unsegmented span.
   - **Fail closed**: Entire group suppressed. Zero resources emitted. Recorded in audit trail as `CONTRADICTORY_TEMPORAL_STATUS_CONFLICT`.

---

## 4. Section C: Worked Examples & Boundary Tracing

### Scenario 1: Trailing Duration
- **Text:** `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks, then 10 mg daily."`
- **Boundaries:** $B_0 = 32$ (`", then"`), $B_1 = 63$ (`", then"`).
- **Partitions:**
  - $[0, 32)$: 40 mg, `"2 weeks"` $\implies$ **Phase 1: 40 mg, 2 wk boundsDuration**.
  - $[32, 63)$: 20 mg, `"2 weeks"` $\implies$ **Phase 2: 20 mg, 2 wk boundsDuration**.
  - $[63, \infty)$: 10 mg, no duration $\implies$ **Phase 3: 10 mg, boundsDuration = None**.

### Scenario 2: Leading Duration
- **Text:** `"For 2 weeks take Prednisone 40 mg daily, then for 2 weeks take 20 mg daily, then take 10 mg daily."`
- **Boundaries:** $B_0 = 39$ (`", then"`), $B_1 = 80$ (`", then"`).
- **Partitions:**
  - $[0, 39)$: Contains `"2 weeks"` (pos 4) and 40 mg (pos 30) $\implies$ **Phase 1: 40 mg, 2 wk boundsDuration**.
  - $[39, 80)$: Contains `"2 weeks"` (pos 49) and 20 mg (pos 71) $\implies$ **Phase 2: 20 mg, 2 wk boundsDuration**.
  - $[80, \infty)$: Contains 10 mg (pos 98), zero durations $\implies$ **Phase 3: 10 mg, boundsDuration = None**.
- **Result:** Preceding duration accurately captured in Phase 1 clause. Zero duration theft.

### Scenario 3: Mixed Placement
- **Text:** `"Prednisone 40 mg daily for 2 weeks, then for 2 weeks take 20 mg daily, then 10 mg daily."`
- **Boundaries:** $B_0 = 32$ (`", then"`), $B_1 = 70$ (`", then"`).
- **Partitions:**
  - $[0, 32)$: Contains 40 mg and `"2 weeks"` (pos 24) $\implies$ **Phase 1: 40 mg, 2 wk boundsDuration**.
  - $[32, 70)$: Contains `"2 weeks"` (pos 43) and 20 mg (pos 61) $\implies$ **Phase 2: 20 mg, 2 wk boundsDuration**.
  - $[70, \infty)$: Contains 10 mg, zero durations $\implies$ **Phase 3: 10 mg, boundsDuration = None**.

### Scenario 10 (Expanded): Internal Commas & Administration Phrases
- **Text:** `"Prednisone 40 mg daily, by mouth, with breakfast, for 2 weeks, then 20 mg daily, with breakfast, for 2 weeks, then 10 mg daily."`
- **Analysis:**
  - Internal commas prefixing `"by mouth"` and `"with breakfast"` are within-clause qualifiers.
  - Transition boundaries occur strictly at explicit markers: $B_0$ (`", then"`, pos 56), $B_1$ (`", then"`, pos 103).
  - Internal commas do **not** split the clause.
- **Partitions:**
  - Phase 1 $[0, 56)$: Contains 40 mg, daily, by mouth, with breakfast, for 2 weeks $\implies$ **boundsDuration = 2 wk**.
  - Phase 2 $[56, 103)$: Contains 20 mg, daily, with breakfast, for 2 weeks $\implies$ **boundsDuration = 2 wk**.
  - Phase 3 $[103, \infty)$: Contains 10 mg $\implies$ **boundsDuration = None**.

### Scenario 11 (Expanded): OCR Line Breaks Mid-Clause
- **Text:** `"Prednisone 40 mg daily for 2\nweeks, then 20 mg\ndaily for 2 weeks."`
- **Analysis:**
  - `\n` between `"2"` and `"weeks"` is an OCR token wrap.
  - `\n` between `"20 mg"` and `"daily"` is an OCR line wrap.
  - Explicit transition boundary $B_0$ (`", then"`) cleanly divides Phase 1 from Phase 2.
  - Line wraps do not split instructions.
- **Partitions:**
  - Phase 1: 40 mg, `boundsDuration = 2 wk`.
  - Phase 2: 20 mg, `boundsDuration = 2 wk`.

### Scenario 12 (Expanded): Absent Transition Cues (Compressed Shorthand — Fail Closed)
- **Text:** `"Prednisone 40 mg daily 2 weeks, 20 mg daily 2 weeks."`
- **Analysis:**
  - Lacks explicit transition cue (`"then"`, `"followed by"`). Separated only by a bare comma.
  - Midpoint fallback is **forbidden**.
  - Under Case D, the system **fails closed**:
    - Instruction 1: 40 mg, `boundsDuration = None`.
    - Instruction 2: 20 mg, `boundsDuration = None`.
    - Narrative `Dosage.text` retains verbatim phrasing: `"Prednisone 40 mg daily 2 weeks"` and `"20 mg daily 2 weeks"`.
    - Audit log records `AMBIGUOUS_TRANSITION_FAIL_CLOSED`.

### Scenario 13 (Expanded): Different Medication Strengths / Tablet Formulations
- **Text:** `"Prednisone 20 mg tablets: take 40 mg (2 tabs) daily for 2 weeks, then 20 mg (1 tab) daily for 2 weeks."`
- **Analysis:**
  - The strength descriptor `"20 mg tablets"` is an attribute of the drug name anchor.
  - Phase doses are 40 mg (2 tabs) and 20 mg (1 tab).
  - Explicit transition boundary $B_0$ (`", then"`) cleanly partitions Phase 1 (40 mg, 2 weeks) and Phase 2 (20 mg, 2 weeks).
  - Formulation annotations do not trigger false dose conflicts.

---

## 5. Section D: Expanded Regression Test Suite Specifications

| Test Case Name | Target Behavior | Expected Resource Mapping | Expected `boundsDuration` | Expected Fail-Closed / Audit Behavior |
| :--- | :--- | :--- | :--- | :--- |
| `test_req44_clause_partitioned_leading_duration` | Leading duration syntax (`"For 2 weeks take 40 mg..."`) | Single `MedicationRequest` (3 instructions) | Inst 1: 2 wk; Inst 2: 2 wk; Inst 3: None | Zero duration theft; leading durations captured. |
| `test_req44_clause_partitioned_mixed_placement` | Mixed trailing and leading syntax | Single `MedicationRequest` (3 instructions) | Inst 1: 2 wk; Inst 2: 2 wk; Inst 3: None | Boundary $B_0$ prevents Phase 2 duration entering Phase 1. |
| `test_req44_internal_commas_and_administration_instructions` | Route and administration qualifiers with commas | Single `MedicationRequest` (3 instructions) | Inst 1: 2 wk; Inst 2: 2 wk; Inst 3: None | Commas and `"with breakfast"` do not split clauses. |
| `test_req44_ocr_line_breaks_mid_clause` | Mid-word/mid-clause OCR line wraps | Single `MedicationRequest` (2 instructions) | Inst 1: 2 wk; Inst 2: 2 wk | Line breaks normalized as intra-clause whitespace. |
| `test_req44_absent_transition_cues_fails_closed` | Compressed dosing without transition markers | Single `MedicationRequest` (2 instructions) | Inst 1: None; Inst 2: None | **Fails closed**: structured duration omitted; narrative intact. |
| `test_req44_orphan_extra_duration_suppressed` | Extra duration without dose | Single `MedicationRequest` (2 instructions) | Inst 1: 2 wk; Inst 2: 1 wk | Extra duration ignored; audit log `UNATTACHED_EXTRA_DURATION`. |
| `test_req44_unknown_temporal_status_fails_closed` | Dose with `temporal_status="unknown"` and active dose | Zero resources emitted (Group suppressed) | N/A | **Fails closed**: `AMBIGUOUS_TEMPORAL_STATUS_CONFLICT`. |
| `test_req44_historical_and_current_partitioning` | Past history of med + new prescription | 2 resources: `MedicationStatement` & `MedicationRequest` | MedStat: None; MedReq: 10 d | Historical med mapped to `status="unknown"` (not `"completed"`). |
| `test_req44_different_medication_strengths_intact` | Strength descriptor (`"20 mg tabs"`) alongside doses | Single `MedicationRequest` (2 instructions) | Inst 1: 2 wk; Inst 2: 2 wk | Formulation strength does not trigger false dose-split. |

---

## 6. Section E: Compliance, Security, & Governance Declarations

1. **Four-Eyes Security Export Blocker Inviolable**:
   The multi-hop Four-Eyes ancestry vulnerability in `src/clindoc/review/service.py` remains an **active, absolute clinical export blocker**. Gate 9 audit ledger verification is preserved with zero bypasses.
2. **Review and Export Code Freeze**:
   Neither [src/clindoc/review/service.py](file:///d:/daa/src/clindoc/review/service.py) nor [src/clindoc/services/fhir_export.py](file:///d:/daa/src/clindoc/services/fhir_export.py) were modified or will be modified under this design.
3. **Strictly Documentation-Only Status**:
   This deliverable is a revised architectural design review (v2.0). Zero production code, test code, or schemas were altered.
4. **Workspace & Git Invariants**:
   All pre-existing workspace modifications remain completely intact. Zero git operations performed.
5. **Awaiting Authorization**:
   Implementation will commence **only upon separate, explicit authorization** following review of this revised specification.
