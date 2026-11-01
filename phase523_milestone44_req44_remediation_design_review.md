# Phase 5.2.3 Sub-Milestone 4.4: REQ-44 Remediation Design Review

**Document Authority:** Phase 5.2.3 Sub-Milestone 4.4 Documentation-Only Remediation Design Review  
**Review Date:** 2026-10-31  
**Operating Mode:** STRICTLY DOCUMENTATION-ONLY REVIEW (Zero production code, test, schema, constant, config, or Git modifications)  
**Status:** **DESIGN REVIEW COMPLETE — PENDING EXPLICIT AUTHORIZATION PRIOR TO IMPLEMENTATION**  

---

## 1. Executive Summary & Review Scope

Following the empirical findings of the Sub-Milestone 4.4 REQ-44 edge-case test investigation ([phase523_milestone44_req44_edge_case_test_report.md](file:///d:/daa/phase523_milestone44_req44_edge_case_test_report.md)), this design review develops a formal, documentation-only specification for remediating two mapper-boundary architectural limitations:

1. **REQ-44-03 Phase-Aware Duration Association**: Replaces the flawed previous-dose window proposal `[prev_d_pos, next_d_pos)` with a robust, syntax-grounded **Clause-Partitioned Window Model** that handles leading, trailing, and mixed durations while preventing cross-phase duration theft or duplicate consumption.
2. **Temporal-Status Boundary Partitioning**: Establishes a deterministic lifecycle and temporal-status segregation protocol at the FHIR mapper boundary to eliminate cross-temporal attribute contamination between active, planned, historical, and discontinued orders.

### Critical Invariants & Boundaries Preserved:
- **Zero Production or Test Code Changes**: No lines of production code or test suites were modified.
- **Zero Schema or Configuration Changes**: FHIR definitions, constants, and extraction configs remain frozen.
- **Review and Export Service Immutability**: [src/clindoc/review/service.py](file:///d:/daa/src/clindoc/review/service.py) and [src/clindoc/services/fhir_export.py](file:///d:/daa/src/clindoc/services/fhir_export.py) remain strictly frozen.
- **Four-Eyes Security Export Blocker Maintained**: The unresolved multi-hop Four-Eyes ancestry vulnerability remains an **absolute clinical export blocker**.
- **Zero Git Operations**: No staging, commits, pushes, resets, cleans, or discards.

---

## 2. Section A: REQ-44-03 — Phase-Aware Duration Association Design

### 2.1 Rejection of Naive `[prev_d_pos, next_d_pos)` Window Model

The naive window proposal defined candidate durations for Phase $k$ as falling within the interval $[\text{prev\_d\_pos}, \text{next\_d\_pos})$. 

#### The Failure Proof (Counterexample):
Consider the trailing-duration sequence:
$$\text{"Prednisone 40 mg daily for 2 weeks, then 20 mg daily."}$$
- Dose 1 ($D_0 = \text{"40 mg"}$) at index 11.
- Duration 1 ($\text{Dur}_0 = \text{"2 weeks"}$) at index 24.
- Dose 2 ($D_1 = \text{"20 mg"}$) at index 46.

Under the naive proposal:
- Phase 0 window $[0, 46)$ contains $\text{Dur}_0$ (pos 24). Phase 0 binds $\text{Dur}_0$.
- Phase 1 window $[\text{prev\_d\_pos}, \text{next\_d\_pos}) = [11, \infty)$ **also contains $\text{Dur}_0$ (pos 24)**!
- Because pos 24 is greater than 11, Phase 1 evaluates $\text{Dur}_0$ as a candidate in its window.
- If Phase 0 already consumed $\text{Dur}_0$, Phase 1 must either re-consume it (duplicate boundsDuration) or steal it.
- **Conclusion:** Expanding the lower bound of a dose window to the preceding dose position causes trailing durations belonging to Phase $k$ to collide with Phase $k+1$. The naive proposal is fatally flawed and is rejected.

---

### 2.2 The Clause-Partitioned Window Model (Formal Specification)

To associate durations accurately without assuming that positional proximity alone establishes clinical relationship, the mapper must model the **syntactic clause structure** of clinical tapering instructions.

#### Mathematical Formulation:
Let a multi-phase regimen text $T$ contain $N$ ordered dose claims $D_0, D_1, \dots, D_{N-1}$ located at character offsets $p(D_0) < p(D_1) < \dots < p(D_{N-1})$.

1. **Transition Marker Identification**:
   A transition marker set $\mathcal{M} = \{m_1, m_2, \dots, m_{N-1}\}$ is detected using `TAPER_TRANSITION_REGEX` and clause delimiters (`;`, `, then`, `, followed by`, `\n`).
   For every adjacent pair of doses $(D_k, D_{k+1})$, there exists a transition boundary $B_k \in (p(D_k), p(D_{k+1}))$ defined by:
   - The start of the transition token (e.g. index of `"then"`, `"followed by"`, `";"`) separating Phase $k$ from Phase $k+1$.
   - If no explicit transition token is found between $D_k$ and $D_{k+1}$, $B_k$ is the index of the first punctuation delimiter (`,`, `;`, `\n`) between them.
   - If neither a transition token nor punctuation exists (e.g. compressed shorthand `"40mg 2wk 20mg 1wk"`), $B_k$ falls back to the midpoint offset:
     $$B_k = \left\lfloor \frac{p(D_k) + p(D_{k+1})}{2} \right\rfloor$$

2. **Phase Clause Partitions**:
   The entire text span $[0, |T|)$ is strictly partitioned into $N$ non-overlapping half-open intervals $[L_k, R_k)$:
   $$\begin{aligned}
   [L_0, R_0) &= [0, B_0) \\
   [L_k, R_k) &= [B_{k-1}, B_k) \quad \text{for } 0 < k < N-1 \\
   [L_{N-1}, R_{N-1}) &= [B_{N-2}, |T|)
   \end{aligned}$$
   **Invariance Property:**
   $$\bigcup_{k=0}^{N-1} [L_k, R_k) = [0, |T|) \quad \text{and} \quad [L_j, R_j) \cap [L_k, R_k) = \emptyset \quad \forall j \neq k$$

3. **Duration Claim Assignment Protocol**:
   Let $\mathcal{U} = \{u_0, u_1, \dots, u_{M-1}\}$ be the set of duration claims resolved via `_get_claim_pos` with occurrence matching.
   For each phase $k \in \{0, \dots, N-1\}$:
   - Identify candidate durations strictly contained in Phase Clause $k$:
     $$\mathcal{C}_k = \{ u \in \mathcal{U} \mid L_k \le p(u) < R_k \}$$
   - **Resolution Rules**:
     1. **Rule A (Unambiguous Single Candidate)**: If $|\mathcal{C}_k| = 1$, bind $u \in \mathcal{C}_k$ to Phase $k$.
     2. **Rule B (Missing Duration / Maintenance)**: If $|\mathcal{C}_k| = 0$, Phase $k$ has `boundsDuration = None`. It fails closed to open-ended maintenance. No duration is borrowed from any other phase clause.
     3. **Rule C (Ambiguous Multi-Duration Conflict)**: If $|\mathcal{C}_k| > 1$ (e.g. `"40 mg daily for 2 weeks for 3 days"`):
        - If one duration has a higher verification/review status, select it deterministically and shadow the competitor via `EXT_COMPETING_CANDIDATE`.
        - If candidates have identical confidence and review state, Phase $k$ structured duration **fails closed** (`boundsDuration = None`), while narrative `Dosage.text` preserves verbatim wording.
     4. **Rule D (Single-Consumption Guarantee)**: Once duration claim $u$ is bound to Phase $k$, it is marked consumed and removed from $\mathcal{U}$. Because $[L_j, R_j) \cap [L_k, R_k) = \emptyset$, a duration can never enter the candidate set of any other phase.

---

### 2.3 Worked Examples: The Nine Investigation Scenarios

#### Scenario 1: Trailing Duration
- **Text:** `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks, then 10 mg daily."`
- **Offsets:**
  - $D_0$ (40 mg) = 11; $\text{Dur}_0$ (2 weeks) = 24.
  - Transition $B_0$ (`", then"`) = 32.
  - $D_1$ (20 mg) = 42; $\text{Dur}_1$ (2 weeks) = 55.
  - Transition $B_1$ (`", then"`) = 63.
  - $D_2$ (10 mg) = 73.
- **Partitions:**
  - Clause 0 $[0, 32)$: Contains $D_0$ (11) and $\text{Dur}_0$ (24) $\implies$ **Phase 1: 40 mg, 2 weeks**.
  - Clause 1 $[32, 63)$: Contains $D_1$ (42) and $\text{Dur}_1$ (55) $\implies$ **Phase 2: 20 mg, 2 weeks**.
  - Clause 2 $[63, \infty)$: Contains $D_2$ (73) and zero durations $\implies$ **Phase 3: 10 mg, unbounded (`None`)**.
- **Outcome:** Exact, unambiguous match. Zero shift.

#### Scenario 2: Leading Duration
- **Text:** `"For 2 weeks take Prednisone 40 mg daily, then for 2 weeks take 20 mg daily, then take 10 mg daily."`
- **Offsets:**
  - $\text{Dur}_0$ (2 weeks) = 4; $D_0$ (40 mg) = 30.
  - Transition $B_0$ (`", then"`) = 39.
  - $\text{Dur}_1$ (2 weeks) = 49; $D_1$ (20 mg) = 71.
  - Transition $B_1$ (`", then"`) = 80.
  - $D_2$ (10 mg) = 98.
- **Partitions:**
  - Clause 0 $[0, 39)$: Contains $\text{Dur}_0$ (4) and $D_0$ (30) $\implies$ **Phase 1: 40 mg, 2 weeks**.
  - Clause 1 $[39, 80)$: Contains $\text{Dur}_1$ (49) and $D_1$ (71) $\implies$ **Phase 2: 20 mg, 2 weeks**.
  - Clause 2 $[80, \infty)$: Contains $D_2$ (98) and zero durations $\implies$ **Phase 3: 10 mg, unbounded (`None`)**.
- **Outcome:** **DEFECT RESOLVED.** Leading duration $\text{Dur}_0$ is correctly bound to Phase 1 because both reside in $[0, 39)$. $\text{Dur}_1$ is correctly bound to Phase 2. Zero durations dropped.

#### Scenario 3: Mixed Duration Placement
- **Text:** `"Prednisone 40 mg daily for 2 weeks, then for 2 weeks take 20 mg daily, then 10 mg daily."`
- **Offsets:**
  - $D_0$ (40 mg) = 11; $\text{Dur}_0$ (2 weeks) = 24.
  - Transition $B_0$ (`", then"`) = 32.
  - $\text{Dur}_1$ (2 weeks) = 43; $D_1$ (20 mg) = 61.
  - Transition $B_1$ (`", then"`) = 70.
  - $D_2$ (10 mg) = 83.
- **Partitions:**
  - Clause 0 $[0, 32)$: Contains $D_0$ (11) and $\text{Dur}_0$ (24) $\implies$ **Phase 1: 40 mg, 2 weeks**.
  - Clause 1 $[32, 70)$: Contains $\text{Dur}_1$ (43) and $D_1$ (61) $\implies$ **Phase 2: 20 mg, 2 weeks**.
  - Clause 2 $[70, \infty)$: Contains $D_2$ (83) and zero durations $\implies$ **Phase 3: 10 mg, unbounded (`None`)**.
- **Outcome:** **DEFECT RESOLVED.** Phase boundary $B_0 = 32$ separates Phase 1 trailing duration from Phase 2 leading duration cleanly.

#### Scenario 4: Repeated Identical Durations
- **Text:** `"Prednisone 60 mg daily for 2 weeks, then 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks."`
- **Partitions:**
  - Clause 0 $[0, 32)$ binds occurrence 0 of `"2 weeks"` $\implies$ **Phase 1: 60 mg, 2 weeks**.
  - Clause 1 $[32, 63)$ binds occurrence 1 of `"2 weeks"` $\implies$ **Phase 2: 40 mg, 2 weeks**.
  - Clause 2 $[63, \infty)$ binds occurrence 2 of `"2 weeks"` $\implies$ **Phase 3: 20 mg, 2 weeks**.
- **Outcome:** Verified. All 3 phases retain independent occurrence tracking.

#### Scenario 5: Missing Intermediate Duration
- **Text:** `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily, then 10 mg daily for 1 week."`
- **Partitions:**
  - Clause 0 $[0, 32)$: Contains $\text{Dur}_0$ (2 weeks) $\implies$ **Phase 1: 40 mg, 2 weeks**.
  - Clause 1 $[32, 53)$: Contains zero durations $\implies$ **Phase 2: 20 mg, unbounded (`None`)**.
  - Clause 2 $[53, \infty)$: Contains $\text{Dur}_1$ (1 week) $\implies$ **Phase 3: 10 mg, 1 week**.
- **Outcome:** Verified. Phase 2 fails closed without inheriting from Phase 1 or Phase 3.

#### Scenario 6: Extra Duration Candidates
- **Text:** `"Prednisone 40 mg daily for 2 weeks, then for 3 days, then 20 mg daily for 1 week."`
- **Partitions:**
  - Between $D_0$ (40 mg) and $D_1$ (20 mg), there are two transition tokens: `", then for 3 days"` and `", then 20 mg"`.
  - The intermediate token `"for 3 days"` constitutes an orphan duration clause without a dose.
  - Clause 0 contains `"2 weeks"` $\implies$ **Phase 1: 40 mg, 2 weeks**.
  - Clause 1 contains `"1 week"` and 20 mg $\implies$ **Phase 2: 20 mg, 1 week**.
  - The orphan duration `"3 days"` does not map to any dose clause and is suppressed with an audit record (`UNATTACHED_EXTRA_DURATION`).
- **Outcome:** Verified. No silent assignment or fabricated dose.

#### Scenario 7: Out-of-Order Input List
- Shuffled input list `[D2, Dur1, D0, Dur0, D1]` resolves identically because claims are sorted by source coordinates prior to clause partitioning.

#### Scenario 8: Alternative Dosing ("or")
- `"Prednisone 40 mg daily for 2 weeks or 20 mg daily for 2 weeks."`
- Does not contain taper transition cues (`has_taper_cues == False`).
- Evaluated under slot competition: Phase 1 selects 40 mg, shadows 20 mg via `EXT_COMPETING_CANDIDATE`. Not a taper.

#### Scenario 9: Contradictory Instructions (Mixed Temporal Status)
- Evaluated under the Temporal-Status Partitioning protocol specified in Section 3.

---

### 2.4 Counterexamples Demonstrating Inability to Silently Shift Durations

#### Counterexample 1: Distant Trailing Duration in Maintenance Phase
- **Input:** `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily. Patient should continue follow-up for 6 months."`
- **Risk:** Could `"for 6 months"` be stolen by Phase 2 (20 mg daily)?
- **Protection in Proposed Model:**
  - Sentence boundary `.` after `"daily"` terminates the taper clause.
  - Upstream segmentation isolates `"Patient should continue follow-up for 6 months"` into a separate non-medication clause.
  - If unsegmented: The sentence boundary `.` sets $B_1$. Any duration after the period occurs in a non-dose clause.
  - Phase 2 clause terminates at `.` $\implies$ Phase 2 duration is `None`. `"for 6 months"` is suppressed.

#### Counterexample 2: Inverted Coordinate Duration Shorthand
- **Input:** `"Prednisone 40 mg, then 20 mg, daily for 2 weeks, for 1 week."`
- **Risk:** Doses appear consecutively, followed by clustered durations.
- **Protection in Proposed Model:**
  - Between $D_0$ (40 mg) and $D_1$ (20 mg), there is `", then "`.
  - Clause 0 is $[0, \text{pos}(\text{then}))$. It contains zero durations.
  - Clause 1 is $[\text{pos}(\text{then}), \infty)$. It contains Dose 2 (20 mg) and **two** durations (`"for 2 weeks"` and `"for 1 week"`).
  - Under Rule C (Ambiguous Multi-Duration Conflict), Clause 1 detects $|\mathcal{C}_1| = 2$.
  - Because two conflicting durations compete for a single dose in Clause 1, **it fails closed**: structured `boundsDuration` is set to `None`, and an audit warning `AMBIGUOUS_CLUSTERED_DURATION_CONFLICT` is logged.
  - Neither duration is arbitrarily assigned to Phase 1.

---

## 3. Section B: Temporal-Status Contamination Remediation Design

### 3.1 Problem Statement: Mapper Slot Fallback on Mixed Statuses

Currently, if claims with mixed temporal statuses (e.g. `discontinued` and `planned`) arrive within the same `group_id`:
1. [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L1810-L1816) correctly sets `has_mixed_inactive = True`, disabling tapers and split dosing.
2. However, lines 1761–1789 call `select_deterministic_slot_candidate` across all dose claims and duration claims in the group without temporal segregation.
3. If a discontinued dose (e.g. 50 mg) and a planned dose (e.g. 10 mg) have equal review confidence, tie-breaking may select the discontinued dose, while pairing it with the planned duration (e.g. 5 days).
4. **Clinical Hazard:** Emits an active prescription for a discontinued dose with a planned duration (`"Prednisone 50 mg daily for 5 days"`), causing severe medication safety risk.

---

### 3.2 Formal Temporal Status Partitioning Protocol

At the entry point of [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L2710) (`claims_to_fhir_bundle`), incoming medication claims within each raw group must undergo **Temporal Lifecycle Partitioning** before any regimen or slot evaluation.

```
                    Incoming Medication Claim Group
                                  │
          ┌───────────────────────┴───────────────────────┐
          ▼                                               ▼
  Pure Temporal Group                             Mixed Temporal Group
  (All claims share same                          (Contains conflicting
   lifecycle category)                             lifecycle categories)
          │                                               │
          │                                 ┌─────────────┴─────────────┐
          ▼                                 ▼                           ▼
Proceed to Regimen &               Safely Partitionable?            Contradictory /
Slot Mapping (Unchanged)           (Distinct Anchors & Spans)       Unresolvable?
                                            │                           │
                                            ▼                           ▼
                                   Split into Distinct           FAIL CLOSED
                                   Sub-Groups (Active vs         (Suppress group with
                                   Discontinued Resources)       Audit Log Record)
```

#### Lifecycle Categories:
Every temporal status string is mapped to one of three canonical lifecycle categories:
1. **ACTIVE_CATEGORY**: `current`, `planned`, `conditional` (and `unspecified` if document is an active prescription).
2. **INACTIVE_CATEGORY**: `discontinued`, `historical`, `stopped`, `completed`.
3. **UNKNOWN_CATEGORY**: `unknown`, `null`, `unspecified` (in progress notes or ambiguous contexts).

---

### 3.3 Partitioning vs. Fail-Closed Matrix

| Combination in Group | Clinical Context | Action | Mapper Behavior |
| :--- | :--- | :---: | :--- |
| **`current` + `planned`** | Sequential taper or dose change | **SAFE TO COMBINE** | Evaluated as sequential taper (`MedicationRequest` or `MedicationStatement`). Phases inherit temporal statuses. |
| **`current` + `discontinued` (Distinct Anchors & Clauses)** | Order cancellation followed by replacement | **SAFE TO PARTITION** | Split into two independent sub-groups: Sub-group A (discontinued) $\to$ `MedicationRequest(status="stopped")`; Sub-group B (active) $\to$ `MedicationRequest(status="active")`. Each resource retains exact provenance to its claims. |
| **`current` + `discontinued` (Conflicting Single Anchor)** | Same dose claim or ambiguous clause claiming both active and stopped | **FAIL CLOSED** | Group suppressed. Emits zero resources. Audit trail records `CONTRADICTORY_TEMPORAL_STATUS_CONFLICT`. |
| **`historical` + `current` (Distinct Clauses)** | Past medication reconciliation alongside current prescription | **SAFE TO PARTITION** | Historical claims $\to$ `MedicationStatement(status="completed")`; Current claims $\to$ `MedicationRequest(status="active")`. |
| **`discontinued` + `planned` (Shared Group / Shorthand)** | e.g. `"D/C 50 mg, start 10 mg"` bundled in one group | **SAFE TO PARTITION** | Partition by clause boundary. 50 mg emits stopped resource; 10 mg emits active/planned order. Duration binds strictly to planned order. |
| **`discontinued` + `unknown` / `unspecified`** | One dose discontinued, another dose without status | **FAIL CLOSED** | Cannot safely assume unknown dose is active. Suppressed with `AMBIGUOUS_TEMPORAL_STATUS_CONFLICT`. |
| **Split Dosing with Mixed Statuses** | Morning dose active, bedtime dose discontinued | **SAFE TO PARTITION** | Active morning dose maps to single `MedicationRequest(sequence=1)`. Bedtime dose maps to separate stopped resource or is suppressed if unanchored. |

---

## 4. Section C: Comprehensive Test Design

For each proposed remediation behavior, this section defines the required regression test specifications:

### Test Specification 1: `test_req44_clause_partitioned_leading_duration`
- **Clinical Input:**
  - Raw Text: `"For 2 weeks take Prednisone 40 mg daily, then for 2 weeks take 20 mg daily, then take 10 mg daily."`
  - Claims: 3 doses (40, 20, 10), 3 frequencies (daily), 2 durations (2 weeks, 2 weeks).
- **Expected Grouping:** Single medication group (sequential taper).
- **Expected Resource:** Single `MedicationRequest` with 3 `dosageInstruction` elements.
- **Expected `doseAndRate` & `boundsDuration`:**
  - Instruction 1: dose = 40.0 mg, `boundsDuration = 2.0 wk` (code `"wk"`, system UCUM).
  - Instruction 2: dose = 20.0 mg, `boundsDuration = 2.0 wk` (code `"wk"`, system UCUM).
  - Instruction 3: dose = 10.0 mg, `boundsDuration = None`.
- **Expected Narrative:**
  - Instruction 1: `"Prednisone 40 mg daily for 2 weeks"`
  - Instruction 2: `"Then 20 mg daily for 2 weeks"`
  - Instruction 3: `"Then 10 mg daily"`
- **Suppression:** None.

### Test Specification 2: `test_req44_clause_partitioned_mixed_placement`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily for 2 weeks, then for 2 weeks take 20 mg daily, then 10 mg daily."`
- **Expected Resource:** Single `MedicationRequest` with 3 `dosageInstruction` elements.
- **Expected Bounds:** Instruction 1 = 2 wk; Instruction 2 = 2 wk; Instruction 3 = None.
- **Expected Narrative:**
  - Instruction 1: `"Prednisone 40 mg daily for 2 weeks"`
  - Instruction 2: `"Then 20 mg daily for 2 weeks"`
  - Instruction 3: `"Then 10 mg daily"`

### Test Specification 3: `test_req44_ambiguous_clustered_duration_fails_closed`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily, then 20 mg daily for 2 weeks for 1 week."`
  - Two contradictory durations inside Clause 1.
- **Expected Resource:** Single `MedicationRequest` with 2 `dosageInstruction` elements.
- **Expected Bounds:**
  - Instruction 1: `boundsDuration = None`.
  - Instruction 2: `boundsDuration = None` (fails closed due to conflict).
- **Expected Audit Record:** `AMBIGUOUS_CLUSTERED_DURATION_CONFLICT` logged in audit trail.
- **Expected Narrative:** Preserves verbatim extracted tokens in text without fabricated structured bounds.

### Test Specification 4: `test_req44_temporal_partitioning_discontinued_and_planned`
- **Clinical Input:**
  - Raw Text: `"Prednisone 50 mg daily. Discontinue Prednisone 50 mg. Prescribe Prednisone 10 mg daily for 5 days."`
  - Claims bundled in single group: Dose 50 mg (`discontinued`), Dose 10 mg (`planned`), Duration 5 days (`planned`).
- **Expected Grouping:** Mapper partitions into Sub-Group A (discontinued) and Sub-Group B (planned).
- **Expected Resources:**
  - Resource 1: `MedicationRequest` with `id="medreq-...-1"`, `status="stopped"`, `medicationCodeableConcept.text="Prednisone"`, `dosageInstruction[0].doseAndRate[0].doseQuantity.value = 50.0`, `boundsDuration = None`.
  - Resource 2: `MedicationRequest` with `id="medreq-...-2"`, `status="active"`, `medicationCodeableConcept.text="Prednisone"`, `dosageInstruction[0].doseAndRate[0].doseQuantity.value = 10.0`, `boundsDuration = 5.0 d`.
- **Expected Narrative:**
  - Resource 1: `"Prednisone 50 mg daily"`
  - Resource 2: `"Prednisone 10 mg daily for 5 days"`
- **Zero Cross-Contamination:** 50 mg dose NEVER receives 5 days duration.

### Test Specification 5: `test_req44_contradictory_temporal_status_single_claim_fails_closed`
- **Clinical Input:**
  - A single dose claim with unresolvable contradictory temporal annotations within the same span.
- **Expected Resource:** Zero resources emitted (Group suppressed).
- **Expected Audit Record:** `CONTRADICTORY_TEMPORAL_STATUS_CONFLICT` in audit trail.

---

## 5. Section D: Compliance, Security, & Governance Declarations

1. **Four-Eyes Security Export Blocker Inviolable**:
   The multi-hop Four-Eyes ancestry vulnerability in `src/clindoc/review/service.py` remains an **active, absolute clinical export blocker**. No changes were made or planned that would weaken, bypass, or waive Gate 9 audit ledger verification.
2. **Strictly Documentation-Only Status**:
   This deliverable is an architectural review. No code implementation has occurred.
3. **Workspace & Git Integrity**:
   The workspace remains in its exact state. Zero git operations performed.
4. **Execution Protocol**:
   Implementation of Remediation Proposal A (Clause-Partitioned Window Model) and Proposal B (Temporal Status Partitioning) will commence **only upon explicit user authorization**.
