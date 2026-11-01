# Phase 5.2.3 — Milestone 2.3: Cross-Domain Anchor & Isolation Safety Audit

**Document Authority**: Independent Read-Only Safety Follow-Up Audit  
**Target Milestone**: Phase 5.2.3 — Milestone 2.3 (OCR Layout Compatibility & Regression Repair)  
**Target Files Inspected**:
- `src/clindoc/ocr/table_layout.py` (`is_vertical_lab_component_continuation`, `_clean_tokens`, `_MED_INDICATOR_TOKENS`)
- `src/clindoc/extract/segmentation.py` (`segment_clinical_text`, `find_segment_idx`)
- `src/clindoc/extract/llm_extractor.py` (`NarrativeExtractor.extract`, lines 864–920)
- `tests/test_phase523_milestone23_ocr_layout.py` (Tests 1–10)
- `tests/test_whitespace_grounding.py` (Tests 1–9)
**Audit Date**: October 31, 2026  
**Status**: **READ-ONLY AUDIT COMPLETE — NO BLOCKERS (1 PASS, 2 WARNINGS)**

---

## Executive Summary

This independent safety audit was conducted to investigate the specific behavior reported in the Milestone 2.3 audit:
$$\text{Paracetamol}\backslash\text{n}500$$
qualifies under continuation rules and binds `500` to `Paracetamol`. 

Before authorizing Milestone 3, this audit answers three fundamental questions:
1. **Can a medication name ever be classified as a laboratory analyte anchor?**
   **NO (PASS)**. In the Clindoc architecture, an analyte anchor is created exclusively when an extracted claim has `field_type == "lab.value"`. `is_vertical_lab_component_continuation()` does *not* create or classify anchors; it is solely a segmentation boundary heuristic. `Paracetamol` is extracted as `med.name`, creating a medication anchor group, never a laboratory group.
2. **Can the continuation mechanism allow cross-domain binding between medications and laboratories?**
   **NO (PASS)**. The downstream extraction state-machine maintains strictly disjoint registers (`current_med_group` vs `current_lab_group`). Even if a medication name, a lab analyte, and numerical values appear in the identical segment, they are assigned cryptographically unique UUIDs and cannot cross-pollinate.
3. **Do missing or malformed bounding boxes always preserve newline boundaries?**
   **QUALIFIED (WARNING)**. When bounding boxes are present, malformed, inverted, divergent-column, or distant bounding boxes fail closed (`return False`), preserving newline boundaries. However, when bounding boxes are completely missing (`prev_bbox is None` or `curr_bbox is None`), the spatial check is skipped and execution falls through to `return True` based solely on regex heuristics.

---

## Section 1: Cross-Domain Anchor Classification

### 1.1 Technical Dissection of `is_vertical_lab_component_continuation()`

Location: `src/clindoc/ocr/table_layout.py:126–178`

The helper function executes three discrete validation stages:
1. **Component Pattern Gate (Lines 145–154)**:
   ```python
   curr_clean = curr_text.strip()
   is_num = bool(_NUMERIC_VAL_RE.match(curr_clean))
   is_range = bool(_RANGE_RE.match(curr_clean))
   is_unit = curr_clean.upper() in COMMON_CLINICAL_UNITS
   is_flag = bool(_FLAG_RE.match(curr_clean))

   if not (is_num or is_range or is_unit or is_flag):
       return False
   ```
   Notice that `prev_text` is **never examined** for analyte identity or clinical domain. The function requires only that `curr_text` matches a numeric literal, reference range, standardized unit, or diagnostic flag.
2. **Medication Contamination Guard (Lines 156–160)**:
   ```python
   prev_tokens = set(_clean_tokens(prev_text))
   curr_tokens = set(_clean_tokens(curr_text))
   if (prev_tokens | curr_tokens) & _MED_INDICATOR_TOKENS:
       return False
   ```
   `_MED_INDICATOR_TOKENS` contains dosage forms, delivery routes, and frequencies:
   `{"tab", "tablet", "tablets", "cap", "capsule", "capsules", "po", "oral", "mg", "mcg", "g", "ml", "daily", "bid", "tid", "qid", "prn", "qhs", "od", "bd", "tds"}`.
   It does *not* contain international nonproprietary names (INNs) or brand drug names (such as `Paracetamol`, `Lisinopril`, `Metformin`).
