"""Unit tests for Phase 3 Terminology Normalization subsystem.

Authority:
  - PROJECT_SPEC §7.5, §8.3, §14, §15.3, §21.6
  - DEC-002: code_confidence thresholding
  - DEC-007: hybrid retrieval and deterministic UCUM lookup
"""
import pytest

from clindoc.extract.contracts import Claim
from clindoc.normalize import (
    CandidateMatch,
    HybridTerminologyRetriever,
    TerminologyNormalizer,
    UCUMNormalizer,
    normalize_ucum,
)


def _make_claim(
    field_type: str,
    value: str,
    unit_raw: str | None = None,
    claim_id: str = "test-claim-1",
) -> Claim:
    """Helper to construct a valid Claim instance."""
    return Claim(
        claim_id=claim_id,
        document_id="doc-test-001",
        group_id="group-1",
        field_type=field_type,
        value=value,
        value_numeric=None,
        unit_raw=unit_raw,
        extraction_method="deterministic",
        extraction_confidence=0.95,
        evidence_id="ev-test-001",
        region_id="reg-1",
    )


class TestUCUMNormalization:
    """Test deterministic UCUM unit normalization per §14.2."""

    def test_canonical_units_exact_match(self):
        units = ["g/dL", "mg/dL", "mmol/L", "10*3/uL", "10*6/uL", "U/L", "%"]
        for u in units:
            canon, conf = normalize_ucum(u)
            assert canon == u
            assert conf == 1.0

    def test_clinical_casing_and_alias_normalization(self):
        test_cases = [
            ("mg/dl", "mg/dL"),
            ("g/dl", "g/dL"),
            ("gm/dl", "g/dL"),
            ("mmol/l", "mmol/L"),
            ("u/l", "U/L"),
            ("iu/l", "[iU]/L"),
            ("10^3/ul", "10*3/uL"),
            ("10*3/ul", "10*3/uL"),
            ("k/ul", "10*3/uL"),
            ("10^6/ul", "10*6/uL"),
            ("m/ul", "10*6/uL"),
            ("mg", "mg"),
            ("mcg", "ug"),
            ("ug", "ug"),
            ("ml", "mL"),
            ("tablet", "{tbl}"),
            ("tabs", "{tbl}"),
        ]
        for raw, expected in test_cases:
            canon, conf = normalize_ucum(raw)
            assert canon == expected, f"Failed for raw unit: {raw}"
            assert conf == 1.0

    def test_parentheses_and_brackets_stripped(self):
        canon, conf = normalize_ucum("(mg/dL)")
        assert canon == "mg/dL"
        assert conf == 1.0

    def test_empty_or_none_unit_returns_null(self):
        canon, conf = normalize_ucum(None)
        assert canon is None
        assert conf == 0.0

        canon, conf = normalize_ucum("   ")
        assert canon is None
        assert conf == 0.0

    def test_unrecognized_unit_returns_null(self):
        canon, conf = normalize_ucum("nonexistent_unit_xyz")
        assert canon is None
        assert conf == 0.0


class TestLOINCRetrieval:
    """Test hybrid LOINC retrieval for lab.test_name claims."""

    @pytest.fixture
    def normalizer(self):
        return TerminologyNormalizer()

    def test_exact_loinc_lookup(self, normalizer):
        claim = _make_claim("lab.test_name", "Hemoglobin")
        normalizer.normalize_claim(claim)
        assert claim.normalized_code == "718-7"
        assert claim.code_system == "LOINC"
        assert claim.code_confidence >= 0.75

    def test_synonym_loinc_lookup(self, normalizer):
        # WBC alias maps to 6690-2 (White Blood Cell Count)
        claim = _make_claim("lab.test_name", "WBC")
        normalizer.normalize_claim(claim)
        assert claim.normalized_code == "6690-2"
        assert claim.code_system == "LOINC"
        assert claim.code_confidence >= 0.75

        # Platelets alias maps to 777-3 (Platelet Count)
        claim2 = _make_claim("lab.test_name", "Platelets")
        normalizer.normalize_claim(claim2)
        assert claim2.normalized_code == "777-3"
        assert claim2.code_system == "LOINC"

    def test_casing_insensitivity(self, normalizer):
        claim = _make_claim("lab.test_name", "glucose")
        normalizer.normalize_claim(claim)
        assert claim.normalized_code == "2345-7"
        assert claim.code_system == "LOINC"


class TestRxNormRetrieval:
    """Test hybrid RxNorm retrieval for med.name claims."""

    @pytest.fixture
    def normalizer(self):
        return TerminologyNormalizer()

    def test_exact_rxnorm_lookup(self, normalizer):
        claim = _make_claim("med.name", "amoxicillin")
        normalizer.normalize_claim(claim)
        assert claim.normalized_code == "723"
        assert claim.code_system == "RxNorm"
        assert claim.code_confidence >= 0.75

    def test_capitalized_rxnorm_lookup(self, normalizer):
        claim = _make_claim("med.name", "Lisinopril")
        normalizer.normalize_claim(claim)
        assert claim.normalized_code == "29046"
        assert claim.code_system == "RxNorm"
        assert claim.code_confidence >= 0.75

    def test_fuzzy_slight_typo_retrieval(self, normalizer):
        # "metformin" with a minor typo
        claim = _make_claim("med.name", "metformn")
        normalizer.normalize_claim(claim)
        # Should either match metformin or have candidates
        candidates = normalizer.audit_candidates.get(claim.claim_id, [])
        assert len(candidates) > 0
        top = candidates[0]
        assert "metformin" in top["display"].lower()


class TestThresholdGatingAndInvariants:
    """Test DEC-002 thresholding and §14.3 audit invariants."""

    def test_strict_invariant_value_never_altered(self):
        normalizer = TerminologyNormalizer()
        raw_strings = [
            "  Hemoglobin (Hb) ",
            "amoxicillin 500mg",
            "mg/dl",
            "Random Unknown Text",
        ]
        for raw in raw_strings:
            claim = _make_claim("med.name", raw)
            normalizer.normalize_claim(claim)
            assert claim.value == raw, "§14.3 violation: claim.value was altered!"

    def test_below_threshold_clears_code_and_system(self):
        # Set impossibly high threshold (0.999) on noisy string
        strict_normalizer = TerminologyNormalizer(rxnorm_threshold=0.999)
        claim = _make_claim("med.name", "some random drug variant")
        strict_normalizer.normalize_claim(claim)

        # Code and system MUST be null per DEC-002 and §7.5
        assert claim.normalized_code is None
        assert claim.code_system is None
        # But candidates must still be preserved for audit trail
        candidates = strict_normalizer.audit_candidates.get(claim.claim_id)
        assert candidates is not None

    def test_batch_normalization(self):
        normalizer = TerminologyNormalizer()
        claims = [
            _make_claim("lab.test_name", "Creatinine", claim_id="c1"),
            _make_claim("med.name", "Atorvastatin", claim_id="c2"),
            _make_claim("lab.unit", "mg/dL", claim_id="c3"),
        ]
        result = normalizer.normalize_claims(claims)
        assert len(result) == 3
        assert result[0].normalized_code == "2160-0"
        assert result[0].code_system == "LOINC"
        assert result[1].normalized_code == "83367"
        assert result[1].code_system == "RxNorm"
        assert result[2].normalized_code == "mg/dL"
        assert result[2].code_system == "UCUM"
