# Clindoc2FHIR web app (FastAPI + trained Stage B ModernBERT + LoRA Qwen judge) for Google Cloud Run
# or any container host. CPU by default; see docs/deploy.md for the GPU variant and the Google sign-in env.
#
# The build context must contain the two gitignored model artifacts (they are not in git):
#   data/m2_results_v3/checkpoints/modernbert_text_only_v3_open_fold0_seed42.pt   (Stage B, 298 MB)
#   data/judge_lora/Qwen2.5-1.5B-Instruct-v3/                                      (Stage C LoRA, 74 MB)
FROM python:3.12-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HUB_DISABLE_TELEMETRY=1 \
    PYTHONPATH=/app/src
WORKDIR /app

RUN pip install torch --index-url ${TORCH_INDEX}
COPY pyproject.toml README.md ./
COPY src ./src
# Editable: the code reads configs/, schemas/ and data/ relative to the repo root.
RUN pip install -e ".[ml,web]"

# Base models baked into the image (no hub download at cold start); ~/.cache/clindoc is where the
# code looks first (stage_b.resolve_base_model_id, stage_c.adapter_base_model, normalize.hybrid).
RUN python -c "from huggingface_hub import snapshot_download as get; from pathlib import Path; \
c = Path.home() / '.cache' / 'clindoc'; \
get('thomas-sounack/BioClinical-ModernBERT-base', local_dir=c / 'BioClinical-ModernBERT-base'); \
get('Qwen/Qwen2.5-1.5B-Instruct', local_dir=c / 'Qwen2.5-1.5B-Instruct'); \
get('cambridgeltl/SapBERT-from-PubMedBERT-fulltext', local_dir=c / 'SapBERT-from-PubMedBERT-fulltext')"

COPY . .

ENV CLINDOC_STAGE_B_CHECKPOINT=/app/data/m2_results_v3/checkpoints/modernbert_text_only_v3_open_fold0_seed42.pt \
    CLINDOC_LLM_MODEL=/app/data/judge_lora/Qwen2.5-1.5B-Instruct-v3 \
    CLINDOC_DEVICE=cpu
EXPOSE 8080
CMD ["sh", "-c", "exec python -m clindoc.cli serve --host 0.0.0.0 --port ${PORT:-8080}"]
