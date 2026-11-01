# Phase 5.2.2 — Medication Association Architecture Design

**Authority:** Phase 5.2.2 Design Specification  
**Document Status:** PROPOSAL / DESIGN ONLY — No Code Modifications Applied  
**Audience:** Clinical Informatics, NLP Extraction Team, FHIR Integration Engineers  

---

## 1. Executive Summary & Problem Formulation

In Phase 5.2, the critical single-UUID defect in `NarrativeExtractor._build_claims()` was resolved: each `med.name` extraction now instantiates a distinct `group_id`, preventing the false downstream suppression of all 38 medication claims in `NARRATIVE_0016`, `NARRATIVE_0017`, and `NARRATIVE_0018`.

However, the subsequent Phase 5.2.1 Adversarial Audit proved that the stateful sequential grouping heuristic has fundamental architectural vulnerabilities:
1. **Parallel / Coordinate Constructions Fail:** Sentences such as *"Metoprolol and Lisinopril were continued at 25 mg and 5 mg, respectively"* assign **both** doses to Lisinopril and none to Metoprolol, creating an acute clinical overdose hazard.
2. **Order Sensitivity:** Group assignment depends on the sequential order of elements in the LLM JSON response rather than physical text coordinates.
3. **Boundary Blindness:** Sentence, clause, and paragraph boundaries are ignored, permitting attributes to leak across distant sentences.
4. **Split Regimens:** Repeated mentions of the same drug create fragmented FHIR resources rather than unified clinical statements.

The primary objective of this architecture design is to establish a **safe, mathematically sound, fail-closed medication association architecture** that eliminates hazardous dose cross-talk while preserving complete compatibility with Mode 0 human review, Four-Eyes approval, and frozen Phase 4 FHIR schemas.

---

## 2. Current Architecture & End-to-End Claim Journey

```
[Raw Document / PDF]
       │
       ▼
[OCR & Ingestion] ──► DocumentText (Pages & Tokens)
       │
       ▼
[Layout Router (DEC-001)] ──► Narrative Extractor (DEC-006, DEC-013)
                                     │
                                     ▼ (LLM JSON output)
                           [_build_claims()] ◄── Current State Machine
                                     │
                                     ▼ (Canonical Claims & Evidence)
                           [Terminology Normalization (RxNorm)]
                                     │
                                     ▼
                           [Verification Cascade (Stage A/B/C)]
                                     │
                                     ▼
                           [Mode 0 Review & Four-Eyes Approval]
                                     │
                                     ▼
                           [FHIR Mapper & Export (claims_to_fhir_bundle)]
                                     │
                                     ▼
                           [FHIR R4B Bundle (MedicationStatement / MedicationRequest)]
```

### 2.1 The Current `_build_claims` Journey
1. The entire page text (~1,865 characters) is extracted as one region.
2. The LLM generates a flat list of `extractions` conforming to `LLM_OUTPUT_SCHEMA`.
3. `_build_claims` iterates over `extractions`:
   - If `field_type == "med.name"`: `current_med_group = str(uuid.uuid4())`.
   - If `field_type` in `("med.dose", "med.route", "med.frequency", "med.duration")`: inherits `current_med_group`.
   - If no prior anchor: assigned sentinel `__no_anchor__`.
4. Evidence character offsets are located via substring search.
5. In `claims_to_fhir_bundle`:
   - Claims are partitioned into groups by `c.group_id`.
   - If a group contains multiple distinct `med.name` claims, the entire group is suppressed by the cross-medication contamination safeguard.
   - If a group contains multiple `med.dose` claims, `select_deterministic_slot_candidate` deterministically selects one and shadows the other.

---

## 3. Evaluation of Design Alternatives

