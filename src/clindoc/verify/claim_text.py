"""Descriptive claim strings for the Stage B encoder.

Authority: src/clindoc/m2/generation.py (V3 benchmark claim templates).
Stage B was trained on strings such as "Hematocrit: 46.1 %" or "Metformin dose: 500 mg",
not on bare extracted values ("46.1"). Live claims must be rendered the same way.
"""
from __future__ import annotations

from typing import Optional

# field_type -> template; {entity}, {value}, {unit} are filled in, then whitespace-trimmed.
_TEMPLATES = {
    "lab.value": "{entity}: {value} {unit}",
    "lab.flag": "{entity} flag: {value}",
    "med.name": "Medication: {value}",
    "med.dose": "{entity} dose: {value} {unit}",
    "med.route": "{entity} route: {value}",
    "med.frequency": "{entity} frequency: {value}",
}


def build_claim_text(
    field_type: str,
    value: str,
    unit: Optional[str] = None,
    entity: Optional[str] = None,
) -> str:
    """Render a claim in the V3 training format; falls back to the bare value.

    The entity (lab test name or medication name) comes from the sibling
    lab.test_name / med.name claim in the same group_id.
    """
    value = str(value).strip()
    unit = (unit or "").strip()
    if unit and value.lower().endswith(unit.lower()):
        unit = ""  # value already carries its unit, e.g. "500 mg"
    template = _TEMPLATES.get(field_type)
    if template is None or ("{entity}" in template and not entity):
        return " ".join(part for part in (value, unit) if part)
    return " ".join(template.format(entity=(entity or "").strip(), value=value, unit=unit).split())


if __name__ == "__main__":
    assert build_claim_text("lab.value", "46.1", "%", "Hematocrit") == "Hematocrit: 46.1 %"
    assert build_claim_text("lab.flag", "HIGH", None, "Total Cholesterol") == "Total Cholesterol flag: HIGH"
    assert build_claim_text("med.name", "Metformin") == "Medication: Metformin"
    assert build_claim_text("med.dose", "500 mg", "mg", "Metformin") == "Metformin dose: 500 mg"
    assert build_claim_text("med.route", "PO", entity="Metformin") == "Metformin route: PO"
    assert build_claim_text("lab.value", "7.2", "mg/dL") == "7.2 mg/dL"  # no entity -> bare value
    print("ok")
