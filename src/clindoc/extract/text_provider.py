"""Rule-based clinical free-text extractor, an offline drop-in for the narrative LLM provider.

Authority: DEC-013 (schema-bound extraction), DEC-017 (field vocabulary); same JSON contract as
llm_extractor.LLM_OUTPUT_SCHEMA, so NarrativeExtractor validates every span as a verbatim substring.
Unlike MockLLMProvider (first match per field, test-only) it finds EVERY medication and lab value,
and tags each entity with a `group` so that dose / route / frequency stay linked to their drug and
lab values to their test name (DEC-015). No model, no network: runs the same on a laptop or a server.

Medication = a drug-like word directly followed by a dose ("Plavix 75 mg", "aspirin (81 mg)");
a dose is what separates drugs from procedures and conditions. Route and frequency are read from the
text after the dose, up to the next medication. Lab = a known analyte name, then its numeric value and unit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_DOSE_UNITS = r"mg/kg|mg/m2|mcg|µg|ug|mg|g|mL|ml|units?|IU|mEq|meq|cc"
# name (1-2 words) [ ( , : "dose of" "at" ] number unit
_MED = re.compile(
    rf"(?P<name>[A-Za-z][A-Za-z\-]{{2,}}(?:[ ][A-Za-z][A-Za-z\-]{{2,}})?)"
    rf"\s{{0,3}}[\(,:]?\s{{0,3}}(?:(?:dose|doses) (?:of )?|at |of |was |is |to )?"
    rf"(?P<dose>\d+(?:\.\d+)?)[ ]?(?P<unit>{_DOSE_UNITS})(?![/\w])")
_STOP = {
    "the", "and", "with", "was", "were", "for", "from", "that", "this", "than", "then", "dose", "doses", "daily",
    "dosage", "tablet", "tablets", "tab", "capsule", "capsules", "cap", "injection", "infusion", "solution", "drip",
    "total", "maximum", "minimum", "approximately", "about", "over", "under", "per", "every", "each", "only", "given",
    "started", "increased", "decreased", "reduced", "continued", "administered", "received", "taking", "takes", "take",
    "weight", "height", "weighs", "body", "blood", "pressure", "volume", "amount", "level", "levels", "approx",
    "intravenous", "intravenously", "oral", "orally", "topical", "twice", "once", "three", "four", "five", "six",
    "normal", "high", "low", "oxygen", "fluid", "fluids", "saline", "water", "loss", "gain", "bolus", "dosing",
    "mild", "moderate", "severe", "initial", "initially", "additional", "another", "following", "after", "before",
    "gram", "grams", "milligrams", "units", "medication", "medications", "plus", "minus", "less", "more", "greater",
    "age", "aged", "year", "years", "old", "month", "months", "week", "weeks", "day", "days", "hour", "hours", "min",
    "size", "diameter", "length", "mass", "lesion", "tumor", "tumour", "nodule", "cyst", "stone", "biopsy", "sample",
    "uptake", "radiation", "radiotherapy", "gamma", "beam", "scan", "section", "sections", "slice", "slices",
}
_CONNECT = {"and", "plus", "with", "or"}

_ROUTE = re.compile(
    r"\b(p\.?o\.?|by mouth|orally|oral|i\.?v\.?|intravenously|intravenous|i\.?m\.?|intramuscularly|intramuscular|"
    r"s\.?c\.?|subcutaneously|subcutaneous|sublingual|topically|topical|inhaled|inhalation|nebulized|rectal|transdermal)\b",
    re.IGNORECASE)
_FREQ = re.compile(
    r"\b(once (?:a day|daily|weekly)|twice (?:a day|daily|weekly)|(?:three|four) times (?:a day|daily)|every \d+(?:-\d+)? ?"
    r"(?:hours?|hrs?|h|days?|weeks?)|q\.?\s?\d+\s?h|q\.?\s?d\.?|b\.?\s?i\.?\s?d\.?|t\.?\s?i\.?\s?d\.?|q\.?\s?i\.?\s?d\.?|"
    r"q\.?\s?h\.?\s?s\.?|p\.?\s?r\.?\s?n\.?|as needed|at bedtime|at night|nightly|daily|weekly|monthly|"
    r"in the (?:morning|evening)|every (?:morning|evening|night|day))\b",
    re.IGNORECASE)

_LABS = {
    "hemoglobin": "Hemoglobin", "haemoglobin": "Hemoglobin", "hgb": "Hemoglobin", "hematocrit": "Hematocrit",
    "platelet count": "Platelet Count", "platelets": "Platelet Count", "white blood cell count": "WBC", "wbc": "WBC",
    "sodium": "Sodium", "potassium": "Potassium", "creatinine": "Creatinine", "glucose": "Glucose", "calcium": "Calcium",
    "albumin": "Albumin", "bilirubin": "Bilirubin", "alt": "ALT", "ast": "AST", "crp": "CRP", "c-reactive protein": "CRP",
    "esr": "ESR", "troponin": "Troponin", "hba1c": "HbA1c", "lactate": "Lactate", "ferritin": "Ferritin", "urea": "Urea",
    "bun": "BUN", "alkaline phosphatase": "Alkaline Phosphatase", "ldh": "LDH", "tsh": "TSH", "cholesterol": "Cholesterol",
    "total cholesterol": "Total Cholesterol", "triglycerides": "Triglycerides", "ldl": "LDL", "hdl": "HDL", "inr": "INR",
    "chloride": "Chloride", "bicarbonate": "Bicarbonate", "magnesium": "Magnesium", "phosphorus": "Phosphorus",
    "uric acid": "Uric Acid", "procalcitonin": "Procalcitonin", "d-dimer": "D-dimer", "amylase": "Amylase", "lipase": "Lipase",
}
_LAB = re.compile(
    r"\b(?P<lab>" + "|".join(sorted(map(re.escape, _LABS), key=len, reverse=True)) + r")\b"
    r"[\s:=(,]*(?:(?:levels?|count|concentration|was|were|is|of|at|serum|total|reading|result)\s+){0,3}[\s:=(]*"
    r"(?P<value>\d+(?:\.\d+)?)(?:\s{1,3}\d+(?:\.\d+)?\s?[-–]\s?\d+(?:\.\d+)?)?\s{0,3}(?P<unit>g/dL|g/L|mg/dL|mg/L|mmol/L|mEq/L|U/L|IU/L|ng/mL|pg/mL|µmol/L|umol/L|mIU/L|mm/h|"
    r"10\*\d+/[uµ]?L|x10\^?\d+/[uµ]?L|%)",
    re.IGNORECASE)
_VITALS = {"heart rate": "Heart rate", "pulse": "Heart rate", "respiratory rate": "Respiratory rate", "resp rate": "Respiratory rate",
           "temperature": "Body temperature", "temp": "Body temperature", "oxygen saturation": "Oxygen saturation",
           "spo2": "SpO2", "spo\u2082": "SpO2", "spoi": "SpO2"}  # "SpOI": PDF fonts render the 2 as I
_VITAL = re.compile(
    r"\b(?P<name>" + "|".join(sorted(map(re.escape, _VITALS), key=len, reverse=True)) + r")\b[\s:=]*"
    r"(?P<value>\d+(?:\.\d+)?)\s{0,3}(?P<unit>beats/min|bpm|breaths/min|/min|[\u00b0\ufffd]\s?[CF]|%|\s?mmHg)", re.IGNORECASE)
_BP = re.compile(r"\b(?P<name>blood pressure|BP)\b[\s:=]*(?P<sys>\d{2,3})\s?/\s?(?P<dia>\d{2,3})\s{0,3}(?P<unit>mmHg)", re.IGNORECASE)
_FLAG = re.compile(r"[\s(,\-]*\(?\b(HIGH|LOW|CRITICAL|ELEVATED|DECREASED|H|L)\b\)?", re.IGNORECASE)
_FLAG_MAP = {"HIGH": "HIGH", "ELEVATED": "HIGH", "H": "HIGH", "LOW": "LOW", "DECREASED": "LOW", "L": "LOW", "CRITICAL": "CRITICAL"}


def _load_lexicon() -> frozenset[str]:
    f = Path(__file__).parent / "data" / "drug_lexicon.txt"  # scripts/build_drug_lexicon.py
    return frozenset(f.read_text(encoding="utf-8").split()) if f.is_file() else frozenset()


_LEXICON = _load_lexicon()
_DRUG_SUFFIX = re.compile(r"(mab|nib|pril|sartan|olol|statin|azole|cillin|mycin|floxacin|oxacin|azepam|zolam|oxetine|dipine|"
                          r"thiazide|parin|prazole|tidine|vir|caine|sone|solone|cycline|platin|taxel|setron|triptan|gliptin|"
                          r"gliflozin|glutide|barbital|phylline|terol|tropium|navir|limus|rubicin|zepine|pamil|semide|"
                          r"formin|glitazone|dronate|fibrate|ciclib|degib|parib|tinib|zomib|metacin|profen|codone|morphone)$", re.I)
_LEX_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _LEXICON), key=len, reverse=True)) + r")\b", re.IGNORECASE) if _LEXICON else None
_DOSE_AFTER = re.compile(rf"\s{{0,3}}[\(,:]?\s{{0,3}}(?:(?:dose|doses) (?:of )?|at |of )?(?P<dose>\d+(?:\.\d+)?)[ ]?(?P<unit>{_DOSE_UNITS})(?![/\w])")


_NOT_DRUG = frozenset({"april", "nasal", "pharmacy"})
_SUFFIX_WORD = re.compile(r"\b[A-Za-z][A-Za-z\-]{3,}\b")


def _is_drug(word: str) -> bool:
    w = word.lower()
    return w not in _NOT_DRUG and (w in _LEXICON or bool(_DRUG_SUFFIX.search(w)))


def _clean_name(raw: str) -> str | None:
    words = raw.split()
    while words and words[0].lower() in _STOP | _CONNECT:
        words.pop(0)
    while words and words[-1].lower() in _STOP | _CONNECT:
        words.pop()
    if not words or len(words) > 2 or all(w.lower() in _STOP for w in words):
        return None
    name = " ".join(words)
    return name if len(name) >= 3 and not name.isupper() or (name.isupper() and 4 <= len(name) <= 12) else None


def extract_text(text: str) -> list[dict]:
    """All medication and lab extractions in `text`, each with a verbatim evidence_text and a `group` key."""
    out: list[dict] = []
    mentions: list[tuple[int, int, str, re.Match | None]] = []  # (start, end, surface, dose match)
    taken: set[tuple[int, int]] = set()
    for m in _MED.finditer(text):  # dose-anchored: "<drug> 75 mg"
        name = _clean_name(m.group("name"))
        toks = [t for t in (name or "").split() if _is_drug(t) and t.lower() not in _LABS]
        if not toks:
            continue
        pos = text.lower().find(toks[-1].lower(), m.start("name"), m.end("name"))
        mentions.append((pos, pos + len(toks[-1]), text[pos:pos + len(toks[-1])], m))
        taken.add((pos, pos + len(toks[-1])))
    for m in (x for x in _SUFFIX_WORD.finditer(text) if _DRUG_SUFFIX.search(x.group(0))):  # drug-like word endings, only when a dose follows (e.g. a table cell)
        d = _DOSE_AFTER.match(text, m.end())
        if d and (m.start(), m.end()) not in taken and m.group(0).lower() not in _LEXICON | _NOT_DRUG:
            mentions.append((m.start(), m.end(), m.group(0), d))
            taken.add((m.start(), m.end()))
    if _LEX_RE is not None:  # lexicon-only mentions, with an optional dose right after
        for m in _LEX_RE.finditer(text):
            if (m.start(), m.end()) in taken or m.group(0).lower() in _LABS:
                continue
            d = _DOSE_AFTER.match(text, m.end())
            mentions.append((m.start(), m.end(), m.group(0), d))
    mentions.sort(key=lambda t: t[0])
    seen: set[tuple[str, str]] = set()
    dosed = {n.lower() for _, _, n, d in mentions if d}
    meds = []
    for st, en, name, d in mentions:
        key = (name.lower(), d.group("dose") + d.group("unit") if d else "")
        if key in seen or (d is None and name.lower() in dosed):
            continue
        seen.add(key)
        meds.append((st, en, name, d))
    for k, (st, en, name, d) in enumerate(meds):
        g = f"med{k}"
        out.append({"field_type": "med.name", "value": name, "evidence_text": name, "unit_raw": None,
                    "confidence": 0.85 if d else 0.7, "group": g})
        tail_from = en
        if d:
            dose_ev = text[d.start("dose"):d.end("unit")]
            out.append({"field_type": "med.dose", "value": dose_ev, "evidence_text": dose_ev, "unit_raw": d.group("unit"),
                        "confidence": 0.9, "group": g})
            tail_from = d.end()
        nxt = meds[k + 1][0] if k + 1 < len(meds) else len(text)
        tail = text[tail_from:min(nxt, tail_from + 110)]
        for pat, ft in ((_ROUTE, "med.route"), (_FREQ, "med.frequency")):
            hit = pat.search(tail)
            if hit:
                out.append({"field_type": ft, "value": hit.group(0), "evidence_text": hit.group(0), "unit_raw": None,
                            "confidence": 0.85, "group": g})
    for k, m in enumerate(_LAB.finditer(text)):
        if m.group("unit") == "%" and _LABS[m.group("lab").lower()] not in ("Hematocrit", "HbA1c"):
            continue  # "sodium hypochlorite 5%" and similar
        g = f"lab{k}"
        test = text[m.start("lab"):m.end("lab")]
        out.append({"field_type": "lab.test_name", "value": _LABS[test.lower()], "evidence_text": test, "unit_raw": None,
                    "confidence": 0.9, "group": g})
        val_ev = text[m.start("value"):m.end("unit")] if text[m.end("value"):m.start("unit")].strip() == "" \
            else text[m.start("value"):m.end("value")]  # a reference range sits between value and unit in table layouts
        out.append({"field_type": "lab.value", "value": m.group("value"), "evidence_text": val_ev, "unit_raw": m.group("unit"),
                    "confidence": 0.9, "group": g})
        flag = _FLAG.match(text, m.end())
        if flag:
            out.append({"field_type": "lab.flag", "value": _FLAG_MAP[flag.group(1).upper()], "evidence_text": flag.group(1),
                        "unit_raw": None, "confidence": 0.85, "group": g})
    k = 0
    for m in _BP.finditer(text):
        ev = text[m.start("sys"):m.end("unit")]
        for label, part in (("Systolic blood pressure", "sys"), ("Diastolic blood pressure", "dia")):
            g, k = f"vit{k}", k + 1
            out.append({"field_type": "lab.test_name", "value": label, "evidence_text": text[m.start("name"):m.end("name")],
                        "unit_raw": None, "confidence": 0.9, "group": g})
            out.append({"field_type": "lab.value", "value": m.group(part), "evidence_text": ev, "unit_raw": m.group("unit"),
                        "confidence": 0.9, "group": g})
    for m in _VITAL.finditer(text):
        g, k = f"vit{k}", k + 1
        out.append({"field_type": "lab.test_name", "value": _VITALS[m.group("name").lower()],
                    "evidence_text": text[m.start("name"):m.end("name")], "unit_raw": None, "confidence": 0.9, "group": g})
        out.append({"field_type": "lab.value", "value": m.group("value"), "evidence_text": text[m.start("value"):m.end("unit")],
                    "unit_raw": m.group("unit").strip(), "confidence": 0.9, "group": g})
    return out


def layout_text(page, start: int, end: int) -> str:
    """The page's words re-read row by row (top to bottom, left to right) from their positions.

    Born-digital PDFs can store table cells in column or block order ("20 mg capsule ... Omeprazole"), which
    separates a drug from its dose. Words keep their original text, so spans found here are verbatim in the page text.
    """
    toks = [t for t, s0, e0 in page.get_token_offsets() if start <= s0 < end]
    if not toks:
        return ""
    heights = sorted(t.bbox.y1 - t.bbox.y0 for t in toks)
    tol = max(heights[len(heights) // 2] * 0.6, 1e-6)
    rows: list[list] = []
    for t in sorted(toks, key=lambda t: (t.bbox.y0 + t.bbox.y1) / 2):
        yc = (t.bbox.y0 + t.bbox.y1) / 2
        if rows and abs(yc - rows[-1][0]) <= tol:
            rows[-1][1].append(t)
        else:
            rows.append([yc, [t]])
    return "\n".join(" ".join(t.text for t in sorted(r[1], key=lambda t: t.bbox.x0)) for r in rows)


def best_reading(page, region_text: str, char_offset: int) -> str | None:
    """The reading of a region that yields more extractions: its stored text, or rows rebuilt from word positions.

    Tables are stored cell by cell in either order depending on the PDF, so neither reading is right for all of them.
    """
    rows = layout_text(page, char_offset, char_offset + len(region_text))
    return rows if rows and len(extract_text(rows)) > len(extract_text(region_text)) else None


class ClinicalTextProvider:
    """LLMProvider-compatible: parses the clinical text out of the extraction prompt and returns the JSON contract."""

    recovers_unparsed_regions = True  # router.py: use me when the template parsers find nothing in a region

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        m = re.search(r"Extract clinical information from the following clinical text:\s*(.+?)Output JSON:", user_prompt, re.DOTALL)
        text = m.group(1).strip("\n") if m else user_prompt
        return json.dumps({"extractions": extract_text(text)})


if __name__ == "__main__":  # self-check on a realistic MTSamples-style snippet
    t = ("MEDICATIONS: Plavix 75 mg p.o. q.d., metoprolol 25 mg po BID, Flomax 0.4 mg daily. "
         "Labs: hemoglobin 9.2 g/dL (L), creatinine 1.8 mg/dL, glucose 142 mg/dL HIGH. Tumor measured 3 mm in diameter.")
    ex = extract_text(t)
    names = [e["value"] for e in ex if e["field_type"] == "med.name"]
    assert names == ["Plavix", "metoprolol", "Flomax"], names
    assert {e["field_type"] for e in ex} >= {"med.dose", "med.route", "med.frequency", "lab.value", "lab.flag", "lab.test_name"}
    assert all(e["evidence_text"] in t for e in ex)
    assert [e["value"] for e in ex if e["field_type"] == "lab.flag"] == ["LOW", "HIGH"]
    print(len(ex), "extractions ok")
