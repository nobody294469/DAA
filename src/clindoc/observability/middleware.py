"""FastAPI Middleware for Prometheus HTTP Metrics.

Authority:
  - Phase 8.10 (Observability & Operational Monitoring)
  - PRODUCTIZATION_SPEC §9.2, §10.4
  - Strict Low-Cardinality Guard: Raw IDs (e.g. UUIDs) are normalized to route parameters.
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from clindoc.observability.metrics import get_metrics, _UUID_PATTERN


def get_normalized_route_path(request: Request) -> str:
    """Extract parameterized route template from request scope to avoid cardinality explosion."""
    # 1. Check if route was already matched by router
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path"):
        return str(route.path)

    # 2. Iterate app routes to find a matching pattern
    app = request.app
    if hasattr(app, "routes"):
        for app_route in app.routes:
            match, _ = app_route.matches(request.scope)
            if match == Match.FULL and hasattr(app_route, "path"):
                return str(app_route.path)

    # 3. Fallback: normalize raw path by replacing any UUIDs with {id}
    raw_path = request.url.path
    normalized = _UUID_PATTERN.sub("{id}", raw_path)
    return normalized


class PrometheusMetricsMiddleware(BaseHTTPMiddleware):
    """Measures HTTP request latency, count, and status code category with bounded labels."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Don't instrument /metrics endpoint itself to avoid scrape recursion
        if request.url.path.endswith("/metrics"):
            return await call_next(request)

        start_time = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start_time
            normalized_route = get_normalized_route_path(request)
            method = request.method
            metrics = get_metrics()
            metrics.record_http_request(
                method=method,
                route=normalized_route,
                status_code=status_code,
                duration_seconds=duration,
            )
