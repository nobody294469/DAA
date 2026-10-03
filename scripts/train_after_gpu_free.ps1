# Waits for a GPU job (e.g. a YOLO training PID) to exit, then runs the full Stage B training.
# Usage: powershell -File scripts\train_after_gpu_free.ps1 -WaitPid 30672
param([int]$WaitPid = 0)
$repo = Split-Path $PSScriptRoot -Parent
if ($WaitPid -gt 0) { Wait-Process -Id $WaitPid -ErrorAction SilentlyContinue }
Set-Location $repo
$env:PYTHONPATH = "src"; $env:PYTHONDONTWRITEBYTECODE = "1"; $env:CUDA_VISIBLE_DEVICES = "0"
& "$env:USERPROFILE\.venvs\daa\Scripts\python.exe" scripts/train_v3_experiment.py --device cuda `
  --model-id "$env:USERPROFILE\.cache\clindoc\BioClinical-ModernBERT-base" `
  --run-name modernbert_text_only_v3_laptop --batch-size 8 --grad-accum 2 --gradient-checkpointing `
  *> "$env:TEMP\daa_train_full.log"