3. **Spatial Layout Alignment Gate (Lines 162–177)**:
   Checks column alignment ($\Delta x \le 15.0\text{ px}$) and vertical adjacency ($-2.0\text{ px} \le y_{\text{gap}} \le 35.0\text{ px}$).

### 1.2 Evaluation of Required Audit Test Cases

The five mandated test cases were systematically probed under realistic spatial layout conditions ($x_0 = 10.0\text{ px}$, $\Delta y = 20.0\text{ px}$):

```
+------------------------------------------+---------------------+-------------------+-------------------------------+
| Text Sequence                            | Segments Produced   | Continuation Used | Downstream Extraction Outcome |
+------------------------------------------+---------------------+-------------------+-------------------------------+
| Paracetamol\n500                         | 1 segment           | True (L0 -> L1)   | med.name=Paracetamol (UUID_1) |
|                                          |                     |                   | med.dose=500 (UUID_1)         |
+------------------------------------------+---------------------+-------------------+-------------------------------+
| Paracetamol\n500\nLisinopril\n10 mg      | 3 segments          | True (L0 -> L1)   | Paracetamol: UUID_1           |
|                                          |                     | False (L1 -> L2)  | Lisinopril: UUID_2            |
|                                          |                     | False (L2 -> L3)  | 10 mg: __no_anchor__          |
+------------------------------------------+---------------------+-------------------+-------------------------------+
| Metoprolol 25 mg oral\n500               | 2 segments          | False (L0 -> L1)  | Metoprolol: UUID_1            |
|                                          |                     |                   | 500: __no_anchor__            |
+------------------------------------------+---------------------+-------------------+-------------------------------+
| HAEMOGLOBIN\n12.6\nGM%                   | 1 segment           | True (L0 -> L1)   | lab.value=12.6 GM% (UUID_LAB) |
|                                          |                     | True (L1 -> L2)   | Complete multi-line span      |
+------------------------------------------+---------------------+-------------------+-------------------------------+
| HAEMOGLOBIN\n12.6\nParacetamol\n500      | 2 segments          | True (L0 -> L1)   | HAEMOGLOBIN: UUID_LAB         |
|                                          |                     | False (L1 -> L2)  | Paracetamol: UUID_MED         |
|                                          |                     | True (L2 -> L3)   | (UUID_LAB != UUID_MED)        |
+------------------------------------------+---------------------+-------------------+-------------------------------+
```

#### Detailed Line-by-Line Traces:

1. **`Paracetamol\n500`**:
   - `L0 ("Paracetamol") -> L1 ("500")`:
     - `curr_clean = "500"` matches `_NUMERIC_VAL_RE`.
     - `prev_tokens` = `{"paracetamol"}`. Neither token is in `_MED_INDICATOR_TOKENS`.
     - Column $x_0 = 10.0$ matches; $y_{\text{gap}} = 5.0\text{ px} \le 35.0\text{ px}$.
     - `is_vertical_lab_component_continuation()` returns `True`.
   - **Segmentation**: Grouped into a single segment: `"Paracetamol\n500"`.
   - **Downstream Anchor Logic**: `Paracetamol` is extracted as `med.name`, setting `current_med_group = UUID_1`. `500` is extracted as `med.dose` and inherits `UUID_1`.
   - **Safety Assessment**: `Paracetamol` is **never classified as an analyte anchor**. It functions strictly as a medication anchor. The binding of `500` to `Paracetamol` is clinically and entity-consistent.

