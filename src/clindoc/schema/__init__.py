"""Schema package containing intermediate clinical and evidence representations."""

from clindoc.schema.evidence import SourceEvidence
from clindoc.schema.lab import ExtractedLabReport, ExtractedObservation, ExtractedPatientInfo
from clindoc.schema.medication import ExtractedMedication, ExtractedPrescriptionDocument
from clindoc.ocr.document_text import DocumentText, DocumentPage, Token

from clindoc.schema.verification import (
    VerificationResult,
    VerificationLabel,
    StageReached,
    ContradictionType,
)

__all__ = [
    "DocumentText",
    "DocumentPage",
    "Token",
    "SourceEvidence",
    "ExtractedLabReport",
    "ExtractedObservation",
    "ExtractedPatientInfo",
    "ExtractedMedication",
    "ExtractedPrescriptionDocument",
    "VerificationResult",
    "VerificationLabel",
    "StageReached",
    "ContradictionType",
]
