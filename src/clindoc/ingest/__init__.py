"""Document ingestion and screening package."""

from clindoc.ingest.service import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    DEFAULT_MAX_PAGE_COUNT,
    SUPPORTED_MIME_TYPES,
    DocumentIngestionService,
    IngestionScreeningError,
    ScreeningResult,
    sniff_mime_type,
)

__all__ = [
    "DocumentIngestionService",
    "ScreeningResult",
    "IngestionScreeningError",
    "sniff_mime_type",
    "DEFAULT_MAX_FILE_SIZE_BYTES",
    "DEFAULT_MAX_PAGE_COUNT",
    "SUPPORTED_MIME_TYPES",
]