| Alternative | Architecture Summary | Association Accuracy | Failure Modes | Complexity | Testability | Risk Profile |
| :--- | :--- | :---: | :--- | :---: | :---: | :--- |
| **A. Source-Offset Sorting** | Pre-sort candidate extractions by `char_start` before stateful group assignment. | Moderate (handles sequential notes well) | Still fails coordinate lists ("respectively"); still leaks across sentences. | Very Low (10 lines) | Extremely High (pure deterministic logic) | Safe from JSON order variation; unsafe on complex syntax. |
| **B. Sentence & Clause Segmentation** | Segment text into sentences and clauses (MedSpaCy/regex) before grouping. Groups reset at boundaries. | High on standard prose; Moderate on fragments | May orphan cross-sentence attributes ("Metoprolol 25 mg. Continue tomorrow."). | Low-Medium (regex or lightweight rules) | High (isolated unit tests on punctuation) | **Fail-Closed:** Prevents cross-sentence attribute leakage. |
| **C. Medication-Centered Spatial Association** | Spatial proximity graph between `med.name` spans and attribute spans using character distance and clause penalties. | High | Inverted coordinate lists still ambiguous; heuristic distance thresholds can be fragile. | Medium | Medium | Good on dense unstructured notes, but requires distance tuning. |
| **D. Explicit LLM-Provided Entity References** | Extend `LLM_OUTPUT_SCHEMA` so attributes explicitly reference a `med_id`. Extractor validates bindings. | Very High (semantic understanding of "respectively") | LLM hallucination of entity IDs; requires grammar schema change (DEC-013). | High | High (schema validation + span checks) | Highest accuracy potential, but dependent on LLM instruction following. |
| **E. Hybrid Structural Association with Fail-Closed Ambiguity Gate** | Pre-sort + clause segmentation for unambiguous patterns; coordinate/ambiguous patterns marked for Mode 0 review. | **Maximum Clinical Safety** (100% precision on automated pairings) | Suppresses complex coordinate lists for human review rather than guessing. | Medium | Extremely High (rule-based deterministic gating) | **Zero False Associations:** Ambiguous syntax is safely quarantined. |

---

## 4. Recommended Architecture: Layered Hybrid (A + B + E)

We recommend a **Layered Deterministic Structural Architecture with a Fail-Closed Ambiguity Gate** (combining Alternatives A, B, and E, architected to allow Alternative D in future LLM upgrades):

```
                   [Candidate Extractions from LLM]
                                  │
                                  ▼
      ┌────────────────────────────────────────────────────────┐
      │ LAYER 1: Verbatim Evidence Span Grounding (§7.4)       │
      │ Locates exact [char_start, char_end] for all entities  │
      └────────────────────────────────────────────────────────┘
                                  │
                                  ▼
      ┌────────────────────────────────────────────────────────┐
      │ LAYER 2: Physical Source-Offset Sorting                │
      │ Sorts extractions strictly by (char_start, char_end)  │
      └────────────────────────────────────────────────────────┘
                                  │
                                  ▼
      ┌────────────────────────────────────────────────────────┐
      │ LAYER 3: Sentence & Clause Boundary Partitioning       │
      │ Segments region into sentence and clause intervals     │
      └────────────────────────────────────────────────────────┘
                                  │
                                  ▼
      ┌────────────────────────────────────────────────────────┐
      │ LAYER 4: Clause-Level Structural Triage Engine         │
      │   ├─ Case 1: Exactly 1 med.name in clause              │
      │   │   └─► UNAMBIGUOUS: Attributes bind to this drug   │
      │   ├─ Case 2: Multi-med interleaved (A dose1 B dose2)   │
      │   │   └─► UNAMBIGUOUS: Proximity partitioning          │
      │   ├─ Case 3: Coordinate list ("respectively", "both") │
      │   │   └─► AMBIGUOUS: Quarantine to Mode 0 Review       │
      │   └─ Case 4: Orphan attribute (no med.name in clause)  │
      │       └─► SUPPRESSION: group_id = "__no_anchor__"      │
      └────────────────────────────────────────────────────────┘
                                  │
                                  ▼
      ┌────────────────────────────────────────────────────────┐
      │ LAYER 5: Downstream Contamination & Review Safeguards  │
      │ Mapper enforces Four-Eyes approval on ambiguous groups  │
      └────────────────────────────────────────────────────────┘
```

---

## 5. Association & Ambiguity Resolution Rules

### Rule 1: Single-Medication Clause Invariant (Unambiguous)
- If a sentence or clause contains **exactly one `med.name`**, all attribute claims (`dose`, `route`, `frequency`, `duration`) situated in that same clause bind to that medication's `group_id`.
- *Result:* Automated association (Clean PASS).

