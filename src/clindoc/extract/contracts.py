"""Canonical Claim and Evidence Python models for the extraction subsystem.

Conforms strictly to:
  - schemas/claim.json  (§8, DEC-015, DEC-017)
  - schemas/evidence.json (§9)
  - PHASE0_ARCHITECTURE_RESOLUTION §5, §6

These are the ONLY models that should be produced by extractors and consumed
by the verification cascade.  Do NOT add fields not present in these schemas.
"""
from __future__ import annotations

import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ─── CANONICAL FIELD TYPES (DEC-017, locked vocabulary) ──────────────────────

FieldType = Literal[
    "lab.value",
    "lab.unit",
    "lab.flag",
    "lab.reference_range",
    "lab.test_name",
    "med.name",
    "med.dose",
    "med.route",
    "med.frequency",
    "med.duration",
]

TIER_1_FIELDS: frozenset[str] = frozenset({
    "lab.value",
    "lab.flag",
    "med.name",
    "med.dose",
    "med.route",
    "med.frequency",
})

TIER_2_FIELDS: frozenset[str] = frozenset({
    "lab.unit",
    "lab.reference_range",
    "lab.test_name",
    "med.duration",
})

ExtractionMethod = Literal["deterministic", "gliner", "llm_narrative"]
TemporalStatus = Literal["current", "historical", "discontinued", "planned", "unspecified"]
CodeSystem = Optional[Literal["LOINC", "RxNorm", "UCUM"]]
RouteType = Literal["deterministic", "gliner", "llm_narrative"]


# ─── BOUNDING BOX ────────────────────────────────────────────────────────────

class BBoxModel(BaseModel):
    """Bounding box conforming to schemas/evidence.json bbox contract."""
    model_config = ConfigDict(extra="forbid")

    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_invariants(self) -> "BBoxModel":
        if self.x0 > self.x1:
            raise ValueError(f"bbox x0 ({self.x0}) > x1 ({self.x1})")
        if self.y0 > self.y1:
            raise ValueError(f"bbox y0 ({self.y0}) > y1 ({self.y1})")
        return self

    def to_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]

    @classmethod
    def from_list(cls, lst: list[float]) -> "BBoxModel":
        if len(lst) != 4:
            raise ValueError(f"Expected 4 coordinates, got {len(lst)}")
        return cls(x0=lst[0], y0=lst[1], x1=lst[2], y1=lst[3])


# ─── EVIDENCE (§9, schemas/evidence.json) ────────────────────────────────────

class Evidence(BaseModel):
    """Source evidence grounding a clinical claim.

    Conforms exactly to schemas/evidence.json.
    Character offsets are page-local: page.get_page_text()[char_start:char_end]
    reproduces the verbatim evidence text.
    """
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique identifier for the evidence span (UUID)",
    )
    document_id: str = Field(..., description="Identifier of the source document")
    page_number: int = Field(..., ge=1, description="1-indexed page number containing the evidence")
    text: str = Field(..., description="Exact verbatim source text span")
    char_start: int = Field(..., ge=0, description="Starting character offset within the page text")
    char_end: int = Field(..., ge=0, description="Ending character offset within the page text")
    bbox: BBoxModel = Field(..., description="Bounding box enclosing the evidence span on the page")
    ocr_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregated OCR confidence for the span tokens; null for born-digital",
    )

    @model_validator(mode="after")
    def validate_char_offsets(self) -> "Evidence":
        if self.char_end < self.char_start:
            raise ValueError(
                f"char_end ({self.char_end}) must be >= char_start ({self.char_start})"
            )
        return self

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Evidence text must not be empty")
        return v

    @classmethod
    def deduplicate_key(cls, document_id: str, page_number: int, char_start: int, char_end: int) -> str:
        """Compute deduplication key per PHASE0_ARCHITECTURE_RESOLUTION §5."""
        return f"{document_id}::{page_number}::{char_start}::{char_end}"


# ─── CLAIM (§8, schemas/claim.json) ──────────────────────────────────────────

class Claim(BaseModel):
    """Atomic extracted clinical claim.

    Conforms exactly to schemas/claim.json.
    Normalized codes are left null by the extraction subsystem; they are
    populated by the subsequent terminology normalization stage (Phase 3).
    """
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Globally unique identifier for the claim (UUID)",
    )
    document_id: str = Field(..., description="Identifier of the source document")
    group_id: Optional[str] = Field(
        default=None,
        description="Groups atomic claims from the same clinical entity instance (DEC-015)",
    )
    field_type: FieldType = Field(..., description="Canonical 10-field controlled vocabulary (DEC-017)")
    value: str = Field(..., description="Extracted raw value as string")
    value_numeric: Optional[float] = Field(
        default=None,
        description="Parsed numeric value for numeric fields (lab.value, med.dose), else null",
    )
    unit_raw: Optional[str] = Field(
        default=None,
        description="Unnormalized unit string from source text",
    )
    extraction_method: ExtractionMethod = Field(
        ..., description="Pipeline method that extracted the claim",
    )
    extraction_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Extraction confidence score in [0, 1]",
    )
    evidence_id: str = Field(..., min_length=1, description="Foreign key into Evidence store (§9)")
    normalized_code: Optional[str] = Field(
        default=None,
        description="Standardized clinical code (LOINC, RxNorm, UCUM); null until terminology stage",
    )
    code_system: CodeSystem = Field(
        default=None,
        description="Standard coding system or null",
    )
    code_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence of terminology retrieval match; 0.0 until terminology stage",
    )
    negated: bool = Field(default=False, description="True if finding is negated in clinical context")
    temporal_status: TemporalStatus = Field(
        default="unspecified",
        description="Clinical temporal qualification",
    )
    region_id: str = Field(..., description="Links back to originating RoutingDecision.region_id")

    @field_validator("value")
    @classmethod
    def value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Claim value must not be empty")
        return v


# ─── ROUTING DECISION (§7.2, schemas/routing_decision.json) ──────────────────

class RoutingDecision(BaseModel):
    """Routing decision for a document region.

    Conforms exactly to schemas/routing_decision.json.
    """
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., description="Identifier of the source document")
    region_id: str = Field(..., description="Identifier of the parsed region/block within the document")
    route: RouteType = Field(..., description="Extraction path selected for this region")
    reason: str = Field(..., description="Human- and machine-readable explanation of routing choice")
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score of the routing classifier/heuristic",
    )


# ─── EXTRACTION RESULT ────────────────────────────────────────────────────────

class ExtractionResult(BaseModel):
    """Container for the output of a single extraction pass.

    Bundles RoutingDecision, Evidence, and Claims produced for one document region.
    """
    routing_decision: RoutingDecision
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    # Human-readable diagnostics
    diagnostics: dict = Field(default_factory=dict)


class DocumentExtractionOutput(BaseModel):
    """Complete extraction output for a single DocumentText.

    Produced by ExtractionRouter.extract_document() and consumed by
    the subsequent terminology normalization and verification stages.
    """
    document_id: str
    regions: list[ExtractionResult] = Field(default_factory=list)

    @property
    def all_claims(self) -> list[Claim]:
        return [c for r in self.regions for c in r.claims]

    @property
    def all_evidence(self) -> list[Evidence]:
        return [e for r in self.regions for e in r.evidence]

    @property
    def all_routing_decisions(self) -> list[RoutingDecision]:
        return [r.routing_decision for r in self.regions]
