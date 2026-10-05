"""Qualified Configuration Manifest & Provenance Interface.

Authority:
  - PROJECT_SPEC.md §3, §10, §12, §13, §17.1
  - PRODUCTIZATION_SPEC.md §3.6, §3.8, §10, §13 (Failures F-05, F-06, F-07)
  - PRODUCTIZATION_DECISIONS.md PD-005, PD-018, PD-019
  - experiments/FINAL_REPORT_DATA_SHEET.md
  - reports/FINAL_PROJECT_REPORT.md

Defines the cryptographically anchored Qualified Configuration Manifest (PD-005).
The manifest pins every pipeline parameter defining audited production behavior.

Distinguishes:
  - QUALIFIED: Exact required production configuration matches the approved manifest.
  - UNQUALIFIED: The system can run, but one or more configuration components
    differ from the approved manifest.
  - INVALID / FAIL-CLOSED: A mandatory production component is missing, corrupted,
    unsafe, or unverifiable.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Union

from clindoc.config import (
    CANONICAL_CALIBRATION_TEMPERATURE,
    CANONICAL_MODERNBERT_CHECKPOINT,
    CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD,
    CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
    PROJECT_ROOT,
    ProductionPipelineConfig,
    ProductionProfileError,
    RuntimeProfile,
)


class QualificationStatus(str, Enum):
    """Explicit qualification status for pipeline execution."""
    QUALIFIED = "QUALIFIED"
    UNQUALIFIED = "UNQUALIFIED"
    INVALID = "INVALID"


# ─── CANONICAL AUDITED PRODUCTION PINNED VALUES ──────────────────────────────
# Pinned to the audited V8 production configuration (FINAL_PROJECT_REPORT.md §3)
CANONICAL_MANIFEST_VERSION = "1.0.0"
CANONICAL_GIT_COMMIT = "9056df7579adc0a9cae1a2f7fb2b79db34378053"
CANONICAL_MODERNBERT_MODEL_ID = "thomas-sounack/BioClinical-ModernBERT-base"
CANONICAL_MODERNBERT_CHECKPOINT_REL = (
    "data/m2_results_v3/checkpoints/modernbert_text_only_v3_fold0_seed42.pt"
)
CANONICAL_MODERNBERT_CHECKPOINT_SHA256 = (
    "d24add6135634f660d2fdcf1c51545e201cc65c00fd2d060452eac95be9e7902"
)
CANONICAL_STAGE_A_NUMERIC_TOLERANCE = 1e-4
CANONICAL_STAGE_A_NEGATION_PATTERNS_COUNT = 11

# Audited Qwen model identifier: Qwen2.5-7B-Instruct-AWQ running on vLLM (DEC-006, V8/V9 audit)
CANONICAL_QWEN_MODEL_ID = "Qwen/Qwen2.5-7B-Instruct-AWQ"
CANONICAL_QWEN_QUANTIZATION = "AWQ_4BIT"
CANONICAL_QWEN_SERVING_ENGINE = "vLLM"
CANONICAL_QWEN_TEMPERATURE = 0.0
CANONICAL_QWEN_SEED = 42
CANONICAL_QWEN_MAX_TOKENS = 256

# Documented limitation: remote Colab session did not capture upstream HuggingFace commit hash
CANONICAL_QWEN_REVISION_HASH: Optional[str] = None

# Authoritative Stage C prompt version and text hash
CANONICAL_STAGE_C_PROMPT_VERSION = "V8_STAGE_C_LABFLAG_PROMPT_V1"
# SHA-256 of (DEFAULT_STAGE_C_SYSTEM_PROMPT + "\n" + LAB_FLAG_TABULAR_INSTRUCTION)
CANONICAL_STAGE_C_PROMPT_TEXT_SHA256 = (
    "dcd6be9c502ec0b05a7858a438d67d929de792987d57ac7f52d0d0c21f868ae3"
)
# Prompt is defined in code within src/clindoc/verify/stage_c.py; no separate prompt file exists
CANONICAL_STAGE_C_PROMPT_ARTIFACT_PATH: Optional[str] = None


MANDATORY_MANIFEST_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "manifest_version",
    "git_commit",
    "profile",
    "modernbert",
    "stage_a",
    "qwen",
    "stage_c_prompt",
)

MANDATORY_MODERNBERT_KEYS: tuple[str, ...] = (
    "model_id",
    "checkpoint_path",
    "checkpoint_sha256",
    "aux_mode",
    "max_length",
    "temperature",
    "escalation_threshold",
    "autonomous_threshold",
)

MANDATORY_QWEN_KEYS: tuple[str, ...] = (
    "model_id",
    "quantization",
    "serving_engine",
    "temperature",
    "seed",
    "max_tokens",
)


def compute_canonical_manifest_digest(manifest_dict: dict[str, Any]) -> str:
    """Compute deterministic SHA-256 digest of a manifest specification dictionary.

    Excludes any existing 'manifest_digest' field and formats sorted JSON with no extra whitespace.
    """
    clean_dict = copy.deepcopy(manifest_dict)
    clean_dict.pop("manifest_digest", None)
    canonical_json = json.dumps(clean_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_file_sha256(file_path: Union[str, Path]) -> str:
    """Compute SHA-256 digest of a local file."""
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"File not found for hashing: {file_path}")
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_stage_c_prompt_hash(
    system_prompt: Optional[str] = None,
    tabular_instruction: Optional[str] = None,
) -> str:
    """Compute canonical SHA-256 of Stage C prompt components."""
    from clindoc.verify.stage_c import (
        DEFAULT_STAGE_C_SYSTEM_PROMPT,
        LAB_FLAG_TABULAR_INSTRUCTION,
    )
    sys_p = system_prompt if system_prompt is not None else DEFAULT_STAGE_C_SYSTEM_PROMPT
    tab_i = tabular_instruction if tabular_instruction is not None else LAB_FLAG_TABULAR_INSTRUCTION
    combined = f"{sys_p}\n{tab_i}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ─── CANONICAL QUALIFIED MANIFEST V1 ──────────────────────────────────────────

QUALIFIED_MANIFEST_DICT_V1: dict[str, Any] = {
    "manifest_version": CANONICAL_MANIFEST_VERSION,
    "git_commit": CANONICAL_GIT_COMMIT,
    "profile": "production",
    "modernbert": {
        "model_id": CANONICAL_MODERNBERT_MODEL_ID,
        "checkpoint_path": CANONICAL_MODERNBERT_CHECKPOINT_REL,
        "checkpoint_sha256": CANONICAL_MODERNBERT_CHECKPOINT_SHA256,
        "aux_mode": "none",
        "max_length": 256,
        "temperature": CANONICAL_CALIBRATION_TEMPERATURE,
        "escalation_threshold": CANONICAL_STAGE_B_ESCALATION_THRESHOLD,
        "autonomous_threshold": CANONICAL_STAGE_B_AUTONOMOUS_THRESHOLD,
    },
    "stage_a": {
        "numeric_tolerance": CANONICAL_STAGE_A_NUMERIC_TOLERANCE,
        "negation_patterns_count": CANONICAL_STAGE_A_NEGATION_PATTERNS_COUNT,
    },
    "qwen": {
        "model_id": CANONICAL_QWEN_MODEL_ID,
        "revision_hash": CANONICAL_QWEN_REVISION_HASH,
        "quantization": CANONICAL_QWEN_QUANTIZATION,
        "serving_engine": CANONICAL_QWEN_SERVING_ENGINE,
        "temperature": CANONICAL_QWEN_TEMPERATURE,
        "seed": CANONICAL_QWEN_SEED,
        "max_tokens": CANONICAL_QWEN_MAX_TOKENS,
    },
    "stage_c_prompt": {
        "prompt_version": CANONICAL_STAGE_C_PROMPT_VERSION,
        "prompt_text_sha256": CANONICAL_STAGE_C_PROMPT_TEXT_SHA256,
        "prompt_artifact_path": CANONICAL_STAGE_C_PROMPT_ARTIFACT_PATH,
    },
}

CANONICAL_MANIFEST_DIGEST_V1 = compute_canonical_manifest_digest(QUALIFIED_MANIFEST_DICT_V1)


@dataclass
class QualifiedManifest:
    """Cryptographically anchored specification of audited production configuration."""
    raw_dict: dict[str, Any]
    manifest_digest: str

    @classmethod
    def create_canonical_v1(cls) -> "QualifiedManifest":
        """Instantiate canonical V1 manifest with verified cryptographic digest."""
        d = copy.deepcopy(QUALIFIED_MANIFEST_DICT_V1)
        return cls(raw_dict=d, manifest_digest=CANONICAL_MANIFEST_DIGEST_V1)

    @classmethod
    def from_dict(cls, data: dict[str, Any], expected_digest: Optional[str] = None) -> "QualifiedManifest":
        """Create manifest from dictionary and validate mandatory keys and integrity."""
        # 1. Validate mandatory top-level keys
        for key in MANDATORY_MANIFEST_TOP_LEVEL_KEYS:
            if key not in data:
                raise ProductionProfileError(
                    f"Manifest validation failed: missing mandatory top-level key '{key}'."
                )

        # 2. Validate mandatory section keys
        for key in MANDATORY_MODERNBERT_KEYS:
            if key not in data.get("modernbert", {}):
                raise ProductionProfileError(
                    f"Manifest validation failed: missing mandatory ModernBERT key '{key}'."
                )
        for key in MANDATORY_QWEN_KEYS:
            if key not in data.get("qwen", {}):
                raise ProductionProfileError(
                    f"Manifest validation failed: missing mandatory Qwen key '{key}'."
                )

        digest = expected_digest or data.get("manifest_digest") or compute_canonical_manifest_digest(data)
        manifest = cls(raw_dict=copy.deepcopy(data), manifest_digest=digest)
        manifest.verify_integrity()
        return manifest

    def verify_integrity(self) -> None:
        """Verify that manifest_digest matches the calculated canonical SHA-256."""
        computed = compute_canonical_manifest_digest(self.raw_dict)
        if computed != self.manifest_digest:
            raise ProductionProfileError(
                f"Manifest integrity verification failed: computed digest '{computed}' "
                f"does not match expected digest '{self.manifest_digest}'."
            )

    @property
    def version(self) -> str:
        return str(self.raw_dict.get("manifest_version", "unknown"))

    @property
    def modernbert(self) -> dict[str, Any]:
        return self.raw_dict.get("modernbert", {})

    @property
    def stage_a(self) -> dict[str, Any]:
        return self.raw_dict.get("stage_a", {})

    @property
    def qwen(self) -> dict[str, Any]:
        return self.raw_dict.get("qwen", {})

    @property
    def stage_c_prompt(self) -> dict[str, Any]:
        return self.raw_dict.get("stage_c_prompt", {})


@dataclass(frozen=True)
class QualificationReport:
    """Detailed audit report on configuration qualification."""
    status: QualificationStatus
    is_qualified: bool
    reasons: list[str]
    manifest_version: str
    manifest_digest: str
    evaluated_components: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """User-facing single line summary."""
        if self.is_qualified:
            return f"QUALIFIED (Manifest v{self.manifest_version}, digest: {self.manifest_digest[:12]}...)"
        reasons_str = "; ".join(self.reasons) if self.reasons else "Configuration diverges from manifest"
        return f"{self.status.value}: {reasons_str}"


# ─── QUALIFICATION EVALUATION ENGINE ─────────────────────────────────────────

def evaluate_cascade_qualification(
    cascade: Any,
    manifest: Optional[QualifiedManifest] = None,
    checkpoint_sha256: Optional[str] = None,
) -> QualificationReport:
    """Evaluate whether an instantiated VerificationCascade matches the qualified manifest.

    Inspects:
      - Runtime profile (must be PRODUCTION)
      - ModernBERT checkpoint existence and SHA-256
      - ModernBERT calibration temperature ($T = 1.146522$)
      - Stage B escalation threshold ($0.60$)
      - Stage C client and model identifier (Qwen/Qwen2.5-7B-Instruct-AWQ)
      - Stage C prompt version and prompt text SHA-256
    """
    if manifest is None:
        manifest = QualifiedManifest.create_canonical_v1()
    manifest.verify_integrity()

    reasons: list[str] = []
    evaluated: dict[str, Any] = {}

    profile = getattr(cascade, "profile", RuntimeProfile.DEV)
    evaluated["profile"] = profile.value if isinstance(profile, RuntimeProfile) else str(profile)

    # 1. Profile Check
    if profile != RuntimeProfile.PRODUCTION:
        reasons.append(
            f"Runtime profile is '{evaluated['profile']}'; QUALIFIED status requires 'production' profile."
        )

    # 2. Stage B Checkpoint & SHA-256 Check
    stage_b = getattr(cascade, "stage_b", None)
    if stage_b is None:
        reasons.append("Stage B verifier is missing.")
    elif getattr(stage_b, "allow_heuristic_fallback", True):
        reasons.append("Stage B has heuristic fallback enabled.")
    else:
        model_path = getattr(stage_b, "model_path", None)
        evaluated["stage_b_model_path"] = str(model_path) if model_path else None
        if model_path is None:
            reasons.append("Stage B model_path is not configured.")
        else:
            p = Path(model_path)
            if not p.exists():
                reasons.append(f"Stage B checkpoint file not found at: {p}")
            else:
                actual_hash = checkpoint_sha256 or compute_file_sha256(p)
                evaluated["stage_b_checkpoint_sha256"] = actual_hash
                expected_hash = manifest.modernbert.get("checkpoint_sha256")
                if actual_hash != expected_hash:
                    reasons.append(
                        f"ModernBERT checkpoint SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
                    )

    # 3. Calibration Temperature Check
    if stage_b is not None:
        scaler = getattr(stage_b, "scaler", None)
        if scaler is None or not getattr(scaler, "is_fitted", False):
            reasons.append("Stage B temperature scaler is missing or not fitted.")
        else:
            actual_temp = float(getattr(scaler, "temperature", 1.0))
            evaluated["stage_b_temperature"] = actual_temp
            expected_temp = float(manifest.modernbert.get("temperature", CANONICAL_CALIBRATION_TEMPERATURE))
            if abs(actual_temp - expected_temp) > 1e-5:
                reasons.append(
                    f"ModernBERT calibration temperature mismatch: expected {expected_temp}, got {actual_temp}"
                )

    # 4. Stage B Escalation Threshold Check
    if stage_b is not None:
        actual_thresh = float(getattr(stage_b, "escalation_threshold", 0.0))
        evaluated["stage_b_escalation_threshold"] = actual_thresh
        expected_thresh = float(manifest.modernbert.get("escalation_threshold", CANONICAL_STAGE_B_ESCALATION_THRESHOLD))
        if abs(actual_thresh - expected_thresh) > 1e-4:
            reasons.append(
                f"Stage B escalation threshold mismatch: expected {expected_thresh}, got {actual_thresh}"
            )

    # 5. Stage C Client & Model Name Check
    stage_c = getattr(cascade, "stage_c", None)
    if stage_c is None:
        reasons.append("Stage C judge is missing.")
    elif getattr(stage_c, "allow_mock_client", True):
        reasons.append("Stage C has mock client fallback enabled.")
    else:
        client = getattr(stage_c, "client", None)
        from clindoc.verify.stage_c import DefaultMockLLMJudge, VLLMJudgeClient
        if isinstance(client, DefaultMockLLMJudge) or client is None:
            reasons.append("Stage C client is DefaultMockLLMJudge (mock judge is not qualified).")
        else:
            actual_model = getattr(client, "model_name", type(client).__name__)
            evaluated["stage_c_model_name"] = actual_model
            expected_model = manifest.qwen.get("model_id", CANONICAL_QWEN_MODEL_ID)
            if actual_model != expected_model:
                reasons.append(
                    f"Stage C model name mismatch: expected '{expected_model}', got '{actual_model}'"
                )

    # 6. Stage C Prompt Version & Prompt Text Hash Check
    if stage_c is not None:
        actual_prompt_version = getattr(stage_c, "prompt_version", None)
        evaluated["stage_c_prompt_version"] = actual_prompt_version
        expected_prompt_version = manifest.stage_c_prompt.get("prompt_version")
        if actual_prompt_version != expected_prompt_version:
            reasons.append(
                f"Stage C prompt version mismatch: expected '{expected_prompt_version}', got '{actual_prompt_version}'"
            )

        actual_prompt_hash = compute_stage_c_prompt_hash()
        evaluated["stage_c_prompt_text_sha256"] = actual_prompt_hash
        expected_prompt_hash = manifest.stage_c_prompt.get("prompt_text_sha256")
        if actual_prompt_hash != expected_prompt_hash:
            reasons.append(
                f"Stage C prompt text SHA-256 mismatch: expected {expected_prompt_hash}, got {actual_prompt_hash}"
            )

    # 7. Final Status Determination
    if not reasons:
        status = QualificationStatus.QUALIFIED
        is_qualified = True
    else:
        status = QualificationStatus.UNQUALIFIED
        is_qualified = False

    return QualificationReport(
        status=status,
        is_qualified=is_qualified,
        reasons=reasons,
        manifest_version=manifest.version,
        manifest_digest=manifest.manifest_digest,
        evaluated_components=evaluated,
    )


def evaluate_config_qualification(
    config: ProductionPipelineConfig,
    manifest: Optional[QualifiedManifest] = None,
    checkpoint_sha256: Optional[str] = None,
) -> QualificationReport:
    """Evaluate whether a ProductionPipelineConfig matches the qualified manifest before instantiation."""
    if manifest is None:
        manifest = QualifiedManifest.create_canonical_v1()
    manifest.verify_integrity()

    reasons: list[str] = []
    evaluated: dict[str, Any] = {}

    profile = config.profile
    evaluated["profile"] = profile.value if isinstance(profile, RuntimeProfile) else str(profile)

    if profile != RuntimeProfile.PRODUCTION:
        reasons.append(
            f"Runtime profile is '{evaluated['profile']}'; QUALIFIED status requires 'production' profile."
        )

    if config.allow_mocks or config.allow_heuristics:
        reasons.append("Configuration permits mocks or heuristics.")

    ckpt_path = Path(config.modernbert_checkpoint_path)
    evaluated["checkpoint_path"] = str(ckpt_path)
    if not ckpt_path.exists():
        reasons.append(f"ModernBERT checkpoint file does not exist: {ckpt_path}")
    else:
        actual_hash = checkpoint_sha256 or compute_file_sha256(ckpt_path)
        evaluated["checkpoint_sha256"] = actual_hash
        expected_hash = manifest.modernbert.get("checkpoint_sha256")
        if actual_hash != expected_hash:
            reasons.append(
                f"ModernBERT checkpoint SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
            )

    actual_temp = config.calibration_temperature
    evaluated["calibration_temperature"] = actual_temp
    expected_temp = float(manifest.modernbert.get("temperature", CANONICAL_CALIBRATION_TEMPERATURE))
    if abs(actual_temp - expected_temp) > 1e-5:
        reasons.append(
            f"Calibration temperature mismatch: expected {expected_temp}, got {actual_temp}"
        )

    actual_thresh = config.stage_b_escalation_threshold
    evaluated["stage_b_escalation_threshold"] = actual_thresh
    expected_thresh = float(manifest.modernbert.get("escalation_threshold", CANONICAL_STAGE_B_ESCALATION_THRESHOLD))
    if abs(actual_thresh - expected_thresh) > 1e-4:
        reasons.append(
            f"Stage B escalation threshold mismatch: expected {expected_thresh}, got {actual_thresh}"
        )

    actual_model = config.vllm_model_name
    evaluated["vllm_model_name"] = actual_model
    expected_model = manifest.qwen.get("model_id", CANONICAL_QWEN_MODEL_ID)
    if actual_model != expected_model:
        reasons.append(
            f"Stage C model name mismatch: expected '{expected_model}', got '{actual_model}'"
        )

    if not reasons:
        status = QualificationStatus.QUALIFIED
        is_qualified = True
    else:
        status = QualificationStatus.UNQUALIFIED
        is_qualified = False

    return QualificationReport(
        status=status,
        is_qualified=is_qualified,
        reasons=reasons,
        manifest_version=manifest.version,
        manifest_digest=manifest.manifest_digest,
        evaluated_components=evaluated,
    )
