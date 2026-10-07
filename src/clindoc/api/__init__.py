"""Phase 8.4 Modular Monolith API Package.

Conforms to PRODUCTIZATION_SPEC.md §9 and PRODUCTIZATION_DECISIONS.md (PD-001, PD-006, PD-009, PD-012).
"""

from __future__ import annotations

def create_app(*args, **kwargs):
    from clindoc.api.app import create_app as _create_app
    return _create_app(*args, **kwargs)

__all__ = ["create_app"]
