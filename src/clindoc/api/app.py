"""FastAPI Application Factory (PRODUCTIZATION_SPEC §9.1, §9.2, §9.3).

Authority:
  - PRODUCTIZATION_SPEC.md §9.1 (Modular monolith architecture)
  - PRODUCTIZATION_SPEC.md §9.2 (API layer responsibilities)
  - PRODUCTIZATION_SPEC.md §9.3 (Versioned endpoint routing under /api/v1)
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-006, PD-009, PD-012

Rules:
  - Thin API handlers: zero OCR, extraction, normalization, verification, or ML inference in request path.
  - Correlation ID middleware (X-Correlation-ID) attached to every request and response.
  - Consistent error handling with strict redaction of PHI, internal paths, and secrets.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from clindoc.api.routes import (
    audit_router,
    documents_router,
    health_router,
    jobs_router,
    review_router,
)

logger = logging.getLogger("clindoc.api")


def create_app(
    *,
    title: str = "Clindoc2FHIR Clinical Document Review API",
    version: str = "1.0.0",
    description: str = "Evidence-Grounded Clinical Document Verification and Review Platform",
) -> FastAPI:
    """Create and configure a production-ready FastAPI application instance."""
    app = FastAPI(
        title=title,
        version=version,
        description=description,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )

    # 1. Request Correlation Middleware (X-Correlation-ID)
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        corr_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = corr_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr_id
        return response

    # 2. Register Versioned API Routers under /api/v1
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(review_router, prefix="/api/v1")

    return app
