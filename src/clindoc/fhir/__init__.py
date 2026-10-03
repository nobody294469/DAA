"""FHIR R4B generation, mapping, and validation module."""

from clindoc.fhir.mapper import (
    build_element_provenance_extension,
    build_resource_provenance_extension,
    claims_to_fhir_bundle,
    lab_report_to_fhir_bundle,
)
from clindoc.fhir.validator import BundleValidationResult, FHIRValidator

__all__ = [
    "build_element_provenance_extension",
    "build_resource_provenance_extension",
    "claims_to_fhir_bundle",
    "lab_report_to_fhir_bundle",
    "FHIRValidator",
    "BundleValidationResult",
]
