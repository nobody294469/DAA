"""Stage B checkpoint loading + batched inference, on a tiny random ModernBERT (offline, fast).

Authority: src/clindoc/verify/stage_b.py, DEC-009 (checkpoint temperature), DEC-003 (context window).
The tokenizer is copied from the local base-model dir; the tests skip when it is absent.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import torch

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.m2.model import ClinicalVerifier
from clindoc.m2.schema import M2ModelConfig
from clindoc.verify import stage_b as stage_b_module
from clindoc.verify.cascade import VerificationCascade
from clindoc.verify.stage_b import LearnedVerifier
from clindoc.verify.stage_c import DefaultMockLLMJudge, StageCJudge

BASE_DIR = Path.home() / ".cache" / "clindoc" / "BioClinical-ModernBERT-base"
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")


def make_tiny_base(dst: Path) -> Path:
    """Tiny random ModernBERT (hidden 32, 2 layers) + the real tokenizer, saved as a base-model dir."""
    if not all((BASE_DIR / f).is_file() for f in TOKENIZER_FILES):
        pytest.skip(f"local base-model tokenizer not found under {BASE_DIR}")
    from transformers import ModernBertConfig, ModernBertModel

    dst.mkdir(parents=True, exist_ok=True)
    for f in TOKENIZER_FILES:
        shutil.copy(BASE_DIR / f, dst / f)
    torch.manual_seed(0)
    cfg = ModernBertConfig(
        vocab_size=50368, hidden_size=32, intermediate_size=64, num_hidden_layers=2,
        num_attention_heads=2, max_position_embeddings=512, global_attn_every_n_layers=2,
        local_attention=64, pad_token_id=50283, bos_token_id=50281, eos_token_id=50282,
        cls_token_id=50281, sep_token_id=50282,
    )
    ModernBertModel(cfg).save_pretrained(dst)
    return dst


@pytest.fixture(scope="module")
def tiny_checkpoint(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("stage_b")
    base = make_tiny_base(root / "base")
    torch.manual_seed(1)
    model = ClinicalVerifier(model_id=str(base), aux_mode="none", load_pretrained=False)
    ckpt = root / "tiny_fold0_seed42.pt"
    torch.save({
        "model_state": model.state_dict(),
        "config": M2ModelConfig(model_id=str(base), max_seq_length=64).model_dump(),
        "aux_mode": "none",
        "temperature": 1.7,
    }, ckpt)
    return ckpt


def _evidence(eid: str, text: str, start: int) -> Evidence:
    return Evidence(evidence_id=eid, document_id="doc-1", page_number=1, text=text,
                    char_start=start, char_end=start + len(text),
                    bbox=BBoxModel(x0=0, y0=0, x1=10, y1=10))


def _claim(cid: str, group: str, field: str, value: str, eid: str, unit: str | None = None) -> Claim:
    return Claim(claim_id=cid, document_id="doc-1", group_id=group, field_type=field, value=value,
                 unit_raw=unit, extraction_method="deterministic", extraction_confidence=0.95,
                 evidence_id=eid, region_id="reg-1")


def _document():
    """Lab rows + medication rows on one page: (claims, evidence_map, page_text_map)."""
    rows = [("Hematocrit", "46.1", "%"), ("Glucose", "112", "mg/dL"), ("Sodium", "139", "mmol/L")]
    meds = [("Metformin", "500 mg", "PO", "BID"), ("Lisinopril", "10 mg", "PO", "daily")]
    lines = [f"{n} {v} {u} 35-50" for n, v, u in rows] + [f"{n} {d} {r} {f}" for n, d, r, f in meds]
    page = "\n".join(lines)
    claims, evidence = [], {}

    def add(cid, group, field, value, span, unit=None):
        eid = f"e-{cid}"
        evidence[eid] = _evidence(eid, span, page.index(span))
        claims.append(_claim(cid, group, field, value, eid, unit))

    for i, (name, value, unit) in enumerate(rows):
        add(f"t{i}", f"g{i}", "lab.test_name", name, name)
        add(f"v{i}", f"g{i}", "lab.value", value, value, unit)
        add(f"u{i}", f"g{i}", "lab.unit", unit, unit)
    for i, (name, dose, route, freq) in enumerate(meds):
        add(f"mn{i}", f"m{i}", "med.name", name, name)
        add(f"md{i}", f"m{i}", "med.dose", dose, dose)
        add(f"mr{i}", f"m{i}", "med.route", route, f"{dose} {route}")
        add(f"mf{i}", f"m{i}", "med.frequency", freq, freq)
    return claims, evidence, {"1": page}


def _cascade(verifier: LearnedVerifier) -> VerificationCascade:
    return VerificationCascade(stage_b=verifier, stage_c=StageCJudge(client=DefaultMockLLMJudge()))


def test_checkpoint_loads_with_config_and_temperature(tiny_checkpoint):
    v = LearnedVerifier(model_path=tiny_checkpoint, device="cpu")
    assert v.is_model_loaded and v.load_error is None
    assert v.temperature == pytest.approx(1.7)
    assert v.max_seq_length == 64
    assert v.base_model_id == str(tiny_checkpoint.parent / "base")
    assert v.device == "cpu"


@pytest.mark.parametrize("make_path, reason", [
    (lambda tmp: tmp, "directory"),
    (lambda tmp: tmp / "missing.pt", "not found"),
])
def test_unusable_model_path_reports_load_error(tmp_path, make_path, reason):
    v = LearnedVerifier(model_path=make_path(tmp_path), device="cpu")
    assert not v.is_model_loaded and v.device is None
    assert reason in v.load_error


def test_env_checkpoint_only_when_opted_in(tiny_checkpoint, monkeypatch):
    monkeypatch.setenv("CLINDOC_STAGE_B_CHECKPOINT", str(tiny_checkpoint))
    assert not LearnedVerifier(device="cpu").is_model_loaded  # heuristic default stays
    v = LearnedVerifier(device="cpu", use_env_checkpoint=True)
    assert v.is_model_loaded and v.model_path == str(tiny_checkpoint)


def test_batched_verdicts_equal_per_claim_verdicts(tiny_checkpoint, monkeypatch):
    claims, evidence, pages = _document()
    calls: list[int] = []
    real = LearnedVerifier._neural_logits
    monkeypatch.setattr(LearnedVerifier, "_neural_logits",
                        lambda self, inputs: calls.append(len(inputs)) or real(self, inputs))

    batched = _cascade(LearnedVerifier(model_path=tiny_checkpoint, device="cpu"))
    res_b = batched.verify_claims(claims, evidence, pages)
    n_batched_forwards = len(calls)

    calls.clear()
    monkeypatch.setattr(LearnedVerifier, "prefetch_logits", lambda self, requests: None)
    sequential = _cascade(LearnedVerifier(model_path=tiny_checkpoint, device="cpu"))
    res_s = sequential.verify_claims(claims, evidence, pages)

    n_tier1 = sum(c.field_type in ("lab.value", "med.name", "med.dose", "med.route", "med.frequency")
                  for c in claims)
    assert n_batched_forwards == 1  # all 11 Tier-1 claims fit in one batch of 16
    assert calls == [1] * n_tier1  # per-claim path: one forward per Stage B claim
    assert len(res_b) == len(res_s) == len(claims)
    for b, s in zip(res_b, res_s):
        assert (b.claim_id, b.label, b.stage_reached, b.stage_b_label, b.escalated) == \
               (s.claim_id, s.label, s.stage_reached, s.stage_b_label, s.escalated)
        assert b.confidence == pytest.approx(s.confidence, abs=1e-5)
        if b.stage_b_raw_confidence is not None:
            assert b.stage_b_raw_confidence == pytest.approx(s.stage_b_raw_confidence, abs=1e-5)


def test_claim_text_and_context_window_reach_the_model(tiny_checkpoint, monkeypatch):
    seen: list[tuple[str, str, str]] = []
    real = stage_b_module.format_input
    monkeypatch.setattr(stage_b_module, "format_input", lambda *a: seen.append(a) or real(*a))
    claims, evidence, pages = _document()
    _cascade(LearnedVerifier(model_path=tiny_checkpoint, device="cpu")).verify_claims(claims, evidence, pages)

    by_claim = {c: (f, e) for f, c, e in seen}
    assert "Hematocrit: 46.1 %" in by_claim
    assert "Metformin dose: 500 mg" in by_claim
    assert "Medication: Lisinopril" in by_claim
    field, window = by_claim["Hematocrit: 46.1 %"]
    assert field == "lab.value"
    assert "46.1" in window and window != "46.1"  # DEC-003 window, not the bare span
    assert all(not c.startswith(("46.1", "500 mg")) for c in by_claim)  # never the bare value


def test_custom_predictor_path_unchanged():
    v = LearnedVerifier(custom_predictor=stage_b_module.MockModelPredictor("DISCREPANCY", 0.9))
    claim = _claim("c", "g", "lab.value", "46.1", "e", "%")
    res = v.verify(claim, _evidence("e", "Hematocrit 46.1 %", 0), claim_text="Hematocrit: 46.1 %")
    assert res.label == "DISCREPANCY"
    assert v.load_error is None and v.device is None and v.base_model_id is None
