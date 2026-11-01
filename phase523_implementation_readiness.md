# Phase 5.2.3 — Architecture Design Refinement & Implementation Readiness

**Authority:** Phase 5.2.3 Specification  
**Status:** SPECIFICATION / DESIGN REFINEMENT ONLY — No Code Modifications Applied  
**Audience:** Clinical Informatics, NLP Extraction Team, FHIR Integration Engineers  

---

## 1. Shared Attribute Association Specification

The Layered Hybrid Architecture establishes deterministic, fail-closed handling for coordinate, shared, and distributive clinical attributes. Under no circumstances may an attribute attach to a medication simply because it appears last in the token sequence.

### 1.1 Category Typology & Association Rules

We partition multi-entity attribute patterns into three mutually exclusive categories:

```
                            [Syntactic Pattern]
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
[1. Clearly Shared]       [2. Parallel Coordinate]       [3. Ambiguous]
("both taken orally",     ("Metoprolol & Lisinopril,     ("with water at 25 mg",
 "each at prescribed")     25 mg & 5 mg respectively")    "taken twice daily")
         │                           │                           │
         ▼                           ▼                           ▼
Replicated Binding /      1-to-1 Ordered Bijection       Fail-Closed Quarantine:
Distributive Semantic      or Explicit Review Gate       Mode 0 Clinician Review
```

#### Category 1: Clearly Shared Attributes
- **Definition:** An attribute that grammatically applies to all members of a coordinated noun phrase.
- **Syntactic Triggers:** Presence of universal coordinate quantifiers (`both`, `each`, `all`, `jointly`) or an unambiguous conjunction governing a shared predicate.
  - *Example 1:* `"Metoprolol and Lisinopril were both taken orally."`
    - **Rule:** The route `"orally"` applies to the coordinated noun phrase `[Metoprolol, Lisinopril]`.
    - **Action:** A dedicated `med.route` claim with value `"oral"` and evidence span `"orally"` is bound to **both** `Metoprolol`'s group and `Lisinopril`'s group. Both claims retain the identical source evidence ID and offsets `[char_start, char_end]`, ensuring full provenance traceability without attribute leakage.
  - *Example 2:* `"Metoprolol and Lisinopril, each at their prescribed dose..."`
    - **Rule:** `"each at their prescribed dose"` is a distributive reference without a numeric quantity.
    - **Action:** No numeric `med.dose` claim is emitted. The text is tagged as a chart reference; automated dose assignment is bypassed.
  - *Example 3:* `"Both medications were taken twice daily."`
    - **Rule:** Anaphoric subject reference (`"Both medications"`) following antecedent drug mentions.
    - **Action:** If the immediately preceding sentence defines exactly two active drugs, `"twice daily"` binds to both entity groups with `provenance_type = "shared_anaphora"`. If antecedent resolution is uncertain (>2 drugs in context), it falls to Category 3 (Ambiguous).

#### Category 2: Parallel Coordinate Attributes ("Respectively")
- **Definition:** A coordinate construction where an ordered list of $N$ drugs is paired with an ordered list of $N$ attributes via an explicit correspondence marker (`respectively`, `in that order`).
  - *Example 4:* `"Metoprolol and Lisinopril were continued at 25 mg and 5 mg, respectively."`
    - **Ordered Entities:** $E = [\text{Metoprolol}, \text{Lisinopril}]$ ($N=2$)
    - **Ordered Doses:** $D = [25\text{ mg}, 5\text{ mg}]$ ($M=2$)
    - **Rule:**
      1. If and only if $N = M$ (exact cardinality match) AND an explicit correspondence marker (`respectively`, `in order`) is present in the clause:
         - The system forms an ordered bijection: $E_i \leftrightarrow D_i$ ($\text{Metoprolol} \leftrightarrow 25\text{ mg}$, $\text{Lisinopril} \leftrightarrow 5\text{ mg}$).
         - Because coordinate list pairing carries catastrophic clinical risk if an extraction is dropped (e.g. if one dose is missed, offset alignment shifts), the system tags the resulting association with:
           `association_status = "PARALLEL_COORDINATE_BIJECTION"`.
         - In the pipeline, this association is permitted as a *provisional candidate*, but **requires explicit Mode 0 clinician confirmation** prior to final FHIR export.
      2. If $N \neq M$ (cardinality mismatch, e.g., 2 drugs and 3 doses):
         - **Automated pairing is strictly prohibited.**
         - All doses are quarantined to `__no_anchor__` with `association_status = "COORDINATE_CARDINALITY_MISMATCH"`.

