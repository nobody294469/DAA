"""Unit tests for automated class-balance CI assertion (DEC-011)."""
from pathlib import Path
import pytest
import yaml

from clindoc.m2.dataset import assert_class_balance, ClassBalanceError


@pytest.fixture
def valid_tier1_metadata():
    """Construct valid metadata satisfying DEC-011 floors across all 6 Tier 1 fields."""
    return {
        "by_field_type": {
            "lab.value": {
                "SUPPORTED": 450,
                "DISCREPANCY": 450,
                "INSUFFICIENT_EVIDENCE": 100,
            },
            "lab.flag": {
                "SUPPORTED": 100,
                "DISCREPANCY": 100,
                "INSUFFICIENT_EVIDENCE": 0,  # Exempt from IE floor
            },
            "med.name": {
                "SUPPORTED": 200,
                "DISCREPANCY": 200,
                "INSUFFICIENT_EVIDENCE": 100,
            },
            "med.dose": {
                "SUPPORTED": 180,
                "DISCREPANCY": 180,
                "INSUFFICIENT_EVIDENCE": 40,
            },
            "med.frequency": {
                "SUPPORTED": 80,
                "DISCREPANCY": 80,
                "INSUFFICIENT_EVIDENCE": 40,
            },
            "med.route": {
                "SUPPORTED": 80,
                "DISCREPANCY": 80,
                "INSUFFICIENT_EVIDENCE": 40,
            },
        }
    }


def test_valid_balanced_field(valid_tier1_metadata):
    """(a) Test that fully balanced metadata passes assertion."""
    assert assert_class_balance(valid_tier1_metadata) is True


def test_supported_below_floor(valid_tier1_metadata):
    """(b) Test failure when SUPPORTED count is below the required floor."""
    # N = 100 + 100 + 0 = 200, floor = max(20, 30) = 30. Set SUPPORTED = 20
    valid_tier1_metadata["by_field_type"]["lab.flag"]["SUPPORTED"] = 20
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    
    err = str(exc_info.value)
    assert "lab.flag" in err
    assert "SUPPORTED" in err
    assert "Required minimum: 30" in err
    assert "Actual count: 20" in err


def test_discrepancy_below_floor(valid_tier1_metadata):
    """(c) Test failure when DISCREPANCY count is below the required floor."""
    valid_tier1_metadata["by_field_type"]["med.name"]["DISCREPANCY"] = 15
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    
    err = str(exc_info.value)
    assert "med.name" in err
    assert "DISCREPANCY" in err
    assert "Actual count: 15" in err


def test_insufficient_evidence_below_floor_when_applicable(valid_tier1_metadata):
    """(d) Test failure when INSUFFICIENT_EVIDENCE is below floor (15) for applicable field."""
    valid_tier1_metadata["by_field_type"]["med.dose"]["INSUFFICIENT_EVIDENCE"] = 10
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    
    err = str(exc_info.value)
    assert "med.dose" in err
    assert "INSUFFICIENT_EVIDENCE" in err
    assert "Required minimum: 15" in err
    assert "Actual count: 10" in err


def test_insufficient_evidence_not_applicable(valid_tier1_metadata):
    """(e) Test that INSUFFICIENT_EVIDENCE=0 is accepted for closed-categorical lab.flag."""
    # lab.flag has IE = 0 in valid_tier1_metadata and is configured as not applicable
    assert valid_tier1_metadata["by_field_type"]["lab.flag"]["INSUFFICIENT_EVIDENCE"] == 0
    assert assert_class_balance(valid_tier1_metadata) is True


def test_missing_required_field_type(valid_tier1_metadata):
    """(f) Test failure when a required Tier 1 field type is omitted entirely."""
    del valid_tier1_metadata["by_field_type"]["med.route"]
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    
    err = str(exc_info.value)
    assert "MISSING_FIELD" in err
    assert "med.route" in err


def test_exact_boundary_at_30(valid_tier1_metadata):
    """(g) Test exact boundary condition where absolute count 30 governs."""
    # For med.frequency: total = 30 + 30 + 15 = 75. 10% is 7.5. max(7.5, 30) = 30.
    valid_tier1_metadata["by_field_type"]["med.frequency"] = {
        "SUPPORTED": 30,
        "DISCREPANCY": 30,
        "INSUFFICIENT_EVIDENCE": 15,
    }
    # Exactly 30 must pass
    assert assert_class_balance(valid_tier1_metadata) is True

    # 29 must fail
    valid_tier1_metadata["by_field_type"]["med.frequency"]["SUPPORTED"] = 29
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    assert "Actual count: 29" in str(exc_info.value)


def test_exact_boundary_at_10_percent(valid_tier1_metadata):
    """(h) Test exact boundary condition where 10% ratio governs (N >= 300)."""
    # For lab.value: total = 50 + 400 + 50 = 500. 10% is 50. max(50, 30) = 50.
    valid_tier1_metadata["by_field_type"]["lab.value"] = {
        "SUPPORTED": 50,
        "DISCREPANCY": 400,
        "INSUFFICIENT_EVIDENCE": 50,
    }
    # Exactly 50 must pass
    assert assert_class_balance(valid_tier1_metadata) is True

    # 49 must fail
    valid_tier1_metadata["by_field_type"]["lab.value"]["SUPPORTED"] = 49
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    err = str(exc_info.value)
    assert "lab.value" in err
    assert "Actual count: 49" in err


def test_useful_failure_diagnostics(valid_tier1_metadata):
    """(i) Test that diagnostic error message contains all mandatory fields."""
    valid_tier1_metadata["by_field_type"]["med.name"] = {
        "SUPPORTED": 5,
        "DISCREPANCY": 5,
        "INSUFFICIENT_EVIDENCE": 0,
    }
    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata)
    
    err = str(exc_info.value)
    # Check field type
    assert "med.name" in err
    # Check total field examples
    assert "Total field examples: 10" in err
    # Check class counts dictionary
    assert "Class counts:" in err
    # Check required minimum
    assert "Required minimum:" in err
    # Check actual count
    assert "Actual count:" in err
    # Check violating classes identified
    assert "SUPPORTED" in err
    assert "DISCREPANCY" in err


def test_loading_thresholds_from_yaml(tmp_path, valid_tier1_metadata):
    """(j) Test that thresholds are dynamically read from YAML config rather than hardcoded."""
    custom_config = {
        "floors": {
            "supported_ratio": 0.25,  # Stricter: 25%
            "supported_min_count": 100,  # Stricter: 100
            "discrepancy_ratio": 0.25,
            "discrepancy_min_count": 100,
            "insufficient_evidence_min_count": 50,
        },
        "active_fields": {
            "tier_1_learned_fields": ["lab.value"],
        },
        "insufficient_evidence_applicability": {
            "lab.value": True,
        },
    }
    custom_yaml = tmp_path / "custom_floor.yaml"
    with open(custom_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(custom_config, f)

    # lab.value in valid_tier1_metadata has N=1000: SUPPORTED=450, DISCREPANCY=450, IE=100
    # Under custom config (25% = 250, min 100, IE min 50), it passes
    assert assert_class_balance(valid_tier1_metadata, config_path=custom_yaml) is True

    # Now make custom config impossibly strict: min_count = 600
    custom_config["floors"]["supported_min_count"] = 600
    with open(custom_yaml, "w", encoding="utf-8") as f:
        yaml.safe_dump(custom_config, f)

    with pytest.raises(ClassBalanceError) as exc_info:
        assert_class_balance(valid_tier1_metadata, config_path=custom_yaml)
    assert "Required minimum: 600" in str(exc_info.value)
