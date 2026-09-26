# Decision DEC-008: MedicationStatement vs. MedicationRequest Mapping Rule

## Status
LOCKED

## Context
PROJECT_SPEC §15.1 requires generating FHIR R4B medication resources. A deterministic rule is needed to map medication claims to either `MedicationStatement` (statement of use) or `MedicationRequest` (prescription/order).

## Decision
Apply a hybrid mapping rule:
1. `MedicationRequest` is emitted if the source document type is a prescription or order form (`clinic_rx_pad`, `mar_order_sheet`, `pharmacy_label`) OR if `temporal_status == "planned"`.
2. `MedicationStatement` is emitted for all other document types (including discharge summaries and narrative notes) where `temporal_status` is `"current"`, `"historical"`, `"discontinued"`, or `"unspecified"`.

## Alternatives Considered
1. Pure `temporal_status` rule — rejected because prescription forms often omit explicit temporal tags.
2. Pure document-type rule — rejected because discharge summary "Plan" sections can describe planned future outpatient orders.
3. Hybrid rule with temporal override (Selected).

## Rationale
Captures dominant clinical workflows: prescriptions are legally orders (`MedicationRequest`), while reconciled medication lists in discharge notes are statements of use (`MedicationStatement`), with explicit plan statements correctly routed to orders.

## Consequences
- Deterministic, zero-ambiguity routing for the Phase 5 FHIR generator.
- Tested cleanly via rule-based unit tests without statistical uncertainty.

## Validation / Experiment
Unit tests in `tests/test_fhir_mapping.py` testing mapping across document templates and temporal statuses.

## Related Specification Sections
- PROJECT_SPEC §5 Component 10, §15.1, §21.4
- PHASE0_DECISION_RESOLUTION_PACKAGE DR-8
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-10
