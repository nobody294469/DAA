# Deploying Clindoc2FHIR to the cloud (Google Cloud Run + Google sign-in)

The `Dockerfile` packages the live system: the FastAPI app and website, the trained Stage B checkpoint,
and the LoRA-tuned Qwen2.5-1.5B Stage C judge. Base models are baked into the image. The static Vercel
demo (`scripts/build_static_site.py`) is separate and replays recorded results only.

## 1. Google sign-in (OAuth client ID)

1. Open Google Cloud Console and go to **Google Auth Platform → Branding**. Set the app name, a support
   email and the authorised domain (for Cloud Run: `run.app`). Under **Audience**, choose **External**;
   while the app is in testing, add your faculty's emails as test users.
2. Go to **Clients → Create client → Web application**. Under **Authorized JavaScript origins**, add
   `http://localhost:8000` (local) and, after the first deploy, the Cloud Run URL
   (`https://clindoc2fhir-….run.app`). No redirect URI is needed.
3. Copy the **Client ID** (it ends in `.apps.googleusercontent.com`). It is public, not a secret.

Sign-in is off unless `CLINDOC_GOOGLE_CLIENT_ID` is set (see `src/clindoc/web/auth.py`):

| Variable | Purpose |
|---|---|
| `CLINDOC_GOOGLE_CLIENT_ID` | Enables sign-in; the Web client ID from step 3 |
| `CLINDOC_SESSION_SECRET` | ≥ 32 random characters that sign the session cookie: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `CLINDOC_ALLOWED_EMAILS` | Optional comma list, e.g. `prof.a@college.edu,me@gmail.com` |
| `CLINDOC_ALLOWED_DOMAINS` | Optional comma list, e.g. `college.edu` |

If neither allowlist is set, any verified Google account gets in. When sign-in is on, every `/api` route
except `/api/health` and `/api/auth/*` returns 401 until the user signs in. Sessions last 8 hours.

To try it locally (PowerShell):

```powershell
$env:CLINDOC_GOOGLE_CLIENT_ID = "<client id>"; $env:CLINDOC_SESSION_SECRET = "<48 random chars>"
$env:PYTHONPATH = "src"; python -m clindoc.cli serve --checkpoint data/m2_results_v3/checkpoints/modernbert_text_only_v3_open_fold0_seed42.pt --device cpu --llm data/judge_lora/Qwen2.5-1.5B-Instruct-v3
```

## 2. Deploy to Cloud Run (CPU)

You need the gcloud CLI, a project with billing enabled, and the two gitignored model artifacts present
locally (`data/m2_results_v3/checkpoints/modernbert_text_only_v3_open_fold0_seed42.pt` and
`data/judge_lora/Qwen2.5-1.5B-Instruct-v3/`). `.gcloudignore` uploads them; `.gitignore` alone would not.

```bash
gcloud config set project <PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud config set builds/timeout 3600
gcloud run deploy clindoc2fhir --source . --region asia-south1 \
  --memory 16Gi --cpu 4 --timeout 900 --concurrency 4 --min-instances 0 --max-instances 1 \
  --allow-unauthenticated \
  --set-env-vars "CLINDOC_GOOGLE_CLIENT_ID=<client id>,CLINDOC_SESSION_SECRET=<secret>,CLINDOC_ALLOWED_DOMAINS=<college.edu>"
```

- `--allow-unauthenticated` lets browsers reach the sign-in page. The app's own Google sign-in protects the API.
- `--max-instances 1`: runs and page images are kept in memory per instance, and one global lock serialises
  the pipeline. Scaling out needs a shared run store first.
- Cold start loads about 7 GB of models (1–2 minutes). Before a presentation, set `--min-instances 1`
  (billed while idle), then set it back to 0.
- Expect each document to take minutes on CPU when several claims escalate to the LLM judge, because
  the 1.5B judge runs in fp32. The GPU variant below takes seconds.
- For production, move `CLINDOC_SESSION_SECRET` into Secret Manager (`--set-secrets`).
- Add the printed service URL to the OAuth client's JavaScript origins (step 1.2).

## 3. GPU variant (faster judge)

In the `Dockerfile`, set `ARG TORCH_INDEX=https://download.pytorch.org/whl/cu128` to use CUDA torch.
Then deploy with an L4 GPU. The project needs Cloud Run GPU quota in a region that offers it,
for example `us-central1`:

```bash
gcloud run deploy clindoc2fhir --source . --region us-central1 \
  --gpu 1 --gpu-type nvidia-l4 --no-cpu-throttling --memory 16Gi --cpu 4 --timeout 900 --max-instances 1 \
  --allow-unauthenticated \
  --set-env-vars "CLINDOC_LLM_DEVICE=cuda,CLINDOC_GOOGLE_CLIENT_ID=<client id>,CLINDOC_SESSION_SECRET=<secret>"
```

Stage B and SapBERT stay on CPU (`CLINDOC_DEVICE=cpu` in the image), and the judge uses the GPU.

## Known limits in the container

- Scanned images (PaddleOCR) and GLiNER fallback extraction run in separate local venvs and are not
  packaged; image uploads return a clear 422. PDFs and text work.
- Any other container host works the same way: run the image with `PORT` set and the env vars above.
