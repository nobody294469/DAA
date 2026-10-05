"""Health and Readiness Endpoints (PRODUCTIZATION_SPEC §9.3, §10.4).

Authority:
  - PRODUCTIZATION_SPEC.md §9.3 (Endpoint sketch: GET /health/live, GET /health/ready)
  - PRODUCTIZATION_SPEC.md §10.4 (Readiness dependencies)

Rules:
  - /health/live is the ONLY unauthenticated endpoint in the application.
  - /health/ready requires authentication and verifies application readiness dependencies (DB, BlobStore).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from clindoc.api.auth.dependencies import get_current_user, get_db_session
from clindoc.api.auth.models import AuthenticatedUser
from clindoc.api.deps import get_blob_store
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
):
    """Readiness probe: verifies essential application dependencies are functional.

    Requires authentication. Verifies:
      1. Relational database connectivity (SELECT 1).
      2. Encrypted blob storage accessibility.
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

    return {
        "status": "ready",
        "database": "connected",
        "blob_store": "ready",
    }