#### Category 3: Ambiguous Attributes
- **Definition:** Attributes appearing in multi-drug clauses without explicit distributive markers or bijective correspondence.
  - *Example 5:* `"Metoprolol and Lisinopril 25 mg daily."`
    - **Syntactic Ambiguity:** Does 25 mg apply only to Lisinopril, or to both?
    - **Rule:** **Never assign an attribute to the trailing drug merely because it is adjacent.**
    - **Action:**
      - Lisinopril is NOT automatically assigned 25 mg.
      - The attribute is tagged `AMBIGUOUS_SCOPE_MULTI_ENTITY`.
      - Both drugs are exported as un-dosed statements unless clarified during Mode 0 human review.

---

## 2. Export Safety Contract

Association ambiguity must never be bypassed by a generic Stage A/B verification label. An individual claim (e.g. `"25 mg"`) may be factually supported by the evidence span `"25 mg"`, but the *relational association* between `"25 mg"` and a specific drug entity may be completely unverified.

```
+───────────────────────────────────────────────────────────────────────────+
|                       PRODUCTION EXPORT SAFETY GATES                      |
+───────────────────────────────────────────────────────────────────────────+
| 1. Evidence Grounding Gate    : Verbatim substring check (§7.4)           |
| 2. Verification Verdict Gate  : Stage A/B label != DISCREPANCY            |
| 3. Negation Safety Gate       : negated == False (Phase 4.1)              |
| 4. Fallback Rejection Gate    : is_fallback == False (F-05/F-06/F-07)     |
| 5. Association Integrity Gate : association_status in PERMITTED_EXPORT    |
| 6. Mode 0 Human Review Gate   : review_state == ACCEPTED (PD-002)         |
| 7. Four-Eyes Approval Gate    : DocumentApprovalStatus == APPROVED        |
+───────────────────────────────────────────────────────────────────────────+
```

### 2.1 Trace Through the Production Export Path

The association safety contract is enforced across three sequential stages in the production pipeline:

#### Step 1: Ingestion & Extraction (`clindoc.extract.llm_extractor`)
- Every claim emitted by `NarrativeExtractor` receives an internal diagnostic metadata tag:
  `association_status`:
  - `UNAMBIGUOUS_SINGLE` (1 drug in clause; clean automated association)
  - `UNAMBIGUOUS_INTERLEAVED` (Interleaved sequential pairing; clean automated association)
  - `SHARED_COORDINATE_CONFIRMED` (Explicit universal sharing via "both/each")
  - `PARALLEL_COORDINATE_BIJECTION` (Respectively structure; provisional, review required)
  - `AMBIGUOUS_SCOPE_MULTI_ENTITY` (Ambiguous coordination; automated export blocked)
  - `AMBIGUOUS_SLOT_CONFLICT` (Multiple competing doses for 1 drug)
  - `UNANCHORED_ORPHAN` (`__no_anchor__`; automated export blocked)

#### Step 2: Claim Eligibility Service (`clindoc.services.fhir_export.FHIRExportService`)
- In `FHIRExportService.evaluate_claim_eligibility()`:
  ```python
  # Gate 5: Association Integrity Gate (Phase 5.2.3)
  assoc_status = getattr(claim, "association_status", "UNAMBIGUOUS_SINGLE")
  if assoc_status in ("AMBIGUOUS_SCOPE_MULTI_ENTITY", "AMBIGUOUS_SLOT_CONFLICT", "UNANCHORED_ORPHAN"):
      if claim.review_state != "EDITED" and not getattr(claim, "association_confirmed_by_clinician", False):
          return ClaimEligibilityEvaluation(
              claim_id=claim_id_str,
              is_eligible=False,
              reason=f"Ambiguous medication-to-attribute association ({assoc_status}); requires clinician reconciliation in Mode 0 review",
          )
  ```
- **Consequence:** A generic verification label (e.g. `VerificationLabel.SUPPORTED`) CANNOT override an ambiguous association. The claim is declared strictly ineligible for export until a human clinician explicitly edits or confirms the association.

