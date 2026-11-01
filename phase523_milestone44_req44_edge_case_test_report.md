# Phase 5.2.3 Sub-Milestone 4.4: REQ-44 Edge-Case Test Investigation Report

**Document Authority:** Phase 5.2.3 Sub-Milestone 4.4 REQ-44 Test Investigation  
**Investigation Date:** 2026-10-31  
**Investigation Mode:** STRICTLY TEST-ONLY INVESTIGATION  
**Scope:** REQ-44-03 Duration Association & Regimen Edge Cases (9 Scenarios)  
**Status:** **INVESTIGATION COMPLETE — 6 PASS, 3 LIMITATIONS / DEFECTS DOCUMENTED WITH REMEDIATION PROPOSALS**  

---

## 1. Executive Summary & Boundaries

This investigation provides a rigorous, code-level test investigation of duration association (REQ-44-03), transition context (REQ-44-04), and multi-phase regimen mapping under edge-case conditions.

### Investigation Boundaries Strictly Preserved:
1. **Zero Modifications to Core Code**: Neither `_get_claim_pos`, `_build_regimen_dosages`, `claims_to_fhir_bundle`, nor any other production code was modified during this investigation.
2. **Zero Modifications to Existing Tests**: All existing test files and baselines remain untouched.
3. **Four-Eyes Security Export Blocker Maintained**: The multi-hop Four-Eyes ancestry vulnerability remains an **active, unresolved clinical export blocker**. No remediation of the security blocker was begun.
4. **Git State Preserved**: Zero files were staged, committed, pushed, reset, cleaned, or discarded.
5. **No Production Remediation Implemented**: Identified defects are documented with proposed minimal remediations and held pending explicit authorization.

---

## 2. Assertion & Verification Methodology

Each test probe evaluated the generated FHIR R4B `MedicationRequest` or `MedicationStatement` across six mandatory criteria:
1. **Dose Independence**: Each `dosageInstruction` has its own `doseAndRate[0].doseQuantity` matching its phase dose.
2. **Duration Independence**: Bounded phases receive exact `timing.repeat.boundsDuration` with UCUM units; unbounded phases have `repeat: None`.
3. **Narrative Transition Context**: `Dosage.text` reflects complete clinical instructions and sequential continuity (`"Then "`).
4. **Zero Dose Summation / Transfer**: Doses are never summed across phases or transferred between distinct orders.
5. **Zero Duration Fabrication**: Missing or ambiguous associations fail closed without fabricating structured durations.
6. **FHIR Schema Validity**: All generated bundles pass `FHIRValidator.validate_bundle` with zero schema errors.

---

## 3. Scenario-by-Scenario Investigation Results

### Scenario 1: Trailing Duration
- **Test Identifier:** `test_req44_trailing_duration`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks, then 10 mg daily."`
  - Claims: 3 doses (40 mg, 20 mg, 10 mg), 3 frequencies (`"daily"`), 2 durations (`"2 weeks"`, `"2 weeks"`).
- **Expected Outcome:**
  - Phase 1: 40 mg, daily, boundsDuration = 2 wk.
  - Phase 2: 20 mg, daily, boundsDuration = 2 wk.
  - Phase 3: 10 mg, daily, boundsDuration = None (open-ended maintenance).
  - Narrative: Seq 1 `"Prednisone 40 mg daily for 2 weeks"`; Seq 2 `"Then 20 mg daily for 2 weeks"`; Seq 3 `"Then 10 mg daily"`.
