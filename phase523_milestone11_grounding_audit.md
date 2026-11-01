# Phase 5.2.3 — Milestone 1.1: Evidence Grounding Edge-Case Verification Report

**Author**: Antigravity Assistant  
**Date**: October 31, 2026  
**Status**: Completed — Milestone 1 Verified Safe; Zero Production Changes Made; Smallest Diagnostic Determinism Correction Identified for Approval  
**File Audited**: [`src/clindoc/extract/llm_extractor.py`](file:///d:/daa/src/clindoc/extract/llm_extractor.py)  

---

## Executive Summary

Following the provisional acceptance of Milestone 1 (Deterministic Extraction Ordering & Evidence Grounding), this audit provides a rigorous, empirical verification of the new evidence-grounding subsystem prior to commencing Milestone 2.

### Summary of Audit Findings
1. **One-to-One Occurrence Matching**: The implementation strictly requires `all_identical = True` and `len(exts) == len(occs)` before matching candidates to occurrences in document order. Because all candidates in the matching set have identical `(field_type, value, unit_raw)`, candidate assignments are symmetric and semantic correspondence cannot misalign identical claims. When counts mismatch or candidate values differ, the logic **fails closed** and rejects all candidates to `diagnostics["rejected"]`.
2. **Overlapping Spans & Anchor Ordering**: Anchor prioritization (`_field_priority = 0` for `med.name` and `lab.value`, `1` for dependent attributes) ensures that when spans share identical start coordinates or overlap, the anchor entity is sorted first and establishes the group before dependent attributes join it. Pre-nominal attributes (attributes occurring before any anchor) safely receive the `__no_anchor__` sentinel and fail closed, preventing incorrect attachment to preceding entities.
3. **Determinism Under Multi-Permutation Testing**:
   - Across all permutations (up to 120 permutations tested per scenario across explicit offsets, missing offsets, duplicate phrases, overlapping spans, and mixed med/lab extractions), **canonical claims, evidences, and normalized group assignments are 100% deterministic and invariant to LLM JSON response ordering**.
   - **Minor Diagnostic Observation**: `diagnostics["rejected"]` preserves the arrival order of rejected candidates. While downstream claims, evidences, and FHIR export are 100% identical, sorting `diagnostics["rejected"]` canonically provides 100% determinism for the rejection log as well.
4. **Outcome & Compliance**: In accordance with user instructions, **no production code changes have been made**. The smallest correction for diagnostic determinism is documented below and held for user approval before Milestone 2.

---

## 1. Investigation of One-to-One Occurrence Matching

### 1.1 Architectural Mechanism
In `_build_claims()`, candidate extractions lacking explicit character offsets are grouped by `evidence_text` in Step 3:
```python
by_ev_text: dict[str, list[dict]] = {}
for ext in needs_search:
    ev_text = str(ext.get("evidence_text", ""))
    by_ev_text.setdefault(ev_text, []).append(ext)

for ev_text, exts in by_ev_text.items():
    occs = _find_occurrences(ev_text)
    if len(occs) == 0:
        # Rejected: not in region_text
        ...
    elif len(occs) == 1:
        # Single occurrence: all candidates bound to this unique span
        ...
    else:
        # Multiple occurrences in region_text
        all_identical = (
            len({(e.get("field_type"), str(e.get("value")), str(e.get("unit_raw") or "")) for e in exts}) == 1
        )
        if all_identical and len(exts) == len(occs):
            for ext, (ls, le, span_text) in zip(exts, occs):
                grounded.append((ext, ls, le, span_text))
        else:
            for ext in exts:
                diagnostics["rejected"].append({
                    "field_type": ext.get("field_type"),
                    "value": ext.get("value"),
                    "evidence_text": ev_text,
                    "reason": f"ambiguous evidence span: evidence_text appears multiple times ({len(occs)}) in region_text without unique source offset",
                })
```

### 1.2 Empirical Test Cases

| Test Case | Scenario / Input | Grounded Claims | Rejection Diagnostic | Analysis & Semantic Safety |
| :--- | :--- | :--- | :--- | :--- |
| **1.1 Two identical med names in different sections** | `"Section 1: Metoprolol 25 mg. Section 2: Metoprolol 50 mg."`<br>Candidates: Metoprolol, 25 mg, Metoprolol, 50 mg | 4 claims:<br>- `Metoprolol` @ `[11:21]` (`G_0`)<br>- `25 mg` @ `[22:27]` (`G_0`)<br>- `Metoprolol` @ `[40:50]` (`G_1`)<br>- `50 mg` @ `[51:56]` (`G_1`) | 0 rejected | **SAFE**. Both occurrences of `Metoprolol` grounded in document order. Coordinate sort attaches `25 mg` to section 1 and `50 mg` to section 2. |
| **1.2 Two identical dose strings for different drugs** | `"Metoprolol 25 mg. Lisinopril 25 mg."`<br>Candidates: Metoprolol, 25 mg, Lisinopril, 25 mg | 4 claims:<br>- `Metoprolol` @ `[0:10]` (`G_0`)<br>- `25 mg` @ `[11:16]` (`G_0`)<br>- `Lisinopril` @ `[18:28]` (`G_1`)<br>- `25 mg` @ `[29:34]` (`G_1`) | 0 rejected | **SAFE**. `25 mg` occurrences are assigned to `[11:16]` and `[29:34]`. Spatial sorting guarantees each dose joins its preceding drug. |
| **1.3 Multiple identical phrases with different field types** | `"Test 1: Normal. Flag 2: Normal."`<br>Candidates: `lab.value="Normal"`, `lab.flag="Normal"` | 0 claims | 2 rejected:<br>`ambiguous evidence span: appears multiple times (2) without unique offset` | **SAFE (FAIL-CLOSED)**. Because `field_type` differs, `all_identical` evaluates to `False`. The logic refuses to guess which "Normal" is value vs flag. |
| **1.4 Repeated med names with different dosage instructions** | `"Morning: Metoprolol once daily. Evening: Metoprolol twice daily."`<br>Candidates: Metoprolol, once daily, Metoprolol, twice daily | 4 claims:<br>- `Metoprolol` @ `[9:19]` (`G_0`)<br>- `once daily` @ `[20:30]` (`G_0`)<br>- `Metoprolol` @ `[41:51]` (`G_1`)<br>- `twice daily` @ `[52:63]` (`G_1`) | 0 rejected | **SAFE**. Frequencies partition cleanly to their respective medication occurrences. |
| **1.5 Equal counts, inverted candidate emission order** | `"Section A: Metoprolol 25 mg. Section B: Metoprolol 50 mg."`<br>LLM emitted: `Metoprolol 50 mg` *before* `Metoprolol 25 mg` | 4 claims:<br>- `Metoprolol` @ `[11:21]` (`G_0`)<br>- `25 mg` @ `[22:27]` (`G_0`)<br>- `Metoprolol` @ `[40:50]` (`G_1`)<br>- `50 mg` @ `[51:56]` (`G_1`) | 0 rejected | **SAFE (ORDER-INVARIANT)**. Regardless of candidate emission order, coordinate sorting restores document sequence: `25 mg` always binds to Section A, and `50 mg` to Section B. |
| **1.6a Candidate count > Occurrence count** | Text has 2 mentions of Metoprolol; LLM emits 3 candidates | 0 claims | 3 rejected (`len(exts)=3 != len(occs)=2`) | **SAFE (FAIL-CLOSED)**. Rejects all candidates when counts mismatch. |
| **1.6b Candidate count < Occurrence count** | Text has 2 mentions of Metoprolol; LLM emits 1 candidate | 0 claims | 1 rejected (`len(exts)=1 != len(occs)=2`) | **SAFE (FAIL-CLOSED)**. Rejects candidate when counts mismatch. |

### 1.3 Key Finding: Semantic Alignment vs Matching Counts
**Question**: *Can the implementation assign an extraction to the wrong occurrence despite matching counts?*

**Mathematical Analysis**:
- Let $E = \{e_1, e_2, \dots, e_K\}$ be candidate extractions for text $T$.
- Let $O = \{o_1, o_2, \dots, o_M\}$ be the sorted occurrences of $T$ in the text.
- Matching is executed **only if** $K = M$ and $\forall i, j: \text{Key}(e_i) = \text{Key}(e_j)$, where $\text{Key}(e) = (\text{field\_type}, \text{value}, \text{unit\_raw})$.
- Since all candidates $e_i$ have identical semantic payloads, any permutation $\pi(E)$ assigned to $O$ produces the exact same set of grounded pairs $\{(o_k, \text{Key}(e))\}$.
- **Conclusion**: The implementation **cannot** assign an extraction to the wrong occurrence when candidates are identical.
- **Boundary Condition (Semantic Homonyms)**: If the narrative mentions a drug in two distinct semantic contexts (e.g., active medication vs allergy mention: `"Patient taking Metoprolol. Allergy to Metoprolol."`), and the LLM errantly extracts both without distinguishing metadata, both will be grounded to their respective positions. Full semantic disambiguation of negation/allergy contexts is handled by upstream prompting and downstream Mode 0 review.

---

## 2. Investigation of Overlapping Spans & Anchor Ordering

### 2.1 Architectural Mechanism
In Step 4, grounded spans are sorted using a multi-key tuple:
```python
def _field_priority(ft: str) -> int:
    if ft == "med.name":
        return 0
    if ft in ("med.dose", "med.route", "med.frequency", "med.duration"):
        return 1
    if ft == "lab.value":
        return 0
    if ft == "lab.flag":
        return 1
    return 2

grounded.sort(key=lambda item: (
    item[1],  # local_start (ascending)
    item[2],  # local_end (ascending)
    _field_priority(item[0].get("field_type", "")),
    str(item[0].get("field_type", "")),
    str(item[0].get("value", "")),
    str(item[0].get("unit_raw", "") or ""),
    -float(item[0].get("confidence", 0.8)),
))
```

### 2.2 Empirical Test Cases

| Test Case | Scenario / Coordinates | Execution Output | Behavior & Safety Evaluation |
| :--- | :--- | :--- | :--- |
| **2.1 `med.name` span overlapping `med.dose` span** | Text: `"Metoprolol 25 mg"`<br>`med.name`: `[0:13]` (`"Metoprolol 25"`)<br>`med.dose`: `[11:16]` (`"25 mg"`) | 2 claims:<br>- `med.name`: `[0:13]` (`G_0`)<br>- `med.dose`: `[11:16]` (`G_0`) | **SAFE**. `med.name` starts at 0 (priority 0) and creates group `G_0`. `med.dose` starts at 11 (priority 1) and correctly joins `G_0`. |
| **2.2 Name and dose sharing identical start offsets** | Text: `"Metoprolol 25 mg"`<br>`med.name`: `[0:10]` (`"Metoprolol"`)<br>`med.dose`: `[0:13]` (`"Metoprolol 25"`) | 2 claims:<br>- `med.name`: `[0:10]` (`G_0`)<br>- `med.dose`: `[0:13]` (`G_0`) | **SAFE**. At offset 0, `med.name` has priority 0 while `med.dose` has priority 1. `med.name` is processed first, establishing `G_0`. |
| **2.3 Attribute beginning before medication name** | Text: `"Oral Metoprolol 25 mg daily"`<br>`med.route`: `[0:4]` (`"Oral"`)<br>`med.name`: `[5:15]` (`"Metoprolol"`) | 4 claims:<br>- `med.route`: `[0:4]` (`__no_anchor__`)<br>- `med.name`: `[5:15]` (`G_0`)<br>- `med.dose`: `[16:21]` (`G_0`)<br>- `med.frequency`: `[22:27]` (`G_0`) | **SAFE (FAIL-CLOSED SENTINEL)**. Because `Oral` begins before `med.name`, it is quarantined to `group_id = "__no_anchor__"`. It does **not** attach to any previous unrelated entity. Milestone 2 (clause segmentation) and Milestone 3 (syntactic pairing) will associate pre-nominal attributes. |
| **2.4 Multi-entity combination claims** | Text: `"Metoprolol Lisinopril combination"`<br>`med.name`: `[0:10]`<br>`med.name`: `[11:21]`<br>`med.dose`: `[22:33]` | 3 claims:<br>- `Metoprolol`: `[0:10]` (`G_0`)<br>- `Lisinopril`: `[11:21]` (`G_1`)<br>- `combination`: `[22:33]` (`G_1`) | **BASELINE PRESERVED**. Each medication receives a distinct group ID. Trailing shared attributes attach to the nearest preceding medication until coordinate/shared rules are added in Milestone 3. |
| **2.5 Identical start and end offsets across different fields** | Text: `"Glucose 95 mg/dL"`<br>`lab.value`: `[8:10]` (`"95"`)<br>`lab.flag`: `[8:10]` (`"95"`) | 2 claims:<br>- `lab.value`: `[8:10]` (`G_0`)<br>- `lab.flag`: `[8:10]` (`G_0`) | **SAFE**. `lab.value` (priority 0) establishes the lab group, and `lab.flag` (priority 1) joins the same group. Both claims preserved. |

---

## 3. Rigorous Permutation Testing

To verify order invariance beyond simple pairwise swaps, an automated permutation suite was executed across 5 test scenarios (`scratch/audit_section3_permutations.py`):

```
==========================================
Permutation Test: Scenario A: Explicit Offsets
Candidates count: 5 | Testing 120 permutations...
PASS: 100% deterministic across all 120 permutations.

==========================================
Permutation Test: Scenario B: Missing Offsets Duplicate Phrases
Candidates count: 4 | Testing 24 permutations...
PASS: 100% deterministic across all 24 permutations.

==========================================
Permutation Test: Scenario C: Overlapping Spans
Candidates count: 3 | Testing 6 permutations...
PASS: 100% deterministic across all 6 permutations.

==========================================
Permutation Test: Scenario D: Mixed Med and Lab Claims
Candidates count: 5 | Testing 120 permutations...
Canonical claims & evidences: 100% deterministic across all 120 permutations.
Observation: Order in diagnostics["rejected"] reflects candidate arrival order.

==========================================
Permutation Test: Scenario E: Mixed Claims with Ambiguous Rejections
Candidates count: 3 | Testing 6 permutations...
Canonical claims & evidences: 100% deterministic across all 6 permutations.
Observation: Order in diagnostics["rejected"] reflects candidate arrival order.
```

### Determinism Matrix Across Permutations

| Component | Invariance to Input Order | Verification Evidence |
| :--- | :---: | :--- |
| **Canonical Claims** (`field_type`, `value`, `unit_raw`) | **100% Invariant** | Identical across all tested permutations in Scenarios A–E. |
| **Evidences** (`char_start`, `char_end`, `text`) | **100% Invariant** | Identical across all tested permutations in Scenarios A–E. |
| **Group Relationships** (intra-medication clustering) | **100% Invariant** | Identical equivalence classes across all tested permutations. |
| **Rejected Multiset** (rejected candidates content) | **100% Invariant** | Exact same set of rejected candidates across all permutations. |
| **Rejection List Order** (`diagnostics["rejected"]`) | Order-dependent | Preserves candidate iteration order when appending to rejection list. |

---

## 4. Minor Observation & Proposed Minimal Correction

### 4.1 Description of Diagnostic Ordering Behavior
In `_build_claims()`, rejected items are appended to `diagnostics["rejected"]` as candidates are evaluated:
- Invalid field types are appended in Step 1 iteration order.
- Invalid explicit offsets are appended in Step 2 iteration order.
- Ambiguous multi-occurrence candidates are appended in Step 3 iteration order.

While this list ordering has **zero effect** on claims, evidences, group IDs, or downstream FHIR resources, sorting `diagnostics["rejected"]` before returning guarantees 100% determinism down to the diagnostic logging level.

### 4.2 Smallest Correction (For Approval)
To achieve complete bitwise determinism in diagnostics, add a canonical sort key before returning:

```python
# Optional 4-line diagnostic stabilization (llm_extractor.py, line ~900):
diagnostics["rejected"].sort(key=lambda r: (
    str(r.get("field_type", "")),
    str(r.get("reason", "")),
    str(r.get("value", "")),
    str(r.get("evidence_text", "")),
))
```

*Empirical Verification*: When this sort was applied in `scratch/audit_section3_permutations.py`, Scenarios D and E achieved 100% pass rates across all 120 and 6 permutations respectively.

---

## 5. Safety Assessment & Milestone 2 Readiness

1. **Safety Contract Maintained**:
   - Evidence grounding validates spans strictly against source tokens and boundaries.
   - Ambiguous or count-mismatched extractions fail closed.
   - Pre-nominal attributes receive the `__no_anchor__` sentinel and cannot attach to unintended previous medications.
   - Distinct medication mentions in different sections receive distinct UUID group IDs.
2. **Workspace & Constraints**:
   - Workspace changes preserved.
   - Ground truth and benchmark labels untouched.
   - Frozen Phase 4 files untouched.
   - No staging, commits, or pushes.
   - **Zero production modifications made during Milestone 1.1 audit.**

**Recommendation**: Proceed to Milestone 2 (Syntactic Clause & Sentence Boundary Segmentation) upon user approval.
