"""Audit Report Generator conforming strictly to PROJECT_SPEC.md §7.8 and schemas/audit_report.json.

Authority:
  - PROJECT_SPEC.md §7.8 (Audit Report I/O)
  - schemas/audit_report.json
  - schemas/claim.json (§8)
  - schemas/evidence.json (§9)
  - schemas/verification_result.json (§10)
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from clindoc.extract.contracts import Claim, Evidence
from clindoc.schema.verification import VerificationResult


class AuditReportSummary(BaseModel):
    """Aggregated verification counts and escalation metrics matching schemas/audit_report.json."""
    model_config = ConfigDict(extra="forbid")

    n_claims: int = Field(..., ge=0, description="Total number of extracted claims")
    n_supported: int = Field(..., ge=0, description="Count of claims verified as SUPPORTED")
    n_discrepancy: int = Field(..., ge=0, description="Count of claims verified as DISCREPANCY")
    n_insufficient_evidence: int = Field(..., ge=0, description="Count of claims categorized as INSUFFICIENT_EVIDENCE")
    n_escalated_to_llm: int = Field(..., ge=0, description="Count of claims escalated to Stage C LLM Judge")

    @model_validator(mode="after")
    def validate_invariants(self) -> AuditReportSummary:
        total = self.n_supported + self.n_discrepancy + self.n_insufficient_evidence
        if total != self.n_claims:
            raise ValueError(
                f"Sum of labels (supported={self.n_supported}, discrepancy={self.n_discrepancy}, "
                f"insufficient_evidence={self.n_insufficient_evidence} -> total={total}) "
                f"must equal n_claims ({self.n_claims})"
            )
        if self.n_escalated_to_llm > self.n_claims:
            raise ValueError(
                f"n_escalated_to_llm ({self.n_escalated_to_llm}) cannot exceed n_claims ({self.n_claims})"
            )
        return self


class AuditReport(BaseModel):
    """Comprehensive document-level verification and audit artifact (§7.8)."""
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(..., min_length=1, description="Identifier of the audited document")
    generated_at: str = Field(..., description="ISO8601 timestamp of audit report generation")
    fhir_bundle_id: str = Field(..., min_length=1, description="Identifier of the corresponding exported FHIR R4B Bundle")
    claims: list[Claim] = Field(..., description="All structured claims extracted from the document")
    evidence: list[Evidence] = Field(..., description="Deduplicated repository of source evidence spans")
    verifications: list[VerificationResult] = Field(..., description="Atomic verification verdicts for all extracted claims")
    summary: AuditReportSummary = Field(..., description="Aggregated verification counts and escalation metrics")

    @field_validator("document_id", "fhir_bundle_id")
    @classmethod
    def string_not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("ID cannot be empty or blank")
        return v.strip()

    @field_validator("generated_at")
    @classmethod
    def validate_iso8601(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("generated_at cannot be empty")
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception as err:
            raise ValueError(f"generated_at must be a valid ISO8601 date-time string: {err}") from err
        return v

    def to_dict(self) -> dict[str, Any]:
        """Serialize audit report to plain dictionary strictly conforming to schemas/audit_report.json."""
        return self.model_dump(mode="json")

    def to_json(self, indent: int = 2) -> str:
        """Serialize audit report to JSON string."""
        return json.dumps(self.to_dict(), indent=indent)

    def save(self, path: Union[Path, str], indent: int = 2) -> None:
        """Write audit report JSON to disk."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(self.to_json(indent=indent))

    @classmethod
    def load(cls, path: Union[Path, str]) -> AuditReport:
        """Load and validate an AuditReport from a JSON file."""
        p = Path(path)
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)

    def validate_schema(self, schemas_dir: Optional[Union[Path, str]] = None) -> bool:
        """Validate this report against schemas/audit_report.json."""
        return validate_audit_report(self, schemas_dir=schemas_dir)