- **Actual Outcome:**
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk`, Text `'Prednisone 40 mg daily for 2 weeks'`
  - Seq 2: Dose `20.0 mg`, Dur `2.0 wk`, Text `'Then 20 mg daily for 2 weeks'`
  - Seq 3: Dose `10.0 mg`, Dur `None`, Text `'Then 10 mg daily'`
- **Verdict:** **PASS (Fully Verified)**
- **Assertions:**
  - Dose and rate checked independently: Verified (40, 20, 10).
  - Duration bounds checked independently: Verified (2 wk, 2 wk, None).
  - Dose summation: Zero summation detected.
  - Fabricated duration: None.

---

### Scenario 2: Leading Duration
- **Test Identifier:** `test_req44_leading_duration`
- **Clinical Input:**
  - Raw Text: `"For 2 weeks take Prednisone 40 mg daily, then for 2 weeks take 20 mg daily, then take 10 mg daily."`
  - Claims: 3 doses (40 mg, 20 mg, 10 mg), 3 frequencies (`"daily"`), 2 durations (`"2 weeks"`, `"2 weeks"`).
  - Character Offsets: Dur 1 at pos 4; Dose 1 (40 mg) at pos 30; Dur 2 at pos 46; Dose 2 (20 mg) at pos 68; Dose 3 (10 mg) at pos 96.
- **Expected Outcome:**
  - Phase 1: 40 mg, daily, boundsDuration = 2 wk.
  - Phase 2: 20 mg, daily, boundsDuration = 2 wk.
  - Phase 3: 10 mg, daily, boundsDuration = None.
- **Actual Outcome:**
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk` (bound to Dur 2 at pos 46), Text `'Prednisone 40 mg daily for 2 weeks'`
  - Seq 2: Dose `20.0 mg`, Dur `None`, Text `'Then 20 mg daily'`
  - Seq 3: Dose `10.0 mg`, Dur `None`, Text `'Then 10 mg daily'`
- **Verdict:** **DEFECT EXPOSED (Preceding Duration Misattribution)**
- **Root Cause Analysis:**
  - In [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L1848-L1850), phase windowing is strictly forward-looking from dose offset to next dose offset:
    ```python
    d_pos <= dur_pos < next_d_pos
    ```
  - For Phase 1, `d_pos = 30` and `next_d_pos = 68`.
  - Dur 1 (pos 4) is *before* pos 30, so it is excluded from Phase 1.
  - Dur 2 (pos 46) falls *inside* $[30, 68)$, so Phase 1 binds Dur 2!
  - For Phase 2, window $[68, 96)$ contains no duration. Because `len(durs) == 2` and `len(doses) == 3`, index parity fallback is bypassed, leaving Phase 2 as `None`.
  - Dur 1 is dropped completely. Structured duration for Phase 2 is lost.
  - Narrative `Dosage.text` in Phase 1 shows 2 weeks, while Phase 2 omits duration.

---

### Scenario 3: Mixed Duration Placement
- **Test Identifier:** `test_req44_mixed_duration_placement`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily for 2 weeks, then for 2 weeks take 20 mg daily, then 10 mg daily."`
  - Structure: Phase 1 has trailing duration (`"40 mg daily for 2 weeks"`), Phase 2 has leading duration (`"then for 2 weeks take 20 mg daily"`).
  - Offsets: Dose 1 at pos 11; Dur 1 at pos 24; Dur 2 at pos 43; Dose 2 at pos 61; Dose 3 at pos 84.
- **Expected Outcome:**
  - Phase 1: 40 mg, boundsDuration = 2 wk.
  - Phase 2: 20 mg, boundsDuration = 2 wk.
  - Phase 3: 10 mg, boundsDuration = None.
