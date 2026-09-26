# Decision DEC-010: Real-Data Dataset Target and Strategy

## Status
LOCKED

## Context
PROJECT_SPEC §18.3 specifies an optional/feasibility-gated real-data qualitative validation slice (Phase 6). External data access credentialing introduces significant scheduling risk.

## Decision
Designate the **n2c2 2018 Track 2 (Medication Extraction & ADE)** dataset as the primary and sole initial real-data target. MIMIC-IV and MIMIC-IV-Note are explicitly deferred and will not be pursued in parallel. The n2c2 slice will be used strictly for qualitative error analysis and failure cataloguing (§23), not quantitative retraining.

## Alternatives Considered
1. MIMIC-IV / MIMIC-IV-Note — deferred due to lengthy credentialing (CITI training, PhysioNet verification, DUA review).
2. n2c2 2014 De-identification / Heart Disease — rejected as less aligned with medication verification.
3. n2c2 2018 Medication Extraction (Selected) — annotations directly match drug mention, dosage, route, frequency extraction.

## Rationale
n2c2 data use agreements through the DBMI portal historically feature shorter, more predictable approval timelines than PhysioNet. Concentrating on a single target mitigates administrative overhead.

## Consequences
- Requires immediate creation of `n2c2_access/REQUEST_STATUS.md` in Phase 0.
- If approval is delayed, Phase 6 will report its absence as a documented limitation per §18.3.

## Validation / Experiment
Phase 6 qualitative error cataloguing run if access is approved.

## Related Specification Sections
- PROJECT_SPEC §2.1, §18.3, §23, §25 Phase 0, §26 Phase 6
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-10
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-11
