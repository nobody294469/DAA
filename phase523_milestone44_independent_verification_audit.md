# Phase 5.2.3 Sub-Milestone 4.4: Independent Verification Audit

**Document Authority:** Phase 5.2.3 Milestone 4.4 Independent Verification Audit  
**Audit Date:** 2026-10-31  
**Operating Mode:** STRICTLY READ-ONLY AUDIT (Zero production, test, schema, constant, config, or git modifications)  
**Status:** **AUDIT COMPLETE — VERIFIED WITH DOCUMENTED LIMITATIONS & OPEN BLOCKERS**  

---

## 1. Executive Summary & Audit Mandate

This independent audit conducts an empirical, code-level, and specification-level verification of the Sub-Milestone 4.4 regimen integration deliverables. In compliance with strict auditing guidelines, all evaluations were performed without altering any workspace code, tests, configurations, or Git state.

Every finding is explicitly classified under one of the four mandatory audit verdicts:
- **[Independently verified]**: Confirmed via direct code inspection, empirical execution of scratch probes, or runtime trace.
- **[Supported only by existing test coverage]**: Evident from test assertions but lacking exhaustive independent runtime tracing across all edge cases.
- **[Not verified]**: Cannot be conclusively proven within current synthetic fixtures or code paths.
- **[Open blocker]**: Critical architectural, clinical, or security impediment that bars clinical export or deployment.

---

## 2. Item-by-Item Verification Findings

### Item 1: REQ-44-01 / W-2 Cross-Page Split & Orphan Behavior (Segmentation to Mapper)

