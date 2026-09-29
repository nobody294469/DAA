"""Unit tests for Phase 3 Terminology Normalization subsystem.

Authority:
  - PROJECT_SPEC §7.5, §8.3, §14, §15.3, §21.6
  - DEC-002: code_confidence thresholding
  - DEC-007: hybrid retrieval and deterministic UCUM lookup
"""
import pytest

from clindoc.extract.contracts import Claim, FieldType
from clindoc.normalize import (
    CandidateMatch,
    HybridTerminologyRetriever,
    NGramProxyEmbedder,
    SapBERTEmbedder,
    SemanticEmbedder,
    TerminologyNormalizer,
    UCUMNormalizer,
    normalize_ucum,
)


def _make_claim(
    field_type: FieldType,
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


class TestSemanticEmbedder:
    """Test DEC-007 pluggable semantic embedder interface and SapBERT / n-gram fallback."""

    def test_ngram_proxy_embedder_similarity(self):
        embedder = NGramProxyEmbedder()
        # Exact match
        assert embedder.similarity("hemoglobin", "hemoglobin") == 1.0
        # High overlap
        sim_high = embedder.similarity("amoxicillin", "amoxicillin 500mg")
        assert sim_high > 0.5
        # Disjoint
        assert embedder.similarity("aspirin", "zofran") < 0.2
        # Empty text
        assert embedder.similarity("", "something") == 0.0

    def test_sapbert_embedder_specification_and_interface(self):
        embedder = SapBERTEmbedder()
        assert embedder.model_name == "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"
        assert isinstance(embedder.is_available(), bool)
        assert isinstance(embedder, SemanticEmbedder)

    def test_custom_pluggable_embedder_in_hybrid_retriever(self):
        class DummyExactEmbedder:
            def similarity_scores(self, query: str, candidates: list[str]) -> list[float]:
                return [1.0 if query.strip().lower() == c.strip().lower() else 0.0 for c in candidates]

        custom_embedder = DummyExactEmbedder()
        retriever = HybridTerminologyRetriever(
            system_name="LOINC",
            semantic_embedder=custom_embedder,
            alpha=0.5,
        )
        assert retriever.semantic_embedder is custom_embedder

        retriever.index_concepts({
            "White Blood Cells": {"code": "6690-2", "canonical_name": "White Blood Cells"},
            "Red Blood Cells": {"code": "789-8", "canonical_name": "Red Blood Cells"},
        })
        scored = retriever.retrieve("White Blood Cells", top_k=2)
        assert len(scored) >= 1
        assert scored[0].code == "6690-2"


class TestNumericClaimsUnitHandling:
    """Test PROJECT_SPEC §8.3 numeric claim preservation and UCUM unit normalization."""

    def test_lab_value_unit_normalization_preserves_claim_value(self):
        normalizer = TerminologyNormalizer()
        claim = _make_claim("lab.value", "14.2", unit_raw="gm/dl")
        normalizer.normalize_claim(claim)

        # Original claim fields must remain unchanged (§8.3 & audit invariant)
        assert claim.value == "14.2"
        assert claim.unit_raw == "gm/dl"

        # Numeric value populated
        assert claim.value_numeric == 14.2

        # Canonical UCUM code and system populated on claim
        assert claim.normalized_code == "g/dL"
        assert claim.code_system == "UCUM"
        assert claim.code_confidence == 1.0

        # Downstream FHIR quantity access via get_normalized_unit
        norm_unit = normalizer.get_normalized_unit(claim.claim_id)
        assert norm_unit == "g/dL"

    def test_med_dose_unit_normalization_and_numeric_parsing(self):
        normalizer = TerminologyNormalizer()
        claim = _make_claim("med.dose", "500", unit_raw="mg")
        normalizer.normalize_claim(claim)

        assert claim.value == "500"
        assert claim.unit_raw == "mg"
        assert claim.value_numeric == 500.0
        assert claim.normalized_code == "mg"
        assert claim.code_system == "UCUM"

        norm_unit = normalizer.get_normalized_unit(claim.claim_id)
        assert norm_unit == "mg"

    def test_numeric_claim_embedded_unit_parsing(self):
        normalizer = TerminologyNormalizer()
        claim = _make_claim("lab.value", "14.2 mg/dl", unit_raw=None)
        normalizer.normalize_claim(claim)

        # Original raw value preserved
        assert claim.value == "14.2 mg/dl"
        assert claim.value_numeric == 14.2
        assert claim.normalized_code == "mg/dL"
        assert claim.code_system == "UCUM"

    def test_numeric_claim_missing_unit_handling(self):
        normalizer = TerminologyNormalizer()
        claim = _make_claim("lab.value", "14.2", unit_raw=None)
        normalizer.normalize_claim(claim)

        assert claim.value == "14.2"
        assert claim.unit_raw is None
        assert claim.value_numeric == 14.2
        assert claim.normalized_code is None
        assert claim.code_system is None
        assert normalizer.get_normalized_unit(claim.claim_id) is None

    def test_numeric_claim_unknown_unit_handling(self):
        normalizer = TerminologyNormalizer()
        claim = _make_claim("lab.value", "14.2", unit_raw="unknown_custom_unit")
        normalizer.normalize_claim(claim)

        assert claim.value == "14.2"
        assert claim.unit_raw == "unknown_custom_unit"
        assert claim.value_numeric == 14.2
        assert claim.normalized_code is None
        assert claim.code_system is None
        assert normalizer.get_normalized_unit(claim.claim_id) is None

    def test_numeric_claim_invalid_numeric_value_handling(self):
        normalizer = TerminologyNormalizer()
        claim = _make_claim("lab.value", "positive", unit_raw=None)
        normalizer.normalize_claim(claim)

        assert claim.value == "positive"
        assert claim.value_numeric is None
        assert claim.normalized_code is None
        assert claim.code_system is None


class TestAuditCandidateMemory:
    """Test audit candidate memory querying and clearing."""

    def test_query_and_clear_audit_candidates(self):
        normalizer = TerminologyNormalizer()
        claims = [
            _make_claim("lab.test_name", "Creatinine", claim_id="c1"),
            _make_claim("med.dose", "500", unit_raw="mg", claim_id="c2"),
        ]
        normalizer.normalize_claims(claims)

        # Query candidates for existing claim
        candidates_c1 = normalizer.get_audit_candidates("c1")
        assert len(candidates_c1) > 0
        assert isinstance(candidates_c1, list)

        # Query candidates for nonexistent claim
        assert normalizer.get_audit_candidates("nonexistent") == []

        # Query normalized unit
        assert normalizer.get_normalized_unit("c2") == "mg"
        assert normalizer.get_normalized_unit("nonexistent") is None

        # Clear audit memory
        normalizer.clear_audit_candidates()
        assert len(normalizer.audit_candidates) == 0
        assert len(normalizer.normalized_units) == 0
        assert normalizer.get_audit_candidates("c1") == []
        assert normalizer.get_normalized_unit("c2") is None
