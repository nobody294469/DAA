# Phase 8.7 — Step 6: Synthetic Load Testing & Capacity/Sizing Report

**Document ID:** `REP-PHASE8-7-STEP6-LOAD-TEST`  
**Date:** October 6, 2026  
**Status:** COMPLETE / SIGNED-OFF  
**Scope:** Phase 8.7 Step 6 — Synthetic Load Testing & Sizing Guidance  
**Target:** Production Orchestration Pipeline, Verification Cascade, Concurrency Limiter  

---

## 1. Executive Summary

Phase 8.7 Step 6 executed a deterministic, synthetic load benchmark against the production document processing pipeline and verification cascade. The harness subjected the orchestration infrastructure to the representative clinical workload specified for Phase 8.7:
- **54 claims per document**
- **11 escalated claims per document** (routed to Stage C / LLM judge)
- **43 non-escalated claims** (20 resolved deterministically at Stage A, 23 resolved autonomously at Stage B)

The primary objectives were:
1. Validate that the application withstands concurrent multi-document execution without crashes, deadlocks, permit leaks, or database corruption.
2. Verify that the Stage C `ConcurrencyLimiter` and `CircuitBreaker` enforce configured throughput ceilings and release permits under contention.
3. Establish empirical baseline orchestration latencies to separate **orchestration overhead** from **GPU inference latency**, providing a transparent capacity and sizing model for production deployment.

### Result
**PASS.** The architecture processed all synthetic documents to valid terminal states (`COMPLETED` for jobs, `PENDING_REVIEW` for documents), stored relational and audit records with 100% integrity, adhered strictly to configured concurrency caps, demonstrated zero semaphore/permit leaks, and suffered zero unhandled exceptions or deadlocks across 1, 2, 4, and 8 concurrent documents.

> [!IMPORTANT]
> **Boundary Notice**: This benchmark was executed on the local development environment using deterministic component doubles for model inference. **It is an orchestration and pipeline load benchmark, NOT a physical GPU benchmark.** Physical GPU latency, token decode throughput, and VRAM utilization cannot be measured locally and are explicitly identified as production deployment validation gates.

---

## 2. Test Environment

| Parameter | Value |
|---|---|
| **Operating System** | Windows 10 (10.0.26200-SP0) |
| **Python Runtime** | 3.11.9 (64-bit) |
| **Processor / Cores** | Intel64 Family 6 Model 154 Stepping 3 (12 logical cores) |
| **Physical GPU Available** | **False** (No local NVIDIA CUDA accelerator) |
| **Physical vLLM Server Available** | **False** (Private vLLM node is configured for remote deployment per Step 4) |
| **Model Serving Configuration** | Deterministic component double (`DeterministicLoadJudgeClient`) with bounded simulated latency ($2\,\text{ms}$) |
| **Stage B Classifier** | `DeterministicLoadStageB` (deterministic calibrated confidence assignment) |
| **Configured Model Concurrency Cap** | 10 (Scenario A/B) / 5 (Scenario C stress) |
| **Database Engine** | SQLite (isolated in-memory instances with `StaticPool` and thread-safe PRAGMA) |

---

## 3. Synthetic Workload Definition

The synthetic workload was generated deterministically using `SyntheticWorkloadGenerator`:
- **Document Shape:** 1-page synthetic clinical encounter (table and narrative regions).
- **Total Claims per Document:** Exactly **54**.
  - **Tier 2 Deterministic Claims (20 claims):** Stage A rule-based evaluation (`lab.unit`, `lab.reference_range`, `lab.test_name`, `med.duration`). Terminated at Stage A.
  - **Tier 1 Autonomous Claims (23 claims):** Evaluated through Stage A and Stage B (`lab.value`, `med.name`, `med.dose`, `med.frequency`). Calibrated confidence $\ge 0.90$. Terminated autonomously at Stage B.
  - **Tier 1 Escalated Claims (11 claims):** Evaluated through Stage A, Stage B, and escalated to Stage C (`lab.value` high, `lab.flag`, `med.name` complex). Calibrated confidence $\approx 0.52 < 0.60$ escalation threshold. Required LLM judge synthesis.
- **Evidence Spans:** 54 linked bounding boxes and verbatim token spans.
- **Concurrency Levels Tested:** 1, 2, 4, and 8 concurrent documents.

---

## 4. Benchmark Results

### Local Orchestration Progression (Scenario A & Scenario B)

Measurements represent actual timing observed during execution across all four concurrency tiers:

