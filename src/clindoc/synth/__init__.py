"""Synthetic laboratory document generation and ground-truth schemas."""

from clindoc.synth.schema import LabReportGroundTruth, LabTestResult, PatientInfo
from clindoc.synth.generator import generate_synthetic_dataset

__all__ = [
    "LabReportGroundTruth",
    "LabTestResult",
    "PatientInfo",
    "generate_synthetic_dataset",
]