### Rule 2: Interleaved Multi-Medication Clause Rule (Unambiguous)
- When multiple `med.name` mentions occur in a single sentence, but each drug is immediately followed by its attributes before the next drug (e.g. *"Metoprolol 25 mg twice daily, and Lisinopril 5 mg daily"*):
- Attributes in the interval `[med_name_i.start, med_name_{i+1}.start)` bind strictly to `med_name_i`.
- *Result:* Automated association (Clean PASS).

### Rule 3: Coordinate List Ambiguity Rule (Fail-Closed)
- When multiple `med.name` mentions are clustered together before their attributes (e.g. *"Metoprolol and Lisinopril at 25 mg and 5 mg respectively"* or *"both taken orally"*):
- The association engine identifies the coordinate syntactic marker (`respectively`, `both`, `each`, or consecutive conjunctions without intervening attributes).
- **Automated association is prohibited.**
- The claims are stamped with an explicit review flag: `association_status = "AMBIGUOUS_COORDINATE_LIST"`.
- *Result:* **Quarantined to Mode 0 Human Review.** Dose cross-talk is prevented.

### Rule 4: Attribute Precedence & Inversion Rule
- When an attribute precedes the drug name within the same clause (e.g. *"Daily 25 mg oral Metoprolol"*):
- If **no other `med.name`** precedes in that sentence, the attribute binds to the subsequent `med.name`.
- If an earlier drug is present in the sentence, the attribute is quarantined as `AMBIGUOUS_INVERTED_ORDER` to prevent backwards leakage.

### Rule 5: Conflicting Attribute Rule (Temporal Ambiguity)
- If a single medication mention receives two different doses within a single clause without explicit temporal qualifications (e.g. *"Dose 25 mg or 50 mg PRN"* or *"25 mg increased to 50 mg"*):
- Both candidate dose claims are assigned to the group, but flagged with `slot_conflict = True`.
- In `claims_to_fhir_bundle`, competing slot candidates trigger mandatory clinician selection in Mode 0 review rather than arbitrary automated resolution.

### Rule 6: Sentence Boundary Containment
- Attribute claims cannot cross sentence boundaries (defined by `.` `?` `!` followed by whitespace and capital letter, or newline bullet points) unless explicitly qualified by a coreferent demonstrative (*"This medication"*, *"The same dose"*).
- Without demonstrative coreference, post-sentence attributes receive `group_id = "__no_anchor__"`.

---

## 6. Data Model Integrity & Compatibility

**Zero breaking changes to schemas:**
- `schemas/claim.json` remains strictly compliant.
- `Claim.group_id: Optional[str]` continues to hold the unified entity UUID or `__no_anchor__`.
- Internal metadata additions are passed via `Claim.diagnostics` or pipeline review annotations:
  - `association_type`: `UNAMBIGUOUS_SINGLE` | `UNAMBIGUOUS_INTERLEAVED` | `AMBIGUOUS_COORDINATE` | `AMBIGUOUS_INVERTED` | `ORPHAN_SUPPRESSED`.
  - `review_required`: `bool`.
- Fully conforms to frozen Phase 4 contracts, validator requirements, and FHIR R4B resource definitions.

---

## 7. Adversarial Test Matrix

