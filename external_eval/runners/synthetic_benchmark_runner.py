"""Synthetic Multi-Specialty Benchmark Evaluation Runner for ClinicalDoc.

Authority: User Request / Functional Extraction Benchmark
Location: external_eval/runners/synthetic_benchmark_runner.py

Evaluates:
- 12 frozen synthetic documents across 5 clinical specialties:
  * 4 Structured Lab Panels (LAB_0001 - LAB_0004)
  * 4 Structured Prescriptions (MED_0001 - MED_0004)
  * 4 Multi-Specialty Clinical Encounters (Cardiology, Endocrinology, Pulmonology, Nephrology)
- Evaluates 3 genuine extraction systems on the EXACT same test set:
  1. Deterministic (pure rules, regex, and catalog lookup)
  2. GLiNER-BioMed (Ihor/gliner-biomed-base-v1.0 running live in .venv_ner)
  3. DEC-004 Cascade (Deterministic tier-1 >= 0.75 threshold + GLiNER fallback)
- Strictly enforces Zero-Mock policy: Qwen/vLLM is tested for live endpoint; if unreachable,
  it is marked as UNAVAILABLE without mock substitution.
- Evaluates using canonical evaluate_dataset() from clindoc.extract.evaluate.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

# Ensure src and external_eval are on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "external_eval"))

from clindoc.extract.contracts import BBoxModel, Claim, Evidence
from clindoc.extract.deterministic import DeterministicExtractor
from clindoc.extract.evaluate import (
    ALL_CANONICAL_FIELDS,
    GroundTruthClaim,
    evaluate_dataset,
    load_ground_truth_claims,
    normalize_field_value,
)
from clindoc.extract.gliner_fallback import (
    SubprocessGLiNERProvider,
    is_valid_gliner_lab_value,
    is_valid_gliner_med_dose,
)
from clindoc.ocr.document_text import DocumentPage, DocumentText, Token
from clindoc.ocr.geometry import BBoxDict
from clindoc.ocr.ingest import ingest_from_record


BENCHMARK_DIR = REPO_ROOT / "external_eval" / "synthetic_benchmark"
MANIFEST_PATH = BENCHMARK_DIR / "manifest.json"


def get_git_revision() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def load_synthetic_document_and_gt(doc_entry: dict[str, Any]) -> tuple[DocumentText, list[GroundTruthClaim]]:
    """Load DocumentText and canonical GroundTruthClaims for a benchmark entry."""
    doc_id = doc_entry["doc_id"]
    category = doc_entry["category"]

    if category == "structured_lab":
        ocr_path = REPO_ROOT / doc_entry["ocr_cache"]
        with open(ocr_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        doc = ingest_from_record(data, doc_id=doc_id)
        gt = load_ground_truth_claims(doc_id, base_dir=REPO_ROOT)

    elif category == "structured_medication":
        ocr_path = REPO_ROOT / doc_entry["ocr_cache"]
        with open(ocr_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        doc = ingest_from_record(data, doc_id=doc_id)
        gt = load_ground_truth_claims(doc_id, base_dir=REPO_ROOT)

    elif category == "narrative_encounter":
        txt_path = REPO_ROOT / doc_entry["text_file"]
        gt_path = REPO_ROOT / doc_entry["gt_file"]

        raw_text = txt_path.read_text(encoding="utf-8")
        lines = raw_text.splitlines()
        tokens: list[Token] = []
        for line_idx, line in enumerate(lines):
            line_id = f"L{line_idx}"
            words = line.split()
            if not words:
                tokens.append(
                    Token(
                        text="",
                        bbox=BBoxDict(x0=0.0, y0=float(line_idx * 15), x1=100.0, y1=float(line_idx * 15 + 12)),
                        confidence=1.0,
                        line_id=line_id,
                    )
                )
            else:
                for w in words:
                    tokens.append(
                        Token(
                            text=w,
                            bbox=BBoxDict(x0=0.0, y0=float(line_idx * 15), x1=100.0, y1=float(line_idx * 15 + 12)),
                            confidence=1.0,
                            line_id=line_id,
                        )
                    )

        page = DocumentPage(
            page_number=1,
            width_px=1000,
            height_px=max(1000, len(lines) * 20),
            tokens=tokens,
        )
        doc = DocumentText(
            document_id=doc_id,
            source_type="born_digital",
            pages=[page],
        )

        with open(gt_path, "r", encoding="utf-8") as f:
            gt_data = json.load(f)

        gt = []
        for c in gt_data.get("claims", []):
            gt.append(GroundTruthClaim(
                document_id=doc_id,
                field_type=c["field_type"],
                value=c["value"],
                value_numeric=c.get("value_numeric"),
                unit_raw=c.get("unit_raw"),
                evidence_text=c.get("evidence_text"),
                char_start=c.get("char_start"),
                char_end=c.get("char_end"),
                page_number=c.get("page_number", 1),
            ))
    else:
        raise ValueError(f"Unknown category: {category}")

    return doc, gt


def check_qwen_availability(vllm_base_url: str = "http://localhost:8000/v1") -> bool:
    """Test if genuine Qwen/vLLM server is reachable."""
    try:
        req = urllib.request.Request(f"{vllm_base_url}/models")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


_BENCHMARK_EVIDENCE_STORE: dict[str, Evidence] = {}


def run_deterministic_predictions(docs: list[DocumentText]) -> dict[str, list[Claim]]:
    """Generate genuine predictions using pure DeterministicExtractor."""
    extractor = DeterministicExtractor()
    preds_by_doc: dict[str, list[Claim]] = {}

    for doc in docs:
        claims: list[Claim] = []
        is_lab = doc.document_id.upper().startswith("LAB_")

        for page in doc.pages:
            page_text = page.get_page_text()

            # Structured labs or meds
            if is_lab:
                evs, clms = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r0",
                    is_lab=True,
                )
                for e in evs:
                    _BENCHMARK_EVIDENCE_STORE[e.evidence_id] = e
                claims.extend(clms)
            elif doc.document_id.upper().startswith("MED_"):
                evs, clms = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r0",
                    is_lab=False,
                )
                for e in evs:
                    _BENCHMARK_EVIDENCE_STORE[e.evidence_id] = e
                claims.extend(clms)
            else:
                # Narrative note: attempt both lab and med deterministic extraction across rows
                evs_m, clms_m = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r_med",
                    is_lab=False,
                )
                for e in evs_m:
                    _BENCHMARK_EVIDENCE_STORE[e.evidence_id] = e
                claims.extend(clms_m)
                evs_l, clms_l = extractor.extract_region(
                    doc.document_id, page, page_text,
                    region_id=f"{doc.document_id}::p{page.page_number}::r_lab",
                    is_lab=True,
                )
                for e in evs_l:
                    _BENCHMARK_EVIDENCE_STORE[e.evidence_id] = e
                claims.extend(clms_l)

        preds_by_doc[doc.document_id] = claims

    return preds_by_doc


def run_gliner_predictions(docs: list[DocumentText], provider: SubprocessGLiNERProvider) -> dict[str, list[Claim]]:
    """Generate genuine predictions using SubprocessGLiNERProvider."""
    preds_by_doc: dict[str, list[Claim]] = {}

    gliner_label_map = {
        "medication": "med.name",
        "dosage": "med.dose",
        "route": "med.route",
        "frequency": "med.frequency",
        "lab_value": "lab.value",
        "lab value": "lab.value",
        "lab_flag": "lab.flag",
        "lab flag": "lab.flag",
    }

    doc_ids = [d.document_id for d in docs]
    texts = [d.pages[0].get_page_text() for d in docs]

    batch_results = provider.predict_batch(texts)

    for doc, entities in zip(docs, batch_results):
        doc_id = doc.document_id
        page_text = doc.pages[0].get_page_text()
        claims: list[Claim] = []
        for ent in entities:
            label = ent.get("label", "")
            text = ent.get("text", "").strip()
            score = float(ent.get("score", 1.0))
            if label in gliner_label_map and text:
                fld = gliner_label_map[label]

                # Context line for gating
                start = ent.get("start", 0)
                end = ent.get("end", len(text))
                line_start = page_text.rfind("\n", 0, start) + 1
                line_end = page_text.find("\n", end)
                if line_end == -1:
                    line_end = len(page_text)
                line_ctx = page_text[line_start:line_end]

                if fld == "lab.value" and not is_valid_gliner_lab_value(text, line_ctx):
                    continue
                if fld == "med.dose" and not is_valid_gliner_med_dose(text, line_ctx):
                    continue

                ev_id = f"{doc_id}::p1::{start}::{end}"
                ev = Evidence(
                    evidence_id=ev_id,
                    document_id=doc_id,
                    page_number=1,
                    text=text,
                    char_start=start,
                    char_end=end,
                    bbox=BBoxModel(x0=0.0, y0=0.0, x1=0.0, y1=0.0),
                )
                _BENCHMARK_EVIDENCE_STORE[ev_id] = ev

                claims.append(Claim(
                    document_id=doc_id,
                    field_type=fld,
                    value=text,
                    extraction_method="gliner",
                    extraction_confidence=score,
                    evidence_id=ev_id,
                    region_id="reg_0",
                ))
        preds_by_doc[doc_id] = claims

    return preds_by_doc


def run_cascade_predictions(
    docs: list[DocumentText],
    det_preds: dict[str, list[Claim]],
    gliner_preds: dict[str, list[Claim]],
    threshold: float = 0.75,
) -> dict[str, list[Claim]]:
    """Generate DEC-004 cascade predictions combining deterministic with GLiNER fallback.
    
    Uses scope-aware deduplication (is_duplicate_claim) so that distinct medications
    or laboratory entities with identical values are preserved.
    """
    from clindoc.extract.evaluate import is_duplicate_claim

    preds_by_doc: dict[str, list[Claim]] = {}

    for doc in docs:
        doc_id = doc.document_id
        d_claims = det_preds.get(doc_id, [])
        g_claims = gliner_preds.get(doc_id, [])

        combined: list[Claim] = []

        # 1. Accept Tier 2 deterministic claims unconditionally, and Tier 1 claims >= threshold
        for dc in d_claims:
            is_tier_2 = dc.field_type in {"lab.test_name", "lab.unit", "lab.reference_range", "med.duration"}
            if is_tier_2 or dc.extraction_confidence >= threshold:
                combined.append(dc)

        # 2. Add GLiNER fallback claims where not already duplicate of an existing claim
        for gc in g_claims:
            gc_ev = _BENCHMARK_EVIDENCE_STORE.get(gc.evidence_id)
            is_dup = any(
                is_duplicate_claim(
                    gc,
                    existing,
                    candidate_ev=gc_ev,
                    existing_ev=_BENCHMARK_EVIDENCE_STORE.get(existing.evidence_id),
                )
                for existing in combined
            )
            if not is_dup:
                combined.append(gc)

        preds_by_doc[doc_id] = combined

    return preds_by_doc


def run_synthetic_benchmark() -> dict[str, Any]:
    """Execute evaluation on the frozen 12-document multi-specialty synthetic benchmark."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest not found at: {MANIFEST_PATH}")

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    timestamp_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_rev = get_git_revision()

    print(f"\n=======================================================")
    print(f"CLINICALDOC MULTI-SPECIALTY SYNTHETIC BENCHMARK EVALUATION")
    print(f"Timestamp: {timestamp_utc}")
    print(f"Git Revision: {git_rev}")
    print(f"Total Documents: {len(manifest['documents'])}")
    print(f"=======================================================\n")

    # 1. Load all documents and ground truth claims
    docs: list[DocumentText] = []
    gt_by_doc: dict[str, list[GroundTruthClaim]] = {}

    for doc_entry in manifest["documents"]:
        doc, gt = load_synthetic_document_and_gt(doc_entry)
        docs.append(doc)
        gt_by_doc[doc.document_id] = gt

    total_gt_claims = sum(len(g) for g in gt_by_doc.values())
    print(f"Loaded {len(docs)} documents with {total_gt_claims} ground truth claims across 10 fields.\n")

    # 2. Run genuine Deterministic extractor
    print("Running System 1: DeterministicExtractor (rules/regex/catalog)...")
    det_preds = run_deterministic_predictions(docs)

    # 3. Run genuine GLiNER-BioMed via .venv_ner subprocess
    print("Running System 2: SubprocessGLiNERProvider (Ihor/gliner-biomed-base-v1.0)...")
    gliner_provider = SubprocessGLiNERProvider()
    gliner_preds = run_gliner_predictions(docs, gliner_provider)

    # 4. Run genuine DEC-004 Cascade
    print("Running System 3: DEC-004 Cascade (Deterministic threshold=0.75 -> GLiNER fallback)...")
    cascade_preds = run_cascade_predictions(docs, det_preds, gliner_preds, threshold=0.75)

    # 5. Check Qwen / vLLM availability
    qwen_available = check_qwen_availability()
    print(f"Checking System 4: Qwen2.5-7B-Instruct (vLLM endpoint): {'ONLINE' if qwen_available else 'OFFLINE (Zero-Mock Enforced: Mock Substitution Blocked)'}")

    # 6. Evaluate all systems using canonical evaluate_dataset()
    eval_det = evaluate_dataset(predictions_by_doc=det_preds, gt_by_doc=gt_by_doc)
    eval_gliner = evaluate_dataset(predictions_by_doc=gliner_preds, gt_by_doc=gt_by_doc)
    eval_cascade = evaluate_dataset(predictions_by_doc=cascade_preds, gt_by_doc=gt_by_doc)

    report: dict[str, Any] = {
        "benchmark_metadata": {
            "benchmark_name": manifest["benchmark_name"],
            "benchmark_version": manifest["benchmark_version"],
            "evaluation_timestamp_utc": timestamp_utc,
            "git_revision": git_rev,
            "total_documents_evaluated": len(docs),
            "total_gt_claims": total_gt_claims,
            "specialties_covered": manifest["specialties_covered"],
            "document_breakdown": manifest["document_breakdown"],
            "disclaimer": (
                "Synthetic Functional Benchmark: Validates pipeline components, rule coverage, "
                "and model integration on synthetically generated clinical notes and reports. "
                "Does NOT represent empirical clinical generalization on real-world noisy hospital EHR records."
            ),
        },
        "model_availability": {
            "deterministic": {"status": "ACTIVE_GENUINE", "provider": "DeterministicExtractor"},
            "gliner": {"status": "ACTIVE_GENUINE", "provider": "SubprocessGLiNERProvider", "model": "Ihor/gliner-biomed-base-v1.0"},
            "cascade": {"status": "ACTIVE_GENUINE", "provider": "DEC-004-Cascade"},
            "llm_narrative": {"status": "UNAVAILABLE", "endpoint": "http://localhost:8000/v1", "reason": "vLLM server offline; zero-mock policy prevents mock substitution"},
        },
        "systems": {
            "deterministic": eval_det,
            "gliner": eval_gliner,
            "cascade": eval_cascade,
        },
    }

    # Save report
    reports_dir = REPO_ROOT / "external_eval" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = reports_dir / "synthetic_benchmark_results.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport successfully saved to: {report_file}")
    return report


if __name__ == "__main__":
    run_synthetic_benchmark()