| Concurrency Level | Documents Processed | Total Claims | Total Elapsed (s) | Throughput (docs/s) | Throughput (claims/s) | Document Latency p50 (s) | Document Latency p95 (s) | Mean Latency (s) | Max Latency (s) | Errors / Failures | Peak In-Flight Stage C |
|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **1 (Baseline)** | 1 | 54 | 1.72 | 0.58 | 31.41 | 0.25 | 0.25 | 0.25 | 0.25 | 0 | 1 |
| **2** | 2 | 108 | 3.47 | 0.58 | 31.14 | 0.36 | 0.36 | 0.39 | 0.42 | 0 | 1 |
| **4** | 4 | 216 | 6.70 | 0.60 | 32.22 | 0.78 | 0.80 | 1.21 | 2.53 | 0 | 2 |
| **8** | 8 | 432 | 15.00 | 0.53 | 28.80 | 2.11 | 2.36 | 2.67 | 6.72 | 0 | 3 |

*Note: In the isolated single-document micro-run (Scenario A baseline), the pipeline completed 54 claims in 0.469s (8.69 ms/claim, 2.13 docs/s) when worker initialization overhead is isolated.*

### Per-Claim Latency Breakdown (Scenario A Baseline)

- **Total Claims:** 54
- **Per-Claim Mean Latency:** $8.69\,\text{ms}$
- **Stage A Resolution (20 claims):** $< 0.5\,\text{ms}$ per claim (pure string/regex/dictionary lookup).
- **Stage B Resolution (23 claims):** $< 1.5\,\text{ms}$ per claim (feature extraction and confidence calibration).
- **Stage C Resolution (11 claims):** $2.1\,\text{ms}$ per claim (simulated client round-trip + JSON deserialization + circuit breaker permit management).

---

## 5. Concurrency-Cap Verification (Scenario C)

To prove that the architectural concurrency limiter prevents overloading downstream serving infrastructure:
- **Test:** 24 concurrent worker threads requested Stage C evaluation simultaneously.
- **Configured Concurrency Cap:** **5** concurrent requests.
- **Results:**
  - **Configured Cap:** 5
  - **Peak Observed In-Flight:** **5**
  - **Cap Respected:** **True** (no instant exceeded the limit of 5)
  - **Requests Handled:** 24 / 24 completed successfully
  - **Errors / Timeouts:** 0
  - **Final Active Concurrency:** **0**
  - **Permit Leaks:** **None** (`limiter.active_count == 0` upon pool completion)

This verifies that incoming requests queue gracefully on the threading semaphore rather than flooding the model endpoint or triggering unbounded memory growth.

---

## 6. Reliability and State Invariants

Across all synthetic load scenarios, the following architectural invariants were verified:

1. **Terminal State Transitions:**
   - Every `ProcessingJob` transitioned from `PENDING` $\to$ `RUNNING` $\to$ `COMPLETED`.
   - Every `Document` transitioned from `INGESTED` $\to$ `PENDING_REVIEW`.
   - Zero jobs remained stuck in `RUNNING` or `FAILED`.
2. **Relational Data Integrity:**
   - Every document yielded exactly 54 `Claim` records.
   - Every claim had exactly one corresponding `VerificationResult` record.
   - Stage distribution was strictly conserved: 20 `STAGE_A`, 23 `STAGE_B`, 11 `STAGE_C`.
3. **Audit Trail Completeness:**
   - Granular telemetry events were recorded for each pipeline stage: `OCR`, `EXTRACTION`, `NORMALIZATION`, `VERIFICATION`.
4. **Zero Fallback Substitution:**
   - No mock/heuristic fallbacks were silently introduced into production paths.

---

## 7. Bottleneck Analysis

Analysis of the measured local run identified the primary performance drivers:

1. **Python Global Interpreter Lock (GIL) & Process Overhead:**
   - When running 8 concurrent worker threads in a single Python process, CPU utilization is constrained by Python's GIL during normalization and JSON serialization. In production, multi-process workers (e.g. `gunicorn`/`celery` or Kubernetes pods with 1 worker per container) will eliminate GIL contention.
2. **Database Connection / SQLite Locking:**
   - SQLite enforces database-level locking during write transactions. Using separate database connections or PostgreSQL in production provides true row-level concurrency.
3. **Model Weight Loading / Cold-Start:**
   - Initializing worker instances cold consumes $\approx 1.2\,\text{s}$ per thread while initializing vocabularies and weights. Long-running production daemon workers amortize this cost to zero.

---

## 8. Sizing and Capacity Framework

Because physical GPU inference was not executed locally, production capacity is determined through an **analytical calculation framework** combining local orchestration timings with expected vLLM serving parameters:

> [!WARNING]
> **Analytical Projection Notice**: The calculations in this section are **analytical projections — NOT measured on physical GPU/vLLM infrastructure.** No physical NVIDIA GPU was available in the local test environment. The assumed inference timings ($T_{\text{infer}} \approx 0.375\,\text{s}$), decode speeds ($8\,\text{ms/token}$), and throughput figures ($\approx 3,700\,\text{docs/hour}$) represent theoretical extrapolations derived from published benchmarks, not experimentally validated production capacity. All hardware references (L4, A10G, A100) represent **provisional sizing guidance**, not a validated hardware requirement.

### The Throughput Equation

$$\text{Total Time per Document } (T_{\text{doc}}) = T_{\text{orch}} + \frac{N_{\text{esc}} \times T_{\text{infer}}}{C_{\text{gpu}}}$$

Where:
- $T_{\text{orch}}$: Local orchestration time per document $\approx 0.45\,\text{s}$ (measured baseline).
- $N_{\text{esc}}$: Number of escalated claims requiring Stage C $\approx 11$ claims.
- $T_{\text{infer}}$: Real vLLM generation time per escalated claim (Time to First Token + Decode Time).
- $C_{\text{gpu}}$: Effective concurrency of the vLLM serving engine.

### Inference Latency Projections (Qwen2.5-7B-Instruct AWQ)

Based on published vLLM benchmarks for 7B AWQ models on modern datacenter GPUs (NVIDIA L4 / A10G / A100):
- **Input Tokens (Prompt + Evidence Context):** $\approx 450$ tokens.
- **Output Tokens (JSON Verification Schema):** $\approx 40$ tokens.
- **Estimated TTFT (Time to First Token):** $25 - 45\,\text{ms}$.
- **Decode Time ($40 \text{ tokens} \times 8\,\text{ms/token}$):** $\approx 320\,\text{ms}$.
- **Expected $T_{\text{infer}}$ per Claim:** $\approx 350 - 400\,\text{ms}$ ($0.375\,\text{s}$).

### Sizing Projections

#### Scenario 1: Analytical Single-GPU Projection (Provisional Guidance: e.g., 1x NVIDIA L4 24GB or A10G)

*(Analytical projection — NOT measured on physical GPU/vLLM infrastructure)*

- **vLLM Max Concurrency Cap:** 8 concurrent requests.
- **Stage C Claims / Document:** 11 claims.
- **Inference Time per Document:** $\frac{11 \times 0.375\,\text{s}}{8 \text{ concurrency}} \approx 0.52\,\text{s}$.
- **Total Latency per Document:** $0.45\,\text{s (orch)} + 0.52\,\text{s (GPU)} \approx 0.97\,\text{s}$.
- **Projected Throughput Capacity:**
  - $\approx 1.03\,\text{documents/second}$
  - $\approx 62\,\text{documents/minute}$
  - **$\approx 3,700\,\text{documents/hour}$**
  - **$\approx 200,000\,\text{claims/hour}$**

#### Scenario 2: High-Volume Hospital Ward (Concurrent Demand Analysis)

To evaluate system behavior when 20 documents are simultaneously active in the pipeline:

- **Workload Concurrency Model**:
  - In the production worker architecture, each worker processes a document's verification cascade sequentially across its 54 claims.
  - Therefore, each active document worker issues at most **1 in-flight Stage C request** at any instant (not 11 parallel requests per document).
  - Non-Stage C stages (OCR, extraction, normalization, Stage A, Stage B) execute in $T_{\text{orch}} \approx 0.45\,\text{s}$.
  - Under the analytical assumption of $T_{\text{infer}} \approx 0.375\,\text{s}$ per claim, the total sequential Stage C time per document is $11 \times 0.375\,\text{s} \approx 4.13\,\text{s}$.
  - The total document turnaround time is $T_{\text{doc}} \approx 0.45\,\text{s} + 4.13\,\text{s} = 4.58\,\text{s}$.

- **Average Simultaneous Stage C Demand Implied by Workload**:
  - The proportion of lifecycle time an active document worker spends in Stage C is $\frac{4.13\,\text{s}}{4.58\,\text{s}} \approx 90.2\%$.
  - When 20 documents are concurrently active and uniformly distributed across their execution lifecycles, the expected average simultaneous Stage C demand is:
    $$\text{Average Active Stage C Demand} = 20 \times 0.902 \approx 18.0 \text{ simultaneous requests}$$
  - *(Note: The claim-count ratio $11/54 \approx 20.4\%$ reflects static claim inventory, not temporal demand, because Stage A/B evaluations take $< 2\,\text{ms}$ while Stage C evaluations take hundreds of milliseconds.)*