def generate_audit_report(
    doc_id: Optional[str] = None,
    claims: Optional[Sequence[Claim]] = None,
    evidence: Optional[Union[Sequence[Evidence], Mapping[str, Evidence]]] = None,
    verifications: Optional[Union[Sequence[VerificationResult], Mapping[str, VerificationResult]]] = None,
    fhir_bundle_id: Optional[str] = None,
    *,
    document_id: Optional[str] = None,
    generated_at: Optional[Union[datetime, str]] = None,
) -> AuditReport:
    """Generate canonical audit_report.json representation.

    Args:
        doc_id: Source document identifier (can also be passed as document_id).
        claims: Sequence of extracted clinical claims.
        evidence: Sequence or mapping (evidence_id -> Evidence) of grounding source spans.
        verifications: Sequence or mapping (claim_id -> VerificationResult) of verification verdicts.
        fhir_bundle_id: Identifier of the corresponding exported FHIR R4B Bundle.
        document_id: Keyword-only alias for doc_id.
        generated_at: Optional explicit timestamp (datetime or ISO8601 string). Defaults to UTC now.

    Returns:
        AuditReport instance strictly conforming to schemas/audit_report.json.

    Raises:
        ValueError: If input arguments are missing, malformed, empty, or inconsistent.
        TypeError: If input elements are not of canonical types.
    """
    effective_doc_id = document_id if document_id is not None else doc_id
    if not effective_doc_id or not isinstance(effective_doc_id, str) or not effective_doc_id.strip():
        raise ValueError("doc_id / document_id must be a non-empty string")
    effective_doc_id = effective_doc_id.strip()

    if not fhir_bundle_id or not isinstance(fhir_bundle_id, str) or not fhir_bundle_id.strip():
        raise ValueError("fhir_bundle_id must be a non-empty string")
    effective_fhir_bundle_id = fhir_bundle_id.strip()

    if claims is None or len(claims) == 0:
        raise ValueError("claims must be a non-empty sequence of Claim objects")

    # Validate claims
    claim_list: list[Claim] = []
    seen_claim_ids: set[str] = set()
    for idx, c in enumerate(claims):
        if not isinstance(c, Claim):
            raise TypeError(f"claims[{idx}] must be a Claim instance, got {type(c)}")
        if c.document_id != effective_doc_id:
            raise ValueError(
                f"Claim '{c.claim_id}' document_id '{c.document_id}' does not match '{effective_doc_id}'"
            )
        if c.claim_id in seen_claim_ids:
            raise ValueError(f"Duplicate claim_id '{c.claim_id}' in claims list")
        seen_claim_ids.add(c.claim_id)
        claim_list.append(c)

    # Process evidence
    if evidence is None:
        raise ValueError("evidence must not be None")

    evidence_lookup: dict[str, Evidence] = {}
    if isinstance(evidence, Mapping):
        for k, ev in evidence.items():
            if not isinstance(ev, Evidence):
                raise TypeError(f"evidence[{k}] must be an Evidence instance, got {type(ev)}")
            if ev.document_id != effective_doc_id:
                raise ValueError(
                    f"Evidence '{ev.evidence_id}' document_id '{ev.document_id}' does not match '{effective_doc_id}'"
                )
            evidence_lookup[ev.evidence_id] = ev
    elif isinstance(evidence, Sequence):
        for idx, ev in enumerate(evidence):
            if not isinstance(ev, Evidence):
                raise TypeError(f"evidence[{idx}] must be an Evidence instance, got {type(ev)}")
            if ev.document_id != effective_doc_id:
                raise ValueError(
                    f"Evidence '{ev.evidence_id}' document_id '{ev.document_id}' does not match '{effective_doc_id}'"
                )
            evidence_lookup[ev.evidence_id] = ev
    else:
        raise TypeError(f"evidence must be a Sequence or Mapping of Evidence objects, got {type(evidence)}")

    # Check evidence referential integrity
    for c in claim_list:
        if c.evidence_id not in evidence_lookup:
            raise ValueError(
                f"Claim '{c.claim_id}' references unknown evidence_id '{c.evidence_id}'"
            )

    # Deduplicated evidence list
    # Preserve order of appearance in claims, then remaining evidence
    deduped_evidence: list[Evidence] = []
    added_ev_ids: set[str] = set()
    for c in claim_list:
        if c.evidence_id not in added_ev_ids:
            deduped_evidence.append(evidence_lookup[c.evidence_id])
            added_ev_ids.add(c.evidence_id)
    for ev_id, ev in evidence_lookup.items():
        if ev_id not in added_ev_ids:
            deduped_evidence.append(ev)
            added_ev_ids.add(ev_id)

    # Process verifications
    if verifications is None:
        raise ValueError("verifications must not be None")

    verification_lookup: dict[str, VerificationResult] = {}
    if isinstance(verifications, Mapping):
        for k, vr in verifications.items():
            if not isinstance(vr, VerificationResult):
                raise TypeError(f"verifications[{k}] must be a VerificationResult instance, got {type(vr)}")
            verification_lookup[vr.claim_id] = vr
    elif isinstance(verifications, Sequence):
        for idx, vr in enumerate(verifications):
            if not isinstance(vr, VerificationResult):
                raise TypeError(f"verifications[{idx}] must be a VerificationResult instance, got {type(vr)}")
            if vr.claim_id in verification_lookup:
                raise ValueError(f"Duplicate VerificationResult for claim_id '{vr.claim_id}'")
            verification_lookup[vr.claim_id] = vr
    else:
        raise TypeError(f"verifications must be a Sequence or Mapping of VerificationResult objects, got {type(verifications)}")

    # Check verification referential integrity
    verification_list: list[VerificationResult] = []
    for c in claim_list:
        if c.claim_id not in verification_lookup:
            raise ValueError(f"Missing VerificationResult for claim_id '{c.claim_id}'")
        vr = verification_lookup[c.claim_id]
        if vr.evidence_id != c.evidence_id:
            raise ValueError(
                f"VerificationResult '{vr.verification_id}' evidence_id '{vr.evidence_id}' "
                f"does not match claim.evidence_id '{c.evidence_id}'"
            )
        verification_list.append(vr)

    # Check for orphaned verifications
    for vr_claim_id in verification_lookup:
        if vr_claim_id not in seen_claim_ids:
            raise ValueError(
                f"VerificationResult for claim_id '{vr_claim_id}' has no corresponding Claim in document"
            )

    # Timestamp
    if generated_at is None:
        timestamp_str = datetime.now(timezone.utc).isoformat()
    elif isinstance(generated_at, datetime):
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        timestamp_str = generated_at.isoformat()
    else:
        timestamp_str = str(generated_at)

    # Calculate summary counts
    n_claims = len(claim_list)
    n_supported = sum(1 for vr in verification_list if vr.label == "SUPPORTED")
    n_discrepancy = sum(1 for vr in verification_list if vr.label == "DISCREPANCY")
    n_insufficient_evidence = sum(1 for vr in verification_list if vr.label == "INSUFFICIENT_EVIDENCE")
    n_escalated_to_llm = sum(1 for vr in verification_list if vr.escalated or vr.stage_reached == "C")

    summary = AuditReportSummary(
        n_claims=n_claims,
        n_supported=n_supported,
        n_discrepancy=n_discrepancy,
        n_insufficient_evidence=n_insufficient_evidence,
        n_escalated_to_llm=n_escalated_to_llm,
    )

    report = AuditReport(
        document_id=effective_doc_id,
        generated_at=timestamp_str,
        fhir_bundle_id=effective_fhir_bundle_id,
        claims=claim_list,
        evidence=deduped_evidence,
        verifications=verification_list,
        summary=summary,
    )

    return report


