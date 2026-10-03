"""Reviewer UI package for demonstration and interactive verification inspection.

Authority: PROJECT_SPEC.md §7.8, §21
"""

from clindoc.ui.app import main
from clindoc.ui.render import (
    format_confidence,
    get_stage_badge_html,
    get_verdict_badge_html,
    render_document_page_with_bboxes,
)

__all__ = [
    "main",
    "format_confidence",
    "get_stage_badge_html",
    "get_verdict_badge_html",
    "render_document_page_with_bboxes",
]