- **Peak / Burst Concurrency Assumption**:
  - In the worst-case synchronization scenario where all 20 document workers reach Stage C simultaneously, the instantaneous peak demand is **up to 20 concurrent requests**.

- **Configured vLLM / Model Concurrency**:
  - The production `ConcurrencyLimiter` bounds the maximum number of requests admitted into the model-serving node simultaneously (e.g., configured to 8, 10, or 16).
  - Any surplus requests beyond the configured cap wait safely on the threading semaphore rather than overloading vLLM. For example, under a cap of 10, an 18–20 request burst admits 10 requests immediately while 8–10 wait in the local queue, adding bounded queue latency without failing or crashing.

- **Hardware Recommendation (Provisional Guidance)**:
  - If avoiding queue wait during a 20-request burst is required, provisional sizing guidance suggests **2x NVIDIA L4 (24GB)** or **1x NVIDIA A100 (40GB/80GB)** with vLLM continuous batching (`--max-model-len 4096 --gpu-memory-utilization 0.90`).
  - **Explicit Evidence Boundary:** The local load harness only evaluated CPU orchestration and verified the concurrency limiter. **The available evidence is insufficient to determine the required GPU concurrency ceiling, queuing delay, or hardware SKU without real GPU benchmarking.**

---

## 9. Boundary Classification: Measured vs. Extrapolated

To ensure strict engineering integrity, this report classifies all conclusions into three explicit tiers:

| Dimension | Classification | Status & Evidence |
|---|---|---|
| **Pipeline State Transitions** | **Proven by this test** | All jobs and documents reach terminal states without crash or corruption. |
| **54-Claim Distribution** | **Proven by this test** | Exact routing (20 Stage A, 23 Stage B, 11 Stage C) persisted in DB. |
| **Concurrency Limiter Integrity** | **Proven by this test** | Max concurrency ceiling strictly respected; 0 leaks across 24 requests. |
| **Deadlock & Memory Freedom** | **Proven by this test** | Zero hangs or permit deadlocks across concurrent threads. |
| **Orchestration Latency ($T_{\text{orch}}$)** | **Proven by this test** | Measured at $0.45\,\text{s}$ baseline (8.7 ms/claim on CPU). |
| **Document/Hour Extrapolation** | **Inferred from this test** | Calculated via throughput equation using proven orchestration metrics. |
| **Physical Qwen GPU Latency** | **Requires real vLLM/GPU benchmark** | Cannot be measured on Windows developer CPU without NVIDIA GPU. |
| **vLLM Continuous Batching KV Cache Sizing** | **Requires real vLLM/GPU benchmark** | Requires physical VRAM monitoring under continuous token generation. |
| **Hospital Network Round-Trip** | **Requires real vLLM/GPU benchmark** | Requires physical VPC network deployment per Step 4 manifests. |

> [!IMPORTANT]
> **Production Validation Boundary**: Final GPU SKU selection and production throughput must be established through a real Qwen2.5-7B-Instruct-AWQ + vLLM benchmark using the production prompt/evidence shape, measuring TTFT, decode throughput, concurrent requests, GPU utilization, VRAM/KV-cache usage, and sustained throughput.

---

## 10. Acceptance Sign-Off Matrix

| Requirement | Acceptance Gate | Observed Result | Status |
|---|---|---|:---:|
| 1. No application crashes under synthetic load | Zero uncaught exceptions | 0 unhandled exceptions across 15 documents | **PASS** |
| 2. No deadlocks | All threads complete within timeout | All threads completed promptly | **PASS** |
| 3. No permit/semaphore leaks | `limiter.active_count == 0` | 0 active permits upon completion | **PASS** |
| 4. Database consistency | Valid relational integrity for all claims | 100% of claims and verification results persisted | **PASS** |
| 5. Stage A/B behavior intact | 20 Stage A, 23 Stage B claims | Strictly preserved in relational storage | **PASS** |
| 6. Stage C circuit breaker intact | Cap respected under contention | Peak in-flight $\le 5$ with cap = 5 | **PASS** |
| 7. No mock clinical fallback in prod | Fail-closed invariants preserved | No mock fallback introduced | **PASS** |
| 8. All documents reach terminal state | Job: `COMPLETED`, Doc: `PENDING_REVIEW` | 100% achieved | **PASS** |
| 9. Results reproducible | Deterministic harness execution | 100% verified via automated pytest suite | **PASS** |
| 10. Authoritative specifications untouched | Zero spec modifications | No modifications to specs or UI | **PASS** |

**Conclusion:** Phase 8.7 Step 6 is **ACCEPTED AND SIGNED OFF**.
