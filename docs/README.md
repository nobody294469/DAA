# Documentation Index & Architecture Map

This repository is strictly **document-driven**. Architectural intent, data contracts, and design resolutions govern all implementation code.

---

## 1. Authoritative Core Documents

| Document | Authority Tier | Role & Scope |
|---|---|---|
| [`PROJECT_SPEC.md`](file:///d:/daa/PROJECT_SPEC.md) | **Tier 1 (Master Spec)** | The authoritative specification for the multimodal clinical document-to-EHR conversion and verification pipeline. Defines objectives, input/output contracts, component responsibilities, schemas, metrics, and acceptance criteria. |
| [`PHASE0_DECISION_RESOLUTION_PACKAGE.md`](file:///d:/daa/PHASE0_DECISION_RESOLUTION_PACKAGE.md) | **Tier 2 (Decision Package)** | Initial Phase 0 decision package resolving decisions DR-1 through DR-13 with technical rationales, options considered, and dependency ordering. |
| [`PHASE0_ARCHITECTURE_RESOLUTION.md`](file:///d:/daa/PHASE0_ARCHITECTURE_RESOLUTION.md) | **Tier 3 (Audit Resolution)** | The binding architectural resolution that reconciled audit findings AUDIT-01 through AUDIT-20, established decisions DEC-014 through DEC-019, amended PROJECT_SPEC.md, and defined the Phase 0 prerequisite checklist. |

---

## 2. Machine-Readable Specifications & Scaffolding

| Directory / Location | Role & Contents |
|---|---|
| [`docs/decisions/`](file:///d:/daa/docs/decisions/) | Formal Architecture Decision Records (**DEC-001** through **DEC-019**). Distinguishes **LOCKED** architectural policies from **EMPIRICALLY OPEN** parameters with provisional development defaults. |
| [`schemas/`](file:///d:/daa/schemas/) | Machine-readable JSON Schema contracts (Draft 2020-12) for core system interfaces: `document_text.json`, `routing_decision.json`, `claim.json` (with `group_id`), `evidence.json`, `verification_result.json`, and `audit_report.json`. |
| [`configs/`](file:///d:/daa/configs/) | Repository-wide configuration files: `class_balance_floor.yaml` (DR-11 locked data quality floors) and `context_window.yaml` (DR-3 context window defaults). |
| [`experiments/`](file:///d:/daa/experiments/) | Scaffolding directory structure for Extraction experiments (`E1`–`E5`), Verification experiments (`V1`–`V13`), and Empirical Decision experiments (`DR2`, `DR3`, `DR4`, `DR5`, `DR6`, `DR7`, `DR9`, `DR13`). |
| [`n2c2_access/`](file:///d:/daa/n2c2_access/) | Access request tracking and Data Use Agreement (DUA) milestones for the external n2c2 2018 Track 2 dataset (`REQUEST_STATUS.md`). |
| [`benchmark/`](file:///d:/daa/benchmark/) | Directory reserved for benchmark generation configurations, document templates, and evaluation splits (§18–§20). |
| [`data/m2_results_v1_legacy/`](file:///d:/daa/data/m2_results_v1_legacy/) | Isolated historical M2 v1 artifacts marked as `LEGACY / SUPERSEDED / NOT FOR FINAL RESULTS`. |
