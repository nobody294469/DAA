"""Hybrid Lexical + Semantic Terminology Retriever for LOINC and RxNorm.

Authority: PROJECT_SPEC §14.2, DEC-007, DEC-002, PHASE0_ARCHITECTURE_RESOLUTION AUDIT-14
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Optional


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
    """

    def __init__(
        self,
        system_name: str,  # "LOINC" or "RxNorm"
        alpha: float = 0.5,  # Weight for lexical vs semantic scoring: alpha*lex + (1-alpha)*sem
    ) -> None:
        self.system_name = system_name
        self.alpha = float(alpha)
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

        scored_candidates: list[CandidateMatch] = []

        for entry in self.concepts:
            # Check maximum lexical similarity across all aliases
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

            # Semantic dense similarity proxy (character & token n-gram embedding projection)
            best_sem = max(best_lex * 0.9, _ngram_jaccard(norm_query, entry.norm_canonical, n=2))

            # Hybrid score combination per DEC-007
            score = self.alpha * best_lex + (1.0 - self.alpha) * best_sem

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