def validate_audit_report(
    report_or_dict: Union[AuditReport, dict[str, Any]],
    schemas_dir: Optional[Union[Path, str]] = None,
) -> bool:
    """Validate that an audit report dictionary or AuditReport strictly complies with schemas/audit_report.json."""
    if isinstance(report_or_dict, AuditReport):
        data = report_or_dict.to_dict()
    elif isinstance(report_or_dict, dict):
        data = report_or_dict
    else:
        raise TypeError(f"Expected AuditReport or dict, got {type(report_or_dict)}")

    # Resolve schema directory
    if schemas_dir is not None:
        base_dir = Path(schemas_dir)
    else:
        candidates = [
            Path("schemas"),
            Path(__file__).resolve().parents[3] / "schemas",
            Path(__file__).resolve().parent / "schemas",
        ]
        base_dir = next((c for c in candidates if (c / "audit_report.json").exists()), Path("schemas"))

    schema_file = base_dir / "audit_report.json"
    if schema_file.exists():
        with open(schema_file, "r", encoding="utf-8") as f:
            schema_data = json.load(f)
        required_from_schema = set(schema_data.get("required", []))
    else:
        required_from_schema = {
            "document_id",
            "generated_at",
            "fhir_bundle_id",
            "claims",
            "evidence",
            "verifications",
            "summary",
        }

    # 1. Top-level checks
    missing_top = required_from_schema - set(data.keys())
    if missing_top:
        raise ValueError(f"Audit report missing required fields: {sorted(missing_top)}")

    extra_top = set(data.keys()) - required_from_schema
    if extra_top:
        raise ValueError(f"Audit report contains forbidden additional properties: {sorted(extra_top)}")

    if not isinstance(data["document_id"], str) or not data["document_id"].strip():
        raise ValueError("document_id must be a non-empty string")
    if not isinstance(data["fhir_bundle_id"], str) or not data["fhir_bundle_id"].strip():
        raise ValueError("fhir_bundle_id must be a non-empty string")
    if not isinstance(data["generated_at"], str):
        raise ValueError("generated_at must be an ISO8601 string")
    try:
        datetime.fromisoformat(data["generated_at"].replace("Z", "+00:00"))
    except Exception as e:
        raise ValueError(f"generated_at is not a valid ISO8601 date-time: {e}")

    # 2. Check summary
    summary = data["summary"]
    if not isinstance(summary, dict):
        raise ValueError("summary must be a dictionary")
    required_summary = {"n_claims", "n_supported", "n_discrepancy", "n_insufficient_evidence", "n_escalated_to_llm"}
    missing_summary = required_summary - set(summary.keys())
    if missing_summary:
        raise ValueError(f"summary missing required fields: {sorted(missing_summary)}")
    extra_summary = set(summary.keys()) - required_summary
    if extra_summary:
        raise ValueError(f"summary contains forbidden additional properties: {sorted(extra_summary)}")
    for k in required_summary:
        val = summary[k]
        if not isinstance(val, int) or isinstance(val, bool) or val < 0:
            raise ValueError(f"summary.{k} must be an integer >= 0, got {val}")

    # 3. Check claims
    claims = data["claims"]
    if not isinstance(claims, list):
        raise ValueError("claims must be a list")

    # 4. Check evidence
    evidence = data["evidence"]
    if not isinstance(evidence, list):
        raise ValueError("evidence must be a list")
    evidence_ids = [e.get("evidence_id") for e in evidence if isinstance(e, dict)]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("evidence array contains duplicate evidence_id entries (must be deduplicated)")

    # 5. Check verifications
    verifications = data["verifications"]
    if not isinstance(verifications, list):
        raise ValueError("verifications must be a list")

    # Summary count cross-checks
    if summary["n_claims"] != len(claims):
        raise ValueError(f"summary.n_claims ({summary['n_claims']}) does not match len(claims) ({len(claims)})")

    label_sum = summary["n_supported"] + summary["n_discrepancy"] + summary["n_insufficient_evidence"]
    if label_sum != summary["n_claims"]:
        raise ValueError(f"Sum of labels ({label_sum}) does not match summary.n_claims ({summary['n_claims']})")

    # Deep validation using Pydantic models (enforces all invariants and forbidden extras)
    AuditReport.model_validate(data)
    return True
