"""Medication Association & Co-reference Engine for Clinical Narrative Extraction.

Location: src/clindoc/extract/medication_association.py
Authority: Phase 5.2.3 Milestone 3
License: Apache-2.0 / MIT

Provides deterministic, conservative medication association within clinical text segments:
1. Implements Layer C association classification:
   - UNAMBIGUOUS_SINGLE
   - UNAMBIGUOUS_INTERLEAVED
   - SHARED_COORDINATE_CONFIRMED
   - PARALLEL_COORDINATE_BIJECTION
   - AMBIGUOUS_SCOPE_MULTI_ENTITY
   - AMBIGUOUS_SLOT_CONFLICT
   - UNANCHORED_ORPHAN
2. Enforces Association Rules 1 through 6:
   - Rule 1: Single medication entity binds attributes in unambiguous clause.
   - Rule 2: Interleaved explicit entities receive strictly local attributes.
   - Rule 3: Shared coordinate attributes (both, each, all, slash co-prescriptions)
     bind to all coordinate drugs with single grounded evidence.
   - Rule 4: Parallel coordinate lists (respectively) create provisional 1:1 mappings
     requiring clinician confirmation before export. Mismatched counts fail closed.
   - Rule 5: Ambiguous multi-entity scope (clustered drugs with trailing attributes)
     fails closed: names export independently, ambiguous attributes are quarantined.
   - Rule 6: Orphan attributes without active medication anchor are quarantined.
3. Classifies temporal qualifications:
   - Discontinuation (discontinued, stopped, d/c) -> temporal_status="discontinued"
   - Conditional hold (hold if SBP < 100) -> temporal_status="conditional"
   - Split dosing / dose changes -> AMBIGUOUS_SLOT_CONFLICT
"""
from __future__ import annotations

from enum import Enum
import re
from typing import Any, Final, Optional
import uuid


class AssociationStatus(str, Enum):
    """Explicit association classification outcome for clinical medication claims."""
    UNAMBIGUOUS_SINGLE = "UNAMBIGUOUS_SINGLE"
    UNAMBIGUOUS_INTERLEAVED = "UNAMBIGUOUS_INTERLEAVED"
    SHARED_COORDINATE_CONFIRMED = "SHARED_COORDINATE_CONFIRMED"
    PARALLEL_COORDINATE_BIJECTION = "PARALLEL_COORDINATE_BIJECTION"
    AMBIGUOUS_SCOPE_MULTI_ENTITY = "AMBIGUOUS_SCOPE_MULTI_ENTITY"
    AMBIGUOUS_SLOT_CONFLICT = "AMBIGUOUS_SLOT_CONFLICT"
    UNANCHORED_ORPHAN = "UNANCHORED_ORPHAN"


NO_ANCHOR_SENTINEL: Final[str] = "__no_anchor__"
MED_ATTR_FIELDS: Final[frozenset[str]] = frozenset({
    "med.dose", "med.route", "med.frequency", "med.duration"
})

# Regular expressions for syntactic coordinate markers
RE_RESPECTIVELY: Final[re.Pattern] = re.compile(r"\brespectively\b", re.IGNORECASE)
RE_SHARED_MARKERS: Final[re.Pattern] = re.compile(
    r"\b(both|each|all|together|co[- ]prescribed)\b", re.IGNORECASE
)
RE_DISCONTINUED: Final[re.Pattern] = re.compile(
    r"\b(discontinue|discontinued|stopped|stopping|ceased|cease|d/c(?:'?d)?|dc(?:'?d)?)\b",
    re.IGNORECASE,
)
RE_CONDITIONAL_HOLD: Final[re.Pattern] = re.compile(
    r"\b(hold|withhold|withholding|holding|if\s+(?:systolic|sbp|bp|hr|pulse|creatinine|inr)|"
    r"in\s+case\s+of|unless|as\s+needed\s+for|prn\s+for)\b",
    re.IGNORECASE,
)


def detect_segment_temporal_qualifiers(segment_text: str) -> dict[str, bool]:
    """Detect clinical temporal qualification markers in a segment."""
    return {
        "is_discontinued": bool(RE_DISCONTINUED.search(segment_text)),
        "is_conditional": bool(RE_CONDITIONAL_HOLD.search(segment_text)),
        "has_respectively": bool(RE_RESPECTIVELY.search(segment_text)),
        "has_shared_marker": bool(RE_SHARED_MARKERS.search(segment_text)),
    }


