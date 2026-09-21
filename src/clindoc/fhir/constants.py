"""Standard FHIR R4 terminology systems and local extension URIs."""

# Official FHIR R4 Terminology Systems
LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
OBS_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
OBS_INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
DIAG_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0074"

# Local Custom Extensions for Clinical Document Extraction & Verification
# Note: These URLs are project-local custom extensions for the research prototype (http://clindoc.local/...)
# and should not be confused with official HL7 published extensions (http://hl7.org/fhir/StructureDefinition/...).
EXT_VERIFICATION_STATUS = "http://clindoc.local/fhir/StructureDefinition/verification-status"
EXT_VERIFICATION_DISCREPANCY = "http://clindoc.local/fhir/StructureDefinition/verification-discrepancy"
EXT_SOURCE_EVIDENCE = "http://clindoc.local/fhir/StructureDefinition/source-evidence"

# Identifier Systems
IDENTIFIER_MRN_SYSTEM = "urn:clindoc:mrn"
