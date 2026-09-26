"""Clinical document extraction module.

Canonical extraction subsystem (Phase 1 Task 6):
  ExtractionRouter   — full pipeline orchestrator
  Claim              — canonical atomic extracted clinical claim
  Evidence           — canonical source evidence with char offsets + bbox
  RoutingDecision    — routing decision per document region

Legacy (pre-Phase 1):
  extract_lab_report — direct lab report parser (still used by existing tests)
"""

from clindoc.extract.contracts import (
    Claim,
    DocumentExtractionOutput,
    Evidence,
    ExtractionResult,
    RoutingDecision,
    TIER_1_FIELDS,
    TIER_2_FIELDS,
)
from clindoc.extract.router import ExtractionRouter
from clindoc.extract.lab_parser import extract_lab_report

__all__ = [
    # Canonical extraction subsystem
    "ExtractionRouter",
    "Claim",
    "Evidence",
    "RoutingDecision",
    "ExtractionResult",
    "DocumentExtractionOutput",
    "TIER_1_FIELDS",
    "TIER_2_FIELDS",
    # Legacy
    "extract_lab_report",
]
