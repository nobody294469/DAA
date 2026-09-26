# PHASE 0 DECISION RESOLUTION PACKAGE

**Companion to:** `PROJECT_SPEC.md` v1.0
**Purpose:** Resolve every `DECISION REQUIRED` item so Phase 1 can begin with zero architectural choices left to an implementing agent. This package does not modify `PROJECT_SPEC.md` directly — §D lists the exact edits to make once these recommendations are approved.

**Scope check performed:** All twelve `DECISION REQUIRED` items in Appendix A were reviewed, plus every inline reference to them in §7, §11, §12, §13, §14, §15, §17, §18, §21. One additional gap was found that the spec requires functionally (§7.4/§13.3 mandate "structured-output enforcement") but never assigned a decision ID to — it is added here as **DR-13**. No other decisions were invented; everything below traces to an explicit `MUST`/`DECISION REQUIRED` in the spec or an unavoidable implementation detail of one.

---

## DR-1 — Region/Layout Classification Method

**Blocks:** Extraction Router (§5 component 2, §11 all routing rules); transitively blocks DR-4, and E1–E4 (§22.1).

**Options:**
- (a) Fine-tune a custom layout model (e.g., LayoutLMv3) on your document templates.
- (b) Rule-based/heuristic classifier: regex triggers for medication-regimen lines (`DRUG DOSE ROUTE FREQUENCY [DURATION]` pattern family) + geometric heuristics for table detection (consistent column alignment of OCR token x-coordinates across consecutive lines) + "everything else → narrative."
- (c) Off-the-shelf pretrained document-layout/table-detection tool used purely for inference (e.g., PaddleOCR's PP-Structure table module), no fine-tuning.

**Recommendation: (b), optionally supplemented by (c) for table boundary detection only.**

**Reason:** Option (a) is explicitly prohibited by §3/§28 ("no custom-trained layout/vision-language model as a project deliverable"). Your document set is template-based and synthetically generated (§18.1), which means the structural patterns (lab table columns, regimen line grammar) are known and finite — a heuristic classifier will hit very high accuracy at near-zero cost, and it keeps the "deterministic first" philosophy (§4.2) consistent even at the routing layer, not just the extraction layer. PP-Structure MAY be added later purely to get cleaner table cell boundaries (it costs nothing extra since PaddleOCR is already in your OCR stack), but it is not required to make the routing decision correct.

**Experiment before locking:** Once the Phase 1 corrected benchmark exists, run the heuristic classifier against the ground-truth region labels and measure routing accuracy per document type. This is a same-day check, not a research experiment. If accuracy is below ~90% on any document type, add PP-Structure as a supplementary signal for that type specifically — do not redesign the whole classifier.

**Final value for spec:** *"§11 region classification MUST use a deterministic rule-based classifier (regex pattern matching for medication-regimen lines; column-alignment heuristics for lab tables; unmatched text defaults to narrative). PP-Structure's pretrained table-detection module MAY be used as a supplementary signal for table boundary refinement. No custom-trained layout model is used. DR-1: RESOLVED."*

**Dependencies:** Must be resolved before DR-4 (the deterministic-extraction threshold is measured *within* regions this classifier produces). Independent of everything else — resolve first.

---

## DR-2 — Terminology `code_confidence` Threshold

**Blocks:** Terminology Normalizer (§5 component 6); §15.3 FHIR coding correctness; §21.6 metric.

**Options:**
- A single global threshold picked a priori (spec's own placeholder: 0.7).
- Per-code-system thresholds (LOINC / RxNorm / UCUM independently), empirically tuned.
- A threshold chosen to hit a target precision on accepted codes (e.g., sweep threshold, pick the point where precision on accepted codes ≥ 95%).

**Recommendation: per-code-system threshold, each independently tuned to a ≥95% precision target on accepted codes, via sweep.**

**Reason:** §14.3 forbids inventing a code — a false positive here silently corrupts a FHIR coded field, which is worse than the null fallback. LOINC, RxNorm, and UCUM have different retrieval difficulty (LOINC test names have many near-duplicate synonyms; RxNorm drug names have brand/generic aliasing; UCUM is a small closed set best handled by exact lookup, not a learned threshold at all — see DR-7). A single global number will be miscalibrated for at least one of the three. Precision-targeting (not F1-targeting) is correct here specifically because the cost of a wrong accepted code is asymmetric versus the cost of a null.

**Experiment before locking:** Cannot be locked until DR-7 (embedding model + scoring combination) is resolved, since the threshold applies to the *combined* retrieval score, not a raw model score. Once DR-7's combined scorer exists, sweep threshold on the Phase 1 benchmark's ground-truth codes (which you already have, since it's synthetically generated) and report the precision/recall/coverage tradeoff curve; lock the value hitting the 95% precision bar per code system.

**Final value for spec (placeholder until sweep completes):** *"§7.5: `code_confidence` threshold is set independently per `code_system`, calibrated to ≥95% precision on accepted codes via sweep against benchmark ground truth (Phase 3). Provisional development default: 0.75 for LOINC and RxNorm; UCUM uses deterministic exact-match lookup, not a threshold (see DR-7). DR-2: PENDING BENCHMARK, provisional value in effect."*

**Dependencies:** Depends on DR-7. Cannot be finalized before Phase 3.

---

## DR-3 — Verification Context Window Size

**Blocks:** Stage A/B/C input construction (§5 components 7–9); Phase 1 dataset generation (the M2-style examples must be generated with the same window definition used at inference); V6/V7 training.

**Options:**
- Fixed character window, e.g. ±200 chars around the evidence span (spec's placeholder).
- Sentence-boundary-aware window (evidence's own sentence + one sentence before/after).
- Paragraph-bounded window with a hard character cap as fallback for dense tabular text.

**Recommendation: paragraph-bounded window, capped at 500 characters, falling back to a fixed ±250-char window when no clear paragraph boundary exists (dense tables).**

**Reason:** A naive fixed-character window risks truncating exactly the clauses that carry negation and temporal status ("...discontinued two weeks ago" may sit in the sentence *after* the drug mention) — and negation/temporal handling is an explicit MUST failure-mode requirement (§23.1). Both ModernBERT-base and any 3–8B LLM candidate (DR-6) have ample context capacity for a few hundred tokens, so cost is not the binding constraint here; correctness of the context is. The fallback to a fixed window for tables exists because tabular text often has no real "paragraph" structure — a pure paragraph rule would misbehave there.

**Experiment before locking:** A small ablation on the existing (pre-correction) M2 examples or a quick regenerated subset: compare Stage B accuracy specifically on the negation/temporal-status hard-negative subset (§18.5, §20.1 `negation_mismatch`/`temporal_mismatch`) across ±100 char, ±250 char, and paragraph-bounded windows. This is cheap (no retraining needed if you reuse a frozen-probe ModernBERT for the ablation) and should run *before* regenerating the full v2 dataset, since the window choice is baked into every generated example.

**Final value for spec:** *"§7.6 context window: paragraph-bounded, max 500 characters; fallback to ±250 fixed-character window when no paragraph boundary is detected within that range. This definition MUST be used identically at both dataset-generation time and inference time. DR-3: RESOLVED pending the negation/temporal ablation confirming no regression versus the fixed-window baseline."*

**Dependencies:** Must be resolved before Phase 1 dataset regeneration (§18.4 fix). Independent of DR-1/DR-6/DR-7.

---

## DR-4 — Deterministic Extraction Confidence Threshold

**Blocks:** Extraction Router re-routing logic (§11 rule 2); E1–E4.

**Options:**
- Fixed default 0.7 (spec placeholder).
- Empirically swept per field_type/region-type against the corrected benchmark.
- A single threshold tuned to a recall floor rather than a balanced F1 point (i.e., bias toward escalating anything even slightly uncertain).

**Recommendation: empirically sweep {0.5, 0.6, 0.7, 0.8, 0.9}, but select the threshold biased toward the recall floor, not the F1-maximizing point — accept a higher escalation rate as the tradeoff.**

**Reason:** The system is offline/batch (§27) — an unnecessary escalation to GLiNER or the LLM costs compute time, not correctness, and compute is not scarce enough (T4 16GB, no latency requirement) to justify optimizing it away. Under-escalation, by contrast, means a wrong deterministic extraction silently passes through as if it were confident, which is the exact failure mode the router exists to prevent. So the threshold choice should explicitly trade a somewhat higher escalation rate for lower risk of silent bad extraction, capped only by a sanity bound on total escalation rate (e.g., don't let more than ~30% of structured regions escalate, or something has gone wrong upstream and the deterministic parser itself needs fixing, not the threshold).

**Experiment before locking:** Requires DR-1 (routing exists) and the corrected v2 benchmark (§18.4) to have ground-truth ready to sweep against. Sweep, report extraction F1 (post-escalation-resolution) and escalation rate per candidate threshold, lock the value on the recall-biased side of the curve subject to the ~30% escalation-rate sanity bound.

**Final value for spec:** *"§11 rule 2: deterministic-confidence re-routing threshold = [VALUE FROM SWEEP], chosen to bias toward escalation over silent low-confidence acceptance, subject to a ≤30% escalation-rate sanity bound per document type. Provisional development default: 0.75 (slightly above the spec's original 0.7 placeholder, reflecting the recall-biased policy) until the sweep completes. DR-4: PENDING BENCHMARK."*

**Dependencies:** Depends on DR-1 and on the Phase 1 corrected dataset (DR-11) existing.

---

## DR-5 — Stage B (ModernBERT/NLI) Escalation Threshold

**Blocks:** Verification cascade routing (§12); V8, V12 (risk-coverage analysis).

**Options:**
- Fixed default 0.6 (spec placeholder).
- Threshold chosen at the "knee" of the risk-coverage curve (§21.5) — the point where marginal accuracy gain per additional percent of claims escalated drops off sharply.
- Per-field-type thresholds.

**Recommendation: the risk-coverage knee, computed after V6 exists — this is the one decision that is *inherently* impossible to lock in Phase 0.**

**Reason:** Unlike DR-2/DR-4, this threshold's correct value is a direct empirical property of the trained Stage B model's confidence distribution — it cannot be reasoned about in the abstract, only measured. Locking a number now would be guessing, and the spec's own §21.5 (AURC / risk-coverage curve) exists specifically to answer this question with evidence rather than a priori choice. Per-field-type thresholds are tempting but should be deferred: start with one global threshold, and only fragment it per field_type in a later ablation (§22.2 V11) if the aggregate risk-coverage curve shows one field_type is systematically miscalibrated relative to the others.

**Experiment before locking:** This *is* the V12 experiment (§22.2). Sequence: retrain V6 on the corrected v2 dataset → apply calibration (DR-9) → compute the risk-coverage curve on the CV-validation split → separately verify the chosen knee-point threshold still behaves reasonably on the OOD split (§21.3/§21.5 both report CV and OOD) → lock. Do not use a single quick heuristic ("just use 0.6") as the final production value — the placeholder is for unblocking earlier development/unit testing only.

**Final value for spec:** *"§12 Stage B escalation threshold: to be set from the V12 risk-coverage knee-point analysis after V6 retraining and calibration (DR-9). Development/testing placeholder: 0.6. DR-5: MUST NOT be finalized before Phase 4 V12 completes — this is a required-empirical decision, not a Phase 0 decision, despite appearing in Appendix A."*

**Dependencies:** Depends on V6 completing (which itself depends on DR-3, DR-11), and on DR-9 (calibration) being applied first, since the threshold is defined on *calibrated* confidence, not raw model score.

---

## DR-6 — LLM Model + Serving Method

**Blocks:** LLM Narrative Extractor (§5 component 5), Stage C Judge (§5 component 9); transitively DR-13; E3/E4, V8/V9.

**Options:**
- ~3B instruction-tuned model (e.g., Phi-3.5-mini class), 4-bit quantized.
- ~7–8B instruction-tuned model (e.g., Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct class), 4-bit quantized.
- Hosted API model (no local serving).
- Serving stack: vLLM, llama.cpp/Ollama, or transformers + bitsandbytes.

**Recommendation: a 7–8B class model, 4-bit quantized, served via vLLM on the T4 16GB Colab instance for batch evaluation runs (llama.cpp as a fallback if vLLM setup proves too time-costly for the team).**

**Reason:** A 4-bit 7–8B model's weights occupy roughly 5–6GB, comfortably fitting the T4's 16GB with headroom for KV cache during batched inference across many benchmark documents — the 3B class is not actually necessary to fit the hardware, so choosing it would be trading away quality for no real resource benefit. The decisive factor is §7.4's hard requirement that `evidence_text` be an exact substring of the input: this is fundamentally a precision/instruction-following property, and 7–8B class instruction-tuned models measurably outperform 3B class models on this kind of constrained, faithful-extraction behavior. Given the local 3050 (6GB) cannot run this size comfortably, all LLM work — extraction and judging — should be planned as **T4/Colab-only**, batch-processed, never assumed to run on the laptop GPU. vLLM is preferred for the Phase 6 full-benchmark evaluation runs because of its throughput advantage on repeated batch calls and its native support for grammar/JSON-constrained decoding (feeds directly into DR-13); llama.cpp/Ollama is an acceptable lower-setup-risk fallback if the team's time budget can't absorb vLLM's setup overhead.

**Experiment before locking:** Run a small, cheap benchmark (a few hundred narrative-extraction examples and a few hundred verification-judge examples from the Phase 1 benchmark) comparing at least one 3B candidate and one 7–8B candidate on: VRAM footprint, latency per call, and JSON-schema-conformance / evidence-substring-validity rate. This should take days, not weeks. Lock the model that clears a substring-validity bar (recommend ≥95% valid outputs before any retry logic) at acceptable latency for your total benchmark size.

**Final value for spec:** *"§13.2: LLM = [Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct — pick the one that wins the substring-validity benchmark], 4-bit quantized (bitsandbytes or GGUF depending on serving stack), served via vLLM on Colab T4 16GB (llama.cpp fallback). Used only for narrative extraction (§7.4), Stage C judging (§12), and the V9 comparison baseline. Local 3050 is not used for LLM inference. DR-6: PENDING a same-week benchmark; both candidates are acceptable fallbacks of each other if the winner proves unstable later."*

**Dependencies:** None blocking it — can and should be resolved early in Phase 0/1, in parallel with dataset work. Blocks DR-13.

---

## DR-7 — Terminology Retrieval Embedding Model + Scoring Combination

**Blocks:** Terminology Normalizer (§5 component 6); DR-2; §21.6.

**Options:**
- SapBERT (biomedical entity-linking-specialized embeddings).
- BioLORD (biomedical semantic embeddings, broader training objective than pure entity linking).
- A general-purpose sentence-embedding model as a cheap fallback/baseline.
- Combination method: fixed weighted sum of lexical + semantic score, vs. a small trained reranker (e.g., logistic regression over the two signals).

**Recommendation: SapBERT as the semantic component; a fixed, grid-searched weighted sum (not a trained reranker) as the combination rule; UCUM handled by deterministic exact-match lookup, not retrieval at all.**

**Reason:** SapBERT is trained specifically for biomedical synonym/entity-linking tasks (mapping surface-form mentions to canonical UMLS-family concepts), which is a closer match to "map extracted drug/test name string to RxNorm/LOINC canonical concept" than BioLORD's broader semantic-similarity training objective or a general sentence embedding model with no biomedical specialization. A trained reranker is not justified at this project's scale — it would need its own labeled training set and its own train/val split discipline, adding a whole extra sub-pipeline for a gain that a grid-searched fixed weight is likely to capture most of, given the retrieval corpus (LOINC/RxNorm) is a closed, well-structured concept space rather than open-domain retrieval. UCUM being handled deterministically (not through the embedding retrieval path at all) directly follows the spec's own "deterministic first" principle (§14.2) — units are a small, closed, enumerable vocabulary and do not benefit from semantic retrieval.

**Experiment before locking:** Once the Phase 1 benchmark's ground-truth LOINC/RxNorm codes exist, run top-1/top-5 retrieval accuracy for SapBERT vs. BioLORD vs. a general fallback (§21.6). Independently, grid-search the lexical/semantic weight (e.g., 0.1 increments from 0.0 to 1.0) against top-1 accuracy on the same ground truth. Lock both the embedding model and the weight together as one combined result.

**Final value for spec:** *"§14.2: semantic component = SapBERT (fallback candidate: BioLORD, only if SapBERT underperforms in the retrieval benchmark). Lexical component = BM25 or edit-distance shortlist (implementer's choice, not architecturally significant). Combination = fixed weighted sum, weight grid-searched against ground-truth top-1 accuracy (Phase 3). UCUM = deterministic lookup table, no retrieval. DR-7: PENDING BENCHMARK, SapBERT is the strong prior."*

**Dependencies:** Must precede DR-2. Independent of DR-1/DR-6.

---

## DR-8 — MedicationStatement vs. MedicationRequest Default Rule

**Blocks:** FHIR Generator (§5 component 10); §15.1.

**Options:**
- Rule based purely on `temporal_status` (current/historical/discontinued → `MedicationStatement`; planned → `MedicationRequest`).
- Rule based purely on source document type (a prescription/order document → `MedicationRequest`; a narrative note/discharge summary → `MedicationStatement`), ignoring `temporal_status`.
- A hybrid: document type as primary signal, `temporal_status: planned` as an override.

**Recommendation: hybrid — document type is the primary signal; `temporal_status: planned` overrides to `MedicationRequest` regardless of document type; `temporal_status: unspecified` on a non-prescription document defaults to `MedicationStatement`.**

**Reason:** Document type captures the dominant real-world case correctly (a prescription document *is* an order; a discharge-summary medication list *is* a statement of fact about use), but a discharge summary's own "Plan" section can describe a future order ("start metoprolol on discharge") embedded inside an otherwise MedicationStatement-typical document — the override handles exactly this case without needing a third resource-routing dimension. This is a pure mapping-logic decision with no statistical uncertainty in it; it does not need empirical tuning, only an unambiguous, written rule so no two team members (or the AI agent) implement it differently.

**Experiment before locking:** None required. This is lockable immediately.

**Final value for spec:** *"§15.1: `MedicationRequest` if source document type is prescription/order OR `temporal_status == planned`; `MedicationStatement` otherwise (including `temporal_status == unspecified` on non-prescription documents). DR-8: RESOLVED."*

**Dependencies:** None.

---

## DR-9 — Calibration Method

**Blocks:** Calibration Module (§5 component 12); §17; DR-5 (threshold is defined on calibrated confidence).

**Options:**
- Temperature scaling (single scalar per model, standard for multi-class softmax calibration).
- Isotonic regression (more flexible, needs more calibration data).
- Platt scaling (fundamentally binary-oriented, awkward fit for a 3-class problem).

**Recommendation: temperature scaling as the default; isotonic regression only as a documented fallback if temperature scaling's OOD calibration is poor.**

**Reason:** The calibration split is carved from CV documents only (§19.5) and, given your project's scale (a few thousand examples in the whole M2 dataset, and the calibration split is a further carve-out of that), is likely to be modest in size. Isotonic regression's extra flexibility is more prone to overfitting the calibration curve itself on a small split, which is a particular risk here since §17.3 requires you to also check whether calibration *transfers* to the OOD template split — an overfit calibration map is exactly the kind of thing that will look fine in-distribution and fail on OOD. Temperature scaling is a single scalar, trivially cheap, and is the standard first choice in the calibration literature for exactly this scenario (post-hoc calibration of a fine-tuned transformer classifier). Platt scaling is excluded because your problem is inherently 3-class, not binary, and Platt scaling would need an artificial one-vs-rest decomposition with no real benefit over temperature scaling's native multi-class form.

**Experiment before locking:** After V6 retrains, fit temperature scaling on the calibration split, report ECE/Brier on both CV-validation and OOD. If OOD ECE is materially worse than CV-validation ECE, run isotonic regression as a documented comparison before finalizing which method ships as default — but temperature scaling is the starting hypothesis, not a coin flip between the two.

**Final value for spec:** *"§17.2: calibration method = temperature scaling (single scalar, fit per model version on the CV-carved calibration split). Isotonic regression is evaluated only as a fallback comparison if OOD ECE under temperature scaling exceeds [team-set tolerance, suggest 2x the CV-validation ECE] as a trigger condition. DR-9: RESOLVED as temperature-scaling-by-default; fallback trigger condition to be checked empirically in Phase 4."*

**Dependencies:** Depends on V6 existing. Must be resolved before DR-5's threshold can be finalized (threshold is on calibrated, not raw, confidence).

---

## DR-10 — Real-Data Dataset Choice

**Blocks:** Phase 6 real-data validation slice (§18.3); does not block anything in Phases 1–5.

**Options:**
- MIMIC-IV / MIMIC-IV-Note (PhysioNet credentialing, CITI training, DUA — approval timeline unpredictable, often weeks).
- n2c2 2018 medication/ADE track (medication mentions, dosage, frequency, adverse-drug-event relations already annotated).
- n2c2 2014 de-identification/heart-disease track.
- i2b2 legacy releases.

**Recommendation: n2c2 2018 (medication/ADE) as the primary and only initial target; do not pursue MIMIC in parallel unless n2c2 access is confirmed with time to spare.**

**Reason:** n2c2 2018's annotation focus (medication mentions, dosage, frequency, ADE relations) is a direct content match to this project's medication-verification focus, more so than MIMIC-IV-Note's general clinical notes or MIMIC-IV-FHIR's structured-record focus. More importantly, n2c2's access process (data use agreement through the DBMI portal) has historically had a more predictable and shorter approval timeline than PhysioNet's full credentialing pipeline for MIMIC — and approval-timeline risk is explicitly the single biggest external scheduling risk in a fixed 2–3 month project. Splitting effort across both datasets' access processes is a distraction; get one working first.

**Experiment before locking:** None — this is a selection decision, not an empirical one. The only "experiment" is the access-request process itself, which is a timeline action, not a technical benchmark.

**Final value for spec:** *"§18.3: primary real-data target = n2c2 2018 medication/ADE track. MIMIC-IV/-Note is explicitly deferred, not pursued in parallel, unless n2c2 access is confirmed early with clear time margin remaining. DR-10: RESOLVED — action item: submit n2c2 DUA request in the first week of Phase 0, independent of where this decision sits in the technical dependency order."*

**Dependencies:** None technically, but time-critical: initiate the request immediately regardless of sequencing below.

---

## DR-11 — Class-Balance Floor

**Blocks:** Dataset generation CI check (§18.4, Phase 1); directly addresses the discovered v1 flaw.

**Options:**
- A pure percentage floor (spec's own suggestion: 5% minimum per class per field_type).
- A higher percentage (10%).
- An absolute-count floor instead of, or in addition to, a percentage.

**Recommendation: combined rule — minimum 10% representation OR minimum 30 absolute examples (whichever is larger) for `SUPPORTED` and `DISCREPANCY` per field_type; minimum 15 absolute examples for `INSUFFICIENT_EVIDENCE` per field_type where that class is semantically applicable to the field.**

**Reason:** A pure percentage floor is fragile for field types that have few total examples to begin with — your own existing counts show `med.route` and `med.frequency` each at 174 total examples (87/87) in the v1-corrected numbers; a 5% floor there is under 9 examples, too few to estimate anything reliably even if the *proportion* looks balanced. The percentage-or-absolute-whichever-is-larger rule protects small field types automatically without needing a separate manual carve-out rule per field. Not every field type needs a forced `INSUFFICIENT_EVIDENCE` case (e.g., some fields may not have a coherent "evidence completely absent" scenario) — the rule should require it only where it's meaningful, matching the spec's own §18.4 wording, rather than force synthetic nonsense examples just to hit a quota.

**Experiment before locking:** None required — this is a dataset-generation policy, not an empirical question. Once implemented, the generation code's self-reported per-field/per-class count table (already required by §18.4) should be checked against this floor as a sanity pass, not as a search process.

**Final value for spec:** *"§18.4: class-balance floor = max(10% of field_type's total examples, 30 absolute examples) for SUPPORTED and DISCREPANCY; minimum 15 absolute examples for INSUFFICIENT_EVIDENCE where applicable to that field_type. Automated CI check enforces this on every dataset regeneration. DR-11: RESOLVED."*

**Dependencies:** None. Lock immediately, first in the sequence — it gates all of Phase 1.

---

## DR-12 — Evidence-Linking Metric / Overlap Threshold

**Blocks:** Evaluation Harness (§5 component 13); §21.2.

**Options:**
- Character-offset exact match.
- Bounding-box IoU with a threshold (commonly ≥0.5, borrowed from object-detection convention).
- Both required simultaneously.

**Recommendation: character-offset overlap F1 (precision/recall/F1 over character sets, SQuAD-style extractive-QA scoring) as the primary/headline metric; bounding-box IoU ≥0.5 reported only as a secondary diagnostic metric, not the headline number.**

**Reason:** Character offsets are the actual ground-truth identity key used throughout the pipeline (Evidence schema, §9, is keyed on `char_start`/`char_end`; bounding boxes are a derived provenance artifact obtained by mapping OCR tokens to those same offsets). Verification consumes text, not geometry, so evidence-linking quality should be measured on the representation that actually drives downstream correctness. Bounding-box IoU is still worth reporting, but as a way to specifically diagnose OCR-token-to-bbox mapping errors (a distinct failure mode from evidence-*identification* errors) — conflating the two into one metric would obscure which of "found the right text" vs. "drew the right box" failed when a case goes wrong.

**Experiment before locking:** None required — this is a metric-definition choice, not an empirical one.

**Final value for spec:** *"§21.2: primary evidence-linking metric = character-offset precision/recall/F1 (extractive-QA style). Secondary/diagnostic metric = bounding-box IoU ≥0.5. Both reported per experiment, but only the character-offset F1 is treated as headline. DR-12: RESOLVED."*

**Dependencies:** None. Lock immediately.

---

## DR-13 — LLM Structured-Output Enforcement Mechanism *(gap found — not in original Appendix A)*

**Note:** §7.4 and §13.3 both state the LLM extractor/judge MUST use "structured-output enforcement," but no decision ID was assigned to *which mechanism* satisfies that MUST. This is a real blocking decision for implementation and is added here as DR-13.

**Blocks:** LLM Narrative Extractor (§5 component 5); Stage C Judge (§5 component 9).

**Options:**
- Grammar/JSON-schema-constrained decoding at generation time (e.g., via `outlines`, `lm-format-enforcer`, or a serving framework's native structured-output support).
- Prompt + parse + bounded-retry: ask for JSON in the prompt, parse the response, and on failure (invalid JSON or failed evidence-substring check per §7.4) retry up to a fixed number of times before dropping and logging as an extraction error.

**Recommendation: grammar-constrained decoding as the primary mechanism (via whichever library the chosen serving stack from DR-6 supports natively — vLLM has built-in structured-output support compatible with `outlines`-style grammars); prompt+parse+bounded-retry (max 2 retries) as the fallback only if constrained decoding is not feasible with the chosen serving stack within the team's time budget.**

**Reason:** Constrained decoding guarantees syntactic JSON validity *at generation time*, which is strictly stronger than hoping the model complies and catching failures after the fact — it removes an entire class of parsing failures before they happen. It does not, by itself, guarantee the *content* is correct (the model can still constrain-decode a plausible-looking but wrong `evidence_text`), which is exactly why the post-hoc substring-verification check in §7.4 must remain regardless of which mechanism is chosen — DR-13 does not replace that check, it reduces how often the check has something to catch.

**Experiment before locking:** Once DR-6's winning model/serving stack is chosen, run a small sample (order of 100–200 narrative-extraction cases) comparing schema-conformance rate and evidence-substring-validity rate with vs. without constrained decoding, to confirm the grammar constraint isn't itself degrading extraction quality (an over-restrictive grammar occasionally causes models to produce syntactically valid but semantically degenerate output). This is a fast check, not a separate research experiment — fold it into the same benchmarking pass as DR-6.

**Final value for spec:** *"§7.4/§13.3: structured-output mechanism = grammar/JSON-schema-constrained decoding via [library determined by DR-6's serving stack], with the §7.4 post-hoc evidence-substring check still mandatory regardless. Fallback = prompt+parse+bounded-retry (max 2 retries) if constrained decoding proves infeasible with the chosen stack. DR-13 (new): RESOLVED in direction, pending the same benchmarking pass as DR-6."*

**Dependencies:** Depends entirely on DR-6 (serving stack choice determines which constrained-decoding library is available).

---

# A. Ordered Decision Sequence

Decisions that gate Phase 1 dataset generation must resolve first; decisions requiring a trained model must resolve last; everything else can be parallelized across team members.

1. **DR-11** (class-balance floor) — gates Phase 1 dataset regeneration. Lock immediately.
2. **DR-12** (evidence-linking metric) — no dependencies. Lock immediately.
3. **DR-8** (MedicationStatement/Request rule) — no dependencies. Lock immediately.
4. **DR-10** (real-data dataset choice) — no technical dependencies; initiate the n2c2 access request the same day this is locked, regardless of where it sits in this sequence.
5. **DR-1** (region/layout classification) — no dependencies. Lock immediately (light validation once Phase 1 benchmark exists).
6. **DR-3** (verification context window) — must resolve before Phase 1 dataset regeneration finalizes (window definition is baked into generated examples). Run the negation/temporal mini-ablation before locking.
7. **DR-6** (LLM model + serving) — no dependencies on 1–6; run the model-comparison mini-benchmark in parallel with Phase 1 dataset work.
8. **DR-13** (structured-output mechanism) — depends on 7.
9. **DR-7** (terminology embedding model + scoring) — depends on Phase 1 benchmark existing (for ground-truth codes to benchmark against); otherwise independent of 1–8.
10. **DR-2** (code_confidence threshold) — depends on 9.
11. **DR-4** (deterministic extraction threshold) — depends on 5 and on the corrected (DR-11) Phase 1 dataset existing.
12. **DR-9** (calibration method) — depends on V6 (Phase 4 ModernBERT retrain, itself depending on 6, 1).
13. **DR-5** (Stage B escalation threshold) — depends on 12, and on the V12 risk-coverage analysis. Locked last, in Phase 4.

---

# B. Final Recommended Values (Summary Table)

| ID | Decision | Recommended Value |
|---|---|---|
| DR-1 | Region classification | Rule-based heuristic (regex + column alignment); PP-Structure optional supplement |
| DR-2 | code_confidence threshold | Per-code-system, swept to ≥95% precision on accepted codes; dev default 0.75 |
| DR-3 | Verification context window | Paragraph-bounded, cap 500 chars; fallback ±250 chars fixed window |
| DR-4 | Deterministic extraction threshold | Swept {0.5–0.9}, recall-biased choice, ≤30% escalation-rate bound; dev default 0.75 |
| DR-5 | Stage B escalation threshold | Risk-coverage knee-point, computed post-V6+calibration; dev default 0.6 |
| DR-6 | LLM model + serving | 7–8B instruction-tuned (Qwen2.5-7B-Instruct or Llama-3.1-8B-Instruct), 4-bit, vLLM on T4 |
| DR-7 | Terminology embedding + scoring | SapBERT + fixed weighted sum (grid-searched weight); UCUM = deterministic lookup |
| DR-8 | MedicationStatement/Request rule | Document-type primary, `temporal_status: planned` overrides to MedicationRequest |
| DR-9 | Calibration method | Temperature scaling by default; isotonic regression fallback if OOD ECE degrades |
| DR-10 | Real-data dataset | n2c2 2018 medication/ADE track; MIMIC deferred |
| DR-11 | Class-balance floor | max(10%, 30 absolute) for SUPPORTED/DISCREPANCY; 15 absolute for INSUFFICIENT_EVIDENCE |
| DR-12 | Evidence-linking metric | Character-offset F1 (primary); bbox IoU ≥0.5 (secondary/diagnostic) |
| DR-13 | LLM structured-output mechanism | Grammar-constrained decoding via serving stack's native support; bounded-retry fallback |

---

# C. Decisions Requiring Empirical Benchmarking Before Final Lock

- **DR-2** — code_confidence threshold sweep (depends on DR-7 first)
- **DR-3** — negation/temporal-status window ablation
- **DR-4** — deterministic threshold sweep (depends on DR-1 and corrected dataset)
- **DR-5** — risk-coverage knee-point (depends on trained + calibrated V6; last decision to close)
- **DR-6** — 3B vs. 7–8B model comparison (VRAM, latency, substring-validity rate)
- **DR-7** — SapBERT vs. BioLORD vs. fallback retrieval accuracy + weight grid search
- **DR-9** — temperature scaling vs. isotonic regression, CV-vs-OOD ECE comparison (depends on trained V6)
- **DR-13** — constrained-decoding quality check (depends on DR-6's stack)

All eight of these have a stated provisional/development default above so that implementation is never blocked waiting on a benchmark — the placeholder unblocks coding; the benchmark result is what gets written into the final spec value.

---

# D. Decisions That Can Be Locked Immediately (No Benchmarking Needed)

- **DR-1** — region classification approach (design choice, constrained by §3/§28's ban on custom-trained layout models)
- **DR-8** — MedicationStatement/Request rule (pure mapping logic)
- **DR-10** — real-data dataset target (selection based on annotation-content fit + access-timeline risk, not a technical benchmark)
- **DR-11** — class-balance floor (statistical hygiene policy)
- **DR-12** — evidence-linking metric definition (metric-design choice)

---

# E. Files to Create/Update After These Decisions Are Approved

1. **`PROJECT_SPEC.md`** — update Appendix A's Open Decision Log table: mark each DR row's Status and link to its `docs/decisions/DEC-NNN-*.md` file (see #2); add a new Appendix A row for DR-13 with a note that it was found during Phase 0 review rather than originally scoped, and correspondingly touch §7.4/§13.3 to reference it explicitly.
2. **`docs/decisions/DEC-001-region-classification.md`** through **`docs/decisions/DEC-013-structured-output-mechanism.md`** — one file per decision (DR-1 → DEC-001, ... DR-13 → DEC-013), each containing: final value, date, options considered, rationale (may be copied from this package), and — for the eight in §C — a placeholder section to be filled in with the actual benchmark result once run, with a link to the corresponding `experiments/` directory.
3. **`experiments/DR2-code-confidence-sweep/`**, **`experiments/DR3-context-window-ablation/`**, **`experiments/DR4-deterministic-threshold-sweep/`**, **`experiments/DR6-llm-model-comparison/`**, **`experiments/DR7-terminology-retrieval-benchmark/`**, **`experiments/DR9-calibration-comparison/`**, **`experiments/DR13-structured-output-check/`** — one directory per empirical decision in §C requiring its own config/results/`REPORT.md`, consistent with the spec's own experiment-directory convention (§22). (DR-5's benchmarking is already covered by the existing `experiments/V12-*` directory required by the spec — no new directory needed for it.)
4. **`benchmark/config/class_balance_floor.yaml`** (or equivalent config file consumed by the Phase 1 dataset generator) — encodes DR-11's floor rule so the automated CI check (§18.4/§24) reads it from config rather than a hardcoded constant.
5. **`benchmark/config/context_window.yaml`** — encodes DR-3's window definition so dataset generation and inference-time verification both read the identical rule from one place, preventing train/inference skew.
6. **`fhir_mapping/medication_resource_rule.md`** (or inline docstring reference to DEC-008, implementer's choice of location) — the DR-8 rule table, since it's referenced by both the FHIR generator implementation and its tests (§24).
7. **`n2c2_access/REQUEST_STATUS.md`** — a simple running log of the DR-10 data-use-agreement request status, since its approval timeline is the project's biggest external scheduling risk and should be visibly tracked, not buried in chat history.
