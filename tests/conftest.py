"""Test configuration and frozen Phase 4 fixture adapters (Phase 5.1.1.2).

Adapts legacy frozen Phase 4 test claim builders outside production service code.
This ensures legacy test fixtures supply human-review acceptance metadata without
requiring caller-detection, inspect frames, or stack inspection in production services.
"""

from typing import Any
import pytest


def _apply_phase4_fixture_adaptations() -> None:
    """Wrap legacy test claim builders in frozen test modules."""
    # 1. Adapt make_test_claim in tests.test_fhir_export
    try:
        import tests.test_fhir_export as tfe
        orig_make_test_claim = getattr(tfe, "make_test_claim", None)
        if orig_make_test_claim and not getattr(orig_make_test_claim, "_is_adapted", False):
            _UNSET = object()
            def adapted_make_test_claim(*args: Any, review_state: Any = _UNSET, **kwargs: Any) -> Any:
                c = orig_make_test_claim(*args, **kwargs)
                if review_state is not _UNSET:
                    if review_state is not None:
                        object.__setattr__(c, "review_state", review_state)
                    else:
                        # Explicit review_state=None passed: ensure it is preserved as None
                        if hasattr(c, "review_state"):
                            object.__delattr__(c, "review_state")
                else:
                    # review_state was omitted by caller -> default to ACCEPTED for legacy frozen test
                    object.__setattr__(c, "review_state", "ACCEPTED")
                return c
            adapted_make_test_claim._is_adapted = True  # type: ignore[attr-defined]
            tfe.make_test_claim = adapted_make_test_claim
    except Exception:
        pass

    # 2. Adapt _make_claim in tests.test_phase44_diagnostic_report_grouping
    try:
        import tests.test_phase44_diagnostic_report_grouping as tp44
        orig_make_claim = getattr(tp44, "_make_claim", None)
        if orig_make_claim and not getattr(orig_make_claim, "_is_adapted", False):
            def adapted_make_claim(*args: Any, **kwargs: Any) -> Any:
                # In tp44._make_claim, review_state is the 14th argument (index 13) or keyword arg 'review_state'
                if "review_state" in kwargs:
                    # Explicit keyword argument passed (could be None, ACCEPTED, REJECTED, etc.)
                    return orig_make_claim(*args, **kwargs)
                elif len(args) >= 14:
                    # Explicit positional argument passed
                    return orig_make_claim(*args, **kwargs)
                else:
                    # Omitted by caller -> default to ACCEPTED for legacy frozen test
                    kwargs["review_state"] = "ACCEPTED"
                    return orig_make_claim(*args, **kwargs)
            adapted_make_claim._is_adapted = True  # type: ignore[attr-defined]
            tp44._make_claim = adapted_make_claim
    except Exception:
        pass


def pytest_configure(config: Any) -> None:
    """Pytest hook running prior to test collection."""
    _apply_phase4_fixture_adaptations()


@pytest.fixture(autouse=True, scope="session")
def _ensure_phase4_fixtures_adapted() -> None:
    """Session-scoped autouse fixture ensuring adaptations are active."""
    _apply_phase4_fixture_adaptations()