#### Step 3: FHIR Resource Mapper (`clindoc.fhir.mapper.claims_to_fhir_bundle`)
- During dosage construction in `claims_to_fhir_bundle()`:
  - If a medication group contains an attribute claim with an unconfirmed ambiguous association:
    - The mapper **suppresses the ambiguous attribute from the `dosage` element**.
    - If the medication name itself is verified and accepted, the medication is exported as an *un-dosed statement* (e.g. `MedicationStatement` with `medicationCodeableConcept="Metoprolol"` and `dosage=None`), rather than exporting a potentially lethal false dose.
    - An explicit entry is written to `audit_trail`:
      ```json
      {
        "group_id": "grp-123",
        "field_slot": "med.dose",
        "status": "SUPPRESSED_FOR_SAFETY",
        "reason": "Dose 25 mg suppressed due to unconfirmed ambiguous coordinate association"
      }
      ```
- Under Four-Eyes Approval (`DocumentApproval`): A document containing any unresolved `AMBIGUOUS_*` claims is blocked from receiving automated document export status.

---

## 3. Repeated Medication Mentions and Regimens

Clinical narrative notes frequently mention the same medication multiple times across different sections (e.g., Admission History, Hospital Course, Discharge Orders). The system must distinguish between **repeated mentions of the same regimen** versus **distinct clinical events or dosage changes**.

### 3.1 Decision Logic: Merge vs. Split

```
                         [Two Mentions of Drug X]
                                    │
                                    ▼
       Are normalized RxNorm codes identical?
       ├─► NO  ──► SPLIT (Distinct chemical entities / formulations)
       └─► YES ──► Check Clinical Context & Temporal Status:
                     │
    ┌────────────────┼────────────────────────────────────────┐
    ▼                ▼                                        ▼
[Identical Sig]  [Different Doses / Sigs]          [Different Temporal States]
(25mg BID in HPI (25mg morning, 50mg bedtime,      (Discontinued Metoprolol 25mg,
 & Discharge)     or 25mg increased to 50mg)        Started Metoprolol 50mg)
    │                │                                        │
    ▼                ▼                                        ▼
MERGE: Single    SPLIT: Distinct Regimens /        SPLIT: Distinct Lifecycle
Resource with    Dosing Steps; or Slot Conflict    Resources (1 Stopped, 1 Active)
Multi-Evidence   (Requires Mode 0 Review)
Provenance
```

### 3.2 Granular Clinical Scenarios

1. **Identical Repeated Mentions (HPI & Discharge Summary):**
   - *Example:* Section A states `"Metoprolol 25 mg PO twice daily"`. Section B states `"Discharge on Metoprolol 25 mg PO twice daily"`.
   - *Rule:* If drug code, dose, route, and frequency are identical and both are `temporal_status == "current"`:
     - They represent a **single active medication regimen**.
     - They are merged into a single `MedicationStatement` in FHIR export.
     - **Traceability:** The merged resource carries **multiple element-level provenance extensions**, linking both Section A evidence and Section B evidence to the resource.
2. **Split Dosing / Complex Regimen:**
   - *Example:* `"Metoprolol 25 mg in the morning and 50 mg at bedtime."`
   - *Rule:* The same drug with different doses at different times of day.
   - *FHIR R4B Mapping:* Mapped to a **single `MedicationStatement` resource with two distinct `dosage` array entries** (`dosage[0]` for morning, `dosage[1]` for bedtime), preserving the unified regimen identity.
3. **Dose Modification / Titration:**
   - *Example:* `"Patient was taking Metoprolol 25 mg daily, increased today to 50 mg daily."`
   - *Rule:* Historical vs. current dose change.
   - *FHIR R4B Mapping:*
     - Resource 1: `MedicationStatement` (Metoprolol 25 mg, status = `completed`, `statusReason` = `"dose-change"`).
     - Resource 2: `MedicationStatement` (Metoprolol 50 mg, status = `active`).
     - Automated merging is forbidden because it would erase the historical titration context.
4. **Discontinued vs. Current:**
   - *Example:* `"Discontinue Metoprolol 25 mg. Initiate Carvedilol 6.25 mg."`
   - *FHIR R4B Mapping:* Metoprolol maps to `MedicationStatement` with `status = "stopped"`. Carvedilol maps to `MedicationStatement` with `status = "active"`. Under DEC-008, if accompanied by a discharge prescription order, Carvedilol also maps to `MedicationRequest` with `status = "active"`.

