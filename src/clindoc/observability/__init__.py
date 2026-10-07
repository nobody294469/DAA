"""ClinDoc Observability Package.

Authority:
  - Phase 8.10 (Observability & Operational Monitoring)
  - PRODUCTIZATION_SPEC §9.2, §10.4
"""

from clindoc.observability.metrics import (
    ClinDocMetrics,
    get_metrics,
    set_metrics,
    reset_metrics,
)

__all__ = [
    "ClinDocMetrics",
    "get_metrics",
    "set_metrics",
    "reset_metrics",
]
