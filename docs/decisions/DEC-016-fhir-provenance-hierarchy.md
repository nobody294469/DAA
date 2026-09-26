# Decision DEC-016: Two-Tier FHIR Provenance Extension Hierarchy

## Status
LOCKED

## Context
AUDIT-03 identified that composite FHIR resources (`Observation`, `MedicationStatement`) combine multiple claims that may have divergent verification verdicts (e.g. value is `SUPPORTED`, but unit is `DISCREPANCY`). A single resource-level provenance extension assuming one `claimId` cannot represent this complexity.

## Decision
Establish a formal two-tier provenance extension hierarchy:
1. **Resource-Level Provenance:** The top-level resource carries a provenance extension linking to the anchor/primary entity claim (e.g., `lab.value` for `Observation`, `med.name` for `MedicationStatement`) and the summary verification verdict.
2. **Element-Level Sub-Extensions:** Constituent elements within composite resources (`Observation.valueQuantity`, `Observation.interpretation`, `Observation.referenceRange`, `Medication*.dosage[0].doseQuantity`, `Medication*.dosage[0].timing`) MUST carry element-level provenance extensions linking to their specific atomic `claim_id` and `verification_id`.

## Alternatives Considered
1. Single forced verdict at resource level (e.g. any discrepancy makes whole resource discrepancy) — rejected because element-level auditability is lost.
2. Emitting completely fragmented, non-standard FHIR resources — rejected because standard FHIR bundles require coherent resources.
3. Two-tier resource and element-level extension hierarchy (Selected).

## Rationale
Ensures 100% compliance with PROJECT_SPEC §4.2 ("Every FHIR element MUST be attributable to exactly one claim ID and one verification result ID") without breaking standard FHIR R4B StructureDefinitions.

## Consequences
- Canonical URL locked to `http://clindoc.org/fhir/StructureDefinition/claim-provenance`.
- Audit report (§7.8) can be verified directly against both resource and element levels.

## Validation / Experiment
CI Provenance Completeness Test in Phase 5 verifying every constituent element's extension resolves to its atomic claim and verification record.

## Related Specification Sections
- PROJECT_SPEC §4.2, §15.1, §16, §21.4
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-03, Section 11