- **Actual Outcome:**
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk` (Dur 1), Text `'Prednisone 40 mg daily for 2 weeks'`
  - Seq 2: Dose `20.0 mg`, Dur `None`, Text `'Then 20 mg daily'`
  - Seq 3: Dose `10.0 mg`, Dur `None`, Text `'Then 10 mg daily'`
- **Verdict:** **DEFECT EXPOSED (Window Collision on Preceding Duration)**
- **Root Cause Analysis:**
  - Phase 1 window is $[11, 61)$.
  - Both Dur 1 (pos 24) and Dur 2 (pos 43) fall into Phase 1 window $[11, 61)$.
  - `cand_durs[0]` binds Dur 1 to Phase 1.
  - Phase 2 window $[61, 84)$ is empty because Dur 2 occurred at pos 43.
  - Phase 2 receives `boundsDuration = None`. Dur 2 is dropped.

---

### Scenario 4: Repeated Identical Durations (3 Phases)
- **Test Identifier:** `test_req44_repeated_identical_durations_3phases`
- **Clinical Input:**
  - Raw Text: `"Prednisone 60 mg daily for 2 weeks, then 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks."`
  - Claims: 3 doses (60 mg, 40 mg, 20 mg), 3 durations (`"2 weeks"`, `"2 weeks"`, `"2 weeks"`).
- **Expected Outcome:**
  - Phase 1: 60 mg, boundsDuration = 2 wk.
  - Phase 2: 40 mg, boundsDuration = 2 wk.
  - Phase 3: 20 mg, boundsDuration = 2 wk.
- **Actual Outcome:**
  - Seq 1: Dose `60.0 mg`, Dur `2.0 wk`, Text `'Prednisone 60 mg daily for 2 weeks'`
  - Seq 2: Dose `40.0 mg`, Dur `2.0 wk`, Text `'Then 40 mg daily for 2 weeks'`
  - Seq 3: Dose `20.0 mg`, Dur `2.0 wk`, Text `'Then 20 mg daily for 2 weeks'`
- **Verdict:** **PASS (Fully Verified)**
- **Assertions:**
  - `_get_claim_pos` with `occurrence_idx` successfully disambiguated 3 identical duration strings across 3 distinct phase windows ($pos=24, 56, 88$).

---

### Scenario 5: Missing Intermediate Duration
- **Test Identifier:** `test_req44_missing_intermediate_duration`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily, then 10 mg daily for 1 week."`
  - Claims: 3 doses (40 mg, 20 mg, 10 mg), 2 durations (`"2 weeks"`, `"1 week"`).
- **Expected Outcome:**
  - Phase 1: 40 mg, boundsDuration = 2 wk.
  - Phase 2: 20 mg, boundsDuration = None (open-ended / ungrounded).
  - Phase 3: 10 mg, boundsDuration = 1 wk.
- **Actual Outcome:**
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk`, Text `'Prednisone 40 mg daily for 2 weeks'`
  - Seq 2: Dose `20.0 mg`, Dur `None`, Text `'Then 20 mg daily'`
  - Seq 3: Dose `10.0 mg`, Dur `1.0 wk`, Text `'Then 10 mg daily for 1 week'`
- **Verdict:** **PASS (Fully Verified)**
- **Assertions:**
  - Phase 2 fails closed without inheriting Phase 1's duration or borrowing Phase 3's duration.
  - Phase 3 accurately binds `"1 week"`.

---

### Scenario 6: Extra Duration (More Durations than Phases)
- **Test Identifier:** `test_req44_extra_duration_candidates`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily for 2 weeks, then for 3 days, then 20 mg daily for 1 week."`
  - Claims: 2 doses (40 mg, 20 mg), 3 durations (`"2 weeks"`, `"3 days"`, `"1 week"`).
- **Expected Outcome:**
  - Phase 1: 40 mg, boundsDuration = 2 wk.
  - Phase 2: 20 mg, boundsDuration = 1 wk.
  - Extra duration `"3 days"` must not be fabricated or assigned as an ungrounded phase.
