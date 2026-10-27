"""build_drug_lexicon.py: data-driven drug-name list for extract/text_provider.py.

A word is kept when, across the open note corpora (data/open/), it is repeatedly the name directly before a
dose ("Plavix 75 mg") AND that is a real share of its occurrences, which drops verbs and nouns
("obtained", "morning") that only occasionally precede a number. Output: src/clindoc/extract/data/drug_lexicon.txt.

Run: python scripts/build_drug_lexicon.py [--max-asclepius 40000]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from clindoc.extract.text_provider import _LABS, _MED, _STOP, _clean_name  # noqa: E402

WORD = re.compile(r"[a-z][a-z\-]{3,}")
BAD_END = ("ing", "ed", "tion", "ment", "ness", "ly", "ous", "ality", "ally", "ity")


def notes(max_asc: int):
    csv.field_size_limit(1 << 30)
    with open(REPO / "data/open/augmented-clinical-notes/augmented_notes_30K.jsonl", encoding="utf-8") as fh:
        for line in fh:
            yield json.loads(line)["note"]
    seen = set()
    with open(REPO / "data/open/asclepius/synthetic.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            if len(seen) >= max_asc:
                break
            if row["note"] not in seen:
                seen.add(row["note"])
                yield row["note"]
    with open(REPO / "data/open/mtsamples/mtsamples.csv", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            yield row["transcription"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-asclepius", type=int, default=40000)
    args = ap.parse_args()
    df_all, df_dose, n = collections.Counter(), collections.Counter(), 0
    for text in notes(args.max_asclepius):
        n += 1
        df_all.update(set(WORD.findall(text.lower())))
        tokens = set()
        for m in _MED.finditer(text):
            name = _clean_name(m.group("name"))
            if name:
                tokens.update(t for t in WORD.findall(name.lower()))
        df_dose.update(tokens)
    keep = sorted(t for t, c in df_dose.items()
                  if c >= 3 and c / df_all[t] >= 0.08 and t not in _STOP and t not in _LABS and not t.endswith(BAD_END))
    out = REPO / "src/clindoc/extract/data/drug_lexicon.txt"
    out.write_text("\n".join(keep) + "\n", encoding="utf-8")
    print(f"{n} notes -> {len(keep)} drug words -> {out}")
    print(keep[:60])


if __name__ == "__main__":
    main()
