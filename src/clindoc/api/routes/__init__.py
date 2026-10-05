"""API Route Modules."""

from __future__ import annotations

from clindoc.api.routes.audit import router as audit_router
from clindoc.api.routes.documents import router as documents_router
from clindoc.api.routes.health import router as health_router
from clindoc.api.routes.jobs import router as jobs_router
from clindoc.api.routes.review import router as review_router

__all__ = [
    "audit_router",
    "documents_router",
    "health_router",
    "jobs_router",
    "review_router",
]
