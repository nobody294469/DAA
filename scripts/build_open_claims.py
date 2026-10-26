"""build_open_claims.py — Stage B claim/evidence examples from open, real-text clinical corpora.

Sources (downloaded under data/open/, used for academic research):
  augmented  AGBonnet/augmented-clinical-notes (MIT; 30K notes written from PubMed Central case reports)
  asclepius  starmpcc/Asclepius-Synthetic-Clinical-Notes (CC BY-NC-SA 4.0; 158K synthetic discharge summaries)
  mtsamples  harishnair04/mtsamples (~5K transcribed medical reports, research use)

Facts are mined from the note text itself so every SUPPORTED claim is verbatim in its evidence window:
  med.name / med.dose / med.frequency  a drug name with a dose right after it in the note (that drops
                                       procedures), plus that dose/frequency. Drug names come from the note's
                                       structured summary (augmented) or from a lexicon of the dosed drug names
                                       in those summaries (asclepius, mtsamples).
  lab.value                            "<lab> ... <number> <unit>" mentions of common analytes
DISCREPANCY = the same evidence with the value perturbed (or a drug name absent from the note);
INSUFFICIENT_EVIDENCE = a drug absent from the note with the V3 document-level absence evidence.
Splits are by note (no leakage). Output: data/m2_open/<source>_{train,validation,test}.jsonl (M2Example rows).

Run: python scripts/build_open_claims.py [--source augmented asclepius mtsamples] [--max-notes N]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import uuid
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OPEN = REPO_ROOT / "data/open"
AUGMENTED = OPEN / "augmented-clinical-notes/augmented_notes_30K.jsonl"
SOURCES = {  # name -> (doc id prefix, doc_template, default max notes)
    "augmented": ("OPEN", "open_pmc_case_report_v1", None),
    "asclepius": ("ASCL", "open_asclepius_discharge_v1", 40000),
    "mtsamples": ("MTS", "open_mtsamples_transcription_v1", None),
}
OUT = REPO_ROOT / "data/m2_open"
WINDOW, LEAD = 500, 200  # V3 evidence windows are <= 500 chars

DOSE_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(mg/kg|mg|mcg|µg|g|mL|ml|units|IU)\b")
FREQ_RE = re.compile(r"\b(once daily|twice daily|three times daily|four times daily|once a day|twice a day|"
                     r"three times a day|every \d+ hours|once weekly|daily|weekly|nightly|at bedtime|"
                     r"bid|tid|qid|qd|q\d+h)\b", re.I)
FREQS = ["once daily", "twice daily", "three times daily", "every 8 hours", "every 12 hours", "weekly",
         "at bedtime", "BID", "TID", "QD"]
LABS = {"hemoglobin": "Hemoglobin", "haemoglobin": "Hemoglobin", "hematocrit": "Hematocrit",
        "platelet count": "Platelet Count", "platelets": "Platelet Count", "white blood cell count": "WBC",
        "wbc": "WBC", "sodium": "Sodium", "potassium": "Potassium", "creatinine": "Creatinine",
        "glucose": "Glucose", "calcium": "Calcium", "albumin": "Albumin", "bilirubin": "Bilirubin",
        "alt": "ALT", "ast": "AST", "crp": "CRP", "c-reactive protein": "CRP", "esr": "ESR",
        "troponin": "Troponin", "hba1c": "HbA1c", "lactate": "Lactate", "ferritin": "Ferritin",
        "urea": "Urea", "alkaline phosphatase": "Alkaline Phosphatase", "ldh": "LDH", "tsh": "TSH"}
LAB_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, LABS), key=len, reverse=True)) + r")\b"
                    r"[\s:=(,]*(?:(?:levels?|count|concentration|was|were|is|of|at|serum)\s+){0,3}[\s:=(]*"
                    r"(\d+(?:\.\d+)?)\s?(g/dL|mg/dL|mmol/L|mEq/L|U/L|IU/L|%|mg/L|ng/mL|g/L|"
                    r"µmol/L|umol/L|pg/mL|mm/h|mIU/L|µIU/mL)", re.I)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
# Summary "treatment" names that are not drugs; kept out of the lexicon for the non-augmented sources.
NOT_DRUG = re.compile(r"[()]|\b(medications?|therapy|treatment|drugs?|agents?|antibiotics|supplements?|fluids?|"
                      r"surgery|procedure|transfusion|plasty|ectomy|otomy|oxygen)\b|plasty$|ectomy$|otomy$", re.I)
DOSE_GAP = re.compile(r"[\s:(]*(?:\w+\s+){0,2}?$")  # at most two words between a lexicon drug and its dose
ABSENT = "Document-level absence: Target entity '{}' not observed in clinical narrative."


def window(note: str, pos: int) -> tuple[str, int, int]:
    start = max(0, pos - LEAD)
    end = min(len(note), start + WINDOW)
    return note[start:end], start, end


def perturb(num: str, rng: random.Random) -> str:
    """A clearly different value in the same format (decimals kept)."""
    v = float(num)
    dec = len(num.split(".")[1]) if "." in num else 0
    for _ in range(10):
        f = rng.choice([2.0, 0.5, 10.0, 0.1, 1.5, 0.6, 3.0])
        out = f"{v * f:.{dec}f}" if dec else str(max(1, round(v * f)))
        if out != num and float(out) > 0:
            return out
    return f"{v + 7:.{dec}f}" if dec else str(int(v) + 7)


def summary_drugs(note: str, summary: dict) -> list[tuple[str, int, int]]:
    """(name, start, end) of the summary's treatment names, first occurrence in the note."""
    out, low = [], note.lower()
    for t in summary.get("treatments") or []:
        name = (t.get("name") or "").strip() if isinstance(t, dict) else ""
        name = re.sub(r"\s+(tablets?|capsules?|injections?|infusion)$", "", name, flags=re.I)
        if not (3 <= len(name) <= 40) or name.lower() == "none" or re.search(r",| and |/", name):
            continue
        m = re.search(r"\b" + re.escape(name.lower()) + r"\b", low)
        if m:
            out.append((name, m.start(), m.end()))
    return out


