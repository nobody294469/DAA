"""Stage B training CLI (scripts/train_v3_experiment.py) and train_one_fold options, offline and fast.

Authority: scripts/train_v3_experiment.py, src/clindoc/m2/train.py, DEC-009.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
import torch

from clindoc.m2.schema import M2Example, M2ModelConfig
from clindoc.m2.train import train_one_fold
from clindoc.verify.stage_b import LearnedVerifier
from tests.test_stage_b_loading import make_tiny_base

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "train_v3_experiment.py"
_spec = importlib.util.spec_from_file_location("train_v3_experiment", SCRIPT)
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

PROGRESS_KEYS = {
    "run_name", "status", "phase", "epoch", "max_epochs", "step", "steps_per_epoch", "global_step",
    "total_steps", "train_loss", "last_val_macro_f1", "started_at", "updated_at", "eta_seconds",
    "device", "history", "error",
}


def test_defaults_reproduce_the_reference_run(monkeypatch):
    monkeypatch.delenv("CLINDOC_BASE_MODEL", raising=False)
    a = cli.parse_args([])
    assert (a.model_id, a.output_dir, a.run_name, a.seed) == (
        "thomas-sounack/BioClinical-ModernBERT-base", None, "modernbert_text_only_v3", 42)
    assert (a.max_epochs, a.patience, a.batch_size, a.grad_accum, a.max_seq_length) == (10, 3, 16, 1, 256)
    assert (a.freeze_lower_n_layers, a.gradient_checkpointing, a.device, a.gpu_mem_fraction) == (0, False, "auto", None)
    assert not a.smoke and not a.no_model_card
    monkeypatch.setenv("CLINDOC_BASE_MODEL", "C:/models/base")
    assert cli.parse_args([]).model_id == "C:/models/base"


@pytest.mark.parametrize("argv", [
    ["--run-name", "../escape"],
    ["--grad-accum", "0"],
    ["--gpu-mem-fraction", "1.5"],
    ["--gpu-mem-fraction", "0.2", "--device", "cpu"],
    ["--device", "tpu"],
])
def test_bad_arguments_are_rejected(argv):
    with pytest.raises(SystemExit):
        cli.parse_args(argv)


def test_hash_check_accepts_crlf_and_lf_and_rejects_tampering(tmp_path):
    lf = b'{"a": 1}\n{"b": 2}\n'
    crlf = lf.replace(b"\n", b"\r\n")
    expected = {"x.jsonl": (hashlib.sha256(crlf).hexdigest(), hashlib.sha256(lf).hexdigest())}
    for content in (lf, crlf):
        (tmp_path / "x.jsonl").write_bytes(content)
        assert cli.verify_dataset_hashes(expected, root=tmp_path)["x.jsonl"] == hashlib.sha256(content).hexdigest()
    (tmp_path / "x.jsonl").write_bytes(lf.replace(b"2", b"3"))
    with pytest.raises(ValueError, match="does not match"):
        cli.verify_dataset_hashes(expected, root=tmp_path)


def test_locked_benchmark_files_verify():
    assert set(cli.verify_dataset_hashes()) == set(cli.DATASET_HASHES)


def test_progress_json_is_atomic_and_complete(tmp_path, monkeypatch):
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda src, dst: replaced.append(str(src)) or real_replace(src, dst))
    path = tmp_path / "run_progress.json"
    p = cli.ProgressWriter(path, "run", "cpu", max_epochs=2, min_interval_s=3600)
    assert set(json.loads(path.read_text(encoding="utf-8"))) == PROGRESS_KEYS

    p({"event": "phase", "phase": "train", "epoch": 1, "step": 0, "steps_per_epoch": 3,
       "global_step": 0, "total_steps": 6})
    p({"event": "step", "epoch": 1, "step": 1, "global_step": 1, "total_steps": 6, "train_loss": 1.1})
    assert json.loads(path.read_text(encoding="utf-8"))["global_step"] == 0  # step writes are throttled
    p({"event": "epoch", "epoch": 1, "train_loss": 1.0, "val_loss": 0.9, "val_macro_f1": 0.5, "val_accuracy": 0.6})
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["global_step"] == 1 and state["last_val_macro_f1"] == 0.5
    assert state["history"] == [{"epoch": 1, "train_loss": 1.0, "val_loss": 0.9, "val_macro_f1": 0.5, "val_accuracy": 0.6}]
    assert state["eta_seconds"] is not None

    p.finish("failed", error="RuntimeError: boom")
    state = json.loads(path.read_text(encoding="utf-8"))
    assert (state["status"], state["error"]) == ("failed", "RuntimeError: boom")
    assert replaced and all(src.endswith(".tmp") for src in replaced)
    assert [f.name for f in tmp_path.iterdir()] == ["run_progress.json"]


def _examples(n: int, split: str) -> list[M2Example]:
    labels = ["SUPPORTED", "DISCREPANCY", "INSUFFICIENT_EVIDENCE"]
    return [
        M2Example(example_id=f"{split}{i}", claim=f"Glucose: {100 + i} mg/dL",
                  evidence=f"Glucose {100 + (i % 2) * 7 + i} mg/dL 70-99", field_type="lab.value",
                  doc_template="template_a", ocr_confidence=1.0, severity="clean",
                  label=labels[i % 3], source_doc_id=f"{split}_doc{i}", generation_method="natural",
                  claim_type="lab")
        for i in range(n)
    ]


def test_train_one_fold_accumulation_checkpointing_progress_and_serving(tmp_path):
    from safetensors.torch import load_file

    base = make_tiny_base(tmp_path / "base")
    config = M2ModelConfig(model_id=str(base), batch_size=2, max_epochs=2, max_seq_length=64, seeds=[7])
    train = _examples(10, "tr")
    train_order = [e.example_id for e in train]
    events: list[dict] = []
    result = train_one_fold(
        train_examples=train, val_examples=_examples(4, "va"), config=config, fold_idx=0, seed=7,
        checkpoint_dir=tmp_path / "ckpt", aux_mode="none", model_name="tiny",
        ood_examples=_examples(4, "ood"), calib_examples=_examples(4, "ca"),
        grad_accum_steps=2, gradient_checkpointing=True, eval_batch_size=4, device="cpu",
        progress_callback=events.append,
    )

    assert [e.example_id for e in train] == train_order  # caller's list is not shuffled
    phases = [e["phase"] for e in events if e["event"] == "phase"]
    assert phases == ["load", "train", "validate", "train", "validate", "calibrate", "evaluate"]
    steps = [e for e in events if e["event"] == "step"]
    # 10 examples / batch 2 = 5 micro-batches -> ceil(5 / 2) = 3 optimizer steps per epoch
    assert [s["global_step"] for s in steps] == [1, 2, 3, 4, 5, 6]
    assert all(s["steps_per_epoch"] == 3 and s["total_steps"] == 6 for s in steps)
    assert all("val_accuracy" in h for h in result["history"])

    ckpt = torch.load(result["checkpoint_path"], map_location="cpu", weights_only=True)
    base_w = load_file(str(base / "model.safetensors"))["layers.1.attn.Wqkv.weight"]
    assert not torch.equal(base_w, ckpt["model_state"]["encoder.layers.1.attn.Wqkv.weight"])  # encoder trained

    served = LearnedVerifier(model_path=result["checkpoint_path"], device="cpu")
    assert served.is_model_loaded and served.load_error is None
    assert served.temperature == pytest.approx(result["calibration_temperature"])
    assert served.max_seq_length == 64