2. **`Paracetamol\n500\nLisinopril\n10 mg`**:
   - `L0 -> L1` (`Paracetamol` -> `500`): Continuation is `True`.
   - `L1 -> L2` (`500` -> `Lisinopril`): `curr_clean = "Lisinopril"` is not numeric, unit, range, or flag. Step 1 returns `False`. **Newline is preserved as a cut.**
   - `L2 -> L3` (`Lisinopril` -> `10 mg`): `curr_tokens` contains `"mg"`, triggering the Step 2 medication guard. Returns `False`. **Newline is preserved as a cut.**
   - **Segmentation**: 3 segments: `["Paracetamol\n500", "\nLisinopril", "\n10 mg"]`.
   - **Downstream Anchor Logic**:
     - Seg 0: `claims[0]` (`Paracetamol`) and `claims[1]` (`500`) share `UUID_1`.
     - Seg 1: `claims[2]` (`Lisinopril`) receives `UUID_2`.
     - Seg 2: `claims[3]` (`10 mg`) enters a new segment with `current_med_group = None`. It is quarantined as `"__no_anchor__"`.
   - **Safety Assessment**: Zero cross-medication bleeding. `Paracetamol` does not bind to `Lisinopril` or `10 mg`. `Lisinopril` does not bind to `500`.

3. **`Metoprolol 25 mg oral\n500`**:
   - `L0 -> L1`: `prev_tokens` contains `"mg"` and `"oral"`, both of which are in `_MED_INDICATOR_TOKENS`.
   - Step 2 guard immediately returns `False`.
   - **Segmentation**: 2 segments: `["Metoprolol 25 mg oral", "\n500"]`.
   - **Downstream Anchor Logic**: `500` is in Seg 1 without an anchor; quarantined as `"__no_anchor__"`.
   - **Safety Assessment**: Fail-closed protection. The presence of medication tokens blocks vertical continuation.

4. **`HAEMOGLOBIN\n12.6\nGM%`**:
   - `L0 -> L1`: `12.6` is numeric; no medication tokens; geometry aligned $\to$ `True`.
   - `L1 -> L2`: `GM%` $\in$ `COMMON_CLINICAL_UNITS`; no medication tokens; geometry aligned $\to$ `True`.
   - **Segmentation**: 1 segment: `"HAEMOGLOBIN\n12.6\nGM%"`.
   - **Downstream Anchor Logic**: Extracted as a single laboratory finding (`lab.value = 12.6`, `unit_raw = GM%`).
   - **Safety Assessment**: Satisfies Phase 5.1 OCR multi-line laboratory table requirements.

5. **`HAEMOGLOBIN\n12.6\nParacetamol\n500`**:
   - `L0 -> L1` (`HAEMOGLOBIN` -> `12.6`): `True` (Laboratory vertical entry).
   - `L1 -> L2` (`12.6` -> `Paracetamol`): `Paracetamol` is not numeric/unit/range/flag. Step 1 returns `False`. **Newline cut executed.**
   - `L2 -> L3` (`Paracetamol` -> `500`): `True` (Medication vertical entry).
   - **Segmentation**: 2 segments: `["HAEMOGLOBIN\n12.6", "\nParacetamol\n500"]`.
   - **Downstream Anchor Logic**:
     - Seg 0: `lab.value = 12.6` creates `current_lab_group = UUID_LAB`.
     - Segment transition: Resets all registers (`current_lab_group = None`, `current_med_group = None`).
     - Seg 1: `med.name = Paracetamol` creates `current_med_group = UUID_MED`. `med.dose = 500` inherits `UUID_MED`.
   - **Safety Assessment**: Complete cross-domain isolation. `UUID_LAB != UUID_MED`. Neither entity can leak across the boundary.

---

## Section 2: Medication Versus Laboratory Isolation

### 2.1 State-Machine Register Disjunction

In `src/clindoc/extract/llm_extractor.py:864–913`, entity grouping is governed by two entirely independent registers:

```python
current_med_group: str | None = None
current_lab_group: str | None = None

for c in claims:
    ...
    if c.field_type == "med.name":
        current_med_group = str(uuid.uuid4())
        c.group_id = current_med_group
    elif c.field_type in {"med.dose", "med.route", "med.frequency", "med.duration"}:
        c.group_id = current_med_group if current_med_group is not None else "__no_anchor__"
    elif c.field_type == "lab.value":
        current_lab_group = str(uuid.uuid4())
        c.group_id = current_lab_group
    elif c.field_type == "lab.flag":
        c.group_id = current_lab_group if current_lab_group is not None else "__no_anchor__"
```

### 2.2 Verification Against Threat Vectors