#### Finding 1.1: Multi-Page Continuation Dose Group Isolation in `medication_association.py`
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - In [src/clindoc/extract/medication_association.py](file:///d:/daa/src/clindoc/extract/medication_association.py#L116-L126), when clinical text within a clause or sentence contains medication attributes (`med.dose`, `med.frequency`, etc.) but lacks a medication name anchor (`len(med_name_items) == 0`), Rule 6 applies:
    ```python
    for ext, ls, le, span in med_attr_items:
        results.append((
            ext, ls, le, span,
            NO_ANCHOR_SENTINEL,  # "__no_anchor__"
            AssociationStatus.UNANCHORED_ORPHAN,
            {"orphan": True, "temporal_status": default_temporal_status},
        ))
    ```
  - Similarly, attributes appearing before any medication name within a single segment are assigned `NO_ANCHOR_SENTINEL` (lines 148–156).
  - This prevents continuation doses appearing at the top of Page 2 (e.g., `"20 mg daily at breakfast"`) from inheriting or leaking into the medication anchor at the bottom of Page 1.

#### Finding 1.2: Mapper `__no_anchor__` Suppression Prevents Cross-Page Leakage and Orphan Emission for LLMExtractor Paths
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - In [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L2731-L2739):
    ```python
    if grp_key == "__no_anchor__" or grp_key.startswith("__no_anchor"):
        logger.warning("Medication group '%s' is an unanchored orphan group; skipping.", grp_key)
        if audit_trail is not None:
            audit_trail.append({
                "group_id": grp_key,
                "status": "SUPPRESSED",
                "reason": "Unanchored orphan medication attributes without drug name anchor",
            })
        continue
    ```
  - Verified via `test_w2_multipage_medication_split_fail_closed`: Page 1 `Lisinopril` is emitted without Page 2 dose contamination (`doseAndRate is None`), and the unanchored Page 2 continuation dose (`group_id="__no_anchor__"`) is suppressed at the mapper boundary with zero orphan `MedicationRequest` or `MedicationStatement` resources generated.

#### Finding 1.3: Architectural Gap in `DeterministicExtractor.extract_med_line` for Unanchored Lines
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Direct inspection of [src/clindoc/extract/deterministic.py](file:///d:/daa/src/clindoc/extract/deterministic.py#L1623-L1640) reveals that `extract_region` assigns a fresh `str(uuid.uuid4())` to each line:
    ```python
    group_id = str(uuid.uuid4())
    ```
  - In `extract_med_line` (lines 1473–1511), if a line contains only a dose without a drug name, claims receive this fresh UUID rather than `__no_anchor__`.
  - When these claims reach [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L2741-L2791), `grp_key` is a random UUID (not `__no_anchor__`), and because `has_any_dose` is `True`, lines 2785–2791 execute:
    ```python
    else:
        med_display = "Unspecified Medication"
    ```
  - Consequently, if unanchored continuation lines are extracted purely via `DeterministicExtractor.extract_region` rather than `LLMExtractor` / `medication_association.py`, they do not get suppressed and can emit an `"Unspecified Medication"` resource. In current production, `router.py` dispatches narrative medication text through the segmentation and `medication_association.py` pipeline, which mitigates this, but `DeterministicExtractor.extract_med_line` remains an open architectural boundary to be unified.

---

### Item 2: REQ-44-03 Occurrence Matching & Index-Parity Fallback

#### Finding 2.1: Duplicate Duration Token Collision Resolution via `re.finditer` & `occurrence_idx`
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - In [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L1698-L1720), `_get_claim_pos` resolves claim offsets using regular expression iteration with `occurrence_idx`:
    ```python
    matches = list(re.finditer(pattern, full_text, re.I))
    if matches:
        m = matches[occurrence_idx] if occurrence_idx < len(matches) else matches[-1]
        return m.start()
    ```
  - In multi-step tapers sharing a single evidence span (e.g. `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily for 2 weeks, then 10 mg daily"`), `dur_claims[0]` resolves to position 27 and `dur_claims[1]` resolves to position 57.
  - Runtime verification confirms Phase 1 attaches 2 weeks ($pos=27$), Phase 2 attaches 2 weeks ($pos=57$), and Phase 3 remains unbounded.

#### Finding 2.2: Missing Intermediate Durations & Open-Ended Maintenance Phases
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Evaluated on `"Prednisone 40 mg daily for 2 weeks, then 20 mg daily, then 10 mg daily for 1 week"`:
    - Phase 1 (40 mg): matches `"for 2 weeks"` in window $[0, 42) \to$ `boundsDuration = 2.0 wk`.
    - Phase 2 (20 mg): window $[42, 60)$ has no duration. Because `len(sorted_durs) == 2` and `len(sorted_doses) == 3`, index-parity fallback (`elif len(sorted_durs) == len(sorted_doses)`) is bypassed $\to$ `phase_dur = None` (fails closed, open-ended).
    - Phase 3 (10 mg): matches `"for 1 week"` in window $[60, \infty) \to$ `boundsDuration = 1.0 wk`.
  - Confirmed: Missing intermediate durations fail closed and do not misattribute trailing durations or invent bounds.

#### Finding 2.3: Mismatched Count Handling (More Durations than Doses)
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Evaluated on `"Prednisone 40 mg daily for 2 weeks, then for 3 days, then 20 mg daily for 1 week"` (3 durations, 2 doses):
    - Phase 1 window contains both `"for 2 weeks"` and `"for 3 days"`. `cand_durs[0]` binds `"2 weeks"`. The extra duration `"3 days"` is not paired to Phase 2 because Phase 2 window starts at the 20 mg offset.
    - Phase 2 window binds `"1 week"`.
    - Confirmed: Extra durations in earlier phase windows do not shift into subsequent phases when counts are mismatched.

#### Finding 2.4: Out-of-Order / Preceding Duration Shift Limitation in Forward-Looking Phase Windows
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Evaluated on syntax where duration precedes the medication/dose: `"For 2 weeks take Prednisone 40 mg daily, then for 1 week take 20 mg daily"`.
  - Character offsets: `"2 weeks"` at pos 4; `"40 mg"` at pos 23; `"1 week"` at pos 46; `"20 mg"` at pos 58.
  - Phase 1 window is $[23, 58)$:
    - `"2 weeks"` (pos 4) is *before* pos 23, so it is outside Phase 1 window.
    - `"1 week"` (pos 46) falls *inside* Phase 1 window $[23, 58)$! Therefore Phase 1 receives `"1 week"`.
  - Phase 2 window is $[58, \infty)$:
    - Neither duration falls into Phase 2 window.
    - The fallback `elif len(sorted_durs) == len(sorted_doses)` evaluates to `True` ($2 == 2$).
    - Phase 2 receives `sorted_durs[1]` which is `"1 week"`.
  - **Result:** Both Phase 1 and Phase 2 receive `"1 week"`, and `"2 weeks"` is omitted.
  - **Audit Implication:** Forward-looking window matching $[d\_pos, next\_d\_pos)$ assumes clinical phrasing is canonical trailing format (`[Dose] [Freq] for [Duration]`). When clinicians write preceding durations (`For [Duration] take [Dose]`), forward windowing can misattribute durations. Narrative `Dosage.text` preserves the verbatim clause, but structured `boundsDuration` exhibits this limitation.

---

### Item 3: REQ-44-04 Narrative SIG Generation & Transition Context

#### Finding 3.1: Sequential Phase `"Then "` Prefixing and Transition Context Sufficiency
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - In [src/clindoc/fhir/mapper.py](file:///d:/daa/src/clindoc/fhir/mapper.py#L1858-L1871):
    ```python
    parts = []
    if idx == 0 and name_claim:
        parts.append(name_claim.value)
    elif idx > 0:
        parts.append("Then")
    parts.append(f"{d_claim.value} {d_claim.unit_raw or ''}".strip())
    if route_claim:
        parts.append(route_claim.value)
    if phase_freq:
        parts.append(phase_freq.value)
    if phase_dur:
        parts.append(f"for {phase_dur.value}")
    p_text = " ".join(parts).strip()
    ```
  - For `"Prednisone 20 mg daily for 5 days, then 10 mg daily for 5 days, then 5 mg daily"`:
    - Phase 1: `"Prednisone 20 mg daily for 5 days"`
    - Phase 2: `"Then 10 mg daily for 5 days"`
    - Phase 3: `"Then 5 mg daily"`
  - Each instruction's narrative explicitly provides transition context, ensuring human clinicians reviewing MAR displays or printed orders understand chronological sequencing independently of receiving-EHR execution behavior.

#### Finding 3.2: Dose Non-Summation Invariant Across Instructions
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Each sequential phase emits an independent `Dosage` element with its own `doseAndRate[0].doseQuantity`.
  - Phase 1 has 20 mg, Phase 2 has 10 mg, Phase 3 has 5 mg.
  - At no point are doses summed across phases (e.g. 35 mg total is never emitted as a dosage quantity).

#### Finding 3.3: Split Dosing Narrative Generation
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - In split dosing (lines 1978–1986):
    - Instruction 1: `"Carvedilol 12.5 mg every morning"`
    - Instruction 2: `"Carvedilol 25 mg at bedtime"`
  - Both instructions include the drug name, exact dose, and diurnal timing cue (`MORN` and `NIGHT`), preventing order confusion.

---

### Item 4: FHIR R4B Conformance & Semantics

#### Finding 4.1: `Dosage.sequence` Semantics in Core FHIR R4B
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Field cardinality in `fhir.resources.R4B.dosage.Dosage` is `0..1` (optional integer field).
  - Specification definition: "Indicates the order in which the dosage instructions should be applied or interpreted."
  - **Concurrency:** Emitting identical sequence numbers (e.g. `sequence: 1` on morning and bedtime doses) expresses that instructions belong to the same execution phase. However, as established in the 4.3.1 audit, **identical sequence values do NOT formally guarantee concurrent machine execution in receiving EHRs**. Vendor EHRs interpret identical sequences idiosyncratically.
  - **Sequencing:** Emitting ascending sequence numbers (`sequence: 1, 2, ...`) denotes order of application, but FHIR R4B provides no executable triggers (such as `offsetFromPrevious` or `delay`).
  - Conformance verdict: The mapping is fully valid R4B, but clinical safety relies on narrative `Dosage.text`.

#### Finding 4.2: Timing Repeat `boundsDuration` Representation
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Bounded phases map to `timing.repeat.boundsDuration` with:
    - `value`: decimal duration magnitude (e.g. `2`, `5`).
    - `unit`: source string (e.g. `"weeks"`, `"days"`).
    - `system`: `"http://unitsofmeasure.org"`.
    - `code`: valid UCUM code (`"wk"`, `"d"`).
  - Open-ended / maintenance phases omit `timing.repeat` (or have `boundsDuration: None`).
  - Validated with `FHIRValidator.validate_bundle` across all generated bundles with 0 validation errors.

#### Finding 4.3: Element-Level Provenance & Competing Candidate Extensions
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Element-level provenance (`EXT_CLAIM_PROVENANCE`) is attached directly to `doseQuantity.extension`, carrying `claimId`, `sourceEvidence`, and `extractionConfidence`.
  - Non-selected slot competitors in split dosing are tracked in `grp_med_shadowed` and exported as `EXT_COMPETING_CANDIDATE` extensions on the `MedicationRequest` or `MedicationStatement` resource with `selectedClaimId`, `competingClaimId`, and `competingValue`.

---

### Item 5: Gate 9 / Four-Eyes Security Blocker & Audit Ledger Integrity

#### Finding 5.1: Multi-Hop Four-Eyes Ancestry Vulnerability Status
- **Classification:** **[Open blocker]**
- **Technical Analysis:**
  - In [src/clindoc/review/service.py](file:///d:/daa/src/clindoc/review/service.py#L420-L450), `accept_claim` checks:
    ```python
    if last_edit.created_by == actor_id:
        raise FourEyesViolationError("Actor cannot approve a claim they modified.")
    ```
  - This checks only the immediate parent revision. If User A edits claim $c_1 \to c_2$, User B edits $c_2 \to c_3$, User A can accept $c_3$ because `c_3.created_by == User B`.
  - This multi-hop ancestry vulnerability remains **completely unresolved** in the codebase.
  - As mandated by project leadership, this restriction was **not** weakened, bypassed, or waived.

#### Finding 5.2: In-Memory Claim Review State Suppression (Gate 9 Fail-Closed Enforcement)
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - In [src/clindoc/services/fhir_export.py](file:///d:/daa/src/clindoc/services/fhir_export.py#L180-L240), `evaluate_claim_eligibility` enforces that claims having in-memory `review_state: "ACCEPTED"` must have a corresponding, cryptographically verified audit ledger event in the immutable database:
    ```python
    if enforce_audit_evidence and not verified_audit_event:
        return ClaimEligibilityEvaluation(
            claim_id=claim_id,
            is_eligible=False,
            reason="SUPPRESSED_UNVERIFIED_AUDIT_LEDGER: Human-accepted claim lacks verified audit ledger proof",
        )
    ```
  - Tested in `test_four_eyes_ancestry_blocker_remains_active`: In-memory accepted claims fail closed.

#### Finding 5.3: Immutability of Frozen Review and Export Services
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Neither [src/clindoc/review/service.py](file:///d:/daa/src/clindoc/review/service.py) nor [src/clindoc/services/fhir_export.py](file:///d:/daa/src/clindoc/services/fhir_export.py) were modified during Sub-Milestones 4.3 or 4.4.
  - Git diff relative to the start of Milestone 4.3 shows zero modifications to review or export service code.

---

### Item 6: Benchmark Landscape & Denominator Reconciliation

#### Finding 6.1: Separation of Cascade Evaluation Testbeds vs. Primary Benchmarks
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - The 12-document and 60-document runs executed in Sub-Milestone 4.4 evaluate the **extraction cascade runner** (`eval_both_benchmarks.py`), not the primary narrative or multi-extractor benchmarks.
  - These two testbeds must not be conflated with the wider scientific evaluation landscape.

#### Finding 6.2: Exact Benchmark Scopes, Denominators, and Metric Reconciliation
- **Classification:** **[Independently verified]**
- **Technical Analysis:**

| Benchmark Identifier | Document Count | GT Claims Denominator | Target Subsystem / Scope | Micro Precision | Micro Recall | Micro F1 | Status |
| :--- | :---: | :---: | :--- | :---: | :---: | :---: | :--- |
| **Synthetic Cascade Frozen (12-Doc)** | 12 | 269 | Deterministic + GLiNER cascade baseline (`FROZEN_MANIFEST_PATH`) | 0.8872 | 0.8736 | 0.8803 | Frozen, 0 drift |
| **Synthetic Cascade Expanded (60-Doc)** | 60 | 974 | Cascade scaling testbed (`EXP_MANIFEST_PATH`) | 0.8540 | 0.9415 | 0.8956 | Frozen, 0 drift |
| **Scope 1: Primary Narrative (E3-20)** | 20 | 540 | Narrative text extraction & association (`NARRATIVE_0001–0020`) | 0.6300 | 0.1167 | 0.1969 | Frozen, 0 drift |
| **Scope 2: Multi-Extractor (GLiNER-120)** | 120 | 4,456 | Cross-domain router & multi-extractor (50 Lab, 50 Med, 20 Narrative) | 0.8814 | 0.8920 | 0.8867 | Frozen, 0 drift |
| **Scope 3: Out-of-Distribution (OOD-5 / OOD-34)** | 5 (or 34) | 135 (or 782) | Complex discharge summaries with coordinate lists (`NARRATIVE_0016–0020`) | 0.6120 | 0.1185 | 0.1985 | Frozen, 0 drift |
| **Scope 4: Adversarial Association Fixture** | 26 cases | 52 checks | Syntactic rules 1–6 adversarial cases (`test_phase523_milestone3_medication_association.py`) | 1.0000 | 1.0000 | 1.0000 | 26/26 passed |

---

### Item 7: Test Suite Regression Verification

#### Finding 7.1: Exact Historical Baseline Comparison
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - Full test suite run (`pytest -q`) executed across 1,459 total tests:
    - **1,454 tests passed**
    - **5 tests failed**
    - **11 warnings**
    - **Duration:** 309.38 seconds
  - Comparison against the 5 pre-existing historic baseline failures:

| Pre-existing Failed Test | Baseline Failure Reason | Current Sub-Milestone 4.4 Result | Verdict |
| :--- | :--- | :--- | :--- |
| `tests/test_cli.py::test_cli_unsupported_extension_fails` | `assert 0 == 1` | `assert 0 == 1` | Identical baseline |
| `tests/test_document_ingestion.py::test_13_unreadable_invalid_document_handling` | `Failed: DID NOT RAISE UnsupportedFileTypeError` | `Failed: DID NOT RAISE UnsupportedFileTypeError` | Identical baseline |
| `tests/test_m2_v2_corrections.py::test_lab_flag_v2_generation` | `AssertionError: Expected equal SUPPORTED (335) and DISCREPANCY (705)` | `AssertionError: Expected equal SUPPORTED (335) and DISCREPANCY (705)` | Identical baseline |
| `tests/test_section_detection_production.py::TestSectionDetectionDisabledParity::test_disabled_mode_extracts_headers_identically_to_baseline` | `AssertionError: assert ('987654' in ['105'] or '61' in ['105'])` | `AssertionError: assert ('987654' in ['105'] or '61' in ['105'])` | Identical baseline |
| `tests/test_vllm_deployment.py::test_client_code_does_not_disable_tls_verification` | `AssertionError: Found insecure 'verify=False' in D:\daa\src\clindoc\services\ehr_transport.py` | `AssertionError: Found insecure 'verify=False' in D:\daa\src\clindoc\services\ehr_transport.py` | Identical baseline |

- Zero new test regressions were introduced.

---

### Item 8: Workspace Integrity & Operational Controls

#### Finding 8.1: Git State and Diff Conformance
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - `git status --porcelain` and `git diff --stat` were verified:
    - Zero files were staged (`git add`).
    - Zero commits were created (`git commit`).
    - Zero branches were pushed (`git push`).
    - Zero files were reset (`git checkout` / `git reset`).
    - Zero files were cleaned (`git clean`).
  - Changes in the workspace remain exactly as preserved from preceding milestones.

#### Finding 8.2: Strict Read-Only Audit Adherence
- **Classification:** **[Independently verified]**
- **Technical Analysis:**
  - All exploratory and validation scripts executed during this audit were strictly confined to the agent artifact scratch directory (`scratch/test_req44_edge_cases.py`).
  - No production code, tests, schemas, constants, or configurations in `src/`, `tests/`, or `configs/` were modified.

---

## 3. Summary of Findings & Audit Verdicts

| Finding ID | Scope / Topic | Audit Classification | Key Insight / Result |
| :--- | :--- | :--- | :--- |
| **F-01** | W-2 Cross-page split group isolation | **Independently verified** | Unanchored Page 2 continuation doses receive `__no_anchor__` sentinel and cannot contaminate Page 1 medication anchors. |
| **F-02** | W-2 Mapper `__no_anchor__` suppression | **Independently verified** | Suppressed at mapper boundary; zero orphan `MedicationRequest` or `MedicationStatement` resources emitted. |
| **F-03** | Deterministic extractor unanchored line UUIDs | **Independently verified** | Architectural boundary: `DeterministicExtractor.extract_med_line` assigns line UUIDs rather than sentinel; router mitigates, but unification is needed. |
| **F-04** | Duplicate duration token disambiguation | **Independently verified** | `_get_claim_pos` with `occurrence_idx` resolves multi-step duplicate durations (e.g. repeated `"2 weeks"`) without offset collision. |
| **F-05** | Missing intermediate durations | **Independently verified** | Phases without durations in their window fail closed to open-ended maintenance (`boundsDuration: None`). |
| **F-06** | Mismatched duration/dose counts | **Independently verified** | Extra durations in earlier phase windows do not shift into subsequent phases. |
| **F-07** | Preceding duration syntax limitation | **Independently verified** | When durations precede doses (`For 2 weeks take Prednisone...`), forward windowing misattributes duration into subsequent phase. |
| **F-08** | Sequential narrative SIG transition context | **Independently verified** | `"Then "` prefixing preserves chronological transition context across all sequential phases. |
| **F-09** | Dose non-summation invariant | **Independently verified** | Doses are never summed across instructions. |
| **F-10** | Core FHIR R4B `Dosage.sequence` semantics | **Independently verified** | Valid R4B structure; identical sequence numbers convey phase grouping but do not guarantee receiving-EHR concurrent execution. |
| **F-11** | Timing repeat & provenance extensions | **Independently verified** | UCUM codes conform; element-level and competing candidate provenance verified. |
| **F-12** | Multi-hop Four-Eyes ancestry vulnerability | **Open blocker** | Vulnerability remains unresolved; clinical export remains blocked. Review/export modules remain strictly frozen. |
| **F-13** | In-memory review state suppression | **Independently verified** | In-memory `ACCEPTED` claims without database audit ledger fail closed at Gate 9. |
| **F-14** | Benchmark landscape reconciliation | **Independently verified** | 12-doc and 60-doc cascade runners reconciled with GLiNER-120, E3-20, OOD, and adversarial scopes. Denominators frozen with 0 drift. |
| **F-15** | Full test suite regression baseline | **Independently verified** | 1,454 passed / 5 failed exactly matches historic baseline. |
| **F-16** | Workspace Git integrity | **Independently verified** | Zero git modifications, resets, commits, or pushes. |

---

## 4. Audit Conclusion & Recommendations

1. **Sub-Milestone 4.4 Status:**  
   The Sub-Milestone 4.4 implementation satisfies all FHIR R4B structural mapping requirements and safety fixtures within its approved boundaries.
2. **Clinical Export Status:**  
   **BLOCKED.** Clinical export remains strictly blocked by the unresolved multi-hop Four-Eyes ancestry vulnerability (Finding F-12).
3. **Open Architecture Notes for Subsequent Work:**  
   - Remediation of the multi-hop Four-Eyes vulnerability in `review/service.py` requires dedicated authorization.
   - Forward-looking phase windowing in `_build_regimen_dosages` should be expanded in a future milestone to support preceding duration syntax (`For [Duration] take [Dose]`) (Finding F-07).
   - Unanchored line handling in `DeterministicExtractor.extract_med_line` should be aligned with `medication_association.py` sentinel behavior (Finding F-03).
