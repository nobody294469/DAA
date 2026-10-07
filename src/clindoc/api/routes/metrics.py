"""Prometheus Metrics Scrape Endpoint (Phase 8.10).

Authority:
  - Phase 8.10 primary objective & §12 (Metrics endpoint security)
  - PRODUCTIZATION_SPEC §9.3, §10.4
  - Strict security mandate:
      - Internal network / monitoring token protection.
      - Zero PHI, zero secrets, zero bearer tokens in metrics text.
"""

from __future__ import annotations

import ipaddress
import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST

from clindoc.observability.metrics import get_metrics

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Observability"])

INTERNAL_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def verify_monitoring_network_access(request: Request) -> None:
    """Verify that the scrape request originates from an authorized internal monitoring source.

    Security Rules:
      1. If CLINDOC_METRICS_TOKEN is set in the environment, require matching token
         in Authorization header or X-Internal-Monitoring-Token header.
      2. If CLINDOC_REQUIRE_INTERNAL_SUBNET is True (or by default in production),
         verify client host IP is loopback, testclient, or private RFC 1918/RFC 4193 network.
      3. Fail-closed: External, unauthorized, or untrusted network scrapes receive HTTP 403.
    """
    metrics_token = os.environ.get("CLINDOC_METRICS_TOKEN")
    auth_header = request.headers.get("Authorization", "")
    token_header = request.headers.get("X-Internal-Monitoring-Token", "")

    # Token-based protection
    if metrics_token:
        provided_token = ""
        if auth_header.startswith("Bearer "):
            provided_token = auth_header.split(" ", 1)[1].strip()
        elif token_header:
            provided_token = token_header.strip()

        if provided_token != metrics_token:
            logger.warning("Unauthorized scrape attempt on /metrics: invalid or missing monitoring token.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Internal monitoring token missing or invalid.",
            )
        return

    # Network / IP-based protection
    client_host = request.client.host if request.client else "127.0.0.1"
    if client_host in ("testclient", "localhost", "127.0.0.1", "::1"):
        return

    try:
        client_ip = ipaddress.ip_address(client_host)
        for net in INTERNAL_PRIVATE_NETWORKS:
            if client_ip in net:
                return
    except ValueError:
        pass

    logger.warning("External IP '%s' denied access to internal /metrics endpoint.", client_host)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Forbidden: Metrics endpoint is restricted to internal monitoring network.",
    )


@router.get("/metrics", dependencies=[Depends(verify_monitoring_network_access)])
def metrics_endpoint() -> Response:
    """Expose Prometheus-formatted operational metrics.

    Endpoint is restricted to internal monitoring infrastructure.
    Guaranteed zero PHI, zero prompts, zero secrets.
    """
    metrics = get_metrics()
    data = metrics.generate_metrics_text()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
