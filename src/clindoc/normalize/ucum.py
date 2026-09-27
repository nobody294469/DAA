"""Deterministic UCUM unit normalizer conforming to PROJECT_SPEC §14.2.

Per DEC-002 and PROJECT_SPEC §14.2:
"UCUM normalization for units MAY use a simpler deterministic lookup table
rather than full retrieval — deterministic lookup is preferred here per the
'deterministic first' principle."
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Optional

DEFAULT_UCUM_PATH = Path("configs/ucum_subset.json")

# Standard clinically grounded alias lookup table mapping common clinical surface forms to valid UCUM syntax
UCUM_SYNONYM_MAP: dict[str, str] = {
    # Concentration mass / volume
    "g/dl": "g/dL",
    "g/dl.": "g/dL",
    "gm/dl": "g/dL",
    "g/100ml": "g/dL",
    "mg/dl": "mg/dL",
    "mg/dl.": "mg/dL",
    "mg%": "mg/dL",
    "ug/dl": "ug/dL",
    "mcg/dl": "ug/dL",
    "ng/dl": "ng/dL",
    "pg/ml": "pg/mL",
    "ng/ml": "ng/mL",
    "ug/ml": "ug/mL",
    "mcg/ml": "ug/mL",
    "mg/l": "mg/L",
    "g/l": "g/L",
    # Molar concentration
    "mmol/l": "mmol/L",
    "umol/l": "umol/L",
    "mcmol/l": "umol/L",
    "nmol/l": "nmol/L",
    "pmol/l": "pmol/L",
    "meq/l": "meq/L",
    # Cell counts / cell density
    "10*3/ul": "10*3/uL",
    "10^3/ul": "10*3/uL",
    "10e3/ul": "10*3/uL",
    "k/ul": "10*3/uL",
    "thou/ul": "10*3/uL",
    "x10^3/ul": "10*3/uL",
    "x10*3/ul": "10*3/uL",
    "10*6/ul": "10*6/uL",
    "10^6/ul": "10*6/uL",
    "10e6/ul": "10*6/uL",
    "m/ul": "10*6/uL",
    "mil/ul": "10*6/uL",
    "x10^6/ul": "10*6/uL",
    "x10*6/ul": "10*6/uL",
    # Enzymatic activity
    "u/l": "U/L",
    "iu/l": "[iU]/L",
    "mu/l": "m[iU]/L",
    # Ratio / Percentage
    "%": "%",
    "percent": "%",
    "pct": "%",
    # Medication dosing units
    "mg": "mg",
    "milligram": "mg",
    "milligrams": "mg",
    "g": "g",
    "gram": "g",
    "grams": "g",
    "gm": "g",
    "mcg": "ug",
    "microgram": "ug",
    "micrograms": "ug",
    "ug": "ug",
    "ml": "mL",
    "milliliter": "mL",
    "milliliters": "mL",
    "l": "L",
    "liter": "L",
    "tablet": "{tbl}",
    "tablets": "{tbl}",
    "tab": "{tbl}",
    "tabs": "{tbl}",
    "capsule": "{caps}",
    "capsules": "{caps}",
    "cap": "{caps}",
    "caps": "{caps}",
    "puff": "{puff}",
    "puffs": "{puff}",
    "drop": "[drp]",
    "drops": "[drp]",
    "gtt": "[drp]",
}


def _clean_unit_string(unit: str) -> str:
    """Normalize whitespace and casing for canonical lookup."""
    u = unit.strip()
    # Normalize common Unicode characters
    u = u.replace("μ", "u").replace("µ", "u")
    return u.lower()


class UCUMNormalizer:
    """Deterministic UCUM unit normalizer."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self.lookup_table: dict[str, str] = dict(UCUM_SYNONYM_MAP)

        # Merge config file if present
        cfg_path = config_path or DEFAULT_UCUM_PATH
        if not cfg_path.exists():
            proj_root = Path(__file__).resolve().parents[3]
            alt_path = proj_root / cfg_path
            if alt_path.exists():
                cfg_path = alt_path

        if cfg_path.exists():
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        canon = v.get("ucum", v.get("canonical", k))
                        self.lookup_table[k.strip().lower()] = canon
                        self.lookup_table[_clean_unit_string(k)] = canon
            except Exception:
                pass

    def normalize(self, raw_unit: Optional[str]) -> tuple[Optional[str], float]:
        """Normalize a raw clinical unit string to standard UCUM code.

        Returns:
            (canonical_ucum_symbol, confidence)
            Confidence is 1.0 on exact/synonym match, 0.0 if unresolvable.
        """
        if not raw_unit or not raw_unit.strip():
            return None, 0.0

        cleaned = _clean_unit_string(raw_unit)

        # Exact match in lookup table
        if cleaned in self.lookup_table:
            return self.lookup_table[cleaned], 1.0

        # Strip surrounding brackets or parentheses: e.g. "(mg/dL)" -> "mg/dL"
        stripped = re.sub(r"^[\(\[\{]+|[\)\]\}]+$", "", cleaned).strip()
        if stripped in self.lookup_table:
            return self.lookup_table[stripped], 1.0

        # Unresolvable unit
        return None, 0.0


# Module-level singleton
_default_ucum = UCUMNormalizer()


def normalize_ucum(raw_unit: Optional[str]) -> tuple[Optional[str], float]:
    """Convenience functional interface for UCUM normalization."""
    return _default_ucum.normalize(raw_unit)
