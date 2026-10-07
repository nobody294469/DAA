"""FastAPI Application Factory (PRODUCTIZATION_SPEC §9.1, §9.2, §9.3).

Authority:
  - PRODUCTIZATION_SPEC.md §9.1 (Modular monolith architecture)
  - PRODUCTIZATION_SPEC.md §9.2 (API layer responsibilities)
  - PRODUCTIZATION_SPEC.md §9.3 (Versioned endpoint routing under /api/v1)
  - PRODUCTIZATION_SPEC.md §5 (Threat T-21: XSS, clickjacking, framing denied, CSP)
  - PRODUCTIZATION_DECISIONS.md PD-001, PD-006, PD-009, PD-012, PD-013

Rules:
  - Thin API handlers: zero OCR, extraction, normalization, verification, or ML inference in request path.
  - Correlation ID middleware (X-Correlation-ID) attached to every request and response.
  - Security headers attached to all responses: X-Frame-Options: DENY, X-Content-Type-Options: nosniff, CSP.
  - Explicit, environment-driven CORS configuration for browser review workbench SPA (PD-013).
  - Consistent error handling with strict redaction of PHI, internal paths, and secrets.
"""

from __future__ import annotations

import logging
import os
from typing import Optional
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from clindoc.api.routes import (
    audit_router,
    documents_router,
    health_router,
    jobs_router,
    metrics_router,
    review_router,
)
from clindoc.observability.middleware import PrometheusMetricsMiddleware

logger = logging.getLogger("clindoc.api")

DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
)
DEFAULT_CSP = "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self';"


def create_app(
    *,
    title: str = "Clindoc2FHIR Clinical Document Review API",
    version: str = "1.0.0",
    description: str = "Evidence-Grounded Clinical Document Verification and Review Platform",
    cors_origins: Optional[list[str]] = None,
) -> FastAPI:
    """Create and configure a production-ready FastAPI application instance."""
    app = FastAPI(
        title=title,
        version=version,
        description=description,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
    )

    # 1. Resolve and register CORS middleware (PD-013)
    if cors_origins is not None:
        allowed_origins = list(cors_origins)
    else:
        env_origins = os.environ.get("CLINDOC_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
        allowed_origins = [o.strip() for o in env_origins.split(",") if o.strip()]

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Correlation-ID",
                "Accept",
                "Origin",
                "X-Requested-With",
            ],
            expose_headers=["X-Correlation-ID", "Content-Disposition"],
        )

    # 2. Prometheus HTTP Metrics Middleware (Phase 8.10)
    app.add_middleware(PrometheusMetricsMiddleware)

    # 3. Security and Correlation Headers Middleware (T-21, PD-013)
    @app.middleware("http")
    async def security_and_correlation_middleware(request: Request, call_next):
        corr_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = corr_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr_id
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        csp = os.environ.get("CLINDOC_CSP", DEFAULT_CSP)
        response.headers["Content-Security-Policy"] = csp
        return response

    # 4. Register Versioned API Routers under /api/v1
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(review_router, prefix="/api/v1")

    # 5. Register Metrics Router (Internal Monitoring Scrape Endpoint)
    app.include_router(metrics_router)
    app.include_router(metrics_router, prefix="/api/v1")

    return app

