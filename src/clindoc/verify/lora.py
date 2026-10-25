"""Minimal LoRA (low-rank adapters) in plain PyTorch for fine-tuning the local Stage C judge.

Authority: DEC-006 (Qwen-family judge), Hu et al. 2021 (LoRA). Used by scripts/train_judge_lora.py
and LocalTransformersJudgeClient. ponytail: hand-rolled instead of `peft` to avoid a new dependency;
only nn.Linear targets, no merging; switch to peft if adapters need sharing with other tooling.
"""
from __future__ import annotations

import json
from pathlib import Path

import torch
import torch.nn as nn

DEFAULT_TARGETS = ("q_proj", "k_proj", "v_proj", "o_proj")
ADAPTER_FILE = "adapter.pt"
CONFIG_FILE = "adapter_config.json"


class LoRALinear(nn.Module):
    """y = W x + (alpha / r) * B(A(dropout(x))); W frozen, A and B trained in fp32."""

    def __init__(self, base: nn.Linear, r: int = 16, alpha: int = 32, dropout: float = 0.05) -> None:
        super().__init__()
        self.base = base
        self.scale = alpha / r
        self.dropout = nn.Dropout(dropout)
        self.lora_a = nn.Linear(base.in_features, r, bias=False, device=base.weight.device, dtype=torch.float32)
        self.lora_b = nn.Linear(r, base.out_features, bias=False, device=base.weight.device, dtype=torch.float32)
        nn.init.kaiming_uniform_(self.lora_a.weight, a=5 ** 0.5)
        nn.init.zeros_(self.lora_b.weight)  # starts as an exact no-op on the base model

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        delta = self.lora_b(self.lora_a(self.dropout(x).float())) * self.scale
        return self.base(x) + delta.to(x.dtype)


def inject_lora(model: nn.Module, targets=DEFAULT_TARGETS, r: int = 16, alpha: int = 32, dropout: float = 0.05) -> int:
    """Freeze the model and wrap every target nn.Linear with LoRA. Returns the trainable parameter count."""
    for p in model.parameters():
        p.requires_grad_(False)
    for name, module in list(model.named_modules()):
        for child_name, child in list(module.named_children()):
            if child_name in targets and isinstance(child, nn.Linear):
                setattr(module, child_name, LoRALinear(child, r=r, alpha=alpha, dropout=dropout))
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def save_lora(model: nn.Module, out_dir: Path, config: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {k: v.detach().cpu() for k, v in model.state_dict().items() if ".lora_a." in k or ".lora_b." in k}
    torch.save(state, out_dir / ADAPTER_FILE)
    (out_dir / CONFIG_FILE).write_text(json.dumps(config, indent=2), encoding="utf-8")


def load_lora(model: nn.Module, adapter_dir: Path) -> None:
    """Wrap targets as at training time and load the adapter weights (strict on the LoRA keys)."""
    cfg = json.loads((adapter_dir / CONFIG_FILE).read_text(encoding="utf-8"))
    if any(p.is_meta for p in model.parameters()):
        # accelerate offload leaves meta tensors; LoRA weights copied onto them are silently dropped
        raise ValueError("load the base model fully on one device (no device_map offload) before attaching LoRA")
    inject_lora(model, targets=tuple(cfg["targets"]), r=cfg["r"], alpha=cfg["alpha"], dropout=0.0)
    state = torch.load(adapter_dir / ADAPTER_FILE, map_location="cpu", weights_only=True)
    missing = [k for k in model.state_dict() if (".lora_a." in k or ".lora_b." in k) and k not in state]
    if missing:
        raise ValueError(f"adapter is missing {len(missing)} LoRA tensors, e.g. {missing[0]}")
    model.load_state_dict(state, strict=False)


if __name__ == "__main__":  # self-check: zero-init LoRA is a no-op, and save/load round-trips
    import tempfile

    torch.manual_seed(0)
    net = nn.Sequential()
    net.add_module("q_proj", nn.Linear(8, 8))
    x = torch.randn(2, 8)
    ref = net(x)
    assert inject_lora(net, r=2, alpha=4) == 2 * 8 * 2
    assert torch.allclose(net(x), ref)
    net.q_proj.lora_b.weight.data.fill_(0.1)
    with tempfile.TemporaryDirectory() as d:
        save_lora(net, Path(d), {"targets": ["q_proj"], "r": 2, "alpha": 4, "base_model": "x"})
        twin = nn.Sequential()
        twin.add_module("q_proj", nn.Linear(8, 8))
        twin.q_proj.load_state_dict(net.q_proj.base.state_dict())
        load_lora(twin, Path(d))
        net.eval(); twin.eval()
        assert torch.allclose(net(x), twin(x))
    print("ok")