- **Actual Outcome:**
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk`, Text `'Prednisone 40 mg daily for 2 weeks'`
  - Seq 2: Dose `20.0 mg`, Dur `1.0 wk`, Text `'Then 20 mg daily for 1 week'`
- **Verdict:** **PASS (Fully Verified)**
- **Assertions:**
  - Ambiguous candidate `"3 days"` was ignored. No extra `Dosage` was fabricated.

---

### Scenario 7: Out-of-Order Evidence Positions
- **Test Identifier:** `test_req44_shuffled_claim_input_order`
- **Clinical Input:**
  - Same text and claims as Scenario 1, but passed in randomly shuffled list order `[d3, dur2, d1, f2, dur1, d2, f1, f3]`.
- **Expected Outcome:**
  - Identical sequence, doses, durations, and narrative text as Scenario 1.
- **Actual Outcome:**
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk`
  - Seq 2: Dose `20.0 mg`, Dur `2.0 wk`
  - Seq 3: Dose `10.0 mg`, Dur `None`
  - `Order identical to Probe 1? True`
- **Verdict:** **PASS (Fully Verified)**
- **Assertions:**
  - Sorting by source text character offsets renders regimen generation invariant to input list ordering.

---

### Scenario 8: Ambiguous Phrasing ("or" Alternative Dosing)
- **Test Identifier:** `test_req44_alternative_or_phrasing`
- **Clinical Input:**
  - Raw Text: `"Prednisone 40 mg daily for 2 weeks or 20 mg daily for 2 weeks."`
  - Phrasing contains `" or "`, indicating alternative prescribing, NOT a sequential taper.
- **Expected Outcome:**
  - Must NOT be mapped as a sequential taper (`sequence: 1, 2`).
  - Should fail closed or fall back to slot competition with shadowed alternative candidate.
- **Actual Outcome:**
  - `Dosage instructions count: 1`
  - Seq 1: Dose `40.0 mg`, Dur `2.0 wk`, Text `'Prednisone 40 mg daily for 2 weeks'`
  - `TAPER_TRANSITION_REGEX` did not match `" or "`.
  - Fallback: Slot competition selected winning candidate (40 mg) and exported non-selected candidate (20 mg) via `EXT_COMPETING_CANDIDATE`:
    ```
    slot: med.dose
    selectedClaimId: c-d1
    competingClaimId: c-d2
    competingValue: 20
    ```
- **Verdict:** **PASS (Fully Verified)**
- **Assertions:**
  - Alternative dosing was not misinterpreted as a chronological taper.

---

### Scenario 9: Contradictory Instructions (Mixed Temporal Status)
- **Test Identifier:** `test_req44_contradictory_mixed_temporal_status`
- **Clinical Input:**
  - Raw Text: `"Prednisone 50 mg daily. Discontinue Prednisone 50 mg. Prescribe Prednisone 10 mg daily for 5 days."`
  - Claims in single group: `c-d1` (50 mg, `temporal_status="discontinued"`), `c-d2` (10 mg, `temporal_status="planned"`), `c-dur2` (5 days, `temporal_status="planned"`).
- **Expected Outcome:**
  - Contradictory orders should fail closed, or separate into distinct discontinued vs planned resources.
  - Discontinued dose (50 mg) should NOT receive the planned duration (5 days).
- **Actual Outcome:**
  - Single active `MedicationRequest` emitted.
  - Seq 1: Dose `50.0 mg`, Dur `5.0 d`, Text `'Prednisone 50 mg daily for 5 days'`.
- **Verdict:** **DEFECT EXPOSED (Mapper-Level Slot Fallback on Mixed Status)**
- **Root Cause Analysis:**
  - When claims with mixed temporal status share a single `group_id`:
    - Mapper correctly recognizes `has_mixed_inactive = True` and disables taper and split branches.
    - However, in slot fallback (lines 1760–1789), `select_deterministic_slot_candidate` does not prioritize active/planned over discontinued claims.
    - Tie-breaking selected `c-d1` (50 mg), while `c-dur2` ("5 days") was the only duration in the group, pairing the planned duration with the discontinued dose.
  - **Mitigation in Production Pipeline:** Upstream `segment_clinical_text` splits this text into 3 sentences:
    - Seg 0: `'Prednisone 50 mg daily.'`
    - Seg 1: `' Discontinue Prednisone 50 mg.'`
    - Seg 2: `' Prescribe Prednisone 10 mg daily for 5 days.'`
    Upstream assigns each to a distinct `group_id`, preventing this collision in normal pipeline execution. However, at the mapper boundary, if an unsegmented or co-grouped set arrives, slot fallback lacks temporal status filtering.