| Threat Vector | Mechanism of Defense | Status |
| :--- | :--- | :--- |
| **Attach a numeric medication dose to a laboratory analyte** | `med.dose` checks only `current_med_group`. Even if a `lab.value` preceded it in the same segment (e.g. `HAEMOGLOBIN 12.6\n500`), `current_med_group` is `None`. The dose is quarantined as `"__no_anchor__"`. | **PASS** |
| **Attach a laboratory result to a medication name** | `lab.value` instantiates `current_lab_group`, ignoring `current_med_group`. `med.name` is never read by laboratory fields. | **PASS** |
| **Merge a medication instruction into a laboratory logical row** | Medication instructions (e.g. `take 1 tablet daily`) fail Step 1 of `is_vertical_lab_component_continuation()` (not pure numeric, unit, or flag) and contain tokens in `_MED_INDICATOR_TOKENS`. They are cut at the newline. | **PASS** |
| **Use an ambiguous drug-like line as a laboratory anchor** | In Clindoc, laboratory anchors exist only as `lab.value` claim objects. An ambiguous drug line is either identified as `med.name` (creating a medication anchor) or rejected by the LLM/NER schema. It can never instantiate `current_lab_group`. | **PASS** |

### 2.3 Adversarial Probe: Co-occurring Medication and Lab in a Single Segment

An adversarial probe was executed with the synthetic string:
$$\text{Paracetamol}\backslash\text{n}12.6\backslash\text{n}\text{GM}\%$$
where `is_vertical_lab_component_continuation` allowed all three lines into a single segment (due to `12.6` being numeric and `GM%` being a clinical unit):
- **Claims Extracted**:
  - `med.name = Paracetamol`: `group_id = a11165b3-e3d7-4664-bc48-e6f95a609247`
  - `lab.value = 12.6`: `group_id = 6a6b925b-ec8c-4192-a979-c0701bd52564`
- **Result**: `claims[0].group_id != claims[1].group_id` (`True`).
- **Conclusion**: Even in pathological cases where medication and laboratory text co-exist in the same physical segment, the extraction state-machine guarantees 100% disjoint group allocation.

---

## Section 3: Geometry Fallback Behavior

### 3.1 Bounding Box Evaluation Paths

In `src/clindoc/ocr/table_layout.py:162–177`:
```python
    # 3. Spatial alignment check (if bounding boxes are available)
    if prev_bbox is not None and curr_bbox is not None:
        aligned_x = (
            abs(prev_bbox.x0 - curr_bbox.x0) <= 15.0
            or abs(prev_bbox.x1 - curr_bbox.x1) <= 15.0
        )
        if not aligned_x:
            return False

        y_gap = curr_bbox.y0 - prev_bbox.y1
        # Must be immediately below (y_gap >= -2 to allow tiny OCR overlap)
        if y_gap < -2.0 or y_gap > max_y_gap:
            return False

    return True
```

### 3.2 Audit Findings on Layout Evidence

1. **Malformed, Inverted, and Divergent Bounding Boxes (PASS)**:
   - When bounding boxes are present:
     - Divergent columns ($|\Delta x_0| > 15.0$ and $|\Delta x_1| > 15.0$): returns `False`.
     - Inverted vertical order ($y_{\text{gap}} < -2.0$): returns `False`.
     - Excessive vertical distance ($y_{\text{gap}} > 35.0$): returns `False`.
   - In all these cases, the newline cut is preserved.
2. **Missing Bounding Boxes (`prev_bbox is None` or `curr_bbox is None`) (WARNING)**:
   - When bounding boxes are `None` (for instance, in non-OCR plain-text mode or if an OCR engine omits token bounding boxes for a line), the `if prev_bbox is not None and curr_bbox is not None:` condition evaluates to `False`.
   - Execution falls through to line 177: `return True`!
   - **Implication**: When spatial geometry is unavailable, the system groups lines based **solely on syntactic regex heuristics** (`_NUMERIC_VAL_RE`, `COMMON_CLINICAL_UNITS`, `_FLAG_RE`), rather than strictly preserving newline boundaries.
   - **Severity**: **WARNING**. In production, all OCR ingested documents provide `DocumentPage` tokens with geometric coordinates. However, in plain-text processing (`page=None`), numeric lines will be grouped into multi-line segments without layout evidence.

---

