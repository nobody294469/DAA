"""Loaders for local FHIR R4B Conformance & Extension Definitions."""
import json
from pathlib import Path
from typing import Any

from fhir.resources.codesystem import CodeSystem
from fhir.resources.structuredefinition import StructureDefinition
from fhir.resources.valueset import ValueSet

_DEFINITIONS_DIR = Path(__file__).parent


def load_definition_json(filename: str) -> dict[str, Any]:
    """Load raw JSON definition file."""
    path = _DEFINITIONS_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"FHIR definition artifact not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_temporal_status_codesystem() -> CodeSystem:
    """Return validated FHIR R4B CodeSystem for temporal-status."""
    data = load_definition_json("CodeSystem-temporal-status.json")
    return CodeSystem(**data)


def get_temporal_status_valueset() -> ValueSet:
    """Return validated FHIR R4B ValueSet for temporal-status."""
    data = load_definition_json("ValueSet-temporal-status.json")
    return ValueSet(**data)


def get_temporal_status_structuredefinition() -> StructureDefinition:
    """Return validated FHIR R4B StructureDefinition for temporal-status extension."""
    data = load_definition_json("StructureDefinition-temporal-status.json")
    return StructureDefinition(**data)