---

## 4. Summary of Discovered Defects & Limitations

| Scenario | Input Pattern | Behavior Observed | Clinical Impact | Severity |
|---|---|---|---|---|
| **Scenario 2** | Leading duration (`"For 2 weeks take 40 mg..."`) | Phase 1 duration precedes Dose 1; falls out of forward window $[d\_pos, next\_d\_pos)$. Dropped or shifted into subsequent phase. | Structured `boundsDuration` is lost on later phases; narrative `Dosage.text` retains verbatim phrasing. | Medium (Syntactic variation) |
| **Scenario 3** | Mixed duration placement (`"40 mg ... then for 2 weeks take 20 mg..."`) | Preceding duration for Phase 2 falls into Phase 1 window. Phase 1 takes first duration; Phase 2 gets `None`. | Phase 2 loses structured duration. | Medium (Syntactic variation) |
| **Scenario 9** | Mixed temporal status in single group | Mapper slot fallback pairs discontinued dose with planned duration if bundled in one group. | Cross-temporal attribute contamination if upstream segmentation fails. | High (Mapper fail-closed boundary) |

---

## 5. Proposed Minimal Remediations (PENDING AUTHORIZATION)

> [!IMPORTANT]
> In accordance with user instructions, **no production code changes were implemented**. The following minimal remediations are documented for review and future authorization.

### Remediation Proposal 1: Bi-directional Clause Windowing in `_build_regimen_dosages`
- **Target:** [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L1844-L1855)
- **Problem:** `d_pos <= dur_pos < next_d_pos` ignores durations appearing before `d_pos`.
- **Proposed Solution:**
  Define phase window boundaries based on transition cues (`"then"`, `","`, `";"`) or midpoint offsets rather than strictly from `d_pos`. Specifically:
  ```python
  # Start window at previous dose offset (or 0 for phase 0)
  prev_d_pos = dose_positions[idx - 1] if idx > 0 else 0
  next_d_pos = dose_positions[idx + 1] if idx + 1 < len(dose_positions) else len(full_grp_text) + 9999
  
  # Candidate durations for phase idx fall between prev_d_pos and next_d_pos
  cand_durs = [dur for dur_i, dur in enumerate(sorted_durs) if prev_d_pos <= _get_claim_pos(...) < next_d_pos]
  ```
- **Safety Guarantee:** Preserves preceding durations (`"For 2 weeks take 40 mg..."`) in Phase 1 window $[0, next\_d\_pos)$ while keeping trailing durations in Phase 2 window.

### Remediation Proposal 2: Temporal Status Filtering in `claims_to_fhir_bundle`
- **Target:** [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L2710-L2740)
- **Problem:** If a group contains mixed temporal statuses (e.g. `discontinued` and `planned`), slot competition can select a discontinued dose and planned duration.
- **Proposed Solution:**
  Partition groups by `temporal_status` at the start of medication grouping:
  ```python
  # If a group has mixed active/planned and discontinued/historical claims, split into sub-groups
  if has_mixed_inactive:
      # Process active/planned sub-group and discontinued sub-group independently
  ```
- **Safety Guarantee:** Prevents cross-temporal attribute contamination if unsegmented claims reach the mapper.

---

## 6. Audit Invariants & Security Blocker Notice

1. **Four-Eyes Security Export Blocker Maintained:**
   The multi-hop Four-Eyes ancestry vulnerability in `src/clindoc/review/service.py` remains **explicitly unresolved** and serves as an absolute clinical export blocker.
2. **Read-Only Invariant:**
   Zero modifications were made to production code, tests, schemas, constants, or configs.
3. **Workspace Invariant:**
   Zero git operations were performed. All workspace files remain in their preserved state.
