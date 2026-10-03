# M2 Verification Benchmark Generation: Root Cause Analysis

**Authority**: `PROJECT_SPEC.md` (§7.1, §7.8, §8, §9, §10, §20.1)  
**Target Dataset**: `data/m2_verification/all_examples.jsonl` (9,168 examples)  
**Investigation Mode**: Read-Only / Non-Destructive Code & Pipeline Forensics  
**Date**: October 2, 2026  

---

## Executive Summary

Following the comprehensive audit of `data/m2_verification/all_examples.jsonl` (which found only 44.57% clean examples, 25.04% definitely invalid examples, 640 header fallbacks, 2,687 degraded offset mismatches, 4 out-of-bounds examples, and 2,745 ground-truth conflicts), we conducted a forensic tracing of the complete benchmark generation pipeline.

We isolated the exact source-code flaws responsible for every corruption category:
1. **The `[0, 100]` Header Fallback**: Caused by lines 786–787 and 911–912 of `src/clindoc/m2/generation.py`, where failed line searches under OCR noise silently default to `char_start=0, char_end=min(len(page_text), 100)` rather than rejecting unlocated tokens.
2. **Degraded OCR Offset Mismatches & Out-of-Bounds**: Caused by lines 780–781 and 905–906, where offsets were calculated against *corrupted OCR cache files* (`data/degraded/ocr_cache/`) whose string lengths differ from canonical documents. `source_doc_id` was saved as the clean document ID with no coordinate translation. When degraded text was longer than clean text (e.g. `LAB_0028`: 607 vs 598 chars), offsets exceeded document bounds.
3. **`SUPPORTED` + `WRONG_ENTITY` Conflicts (782 examples)**: Caused by line 773: `if tname.lower() in ltxt.lower() or v_str in ltxt:`. Using `or v_str in ltxt` allowed any unrelated table row containing the same number (e.g. "24" or "0.4") to be selected as the evidence row for a completely different analyte when OCR noise obscured the test name.
4. **Ex9 Forensic Root Cause**: Caused by lines 624–640, where the generator intentionally bound the first 3 lines of the document (`page_text.split("\n")[:3]`, clinic letterhead) as dummy evidence for unobserved negative medication claims.
5. **Ex12 Forensic Root Cause**: Caused by tabular context window extraction (`src/clindoc/m2/context.py:75`), which unconditionally slices 250 characters before the target cell without row alignment, shifting demographic headers into the start of the evidence window.

---

## A. Complete Actual Generation Pipeline Diagram

The canonical benchmark was generated in two historical phases (100 core documents followed by 20 narrative documents). Below is the actual execution flow discovered in the repository:

```mermaid
flowchart TD
    subgraph Phase 1: Source Document Ingestion
        A1[Clean Lab M0 GT & M1 Extraction\ndata/extracted/LAB_*.json\ndata/ground_truth/LAB_*.json] --> B1
        A2[Clean Med M0 GT & M1 Extraction\ndata/med_extracted/MED_*.json\ndata/med_ground_truth/MED_*.json] --> B2
        A3[Degraded OCR Caches\ndata/degraded/ocr_cache/corrupt_lab/\ndata/degraded/ocr_cache/corrupt_med/] --> B3
        A4[Narrative M0 GT & OCR\ndata/narrative_ground_truth/\ndata/narrative_ocr_cache/] --> B4
    end

    subgraph Phase 2: Example Generation in src/clindoc/m2/generation.py
        B1 -->|generate_clean_lab_examples| C1[Clean Lab Observations\nSUPPORTED & DISCREPANCY\nUnobserved IE with Header]
        B2 -->|generate_clean_med_examples| C2[Clean Med Observations\nSUPPORTED & DISCREPANCY\nUnobserved IE with Header]
        B3 -->|generate_degraded_lab_examples\ngenerate_degraded_med_examples| C3[Degraded Observations\nOffset against corrupt OCR lines\n[0,100] Fallback on search failure\nWrong Entity via 'or v_str in ltxt']
        B4 -->|generate_narrative_m2_examples| C4[Narrative Observations\nDEC-014 20 Documents]
    end

    subgraph Phase 3: Assembly & Context Window Extraction
        C1 & C2 & C3 --> D1[generate_all_m2_examples\nCombines 100 docs -> 8,568 ex]
        D1 --> D2[extract_context_window in context.py\nTabular: ±250 chars mid-row cut\nNarrative: paragraph or ±250 chars]
        D2 --> E1[Task 2 Canonical Benchmark\n8,568 examples]
    end

    subgraph Phase 4: Narrative Integration & Serialization
        E1 & C4 --> F1[integrate_narrative_benchmark\nAppends 600 narrative examples]
        F1 --> G1[(data/m2_verification/all_examples.jsonl\n9,168 Total Examples)]
        F1 --> G2[data/m2_verification/split_manifest.json\n86 CV docs / 34 OOD docs]
        F1 --> G3[data/m2_verification/generation_manifest.json\nMetadata & Class Balance Assertion]
    end
```

