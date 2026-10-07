"""Standard FHIR R4 terminology systems and local extension URIs."""

# Official FHIR R4 Terminology Systems
LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
OBS_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
OBS_INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
DIAG_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0074"

# Official Terminology for Medications (PROJECT_SPEC §14, §15)
RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Canonical Two-Tier Provenance Extension URI (DEC-016, PROJECT_SPEC §16)
EXT_CLAIM_PROVENANCE = "http://clindoc.org/fhir/StructureDefinition/claim-provenance"

# Local Custom Extensions for Clinical Document Extraction & Verification (Legacy Phase 0)
EXT_VERIFICATION_STATUS = "http://clindoc.local/fhir/StructureDefinition/verification-status"
EXT_VERIFICATION_DISCREPANCY = "http://clindoc.local/fhir/StructureDefinition/verification-discrepancy"
EXT_SOURCE_EVIDENCE = "http://clindoc.local/fhir/StructureDefinition/source-evidence"

# Identifier Systems
IDENTIFIER_MRN_SYSTEM = "urn:clindoc:mrn"

# Document & Encounter Systems (Phase 8.8)
DOC_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/document-classcodes"
ACT_CODE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
