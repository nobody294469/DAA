"""OCR and document text extraction module."""

from clindoc.ocr.schema import DocumentOCRRecord, LineBox, WordBox
from clindoc.ocr.born_digital import extract_born_digital_pdf
from clindoc.ocr.pipeline import process_document, batch_process_dataset

__all__ = [
    "DocumentOCRRecord",
    "LineBox",
    "WordBox",
    "extract_born_digital_pdf",
    "process_document",
    "batch_process_dataset",
]
