"""Health and Readiness Endpoints (PRODUCTIZATION_SPEC SS9.3, SS10.4).

Authority:
  - PRODUCTIZATION_SPEC.md SS9.3 (Endpoint sketch: GET /health/live, GET /health/ready)
  - PRODUCTIZATION_SPEC.md SS10.4 (Readiness dependencies)
  - PRODUCTIZATION_DECISIONS.md Phase 8.7 Step 3 (Model Health / Readiness Probes)

Rules:
  - /health/live is the ONLY unauthenticated endpoint in the application.
  - /health/ready requires authentication and verifies readiness dependencies (DB, BlobStore).
  - /health/ready additionally reports ModernBERT and Qwen/vLLM model readiness.
  - No PHI, secrets, bearer tokens, or sensitive configuration are exposed in any response.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.deps import get_blob_store, get_model_health_registry
from clindoc.storage.blob import BlobStore

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("/live", status_code=status.HTTP_200_OK)
def liveness_check():
    """Liveness probe: verifies process is responding.

    This is the only unauthenticated endpoint in the application.
    """
    return {
        "status": "ok",
        "service": "clindoc2fhir-api",
    }


@router.get("/ready", status_code=status.HTTP_200_OK)
def readiness_check(
    current_user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    blob_store: BlobStore = Depends(get_blob_store),
    model_health=Depends(get_model_health_registry),
):
    """Readiness probe: verifies essential application dependencies are functional.

    Requires authentication. Verifies:
      1. Relational database connectivity (SELECT 1).
      2. Encrypted blob storage accessibility.
      3. ModernBERT (Stage B) readiness status -- informational.
      4. Qwen/vLLM (Stage C) readiness status -- informational.

    HTTP 503 is returned only for infrastructure dependency failures (DB, blob).
    Model readiness fields are informational: model unavailability does not cause a
    503 -- the worker's F-05/F-06 semantics remain authoritative for per-job behavior.
    Stage A/B processing continues for non-escalated claims regardless of vLLM state.

    No PHI, secrets, bearer tokens, or internal credentials appear in any response.
    """
    # 1. Database readiness check
    try:
        session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connectivity probe failed.",
        ) from exc

    # 2. Blob store readiness check
    try:
        # Check that blob store is initialized and directory/backend is available
        if hasattr(blob_store, "root_dir") and not blob_store.root_dir.exists():
            raise RuntimeError("Blob store root directory does not exist.")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Blob storage probe failed.",
        ) from exc

    # 3. Model readiness checks (lightweight, no inference, no secrets surfaced)
    model_summary = model_health.readiness_summary(vllm_probe_timeout=3.0)

    return {
        "status": "ready",
        "database": "connected",
        "blob_store": "ready",
        "modernbert": model_summary["modernbert"],
        "modernbert_detail": model_summary["modernbert_detail"],
        "qwen_vllm": model_summary["qwen_vllm"],
        "qwen_vllm_detail": model_summary["qwen_vllm_detail"],
    }