---

## B. Exact Source-Code References

### 1. `[0, 100]` Clinic Header Fallback
- **File**: [`src/clindoc/m2/generation.py`](file:///d:/daa/src/clindoc/m2/generation.py#L770-L790)
- **Functions**: `generate_degraded_lab_examples()` (lines 770–790) and `generate_degraded_med_examples()` (lines 895–915)
- **Relevant Logic**:
  ```python
  # Lines 770-790
  target_line_idx = -1
  for li, line_obj in enumerate(lines):
      ltxt = line_obj.get("text", "")
      if tname.lower() in ltxt.lower() or v_str in ltxt:
          target_line_idx = li
          break

  if target_line_idx >= 0:
      ...
  else:
      char_start = 0
      char_end = min(len(page_text), 100)
      bbox = [52.0, 193.32, 517.01, 205.76]
      snip, _, _ = extract_context_window(page_text, 0, char_end, is_tabular=True)
  ```
- **Explanation**: When OCR noise perturbs the analyte or medication name so that exact substring matching fails, `target_line_idx` remains `-1`. Instead of discarding the ungrounded observation, the generator silently falls back to `char_start = 0, char_end = 100` and extracts a 500-character window starting from index 0. This binds the document's clinic letterhead/address header as the evidence span for **640 examples** (255 `SUPPORTED`, 255 `DISCREPANCY`, 130 `INSUFFICIENT_EVIDENCE`).

---

### 2. Degraded OCR Offset Mismatch & Out-of-Bounds
- **File**: [`src/clindoc/m2/generation.py`](file:///d:/daa/src/clindoc/m2/generation.py#L780-L782)
- **Functions**: `generate_degraded_lab_examples()` (lines 780–782) and `generate_degraded_med_examples()` (lines 905–907)
- **Relevant Logic**:
  ```python
  # Lines 780-782
  t_line = lines[target_line_idx]
  target_text = t_line.get("text", "")
  char_start = page_text.find(target_text)
  char_end = char_start + len(target_text) if char_start >= 0 else len(target_text)
  char_start = max(0, char_start)
  ```
- **Explanation**: In degraded generation, `page_text` and `lines` were loaded from `data/degraded/ocr_cache/corrupt_lab/` and `corrupt_med/`. Therefore, `char_start` and `char_end` are character positions within the *corrupted OCR string*. However, when serializing `M2Example`, `source_doc_id` was written as the clean canonical document ID (`LAB_0020`, `LAB_0028`) without saving an alignment mapping back to clean document coordinates.
  - When corrupted text contains inserted OCR characters/quotes, its length exceeds canonical text.
  - In `LAB_0028`, degraded length is 607 chars; clean length is 598 chars. The offset `[598, 603]` was valid in the degraded file (`6.12'`), but when evaluated against the clean document, it was strictly **out of bounds**.

---

### 3. `SUPPORTED` + `WRONG_ENTITY` Semantic Contamination (782 examples)
- **File**: [`src/clindoc/m2/generation.py`](file:///d:/daa/src/clindoc/m2/generation.py#L773)
- **Function**: `generate_degraded_lab_examples()`
- **Relevant Logic**:
  ```python
  # Line 773
  if tname.lower() in ltxt.lower() or v_str in ltxt:
      target_line_idx = li
      break
  ```
- **Explanation**: The search uses a loose disjunction: `or v_str in ltxt`. If `tname` (`Carbon Dioxide`) was corrupted by OCR noise, but `v_str` (`24` or `2.0` or `0.4`) appeared anywhere else in the document (such as in a `BUN`, `Glucose`, or date line), that completely unrelated row was chosen as `target_line_idx`. The generator then emitted a `SUPPORTED` claim for `Carbon Dioxide: 24 mmol/L` linked to the `BUN` evidence row. This single line created all **782** degraded `SUPPORTED -> WRONG_ENTITY` corruptions.

---

### 4. Intentional Header Binding for Negative Controls (Ex9 Root Cause)
- **File**: [`src/clindoc/m2/generation.py`](file:///d:/daa/src/clindoc/m2/generation.py#L624-L640)
- **Function**: `generate_clean_med_examples()`
- **Relevant Logic**:
  ```python
  # Lines 628-634
  if page_text:
      first_lines = page_text.split("\n")[:3]
      header_med_ev = " | ".join(first_lines)
      header_end = len(header_med_ev)
      context_header, _, _ = extract_context_window(
          page_text, 0, header_end, is_tabular=False
      )
  ...
  examples.append(M2Example(
      claim=f"{c[0]} frequency: {c[3]}",
      evidence=context_header,
      field_type="med.frequency",
      label=VerificationLabel.INSUFFICIENT_EVIDENCE,
      char_start=header_start,
      char_end=header_end,
  ))
  ```
- **Explanation**: To create negative controls (`INSUFFICIENT_EVIDENCE`), the author chose synthetic unobserved candidate tuples (such as `("Insulin glargine", "20 units", "subcutaneously", "at bedtime")`). Because the medication is absent from the document, there was no true evidence span. The author explicitly bound the top 3 lines of the page (`context_header`) as dummy evidence.
  - When Stage A/C evaluated Ex9 (`bad63dac-be2f-46e9-b7d3-22a8278315bf`), the evidence text contained `Gabapentin TID` from the hospital discharge header, causing Stage A to declare a frequency contradiction instead of recognizing an unobserved entity.

---

### 5. Mid-Row Tabular Window Slicing (Ex12 Root Cause)
- **File**: [`src/clindoc/m2/context.py`](file:///d:/daa/src/clindoc/m2/context.py#L73-L77)
- **Function**: `extract_context_window()`
- **Relevant Logic**:
  ```python
  # Lines 73-77
  if is_tabular and dense_override.get("enabled", True):
      start = max(0, char_start - tabular_chars)
      end = min(len(page_text), char_end + tabular_chars)
      return page_text[start:end], start, end
  ```
- **Explanation**: In tabular documents, `extract_context_window` subtracts 250 characters from `char_start` without aligning to newline boundaries (`\n`). In `LAB_0037` (Ex12), the target observation `Creatinine 0.63 NORMAL` is at char 440. Subtracting 250 characters sets `start = 190`, which lands in the middle of the patient demographic header (`"ate: 2025-11-20 Date of Birth: 1954-08-15..."`). The resulting evidence string is dominated by patient metadata, placing the clinical target row 250 characters down inside a 500-character blob.

---

## C. Comprehensive Root-Cause Table

| Problem Category | Affected Examples | Primary File & Line | Current Defect | Correct Invariant | Repair Class |
| :--- | :--- | :--- | :--- | :--- | :---: |
| **Corrupted Fallback Spans** | 640 examples | `src/clindoc/m2/generation.py:786, 911` | Defaults to `[0, 100]` (clinic header) when line search fails | Reject example or perform fuzzy Levenshtein match; never default to header | **B** (Regeneration Required) |
| **Degraded Offset Mismatch** | 2,687 examples | `src/clindoc/m2/generation.py:780, 905` | Offsets point into corrupt OCR text files, mismatched with canonical cache | Re-anchor offsets to clean canonical OCR text via bounded alignment | **A** (Deterministic Repair) |
| **Out-of-Bounds Offsets** | 4 examples | `src/clindoc/m2/generation.py:781` | Degraded text expansion caused offsets to exceed clean doc length | Canonical re-anchoring guarantees `char_end <= len(clean_text)` | **A** (Deterministic Repair) |
| **Wrong Entity in Supported** | 782 examples | `src/clindoc/m2/generation.py:773` | `or v_str in ltxt` bound unrelated rows containing common numbers | Conjunction required: analyte name AND value must co-occur on the row | **B** (Regeneration Required) |
| **Dummy Header in Insufficient Ev** | 140 examples | `src/clindoc/m2/generation.py:628, 293` | Document header bound as dummy evidence for unobserved claims | Evidence must represent document-wide scope or empty span contract | **A/B** (Protocol Standardization) |
| **Context Window Mid-Row Clipping** | 772 examples | `src/clindoc/m2/context.py:75` | Fixed 250-character symmetric window slices mid-line | Snap window boundaries to newline `\n` row delimiters | **A** (Deterministic Repair) |
| **Contradictory Supported Claims** | 494 examples | `src/clindoc/m2/generation.py:818` | Generated `SUPPORTED` without post-generation contract validation | Post-generation verification contract validation before serialization | **B/C** (Re-validation / Manual) |

*Repair Classes:*
- **Class A**: Deterministic automated repair possible using canonical OCR text without altering experimental design.
- **Class B**: Requires re-running the generator with corrected logic and invariants.
- **Class C**: Requires manual clinician / annotator review.
- **Class D**: Fundamentally unrecoverable (must be pruned).

---

## D. Forensic Trace of Suspicious Examples Ex9 and Ex12

### Ex9 Forensic Trace (`bad63dac-be2f-46e9-b7d3-22a8278315bf`)
1. **Creation**: Generated by `generate_clean_med_examples()` in `src/clindoc/m2/generation.py` (lines 623–730) on document `MED_0010`.
2. **Intent**: Provide an `INSUFFICIENT_EVIDENCE` negative control for the unobserved medication `Insulin glargine`.
3. **Flaw**: Because `Insulin glargine` does not exist in `MED_0010`, the code executed lines 628–634:
   ```python
   first_lines = page_text.split("\n")[:3]
   header_med_ev = " | ".join(first_lines)
   ```
   This assigned `char_start=0, char_end=217` and populated `evidence` with:
   `"Memorial General Hospital - Department of Inpatient Medicine DISCHARGE INSTRUCTIONS & POST-ACUTE MEDICATION RECONCILIATI..."`
4. **Consequence at Inference**: When evaluated in the Phase 4 cascade, Stage A compared the claim (`Insulin glargine frequency: at bedtime`) against the discharge header text (which mentioned `Gabapentin TID`), declaring a false `DISCREPANCY` instead of `INSUFFICIENT_EVIDENCE`.

### Ex12 Forensic Trace (`02f9d689-ba1b-4a7e-adfb-0ec2dc30faa7`)
1. **Creation**: Generated by `generate_clean_lab_examples()` in `src/clindoc/m2/generation.py` (lines 217–239) on document `LAB_0037`.
2. **Intent**: Provide a `DISCREPANCY` claim for `Creatinine: 0.16 mg/dL` (perturbed from true value `0.63`).
3. **Flaw**: The stored atomic offsets `char_start=440, char_end=480` point accurately to `Creatinine 0.63 NORMAL 0.60 - 1.30 mg/dL` in `LAB_0037`. However, `extract_context_window(page_text, 440, 480, is_tabular=True)` subtracted 250 characters, capturing lines starting at char 190.
4. **Consequence at Inference**: The resulting evidence string began with:
   `"ate: 2025-11-20\nDate of Birth: 1954-08-15 (F) Ordering Clinician: Dr. Marcus Vance, DO\nPanel: Comprehensive Metabolic Panel..."`
   Because `eval_phase4_cascade.py` initially passed only `evidence.text` to Stage C without centering the prompt on the atomic offset, the LLM evaluated the patient header block, failing to recognize the target laboratory row buried further down in the prompt.

---

## E. Proposed Corrected Generation Pipeline

```mermaid
flowchart TD
    A[M0 Ground Truth & Authoritative Canonical OCR] --> B[Candidate Claim Generator]
    B --> C{Claim Type}
    
    C -->|Observed Supported| D1[Extract Exact Token from Canonical Text]
    C -->|Observed Discrepancy| D2[Perturb Value & Locate Ground Truth Span]
    C -->|Unobserved Negative| D3[Document-Wide Scope: char_start=null, char_end=null]
    
    D1 & D2 --> E[Snap Context Window to Row/Sentence Boundaries]
    
    E --> F[Optional Degradation Layer]
    F --> G[Fuzzy Alignment: Map Degraded Token -> Canonical [char_start, char_end]]
    
    G --> H{Post-Generation Integrity Gate}
    D3 --> H
    
    H -->|Passes All Invariants| I[Serialize M2Example to Benchmark Pool]
    H -->|Fails Any Invariant| J[DROP EXAMPLE - Do Not Fallback]
    
    I --> K[Stratified Group-Aware Split Assignment DEC-015/011]
```

---

## F. Mandatory Safety Invariants for Corrected Generator

A corrected generator MUST enforce these 10 programmatic assertions before any example is serialized:

1. **Exact Canonical Resolvability**:  
   For every example where `char_start` and `char_end` are not null, `canonical_page_text[char_start:char_end]` MUST extract a non-empty string that matches or is a verified substring of the target clinical entity.
2. **Zero Default Fallback Toleration**:  
   Under NO circumstances may `char_start = 0, char_end = 100` be used as a fallback for missing tokens. If an analyte cannot be located, the generator MUST drop the example.
3. **Strict Entity-Presence Invariant**:  
   For `SUPPORTED` and `DISCREPANCY` labels, the target entity (analyte name or medication name) MUST be present in the evidence window (`verifier._is_entity_present_in_text(entity, evidence) == True`).
4. **No Disjunction in Line Matching**:  
   Candidate matching in tabular documents MUST require entity presence: `tname.lower() in line_text.lower()`. Matching on raw numeric values (`or v_str in ltxt`) is strictly prohibited.
5. **No Out-of-Bounds Offsets**:  
   `0 <= char_start < char_end <= len(canonical_page_text)` MUST hold for 100% of grounded examples.
6. **Row-Aligned Context Windows**:  
   Tabular context windows MUST snap to newline `\n` boundaries so that table rows are never bisected mid-word or mid-number.
7. **Negative Control Scope Contract**:  
   For `INSUFFICIENT_EVIDENCE` claims where an entity is genuinely unobserved across the entire document, `char_start` and `char_end` should be `null` (or span the full document), and the evidence snippet must NOT be populated with a misleading local letterhead.
8. **Degraded-to-Canonical Coordinate Mapping**:  
   When generating from degraded OCR caches, offsets MUST be mapped back to canonical clean document coordinates before saving.
9. **Pre-Serialization Contract Validation**:  
   Every generated candidate example MUST pass through a validation gate using `DeterministicVerifier` to verify that `label == SUPPORTED` actually evaluates to `SUPPORTED`.
10. **Post-Validation Split Assignment**:  
    Dataset split assignment (`train`, `calibration`, `validation`, `ood`) MUST occur only AFTER all invalid candidates have been pruned, guaranteeing exact class balance and split integrity.

---

## G. Exact Files Requiring Modification to Fix Generator

1. **`src/clindoc/m2/generation.py`**:
   - Rewrite `generate_degraded_lab_examples()` and `generate_degraded_med_examples()`:
     - Remove `else: char_start = 0; char_end = min(len(page_text), 100)`. Drop unlocated observations.
     - Remove `or v_str in ltxt` from line 773. Require test name match.
     - Implement coordinate re-anchoring from degraded lines back to canonical clean OCR cache.
   - Refactor `clean_unobserved_claim_insufficient_evidence` (lines 287–326 and 623–730): Stop assigning dummy header snippets to negative controls.
2. **`src/clindoc/m2/context.py`**:
   - Modify `extract_context_window()`:
     - For `is_tabular=True`, expand `start` backwards to the previous `\n` and `end` forwards to the next `\n` within character limits to prevent mid-row truncation.
3. **`src/clindoc/m2/dataset.py`**:
   - Add pre-save schema validation enforcing the 10 invariants from Section F.
4. **`src/clindoc/audit/benchmark_auditor.py`**:
   - Integrate as a mandatory CI/validation step in `src/clindoc/m2/run.py` to prevent regression.

---

## H. Recommended Implementation Order

1. **Step 1: Context Window Snapping**: Update `src/clindoc/m2/context.py` so that tabular extractions respect row boundaries (`\n`).
2. **Step 2: Generator Invariant Enforcement**: Update `src/clindoc/m2/generation.py` to eliminate `[0, 100]` fallbacks, eliminate `or v_str in ltxt`, and re-anchor degraded offsets to canonical coordinates.
3. **Step 3: Verification Gate**: Add the 10 safety assertions into `dataset.py` / `generation.py`.
4. **Step 4: Regenerate Clean Canonical Benchmark**: Run corrected generation, producing a verified `all_examples.jsonl`.
5. **Step 5: Run Benchmark Auditor**: Verify that clean examples reach >98% and invalid examples reach 0%.
6. **Step 6: Update Split Manifest**: Recompute `split_manifest.json` on the certified dataset.
7. **Step 7: Proceed to Evaluation**: Resume Phase 4 cascade and ModernBERT evaluation on the certified benchmark.