---

## 4. Source Grounding and Segmentation Engine

### 4.1 Resolution of Duplicate & Overlapping Spans
- **Exact Identical Spans:**
  - When multiple claims cite the identical character bounds `[char_start, char_end]` (e.g. `med.name` and `med.dose` both extracted from `"Metoprolol 25 mg"`):
  - Handled via `Evidence.deduplicate_key(document_id, page_number, char_start, char_end)`.
  - Exactly **one** canonical `Evidence` object is created and shared across both claims via `evidence_id` foreign key.
- **Partially Overlapping Spans:**
  - Span A: `"Metoprolol 25 mg"` `[1500:1516]`. Span B: `"25 mg PO twice daily"` `[1511:1531]`.
  - Both spans are preserved independently as distinct `Evidence` records. No lossy span union or trimming is performed.

### 4.2 OCR Errors & Robust Matching (Fail-Closed)
- The system enforces **strict substring grounding** (§7.4):
  1. *Exact Substring:* `region_text.find(evidence_text) >= 0`.
  2. *Whitespace-Normalized Match (Root Cause A):* Normalizes only whitespace differences (spaces, tabs, newlines) while preserving exact token sequence fail-closed:
     ```python
     pattern = r"\s+".join(re.escape(tok) for tok in evidence_text.split())
     match = re.search(pattern, region_text)
     ```
  3. *Rejection Boundary:* **Fuzzy matching, edit-distance tolerance, and phonetic approximation are strictly prohibited.**
     - If OCR garbled `"25 mg"` into `"2S mg"` or `"75 mg"`, the LLM cannot hallucinate `"25 mg"`. Any claim whose evidence text is not verifiable in the raw OCR text is rejected as `llm_span_hallucination` (fail-closed).

### 4.3 Clinical Shorthand-Aware Clause Segmentation
Standard NLP sentence splitters break at every period, catastrophically shattering clinical prescriptions:
- Shorthand: `b.i.d.`, `p.o.`, `q.d.`, `t.i.d.`, `mg.`, `tab.`, `Dr.`, `pt.`, `H.P.I.`, `wt.`, `ht.`, `BP 120/80 mmHg.`, `C.Autrin`, `T.Shelcal`.

The `split_clinical_clauses()` engine utilizes a **Negative Lookbehind / Lookahead Clinical Sentencizer**:
1. *Protected Abbreviation Table:* A compiled Trie of 85+ Latin prescription sigs, measurement units, and medical titles.
2. *Punctuation Rule:* A period followed by whitespace is considered a sentence boundary **only if** the preceding token is NOT in the protected table AND the subsequent token begins with a capital letter or numbered list indicator (`1.`, `2.`, `•`).
3. *Clause Splitters:* Semicolons (`;`), em-dashes (`—`), bullet points (`\n-`, `\n*`), and numbered lists (`\n\d+\.`) establish hard clause boundaries.
4. *Offset Invariance:* Slicing produces intervals `(clause_start, clause_end)` indexing directly into the original, immutable `page.get_page_text()`. Character offsets are never re-indexed or shifted.

### 4.4 Deterministic Ordering Guarantee
- Before any grouping or triage logic executes, candidate extractions are sorted:
  ```python
  extractions.sort(key=lambda ext: (ext["char_start"], ext["char_end"], ext["field_type"]))
  ```
- **Result:** Regardless of whether the LLM emits attributes before names or reverses their order in JSON, the sorted sequence is mathematically invariant.

---

## 5. Milestone-by-Milestone Implementation Plan

```
┌────────────────────────────────────────────────────────────────────────┐
│ MILESTONE 1: Pre-Sorting & Evidence Grounding Invariants               │
│ - Files: src/clindoc/extract/llm_extractor.py                          │
│ - Invariants: Pre-sort extractions by char_start; eliminate JSON drift │
│ - Acceptance: 26/26 Phase 5.2 tests pass; 18/18 benchmark pass         │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MILESTONE 2: Clinical Clause & Sentence Segmentation Module            │
│ - Files: src/clindoc/extract/clause_segmenter.py (NEW)                 │
│ - Invariants: Protected sigs; offset-stable clause interval slices     │
│ - Acceptance: Zero shattering on 50 clinical abbreviation test cases   │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MILESTONE 3: Layered Association & Ambiguity Triage Engine             │
│ - Files: src/clindoc/extract/llm_extractor.py, contracts.py            │
│ - Invariants: Respectively & coordinate quarantine; Single-med pairing │
│ - Acceptance: 10/10 adversarial scenarios pass expected dispositions   │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│ MILESTONE 4: Export Safety & Mode 0 Review Gate Integration            │
│ - Files: src/clindoc/services/fhir_export.py, src/clindoc/fhir/mapper  │
│ - Invariants: AMBIGUOUS_* blocked from auto-export; Four-Eyes enforced │
│ - Acceptance: Full 20-doc narrative benchmark; 1,318 regression tests  │
└────────────────────────────────────────────────────────────────────────┘
```