| Test ID | Input Clinical Text | Expected Grouping | Expected Export Action | Safety Rationale |
| :--- | :--- | :--- | :--- | :--- |
| **ADV-01** | *"Metoprolol and Lisinopril at 25 mg and 5 mg respectively."* | Distinct Groups; `AMBIGUOUS_COORDINATE` | **Hold for Review** (No automated pairing) | Prevents 5x overdose cross-talk. |
| **ADV-02** | *"Metoprolol 25 mg twice daily. Lisinopril was prescribed."* | Group 1: Metoprolol + 25mg + BID<br>Group 2: Lisinopril (un-dosed) | Export Group 1; Export Group 2 as un-dosed statement | Prevents dose leakage to Lisinopril. |
| **ADV-03** | *"Metoprolol and Lisinopril were both taken orally."* | Distinct Groups; `SHARED_ROUTE_COORDINATE` | Export both with route confirmed or review | Route shared safely or flagged. |
| **ADV-04** | *"Metoprolol 25 mg was continued, while Lisinopril was started at 5 mg."* | Group 1: Metoprolol + 25mg<br>Group 2: Lisinopril + 5mg | **Automated Export** | Interleaved syntax is safe. |
| **ADV-05** | *"Takes Metoprolol 25 mg daily, later increased to 50 mg."* | Single Group; 2 doses; `slot_conflict=True` | **Hold for Review** | Requires temporal disambiguation. |
| **ADV-06** | *"Daily 25 mg oral Metoprolol was initiated."* | Group 1: Metoprolol + 25mg + oral + Daily | **Automated Export** | Inverted prefix safely bound. |
| **ADV-07** | *"Hold Metoprolol if SBP < 100. Dose 25 mg or 50 mg PRN."* | Single Group; `conditional_hold=True` | **Hold for Review** | Conditional instruction requires review. |
| **ADV-08** | *"Discontinue Metoprolol. Start Lisinopril 5 mg daily."* | Group 1: Metoprolol (discontinued)<br>Group 2: Lisinopril + 5mg (current) | Export Lisinopril; suppress/status Metoprolol | Preserves temporal status boundaries. |
| **ADV-09** | *"Take Metoprolol 25 mg and Lisinopril 10 mg with water."* | Group 1: Metoprolol + 25mg<br>Group 2: Lisinopril + 10mg | **Automated Export** | Clean interleaved structure. |
| **ADV-10** | Random LLM JSON emission order (attributes before names) | Extractions sorted by text offset before grouping | **Identical Invariant Output** | Eliminates JSON generation variance. |

---

## 8. Migration & Implementation Plan

### Milestone 1: Pre-Sorting & Evidence Grounding Invariants (Minimal Risk)
- In `_build_claims()`, pre-compute verbatim character offsets for all items in `extractions`.
- Sort candidate extractions by `(char_start, char_end)` prior to any group assignment.
- Eliminates 100% of LLM JSON sequence randomness. Zero impact on benchmark ground truth.

### Milestone 2: Sentence & Clause Segmentation Engine
- Introduce lightweight regex/rule-based clause segmentation (`split_clinical_clauses`) in `src/clindoc/extract/clause_segmenter.py`.
- Enforce Sentence Boundary Containment (Rule 6).

### Milestone 3: Fail-Closed Ambiguity Detection Gate
- Implement Coordinate Syntactic Marker detection (`respectively`, `both`, multiple clustered names).
- If coordinate markers are present, tag claims with `AMBIGUOUS_COORDINATE` and route to Mode 0 human review instead of performing greedy sequential pairing.

### Milestone 4: Verification Cascade & FHIR Mapper Integration
- Ensure `claims_to_fhir_bundle` respects the `AMBIGUOUS_COORDINATE` flag, suppressing automated resource export until Four-Eyes approval is logged.
- Run the full benchmark suite across all 20 narrative documents (`NARRATIVE_0001`–`0020`).

---

## 9. Risks, Limitations & Unresolved Decisions

1. **Human Review Load vs. Automation Yield:**
   - *Trade-off:* Failing closed on coordinate lists ("respectively") prevents lethal dose cross-talk but increases review tasks for notes with complex coordinate prose.
   - *Clinical Position:* In clinical informatics, a 5-second human verification of an ambiguous dose is vastly preferable to an automated 5x medication overdose.
2. **DEC-013 Grammar Schema Evolution:**
   - In a future phase, `LLM_OUTPUT_SCHEMA` could be enhanced to support explicit relational entity references (Alternative D). However, grammar-constrained decoding with relational schemas requires Colab/vLLM batch evaluation. The hybrid approach (A+B+E) works immediately offline with zero schema changes.

---

## 10. Final Recommendation

**Retain the current Phase 5.2 baseline for current frozen benchmarks**, and adopt the **Layered Hybrid Architecture (A + B + E)** as the target specification for Phase 5.3 implementation. This guarantees patient safety, maintains 100% fail-closed containment of ambiguous syntax, and preserves all Mode 0 human review and Four-Eyes verification gates.