def associate_segment_medications(
    segment_text: str,
    candidates: list[tuple[dict[str, Any], int, int, str]],
) -> list[tuple[dict[str, Any], int, int, str, str, AssociationStatus, dict[str, Any]]]:
    """Perform layered, conservative medication association within a single clause or sentence.

    Args:
        segment_text: The verbatim text of the clinical segment.
        candidates: List of (ext_dict, local_start, local_end, source_span) for medication
                    fields in this segment, sorted by (local_start, field_priority, ...).

    Returns:
        List of tuples:
            (ext_dict, local_start, local_end, source_span, group_id, association_status, metadata)
    """
    if not candidates:
        return []

    qualifiers = detect_segment_temporal_qualifiers(segment_text)
    is_discontinued = qualifiers["is_discontinued"]
    is_conditional = qualifiers["is_conditional"]
    has_respectively = qualifiers["has_respectively"]
    has_shared_marker = qualifiers["has_shared_marker"]

    default_temporal_status = "unspecified"
    if is_discontinued:
        default_temporal_status = "discontinued"
    elif is_conditional:
        default_temporal_status = "conditional"

    med_name_items = [c for c in candidates if c[0].get("field_type") == "med.name"]
    med_attr_items = [c for c in candidates if c[0].get("field_type") in MED_ATTR_FIELDS]

    results: list[tuple[dict[str, Any], int, int, str, str, AssociationStatus, dict[str, Any]]] = []

    # ─── CASE 1: No medication name in segment (Rule 6: Orphan attributes) ─────
    if len(med_name_items) == 0:
        for ext, ls, le, span in med_attr_items:
            results.append((
                ext, ls, le, span,
                NO_ANCHOR_SENTINEL,
                AssociationStatus.UNANCHORED_ORPHAN,
                {"orphan": True, "temporal_status": default_temporal_status},
            ))
        return results

    # ─── CASE 2: Exactly 1 medication name in segment (Rule 1) ─────────────────
    if len(med_name_items) == 1:
        name_ext, name_ls, name_le, name_span = med_name_items[0]
        med_group = str(uuid.uuid4())

        results.append((
            name_ext, name_ls, name_le, name_span,
            med_group,
            AssociationStatus.UNAMBIGUOUS_SINGLE,
            {"temporal_status": default_temporal_status},
        ))

        # Check for multiple dose claims (split dosing / dose changes / slot conflicts)
        dose_items = [c for c in med_attr_items if c[0].get("field_type") == "med.dose"]
        has_slot_conflict = len(dose_items) > 1

        for ext, ls, le, span in med_attr_items:
            ft = ext.get("field_type", "")
            meta: dict[str, Any] = {"temporal_status": default_temporal_status}

            # Check if attribute precedes the medication name in source coordinates
            if le <= name_ls:
                # Preceding attribute: in Phase 5.2 baseline, attributes appearing before
                # any medication anchor receive sentinel group_id (fail-closed).
                results.append((
                    ext, ls, le, span,
                    NO_ANCHOR_SENTINEL,
                    AssociationStatus.UNANCHORED_ORPHAN,
                    {"preceding_attribute": True, "temporal_status": default_temporal_status},
                ))
            elif ft == "med.dose" and has_slot_conflict:
                meta["slot_conflict"] = True
                results.append((
                    ext, ls, le, span,
                    med_group,
                    AssociationStatus.AMBIGUOUS_SLOT_CONFLICT,
                    meta,
                ))
            else:
                results.append((
                    ext, ls, le, span,
                    med_group,
                    AssociationStatus.UNAMBIGUOUS_SINGLE,
                    meta,
                ))
        return results

    # ─── CASE 3: Multiple medication names in segment ──────────────────────────
    # Instantiate distinct group UUID for each medication
    med_groups = [str(uuid.uuid4()) for _ in med_name_items]
    dose_items = [c for c in med_attr_items if c[0].get("field_type") == "med.dose"]

    # SUBCASE 3A: Parallel coordinate list with explicit "respectively" (Rule 4)
    if has_respectively:
        if len(dose_items) == len(med_name_items):
            # 1-to-1 bijection established
            for i, (ext, ls, le, span) in enumerate(med_name_items):
                results.append((
                    ext, ls, le, span,
                    med_groups[i],
                    AssociationStatus.PARALLEL_COORDINATE_BIJECTION,
                    {"temporal_status": default_temporal_status, "provisional": True},
                ))
            for i, (ext, ls, le, span) in enumerate(dose_items):
                results.append((
                    ext, ls, le, span,
                    med_groups[i],
                    AssociationStatus.PARALLEL_COORDINATE_BIJECTION,
                    {"temporal_status": default_temporal_status, "provisional": True},
                ))
            # Any remaining attributes without explicit bijection
            other_attrs = [c for c in med_attr_items if c[0].get("field_type") != "med.dose"]
            for ext, ls, le, span in other_attrs:
                results.append((
                    ext, ls, le, span,
                    NO_ANCHOR_SENTINEL,
                    AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY,
                    {"temporal_status": default_temporal_status},
                ))
            return results
        else:
            # Mismatched counts with "respectively" -> fail closed
            for i, (ext, ls, le, span) in enumerate(med_name_items):
                results.append((
                    ext, ls, le, span,
                    med_groups[i],
                    AssociationStatus.UNAMBIGUOUS_SINGLE,
                    {"temporal_status": default_temporal_status},
                ))
            for ext, ls, le, span in med_attr_items:
                results.append((
                    ext, ls, le, span,
                    NO_ANCHOR_SENTINEL,
                    AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY,
                    {"mismatched_coordinate_counts": True, "temporal_status": default_temporal_status},
                ))
            return results

    # SUBCASE 3B: Shared coordinate attributes (Rule 3)
    # Marked by 'both', 'each', 'all', 'together', 'jointly', 'co-prescribed'
    if has_shared_marker:
        # Check if attribute appears after all medication names
        last_name_end = max(item[2] for item in med_name_items)
        trailing_attrs = [c for c in med_attr_items if c[1] >= last_name_end]

        if trailing_attrs:
            # Medication names get their own distinct groups
            for i, (ext, ls, le, span) in enumerate(med_name_items):
                results.append((
                    ext, ls, le, span,
                    med_groups[i],
                    AssociationStatus.UNAMBIGUOUS_SINGLE,
                    {"temporal_status": default_temporal_status},
                ))

            # Shared trailing attributes replicate safely across all coordinate groups
            # preserving one shared source evidence reference
            trailing_by_field: dict[str, list[tuple[dict[str, Any], int, int, str]]] = {}
            for item in trailing_attrs:
                ft = item[0].get("field_type", "")
                trailing_by_field.setdefault(ft, []).append(item)

            for ft, items_for_ft in trailing_by_field.items():
                if len(items_for_ft) == len(med_groups):
                    # Already 1 candidate extraction per coordinate medication
                    for i, (ext, ls, le, span) in enumerate(items_for_ft):
                        results.append((
                            ext, ls, le, span,
                            med_groups[i],
                            AssociationStatus.SHARED_COORDINATE_CONFIRMED,
                            {"shared": True, "temporal_status": default_temporal_status},
                        ))
                else:
                    for ext, ls, le, span in items_for_ft:
                        for grp in med_groups:
                            results.append((
                                ext, ls, le, span,
                                grp,
                                AssociationStatus.SHARED_COORDINATE_CONFIRMED,
                                {"shared": True, "temporal_status": default_temporal_status},
                            ))

            # Any non-trailing attributes are handled locally
            non_trailing = [c for c in med_attr_items if c[1] < last_name_end]
            for ext, ls, le, span in non_trailing:
                results.append((
                    ext, ls, le, span,
                    NO_ANCHOR_SENTINEL,
                    AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY,
                    {"temporal_status": default_temporal_status},
                ))
            return results

    # SUBCASE 3C: Interleaved explicit medication entities (Rule 2)
    # Check if each drug is immediately followed by its attributes before the next drug
    name_intervals = []
    for i in range(len(med_name_items)):
        start_i = med_name_items[i][1]
        end_i = med_name_items[i+1][1] if i + 1 < len(med_name_items) else float("inf")
        name_intervals.append((start_i, end_i))

    # Test if every attribute falls strictly after its corresponding medication
    # and before any subsequent medication (clean interleaving)
    is_interleaved = True
    clustered_names = False

    # If first two medications have no intervening attributes, names are clustered
    if len(med_name_items) >= 2:
        first_start = med_name_items[0][1]
        second_start = med_name_items[1][1]
        intervening = [c for c in med_attr_items if first_start <= c[1] < second_start]
        if not intervening:
            clustered_names = True
            is_interleaved = False

    if is_interleaved and not clustered_names:
        # Each medication name gets its group
        for i, (ext, ls, le, span) in enumerate(med_name_items):
            results.append((
                ext, ls, le, span,
                med_groups[i],
                AssociationStatus.UNAMBIGUOUS_INTERLEAVED,
                {"temporal_status": default_temporal_status},
            ))

        # Attributes bind strictly to the preceding medication in their partition interval
        for ext, ls, le, span in med_attr_items:
            assigned_idx = None
            for i, (start_i, end_i) in enumerate(name_intervals):
                if start_i <= ls < end_i:
                    assigned_idx = i
                    break

            if assigned_idx is not None:
                results.append((
                    ext, ls, le, span,
                    med_groups[assigned_idx],
                    AssociationStatus.UNAMBIGUOUS_INTERLEAVED,
                    {"temporal_status": default_temporal_status},
                ))
            else:
                # Attribute before first medication
                results.append((
                    ext, ls, le, span,
                    NO_ANCHOR_SENTINEL,
                    AssociationStatus.UNANCHORED_ORPHAN,
                    {"temporal_status": default_temporal_status},
                ))
        return results

    # SUBCASE 3D: Clustered medication names with trailing attributes (Rule 5: Ambiguous multi-entity scope)
    # Example: 'Metoprolol and Lisinopril 25 mg'
    # Fail-closed: medication names remain independently represented; dose is NOT assigned to either.
    for i, (ext, ls, le, span) in enumerate(med_name_items):
        results.append((
            ext, ls, le, span,
            med_groups[i],
            AssociationStatus.UNAMBIGUOUS_SINGLE,
            {"temporal_status": default_temporal_status},
        ))

    for ext, ls, le, span in med_attr_items:
        results.append((
            ext, ls, le, span,
            NO_ANCHOR_SENTINEL,
            AssociationStatus.AMBIGUOUS_SCOPE_MULTI_ENTITY,
            {"ambiguous_scope": True, "temporal_status": default_temporal_status},
        ))

    return results