def lexicon_drugs(note: str, lexicon: dict[str, str]) -> list[tuple[str, int, int]]:
    """(canonical name, start, end) of lexicon drugs (1-3 words) followed by a dose; first per name."""
    words = list(WORD_RE.finditer(note))
    out, seen = [], set()
    for i in range(len(words)):
        for n in (3, 2, 1):
            if i + n > len(words):
                continue
            key = " ".join(w.group(0).lower() for w in words[i:i + n])
            name = lexicon.get(key)
            d = DOSE_RE.search(note, words[i + n - 1].end(), words[i + n - 1].end() + 60) if name else None
            # the dose must follow the name directly ("Plavix 75 mg"), not a later drug ("X and Marcaine 1.7 mL")
            gap = note[words[i + n - 1].end(): d.start()] if d else ""
            if d and name not in seen and DOSE_GAP.fullmatch(gap) and not re.search(r",| and |;", gap):
                seen.add(name)
                out.append((name, words[i].start(), words[i + n - 1].end()))
                break
    return out


def note_facts(note: str, drugs: list[tuple[str, int, int]]) -> list[tuple[str, str, str, str, int]]:
    """(field_type, claim, perturbed_claim, contradiction_type, char_pos) facts verbatim in the note."""
    rng = random.Random(len(note))
    facts = []
    for name, start, end in drugs:
        d = DOSE_RE.search(note[end: end + 60])
        if not d:  # a dose right after the name is what separates drugs from procedures in "treatments"
            continue
        facts.append(("med.name", f"Medication: {name}", "", "", start))
        facts.append(("med.dose", f"{name} dose: {d.group(1)} {d.group(2)}",
                      f"{name} dose: {perturb(d.group(1), rng)} {d.group(2)}", "dose_mismatch", start))
        f = FREQ_RE.search(note[end: end + 90])
        if f:
            other = rng.choice([x for x in FREQS if x.lower() != f.group(1).lower()])
            facts.append(("med.frequency", f"{name} frequency: {f.group(1)}", f"{name} frequency: {other}",
                          "temporal_mismatch", start))
    for m in LAB_RE.finditer(note):
        lab = LABS[m.group(1).lower()]
        if m.group(3) == "%" and lab not in ("Hematocrit", "HbA1c"):  # "sodium hypochlorite 5%" etc.
            continue
        facts.append(("lab.value", f"{lab}: {m.group(2)} {m.group(3)}",
                      f"{lab}: {perturb(m.group(2), rng)} {m.group(3)}", "value_mismatch", m.start()))
    return facts


def load_augmented() -> list[tuple[str, str, dict]]:
    notes = []
    with AUGMENTED.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            try:
                summary = json.loads(row["summary"])
            except (json.JSONDecodeError, TypeError):
                continue
            notes.append((row["idx"], row["note"], summary))
    return notes


def load_csv(path: Path, id_col: str, text_col: str) -> list[tuple[str, str]]:
    """Unique (id, text) rows; Asclepius repeats each note once per question."""
    csv.field_size_limit(1 << 30)
    out, seen = [], set()
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            text = (row.get(text_col) or "").strip()
            if len(text) > 200 and text not in seen:
                seen.add(text)
                out.append((row.get(id_col) or str(len(out)), text))
    return out


