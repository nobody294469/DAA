"""Hybrid Lexical + Semantic Terminology Retriever for LOINC and RxNorm.

Authority: PROJECT_SPEC §14.2, DEC-007, DEC-002, PHASE0_ARCHITECTURE_RESOLUTION AUDIT-14
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Optional, Protocol, Union, runtime_checkable


@dataclass(frozen=True)
class CandidateMatch:
    """A scored retrieval candidate concept."""
    code: str
    display: str
    score: float
    system: str  # "LOINC" | "RxNorm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "display": self.display,
            "score": round(self.score, 4),
            "system": self.system,
        }


@runtime_checkable
class SemanticEmbedder(Protocol):
    """Protocol for semantic embedding model backends conforming to DEC-007."""

    def similarity_scores(self, query: str, candidates: list[str]) -> list[float]:
        """Compute semantic cosine similarity scores in [0, 1] between query and candidates."""
        ...


class SapBERTEmbedder:
    """Primary semantic embedder using SapBERT (DEC-007).

    Model: cambridgeltl/SapBERT-from-PubMedBERT-fulltext
    Authority: DEC-007, PROJECT_SPEC §14.2.
    Designed for memory efficiency on 6GB RTX 3050 (torch.no_grad, batching, lazy load).
    """

    def __init__(
        self,
        model_name: str = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        device: Optional[str] = None,
        local_files_only: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.local_files_only = local_files_only
        self._tokenizer = None
        self._model = None
        self._initialized = False

    def _lazy_init(self) -> bool:
        if self._initialized:
            return self._model is not None
        self._initialized = True
        try:
            import os
            import torch
            from transformers import AutoModel, AutoTokenizer

            allow_download = os.environ.get("ALLOW_SAPBERT_DOWNLOAD", "0").lower() in ("1", "true")
            local_only = self.local_files_only and not allow_download

            if self.device is None:
                self.device = "cuda" if torch.cuda.is_available() else "cpu"

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                local_files_only=local_only,
            )
            self._model = AutoModel.from_pretrained(
                self.model_name,
                local_files_only=local_only,
            ).to(self.device)
            self._model.eval()
            return True
        except Exception:
            self._model = None
            self._tokenizer = None
            return False

    def is_available(self) -> bool:
        return self._lazy_init()

    def similarity_scores(self, query: str, candidates: list[str]) -> list[float]:
        if not candidates:
            return []
        if not self._lazy_init():
            raise RuntimeError(
                f"SapBERT model '{self.model_name}' could not be initialized. "
                "Ensure transformers and torch are installed and weights are accessible, "
                "or pass an explicit NGramProxyEmbedder for offline/testing environments."
            )

        import torch
        import torch.nn.functional as F

        all_texts = [query] + candidates
        with torch.no_grad():
            toks = self._tokenizer(
                all_texts,
                padding=True,
                truncation=True,
                max_length=64,
                return_tensors="pt",
            ).to(self.device)
            outputs = self._model(**toks)
            cls_reps = outputs[0][:, 0, :]
            norm_reps = F.normalize(cls_reps, p=2, dim=1)
            query_rep = norm_reps[0:1]
            cand_reps = norm_reps[1:]
            sims = torch.mm(query_rep, cand_reps.t()).squeeze(0)
            sims = torch.clamp((sims + 1.0) / 2.0, 0.0, 1.0)
            return [float(s) for s in sims.cpu().tolist()]

    def similarity(self, s1: str, s2: str) -> float:
        scores = self.similarity_scores(s1, [s2])
        return scores[0] if scores else 0.0


class NGramProxyEmbedder:
    """Explicit offline/mock fallback embedder using character n-gram Jaccard heuristics.

    Authority: DEC-007.
    Used for unit testing, offline execution, or environments where SapBERT
    weights are not available. This is an explicit lexical surrogate, NOT neural
    semantic embeddings.
    """

    def __init__(self, n: int = 2) -> None:
        self.n = n

    def similarity_scores(self, query: str, candidates: list[str]) -> list[float]:
        norm_q = _normalize_text(query)
        scores = []
        for cand in candidates:
            norm_c = _normalize_text(cand)
            score = _ngram_jaccard(norm_q, norm_c, n=self.n)
            scores.append(score)
        return scores

    def similarity(self, s1: str, s2: str) -> float:
        scores = self.similarity_scores(s1, [s2])
        return scores[0] if scores else 0.0


def _normalize_text(s: str) -> str:
    """Normalize text for lexical matching."""
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _char_ngrams(s: str, n: int = 3) -> set[str]:
    """Generate character n-grams from normalized string."""
    s = f"^{s}$"
    return {s[i : i + n] for i in range(max(1, len(s) - n + 1))}


def _ngram_jaccard(s1: str, s2: str, n: int = 3) -> float:
    """Compute character n-gram Jaccard similarity between two strings."""
    ng1 = _char_ngrams(s1, n)
    ng2 = _char_ngrams(s2, n)
    if not ng1 or not ng2:
        return 0.0
    intersection = len(ng1 & ng2)
    union = len(ng1 | ng2)
    return intersection / union if union > 0 else 0.0


def _token_overlap(s1: str, s2: str) -> float:
    """Compute token Jaccard similarity."""
    t1 = set(s1.split())
    t2 = set(s2.split())
    if not t1 or not t2:
        return 0.0
    return len(t1 & t2) / len(t1 | t2)


def _levenshtein_ratio(s1: str, s2: str) -> float:
    """Compute normalized Levenshtein similarity in [0, 1]."""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    len1, len2 = len(s1), len(s2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

    for i in range(len1 + 1):
        dp[i][0] = i
    for j in range(len2 + 1):
        dp[0][j] = j

    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,      # deletion
                dp[i][j - 1] + 1,      # insertion
                dp[i - 1][j - 1] + cost  # substitution
            )

    distance = dp[len1][len2]
    max_len = max(len1, len2)
    return 1.0 - (distance / max_len)


class ConceptEntry:
    """A terminology concept entry in the index."""

    def __init__(self, code: str, canonical_name: str, aliases: list[str], system: str) -> None:
        self.code = code
        self.canonical_name = canonical_name
        self.aliases = aliases
        self.system = system
        self.norm_canonical = _normalize_text(canonical_name)
        self.norm_aliases = [_normalize_text(a) for a in aliases]
        self.all_norm_forms = {self.norm_canonical} | set(self.norm_aliases)


class HybridTerminologyRetriever:
    """Hybrid lexical + dense similarity retriever over a standard terminology subset.

    Conforms to PROJECT_SPEC §14.2 and DEC-007.
    Combines BM25/lexical scoring and semantic embedding retrieval:
        score = alpha * lexical + (1 - alpha) * semantic
    Uses SapBERT (cambridgeltl/SapBERT-from-PubMedBERT-fulltext) as primary
    semantic model, with NGramProxyEmbedder as explicit offline/testing fallback.
    """

    def __init__(
        self,
        system_name: str,  # "LOINC" or "RxNorm"
        alpha: float = 0.5,  # Weight for lexical vs semantic scoring: alpha*lex + (1-alpha)*sem
        semantic_embedder: Optional[SemanticEmbedder] = None,
    ) -> None:
        self.system_name = system_name
        self.alpha = float(alpha)
        if semantic_embedder is not None:
            self.semantic_embedder = semantic_embedder
        else:
            sapbert = SapBERTEmbedder()
            if sapbert.is_available():
                self.semantic_embedder = sapbert
            else:
                self.semantic_embedder = NGramProxyEmbedder(n=2)

        self.concepts: list[ConceptEntry] = []
        self._exact_index: dict[str, ConceptEntry] = {}

    def index_concepts(self, raw_data: dict[str, Any]) -> None:
        """Build retrieval index from terminology subset JSON data."""
        self.concepts.clear()
        self._exact_index.clear()

        # Group aliases by concept code
        code_to_aliases: dict[str, list[str]] = {}
        code_to_canonical: dict[str, str] = {}

        for surface_name, record in raw_data.items():
            if self.system_name == "LOINC":
                code = record.get("loinc_code") or record.get("code")
                canon = record.get("canonical_name", surface_name)
            else:  # RxNorm
                code = record.get("rxcui") or record.get("code")
                canon = record.get("canonical_name", surface_name)

            if not code:
                continue

            if code not in code_to_canonical:
                code_to_canonical[code] = canon
                code_to_aliases[code] = []

            if surface_name not in code_to_aliases[code]:
                code_to_aliases[code].append(surface_name)
            if canon not in code_to_aliases[code]:
                code_to_aliases[code].append(canon)

        for code, canon in code_to_canonical.items():
            entry = ConceptEntry(
                code=code,
                canonical_name=canon,
                aliases=code_to_aliases[code],
                system=self.system_name,
            )
            self.concepts.append(entry)
            for form in entry.all_norm_forms:
                self._exact_index[form] = entry

    def retrieve(self, query: str, top_k: int = 5) -> list[CandidateMatch]:
        """Retrieve top-k candidates for query string using hybrid scoring."""
        if not query or not query.strip():
            return []

        norm_query = _normalize_text(query)

        # 1. Exact match fast path (lexical = 1.0, score = 1.0)
        if norm_query in self._exact_index:
            entry = self._exact_index[norm_query]
            match = CandidateMatch(
                code=entry.code,
                display=entry.canonical_name,
                score=1.0,
                system=self.system_name,
            )
            return [match]

        # 2. Gather candidates, compute lexical similarity
        candidate_entries: list[ConceptEntry] = []
        candidate_names: list[str] = []
        lex_scores: list[float] = []

        for entry in self.concepts:
            best_lex = 0.0
            for form in entry.all_norm_forms:
                if norm_query == form:
                    best_lex = 1.0
                    break

                # Substring containment bonus
                if len(norm_query) > 3 and (norm_query in form or form in norm_query):
                    containment = min(len(norm_query), len(form)) / max(len(norm_query), len(form))
                else:
                    containment = 0.0

                ngram_sim = _ngram_jaccard(norm_query, form, n=3)
                token_sim = _token_overlap(norm_query, form)
                lev_sim = _levenshtein_ratio(norm_query, form)

                lex_score = max(ngram_sim * 0.4 + lev_sim * 0.4 + token_sim * 0.2, containment * 0.85)
                if lex_score > best_lex:
                    best_lex = lex_score

            candidate_entries.append(entry)
            candidate_names.append(entry.canonical_name)
            lex_scores.append(best_lex)

        # 3. Compute semantic similarity via SemanticEmbedder (DEC-007)
        try:
            sem_scores = self.semantic_embedder.similarity_scores(query, candidate_names)
            if len(sem_scores) != len(candidate_entries):
                sem_scores = [0.0] * len(candidate_entries)
        except Exception:
            # Fallback to lexical proxy if embedder fails
            sem_scores = [s * 0.9 for s in lex_scores]

        scored_candidates: list[CandidateMatch] = []
        for entry, lex_score, sem_score in zip(candidate_entries, lex_scores, sem_scores):
            eff_sem = max(sem_score, lex_score * 0.9)
            # Hybrid score combination per DEC-007
            score = self.alpha * lex_score + (1.0 - self.alpha) * eff_sem

            if score > 0.15:
                scored_candidates.append(
                    CandidateMatch(
                        code=entry.code,
                        display=entry.canonical_name,
                        score=score,
                        system=self.system_name,
                    )
                )

        scored_candidates.sort(key=lambda c: c.score, reverse=True)
        return scored_candidates[:top_k]
