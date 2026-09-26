# Decision DEC-019: Verification Field Tiering & Verification Cascade Routing

## Status
LOCKED

## Context
AUDIT-08 identified that the M2 v2 dataset actively generated and evaluated only 6 field types (`lab.value`, `lab.flag`, `med.name`, `med.dose`, `med.frequency`, `med.route`), leaving 4 canonical field types (`lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`) unrepresented. It needed to be formally determined whether all 10 canonical field types require learned Stage B/C neural verification or if deterministic fields should be partitioned into a deterministic verification stage.

## Decision
Establish an explicit two-tier verification architecture partitioning the 10 canonical field types:

1. **Tier 1 (Learned Verification Cascade — Stages A, B, and C):**
   - Fields characterized by high semantic variability, numeric thresholds, OCR distortion sensitivity, or critical clinical impact:
     - `lab.value`
     - `lab.flag`
     - `med.name`
     - `med.dose`
     - `med.frequency`
     - `med.route`
   - These 6 fields are trained, calibrated, and evaluated in the learned neural verification cascade (Stage B ModernBERT and Stage C LLM Judge when escalated).

2. **Tier 2 (Deterministic Verification — Stage A Only):**
   - Fields governed by exact string matching, standardized unit vocabularies (e.g. UCUM lookup tables), or literal document interval tokens:
     - `lab.unit`
     - `lab.reference_range`
     - `lab.test_name`
     - `med.duration`
   - These 4 fields are resolved definitively at Stage A using deterministic rule-based comparison. They bypass Stage B and Stage C entirely unless a normalization ambiguity explicitly requires escalation.

3. **INSUFFICIENT_EVIDENCE Policy:**
   - Reaffirm DR-11 class balance rules. For Tier 1 fields, `INSUFFICIENT_EVIDENCE` is required where semantically meaningful (unobserved tests, unprescribed medications, OCR-destroyed values/doses). It is not synthetically forced onto closed categorical flags where "missing" collapses to unobserved values.

## Alternatives Considered
1. Training 10-field ModernBERT classifier covering all fields — rejected because exact unit and reference interval verification is more reliably solved deterministically without neural classification noise.
2. Omitting the 4 fields from verification entirely — rejected because all extracted claims must be verified prior to FHIR mapping.
3. Two-tier verification partitioning (Selected).

## Rationale
Partitioning verification into learned and deterministic tiers maximizes reliability, preserves computational efficiency on Colab T4, and aligns model capacity with the true clinical risk surfaces.

## Consequences
- M2 ModernBERT dataset generation and fine-tuning focus rigorously on the 6 Tier 1 fields with balanced classes (DR-11).
- Stage A deterministic verifier implements unit and range comparison rules for Tier 2 fields.
- Verification router directs Tier 1 claims to Stage B and Tier 2 claims directly to Stage A resolution.

## Validation / Experiment
- Unit tests verifying Stage A correctly evaluates Tier 2 fields without invoking ModernBERT.
- Phase 4 verification experiments (V1–V8, V10, V12) evaluating Tier 1 fields across CV and OOD splits.

## Related Specification Sections
- PROJECT_SPEC §8.2, §12.1, §18.4
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-11
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-08, Section 4
