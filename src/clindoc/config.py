"""Runtime profiles and production configuration containment.

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13
  - PRODUCTIZATION_SPEC.md §3.6, §3.8, §10, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md PD-003, PD-004, PD-005, PD-018, PD-019

Defines explicit execution profiles:
  - DEV: Allows MockLLMProvider, DefaultMockLLMJudge, MockModelPredictor, and
    heuristic fallback. Preserves existing test determinism and offline demos.
  - PRODUCTION: Enforces strict containment. Mocks and heuristic fallbacks are
    strictly forbidden. Requires valid ModernBERT checkpoint, fitted temperature
    calibration, and valid vLLM endpoint configuration. Fail-closed on startup.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Any, Optional, Union
from urllib.parse import urlparse


class RuntimeProfile(str, Enum):
    """Runtime execution profile."""
    DEV = "dev"
    PRODUCTION = "production"

    @classmethod
    def from_string(cls, value: Optional[str]) -> "RuntimeProfile":
        if not value:
            return cls.DEV
        val = value.strip().lower()
        if val in ("prod", "production"):
            return cls.PRODUCTION
        elif val in ("dev", "development", "test", "testing"):
            return cls.DEV
        else:
            raise ValueError(
                f"Unknown runtime profile '{value}'. Permitted values: 'dev', 'production'."
            )


class ProductionProfileError(RuntimeError):
    """Raised when a production profile invariant is violated."""
    pass


# Default benchmarked checkpoint path and constants
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MODERNBERT_CHECKPOINT = (
    PROJECT_ROOT
    / "data"
    / "m2_results_v3"
    / "checkpoints"
    / "modernbert_text_only_v3_fold0_seed42.pt"
)
CANONICAL_CALIBRATION_TEMPERATURE = 1.146522  # Data sheet §5 / DEC-009
CANONICAL_STAGE_B_ESCALATION_THRESHOLD = 0.60   # DEC-005
CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD = 0.85   # V12 / Data sheet §5
CANONICAL_AUDITED_VLLM_MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct-AWQ"  # DEC-006 / Audited V8/V9


def get_active_profile() -> RuntimeProfile:
    """Read the active runtime profile from the CLINDOC_PROFILE environment variable.

    Defaults to RuntimeProfile.DEV to preserve backward compatibility for tests and research.
    """
    env_val = os.environ.get("CLINDOC_PROFILE", "dev")
    return RuntimeProfile.from_string(env_val)


@dataclass
class ProductionPipelineConfig:
    """Explicit configuration container for production pipeline execution."""
    profile: RuntimeProfile = RuntimeProfile.PRODUCTION
    modernbert_checkpoint_path: Path = CANONICAL_MODERNBERT_CHECKPOINT
    calibration_temperature: float = CANONICAL_CALIBRATION_TEMPERATURE
    stage_b_escalation_threshold: float = CANONICAL_STAGE_B_ESCALATION_THRESHOLD
    stage_b_autonomous_threshold: float = CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD
    vllm_base_url: str = "http://localhost:8000/v1"
    vllm_model_name: str = CANONICAL_AUDITED_VLLM_MODEL_NAME
    vllm_api_key_env: str = "VLLM_API_KEY"
    vllm_timeout_seconds: float = 30.0
    allow_mocks: bool = False
    allow_heuristics: bool = False

    def validate(self) -> None:
        """Validate production configuration. Raises ProductionProfileError if invalid."""
        if self.profile == RuntimeProfile.PRODUCTION:
            if self.allow_mocks:
                raise ProductionProfileError(
                    "Production configuration violation: allow_mocks=True is forbidden in PRODUCTION profile."
                )
            if self.allow_heuristics:
                raise ProductionProfileError(
                    "Production configuration violation: allow_heuristics=True is forbidden in PRODUCTION profile."
                )

            # 1. Validate ModernBERT Checkpoint
            if not self.modernbert_checkpoint_path:
                raise ProductionProfileError(
                    "Production configuration violation: modernbert_checkpoint_path is required."
                )
            ckpt_path = Path(self.modernbert_checkpoint_path)
            if not ckpt_path.exists():
                raise ProductionProfileError(
                    f"Production configuration violation: ModernBERT checkpoint file not found: {ckpt_path}"
                )
            if not ckpt_path.is_file() or ckpt_path.stat().st_size == 0:
                raise ProductionProfileError(
                    f"Production configuration violation: ModernBERT checkpoint is empty or invalid: {ckpt_path}"
                )

            # 2. Validate Calibration Parameters
            if self.calibration_temperature is None or self.calibration_temperature <= 0.0:
                raise ProductionProfileError(
                    f"Production configuration violation: Invalid calibration temperature {self.calibration_temperature}. "
                    "Must be positive scalar (audited: 1.146522)."
                )

            # 3. Validate Thresholds
            if not (0.0 < self.stage_b_escalation_threshold < 1.0):
                raise ProductionProfileError(
                    f"Production configuration violation: Invalid escalation threshold {self.stage_b_escalation_threshold}."
                )

            # 4. Validate vLLM Endpoint
            if not self.vllm_base_url or not isinstance(self.vllm_base_url, str):
                raise ProductionProfileError(
                    "Production configuration violation: vllm_base_url is required."
                )
            parsed_url = urlparse(self.vllm_base_url)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
                raise ProductionProfileError(
                    f"Production configuration violation: Invalid vllm_base_url '{self.vllm_base_url}'. "
                    "Must be an absolute http:// or https:// URL."
                )
            if not self.vllm_model_name or not self.vllm_model_name.strip():
                raise ProductionProfileError(
                    "Production configuration violation: vllm_model_name is required."
                )


# Configurable operational defaults for queue leasing and retries.
# (PRODUCTIZATION_SPEC.md §4.1 R-3 and §13 F-17 mandate that lease expiry and retry limits
# exist and be enforced, but do NOT explicitly prescribe these exact numerical values.
# These values are operational defaults isolated behind configuration).
DEFAULT_JOB_LEASE_SECONDS: int = 300  # 5 minutes configurable operational default
DEFAULT_JOB_MAX_ATTEMPTS: int = 3     # 3 attempts configurable operational default


def get_job_lease_seconds() -> int:
    """Read the configured job lease duration in seconds from the environment.

    Falls back to DEFAULT_JOB_LEASE_SECONDS (300 s).
    """
    raw = os.environ.get("CLINDOC_JOB_LEASE_SECONDS")
    if raw is not None and raw.strip():
        try:
            val = int(raw.strip())
            if val > 0:
                return val
        except ValueError:
            pass
    return DEFAULT_JOB_LEASE_SECONDS

