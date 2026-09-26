# Decision DEC-017: Canonical Field Vocabulary and Feature Dimensions

## Status
LOCKED

## Context
AUDIT-04 identified that `PROJECT_SPEC.md` §8.2 defines 10 field types, whereas legacy code in `src/clindoc/m2/schema.py` defined 13 field types (using `lab.ref_range`, and adding `lab.loinc_code`, `med.dose_unit`, `med.quantity`), with `AUX_DIM` hardcoded to 21 in `features.py`.

## Decision
1. Lock the canonical field vocabulary to exactly the **10 field types** in PROJECT_SPEC §8.2:
   `lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`, `lab.test_name`, `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`.
2. Standardize on `lab.reference_range` (deprecate `lab.ref_range`).
3. Deprecate and remove obsolete types `lab.loinc_code`, `med.dose_unit`, and `med.quantity` from active schemas.
4. Update the auxiliary feature dimension in `src/clindoc/m2/features.py`:
   $$\text{AUX\_DIM} = 1 \text{ (OCR conf)} + 10 \text{ (field types)} + 7 \text{ (doc templates)} = 18$$
   $$\text{FUSION\_DIM} = 768 + 64 = 832 \text{ (unchanged)}$$

## Alternatives Considered
1. Expanding spec to 13 fields — rejected because LOINC codes are normalizer outputs (not raw extraction claims), and dose units are handled by `lab.unit` and numeric parsing.
2. Keeping 10 in spec but 13 in code — rejected as a direct violation of document-driven development.
3. Aligning code to canonical 10 fields and resizing `AUX_DIM` to 18 (Selected).

## Rationale
Eliminates redundant fields and guarantees that model projection layers match the authoritative specification.

## Consequences
- `FieldType` enum updated to 10 canonical values.
- `AUX_DIM` resized to 18 prior to retraining ModernBERT in Phase 4.

## Validation / Experiment
Unit test in `tests/test_m2_model.py` verifying auxiliary feature batch encoding produces tensor shape `(batch, 18)`.

## Related Specification Sections
- PROJECT_SPEC §8.2, §22.2 (V7)
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-04, Section 6
