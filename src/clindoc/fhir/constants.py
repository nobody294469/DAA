"""Standard FHIR R4 terminology systems and local extension URIs."""

# Terminology Systems
LOINC_SYSTEM = "http://loinc.org"
UCUM_SYSTEM = "http://unitsofmeasure.org"
OBS_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/observation-category"
OBS_INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
DIAG_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0074"
SNOMED_SYSTEM = "http://snomed.info/sct"

# Curated SNOMED CT Mappings for Qualitative Laboratory Determinations (Phase 4.1)
QUALITATIVE_SNOMED_MAP: dict[str, tuple[str, str]] = {
    "NEGATIVE": ("260385009", "Negative (qualifier value)"),
    "POSITIVE": ("10828004", "Positive (qualifier value)"),
    "NOT_DETECTED": ("260415000", "Not detected (qualifier value)"),
    "DETECTED": ("260373001", "Detected (qualifier value)"),
    "NO_GROWTH": ("264868006", "No growth (qualifier value)"),
    "REACTIVE": ("11214006", "Reactive (qualifier value)"),
    "NON_REACTIVE": ("131194007", "Non-Reactive (qualifier value)"),
    "EQUIVOCAL": ("42425007", "Equivocal (qualifier value)"),
    "INDETERMINATE": ("82334004", "Indeterminate (qualifier value)"),
    "TRACE": ("1511000179104", "Trace amount (qualifier value)"),
}

# Approved HL7 v3 ObservationInterpretation Mappings (Phase 4.1)
INTERPRETATION_HL7_MAP: dict[str, tuple[str, str]] = {
    "H": ("H", "High"),
    "HH": ("HH", "Critical high"),
    "L": ("L", "Low"),
    "LL": ("LL", "Critical low"),
    "A": ("A", "Abnormal"),
    "AA": ("AA", "Critical abnormal"),
    "N": ("N", "Normal"),
    "IND": ("IND", "Indeterminate"),
}

# Official Terminology for Medications (PROJECT_SPEC §14, §15)
RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"

# Canonical Two-Tier Provenance Extension URI (DEC-016, PROJECT_SPEC §16)
EXT_CLAIM_PROVENANCE = "http://clindoc.org/fhir/StructureDefinition/claim-provenance"

# Competing Candidate Audit Extension URI (Phase 4.4)
EXT_COMPETING_CANDIDATE = "http://clindoc.org/fhir/StructureDefinition/competing-candidate"

# Canonical Temporal-Status Extension & Value Sets (Phase 4.0.2, Phase 4.2, Phase 4.2.1)
EXT_TEMPORAL_STATUS = "http://clindoc.org/fhir/StructureDefinition/temporal-status"
TEMPORAL_STATUS_SYSTEM = "http://clindoc.org/fhir/CodeSystem/temporal-status"
TEMPORAL_STATUS_VALUESET = "http://clindoc.org/fhir/ValueSet/temporal-status"

# Formal value set: all four approved temporal codes (Phase 4.0.2 / 4.2.1)
ALL_TEMPORAL_STATUS_CODES = {"current", "historical", "planned", "discontinued"}

# Resource-specific permitted temporal status codes
OBSERVATION_TEMPORAL_STATUSES = {"current", "historical"}
MEDICATION_STATEMENT_TEMPORAL_STATUSES = {"current", "historical", "discontinued"}
MEDICATION_REQUEST_TEMPORAL_STATUSES = {"current", "historical", "planned", "discontinued"}

# Approved FHIR R4B DiagnosticReport Statuses (HL7 FHIR R4B ValueSet http://hl7.org/fhir/diagnostic-report-status)
DIAGNOSTIC_REPORT_STATUSES = {
    "registered",
    "partial",
    "preliminary",
    "final",
    "amended",
    "corrected",
    "appended",
    "cancelled",
    "entered-in-error",
    "unknown",
}

# Local Custom Extensions for Clinical Document Extraction & Verification (Legacy Phase 0)
EXT_VERIFICATION_STATUS = "http://clindoc.local/fhir/StructureDefinition/verification-status"
EXT_VERIFICATION_DISCREPANCY = "http://clindoc.local/fhir/StructureDefinition/verification-discrepancy"
EXT_SOURCE_EVIDENCE = "http://clindoc.local/fhir/StructureDefinition/source-evidence"

# Identifier Systems
IDENTIFIER_MRN_SYSTEM = "urn:clindoc:mrn"

# Document & Encounter Systems (Phase 8.8)
DOC_CATEGORY_SYSTEM = "http://terminology.hl7.org/CodeSystem/document-classcodes"
ACT_CODE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

