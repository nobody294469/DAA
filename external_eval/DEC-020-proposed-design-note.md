# Decision Note DEC-020 (PROPOSED / DRAFT ONLY): Ocular Laterality and Anatomical Site Representation

**Status**: PROPOSED / DRAFT ONLY (Post-MVP Architectural Review)  
**Authority**: External Evaluation Audit / DEC-017 / DEC-008 / DEC-016  
**Applicability**: Architectural separation of administration route from anatomical site/laterality.  
**Guardrail**: Zero changes to authoritative specifications, claim schemas, or production code.

---

## 1. Clinical Context & Motivation

During external evaluation on ophthalmic clinical notes (`Glaucoma_Med_Dataset`), **95.1% of all extracted "route" annotations were anatomical laterality designators rather than physiological routes of administration**:
* `OD` (*oculus dexter*): Right eye (SNOMED CT `18944008`)
* `OS` (*oculus sinister*): Left eye (SNOMED CT `8966001`)
* `OU` (*oculus uterque*): Both eyes (SNOMED CT `40638003`)

In current practice across clinical documentation (ophthalmology, dermatology, otolaryngology), physicians frequently write sig lines like:
$$\text{"Latanoprost 0.005\% 1 drop QHS OU"}$$
In this order:
* **Drug**: Latanoprost (`med.name`)
* **Dose / Strength**: 0.005% 1 drop (`med.dose`)
* **Frequency**: QHS / nightly (`med.frequency`)
* **Laterality / Target Site**: `OU` (Both eyes)
* **Physiological Route**: Ophthalmic / topical eye drop (often implicit)

---

## 2. Problem Statement: Semantic Conflation in Current Architecture

1. **Claim Schema Limitation (DEC-017, §8.2)**:
   - [`schemas/claim.json`](file:///d:/daa/schemas/claim.json) strictly enforces a 10-field controlled vocabulary:
     `lab.value`, `lab.unit`, `lab.flag`, `lab.reference_range`, `lab.test_name`, `med.name`, `med.dose`, `med.route`, `med.frequency`, `med.duration`.
   - **There is no `med.site` or `med.laterality` field.**
2. **FHIR R4 Non-Conformance in [`src/clindoc/fhir/mapper.py`](file:///d:/daa/src/clindoc/fhir/mapper.py#L1053-L1094)**:
   - In HL7 FHIR R4, `Dosage` explicitly distinguishes:
     - **`Dosage.route`** (`CodeableConcept`): Mechanism by which the drug enters the body (e.g. SNOMED CT `54485002` *Ophthalmic route*, `26643006` *Oral route*).
     - **`Dosage.site`** (`CodeableConcept`): Anatomical body structure receiving the medication (e.g. SNOMED CT `40638003` *Both eyes*, `18944008` *Right eye*).
   - In the current mapper, `route_claim.value` directly populates `Dosage.route.text`, while `Dosage.site` is left completely unpopulated.
   - If `OU`, `OD`, or `OS` is stored in `med.route`, the resulting FHIR resource claims that the *route* of entry is "both eyes", violating FHIR R4 / US Core terminology standards.

---

## 3. Architectural Options Under Consideration

### Option A: Schema Expansion to 11 Canonical Fields (`med.site`)
* **Mechanism**:
  - Add `med.site` to `field_type` enum in [`schemas/claim.json`](file:///d:/daa/schemas/claim.json).
  - Update `FieldType` enum in `src/clindoc/m2/schema.py`.
  - Resize auxiliary feature dimension in ModernBERT verification:
    $$\text{AUX\_DIM} = 1 \text{ (OCR conf)} + 11 \text{ (field types)} + 7 \text{ (doc templates)} = 19$$
  - Map `med.site` directly to FHIR `Dosage.site`.
* **Trade-offs**:
  - *Pros*: Complete semantic fidelity; clean 1-to-1 mapping with FHIR `Dosage.site`; supports otic (ears) and dermal (skin sites) laterality.
  - *Cons*: Modifies canonical schema (DEC-017); requires updating Stage B/C model auxiliary dimensions prior to retraining.

### Option B: Normalizer-Level Decomposition (Stage 2 Semantic Translation)
* **Mechanism**:
  - Retain strictly 10 fields in `Claim`.
  - Treat `med.route` as capturing the raw textual target string (e.g., `"OU"`, `"oral"`).
  - In `src/clindoc/normalize/`, the medication normalizer parses the value:
    - If value matches an anatomical site/laterality dictionary (`OU`, `OD`, `OS`, `left ear`, etc.):
      - Normalizes `code_system="SNOMED-CT"`, mapping `OU` to `40638003`.
      - Adds metadata flag `is_anatomical_site: true`.
    - In `src/clindoc/fhir/mapper.py`, if the claim is an anatomical site, it populates `Dosage.site` (with SNOMED code), and infers `Dosage.route = Ophthalmic route` (SNOMED `54485002`).
* **Trade-offs**:
  - *Pros*: **Zero schema changes**. Preserves the frozen 10-field vocabulary and model architecture intact.
  - *Cons*: Overloads `med.route` with site concepts during extraction, requiring post-processing disambiguation.

### Option C: Defer to Post-MVP Specialized Domain Extension (Recommended)
* **Mechanism**:
  - Keep ClinicalDoc MVP frozen on general inpatient/outpatient medicine (DEC-017).
  - Document that ophthalmology-specific laterality extraction requires specialized multi-modal models or a dedicated Phase 5 Ophthalmology Plugin.
* **Trade-offs**:
  - Eliminates scope creep and preserves existing frozen verification cascade and benchmarks.

---

## 4. Next Step Recommendation

Do not modify schemas, production code, or models at this time. Maintain this design note as the authoritative reference for post-MVP architecture discussions.