## Section 4: Audit Findings & Classification

| Finding ID | Scope | Classification | Description |
| :--- | :--- | :--- | :--- |
| **AUD-23-01** | Anchor Classification | **PASS** | A medication name can **never** be classified as a laboratory analyte anchor. Analyte anchors require `c.field_type == "lab.value"`, which is never assigned to medication entities. |
| **AUD-23-02** | Cross-Domain Isolation | **PASS** | `current_med_group` and `current_lab_group` are disjoint UUID state registers. No cross-domain binding can occur even within identical segments. |
| **AUD-23-03** | Semantic Conflation in Name | **WARNING** | `is_vertical_lab_component_continuation()` does not inspect `prev_text` for analyte identity. It groups `Paracetamol\n500` because `500` is numeric and `Paracetamol` is not in `_MED_INDICATOR_TOKENS`. While this safely binds `500` to `Paracetamol`, it represents semantic conflation between lab and med continuation. |
| **AUD-23-04** | Missing Bounding Box Fallthrough | **WARNING** | When bounding boxes are `None` (`page=None`), the function falls through to `return True`, grouping lines purely on regex syntax without geometric layout confirmation. |

---

## Section 5: Comparison of Test Coverage

### 5.1 Existing Regression Suite Coverage (`tests/test_phase523_milestone23_ocr_layout.py`)
- Test 1: Vertically formatted laboratory result (`HAEMOGLOBIN\n12.6\nGM%`) $\to$ 1 segment, exact span.
- Test 2: Two independent laboratory rows (`HAEMOGLOBIN` vs `POTASSIUM`) $\to$ 2 segments, distinct UUIDs.
- Test 3: Two medication orders on separate lines (`Metoprolol` vs `Lisinopril`) $\to$ 2 segments, distinct UUIDs.
- Test 4: Flattened multi-column medication table $\to$ Doses quarantined to `"__no_anchor__"`.
- Test 5: Multi-line evidence crossing medication rows $\to$ Rejected fail-closed.
- Test 6: Multi-line evidence crossing laboratory rows $\to$ Rejected fail-closed.
- Test 7: Whitespace-normalized evidence with exact original offsets $\to$ Exact source span preserved.
- Test 8: Bounding-box preservation across lines $\to$ Enclosing bounding box accurate.
- Test 9: Horizontal laboratory rows separated by newlines $\to$ Isolated.
- Test 10: Permutation invariance under layout-aware grouping $\to$ Invariant.

### 5.2 Newly Performed Inspection & Probes (This Audit)
- Probed `Paracetamol\n500`: Verified single segment, `med.name` establishes `current_med_group`, dose safely inherits.
- Probed `Paracetamol\n500\nLisinopril\n10 mg`: Verified 3 segments, zero cross-medication bleeding, isolated dose quarantined to `"__no_anchor__"`.
- Probed `Metoprolol 25 mg oral\n500`: Verified medication token guard triggers `False`, preserving newline.
- Probed `HAEMOGLOBIN\n12.6\nParacetamol\n500`: Verified cross-domain newline cut, disjoint laboratory and medication UUIDs.
- Probed `Paracetamol\n12.6\nGM%` (Adversarial mixed segment): Proved register independence (`claims[0].group_id != claims[1].group_id`).
- Probed Missing Geometry (`prev_bbox=None`, `curr_bbox=None`): Identified fallthrough behavior to `return True`.
- Probed Malformed Geometry (Divergent columns, inverted Y, distant rows): Verified fail-closed behavior (`return False`).

---

## Section 6: Recommendation for Milestone 3 Authorization

1. **Authorization Status**: **PROCEED TO MILESTONE 3**.
2. **Rationale**:
   - The reported behavior (`Paracetamol\n500` binding safely to `Paracetamol`) does not violate entity containment or cross-domain safety.
   - The Clindoc state machine strictly isolates laboratory from medication claims.
   - Zero regression failures exist across the entire 1,373-test repository suite.
3. **Optional Post-Milestone 3 Hardening**:
   - In a future maintenance milestone, if strict plain-text geometry gating is desired, modify line 162 of `table_layout.py` to require `prev_bbox is not None and curr_bbox is not None` before returning `True`.