def drug_pool(augmented: list[tuple[str, str, dict]]) -> list[str]:
    """Dosed drug names from the augmented summaries: absent-drug claims and the lexicon for other sources."""
    return sorted({(t.get("name") or "").strip() for _, _, s in augmented for t in (s.get("treatments") or [])
                   if isinstance(t, dict) and DOSE_RE.search(str(t.get("dosage")))
                   and 3 <= len((t.get("name") or "").strip()) <= 30 and len(t["name"].split()) <= 3
                   and not re.search(r",| and |/", t["name"])})


def build(source: str, max_notes: int | None, pool: list[str], augmented, seed: int = 42) -> dict[str, list[dict]]:
    rng = random.Random(seed)
    prefix, template, default_max = SOURCES[source]
    if source == "augmented":
        notes = list(augmented)
    else:
        lexicon = {n.lower(): n for n in pool if (not n.isupper() or len(n) > 3) and not NOT_DRUG.search(n)
                   and n.lower() not in LABS and n.lower() != "infusion"}
        path, id_col, text_col = {"asclepius": (OPEN / "asclepius/synthetic.csv", "patient_id", "note"),
                                  "mtsamples": (OPEN / "mtsamples/mtsamples.csv", "", "transcription")}[source]
        notes = [(i, text, None) for i, text in load_csv(path, id_col, text_col)]
    rng.shuffle(notes)
    limit = max_notes or default_max
    notes = notes[:limit] if limit else notes

    out = {"train": [], "validation": [], "test": []}
    for i, (idx, note, summary) in enumerate(notes):
        split = "test" if i % 10 == 0 else "validation" if i % 10 == 1 else "train"
        doc_id = f"{prefix}_{idx}"
        facts = note_facts(note, summary_drugs(note, summary) if summary is not None else lexicon_drugs(note, lexicon))
        rng.shuffle(facts)
        rows = []

        def add(field, claim, evidence, label, method, ctype=None, span=(None, None), group=None):
            rows.append({"example_id": str(uuid.UUID(int=rng.getrandbits(128))), "claim": claim,
                         "evidence": evidence, "field_type": field, "doc_template": template,
                         "ocr_confidence": 1.0, "severity": "clean", "label": label, "source_doc_id": doc_id,
                         "generation_method": method, "claim_type": field.split(".")[0], "split": split,
                         "group_id": group, "contradiction_type": ctype, "evidence_id": str(uuid.uuid4()),
                         "char_start": span[0], "char_end": span[1], "page_number": 1, "bbox": None})

        for k, (field, claim, bad, ctype, pos) in enumerate(facts[:4]):  # <= 4 facts per note keeps variety
            ev, s, e = window(note, pos)
            group = f"{doc_id}_{k}"
            add(field, claim, ev, "SUPPORTED", "open_verbatim_supported", None, (s, e), group)
            if field == "med.name":
                absent = [d for d in rng.sample(pool, 20) if d.lower() not in note.lower()]
                if absent:
                    add(field, f"Medication: {absent[0]}", ev, "DISCREPANCY", "open_wrong_entity_discrepancy",
                        "wrong_entity", (s, e), group)
            else:
                add(field, bad, ev, "DISCREPANCY", "open_perturbed_discrepancy", ctype, (s, e), group)
        if facts and rng.random() < 0.3:
            absent = next((d for d in rng.sample(pool, 20) if d.lower() not in note.lower()), None)
            if absent:
                field = rng.choice(["med.name", "med.dose", "med.frequency"])
                claim = {"med.name": f"Medication: {absent}", "med.dose": f"{absent} dose: {rng.choice([5, 10, 20, 50, 100, 250, 500])} mg",
                         "med.frequency": f"{absent} frequency: {rng.choice(FREQS)}"}[field]
                add(field, claim, ABSENT.format(absent), "INSUFFICIENT_EVIDENCE",
                    "open_unobserved_claim_insufficient_evidence")
        out[split] += rows
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--source", nargs="+", choices=list(SOURCES), default=list(SOURCES))
    p.add_argument("--max-notes", type=int, default=None, help="per source (asclepius defaults to 40000)")
    args = p.parse_args()
    augmented = load_augmented()
    pool = drug_pool(augmented)
    OUT.mkdir(parents=True, exist_ok=True)
    for source in args.source:
        for split, rows in build(source, args.max_notes, pool, augmented).items():
            with (OUT / f"{source}_{split}.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
                fh.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
            c = Counter(r["label"] for r in rows)
            print(f"{source} {split}: {len(rows)} examples from {len({r['source_doc_id'] for r in rows})} notes "
                  f"{dict(sorted(c.items()))}")


if __name__ == "__main__":
    main()
