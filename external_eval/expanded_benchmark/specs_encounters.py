"""Expanded Benchmark - Combined Encounter Specs (44 documents).

Authority: User Request / Expanded Synthetic Benchmark
Location: external_eval/expanded_benchmark/specs_encounters.py
"""
from __future__ import annotations

from external_eval.expanded_benchmark.specs_encounters_part1 import ENCOUNTERS_PART1
from external_eval.expanded_benchmark.specs_encounters_part2 import ENCOUNTERS_PART2
from external_eval.expanded_benchmark.specs_encounters_part3 import ENCOUNTERS_PART3

ENCOUNTER_SPECS = ENCOUNTERS_PART1 + ENCOUNTERS_PART2 + ENCOUNTERS_PART3
