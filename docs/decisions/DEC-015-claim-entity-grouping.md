# Decision DEC-015: Claim Entity Grouping Identifier (`group_id`)

## Status
LOCKED

## Context
AUDIT-02 identified that the atomic `Claim` schema (§8) lacked an entity grouping key. When a document contains multiple clinical entities (e.g. multiple medications or multiple lab rows), the downstream FHIR generator cannot deterministically group atomic claims (e.g. which `med.dose` belongs to which `med.name`).

## Decision
Amend the `Claim` schema (§8.1) to include:
```json
"group_id": "string (uuid) or null"
```
Extraction components MUST assign an identical UUID or entity-mention identifier to `group_id` for all atomic claims originating from the same clinical entity instance (e.g., the same lab test table row or the same medication order). For isolated or un-grouped claims, `group_id` is `null`.

## Alternatives Considered
1. Leaving Claim flat and using fuzzy line-number heuristics during FHIR generation — rejected as fragile, non-deterministic, and prone to cross-order contamination.
2. Embedding claims inside nested hierarchical objects — rejected because verification models (ModernBERT) operate on atomic claims.
3. Explicit foreign grouping key `group_id` on atomic claims (Selected).

## Rationale
Preserves atomic claim independence for the verification cascade while enabling deterministic reconstruction of multi-field composite FHIR resources.

## Consequences
- `group_id` added to `schemas/claim.json` and Pydantic models.
- Extractors populate `group_id` during extraction.
- FHIR mapper groups claims by `group_id` before emitting resources.

## Validation / Experiment
Unit test in `tests/test_claim_grouping.py` verifying multi-order documents produce distinct `group_id` clusters that map to distinct FHIR resources.

## Related Specification Sections
- PROJECT_SPEC §8.1, §15.1, §16
- PHASE0_ARCHITECTURE_RESOLUTION AUDIT-02, Section 5