### Detailed Milestone Breakdown

#### Milestone 1: Pre-Sorting & Evidence Grounding Invariants
- **Files Modified:** `src/clindoc/extract/llm_extractor.py` (`_build_claims`).
- **Interfaces Preserved:** `NarrativeExtractor.extract()` return signature `tuple[list[Evidence], list[Claim], dict]` unchanged.
- **Change:** Compute `char_start` for all extractions first; sort extractions by `(char_start, char_end)`.
- **Tests Required:** Unit tests verifying that permuting LLM JSON extractions yields identical `Claim` and `Evidence` lists.
- **Acceptance Criteria:** `tests/test_phase52_medication_coreference.py` (26/26) and `tests/test_narrative_benchmark.py` (18/18) pass 100%.

#### Milestone 2: Clinical Clause & Sentence Segmentation Module
- **Files Created:** `src/clindoc/extract/clause_segmenter.py`.
- **Interfaces Created:** `split_clinical_clauses(text: str) -> list[ClauseSpan]`.
- **New Metadata:** `ClauseSpan` dataclass containing `(char_start, char_end, text, clause_type)`.
- **Tests Required:** Dedicated test suite `tests/test_clause_segmenter.py` covering all Latin medical sigs (`b.i.d.`, `p.o.`, `q.d.`), numbered lists, dot-leaders, and compound sentences.
- **Acceptance Criteria:** Zero shattered abbreviations; exact character reconstruction: `''.join(c.text for c in clauses) == original_text`.

#### Milestone 3: Layered Association & Ambiguity Triage Engine
- **Files Modified:** `src/clindoc/extract/llm_extractor.py`.
- **New Internal Metadata:** Claim diagnostic property `association_status`.
- **Change:** Integrate `split_clinical_clauses` into `_build_claims`. Apply Rules 1–6 (Single-Med Clause Invariant, Coordinate Marker Quarantine).
- **Tests Required:** Implement test suite covering all 10 adversarial scenarios from Phase 5.2.1.
- **Acceptance Criteria:**
  - Coordinate lists with "respectively" are tagged `PARALLEL_COORDINATE_BIJECTION`.
  - Ambiguous coordinate scopes are tagged `AMBIGUOUS_SCOPE_MULTI_ENTITY`.
  - Zero dose cross-pollination between medications.

#### Milestone 4: Export Safety & Mode 0 Review Gate Integration
- **Files Modified:** `src/clindoc/services/fhir_export.py`, `src/clindoc/fhir/mapper.py`.
- **Interfaces Preserved:** `FHIRExportService.evaluate_claim_eligibility()`, `claims_to_fhir_bundle()`.
- **Change:** Wire `association_status` into `ClaimEligibilityEvaluation` and FHIR dosage construction. Ambiguous claims are blocked from automated export and require clinician confirmation.
- **Acceptance Criteria:**
  - Frozen Phase 4 tests pass with zero regressions.
  - Full suite maintains $\ge 1,318$ passes.
  - The 38 claims across `NARRATIVE_0016`–`0018` continue to export cleanly.

### Rollback & Safety Conditions
- If any milestone causes a regression in the 20-document narrative benchmark (`tests/test_narrative_benchmark.py`), execution must immediately halt.
- If an ambiguous claim is exported to FHIR without explicit human acceptance metadata in `audit_trail`, the safety circuit trips and halts export.

---

## 6. Recommendation

The Layered Hybrid Architecture is fully specified and ready for implementation. It resolves all failure modes identified in Phase 5.2.1, eliminates the lethal risk of coordinate dose cross-talk, guarantees deterministic extraction, and preserves 100% of the project's regulatory safety boundaries.

Awaiting approval to begin Milestone 1.
