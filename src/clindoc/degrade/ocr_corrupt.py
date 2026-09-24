"""Track B: Deterministic OCR-level text corruption simulating optical recognition errors."""

import random
from typing import Any

from clindoc.ocr.schema import DocumentOCRRecord, LineBox, WordBox

OPTICAL_CONFUSIONS: dict[str, list[str]] = {
    "O": ["0", "Q"],
    "0": ["O", "D"],
    "o": ["0"],
    "l": ["1", "I", "|"],
    "1": ["l", "I"],
    "I": ["l", "1"],
    "S": ["5"],
    "5": ["S"],
    "B": ["8"],
    "8": ["B"],
    "Z": ["2"],
    "2": ["Z"],
    "rn": ["m"],
    "m": ["rn"],
    "cl": ["d"],
    "vv": ["w"],
}


def corrupt_text_string(text: str, error_rate: float, rng: random.Random) -> str:
    """Apply realistic OCR recognition substitutions, digit swaps, and punctuation drops."""
    if not text or error_rate <= 0.0:
        return text

    # 1. Check for multi-char confusions first ('rn' -> 'm', 'cl' -> 'd', etc.)
    result = text
    for multi, replacements in [("rn", ["m"]), ("m", ["rn"]), ("cl", ["d"]), ("vv", ["w"])]:
        if multi in result and rng.random() < (error_rate * 1.5):
            rep = rng.choice(replacements)
            result = result.replace(multi, rep, 1)

    # 2. Check for decimal point drop (e.g., '12.5' -> '125', '0.5' -> '5')
    if "." in result and rng.random() < error_rate:
        # Avoid dropping if it looks like an initial or sentence end
        import re
        m = re.search(r"(\d)\.(\d)", result)
        if m:
            result = result[:m.start() + 1] + result[m.start() + 2:]

    # 3. Character-by-character stochastic corruptions
    chars = list(result)
    for i, ch in enumerate(chars):
        if rng.random() < error_rate:
            action = rng.choice(["substitute", "drop", "insert"])
            if action == "substitute":
                if ch in OPTICAL_CONFUSIONS:
                    chars[i] = rng.choice(OPTICAL_CONFUSIONS[ch])
                elif ch.isdigit():
                    chars[i] = rng.choice(["0", "1", "3", "5", "8", "9"])
                elif ch.isalpha():
                    # Random visual case switch or small typo
                    chars[i] = ch.lower() if ch.isupper() else ch.upper()
                elif ch in [":", "-", "/", ","]:
                    # Punctuation dropped or substituted
                    chars[i] = " " if rng.random() < 0.5 else "-"
            elif action == "drop":
                chars[i] = ""
            elif action == "insert":
                chars[i] = ch + rng.choice(["'", ".", " ", "1"])

    return "".join(chars)


def corrupt_ocr_record(
    clean_record: DocumentOCRRecord,
    error_rate: float,
    seed: int = 42,
    degraded_doc_id: str | None = None,
) -> DocumentOCRRecord:
    """Apply Track B OCR character corruption to a DocumentOCRRecord deterministically."""
    rng = random.Random(seed)
    new_doc_id = degraded_doc_id or f"{clean_record.doc_id}_corrupt_{int(error_rate*100)}"

    corrupted_lines: list[LineBox] = []
    corrupted_words: list[WordBox] = []

    for line in clean_record.lines:
        new_line_text = corrupt_text_string(line.text, error_rate=error_rate, rng=rng)
        
        # Scale confidence down slightly when errors occur to simulate real OCR behavior
        char_diff = sum(1 for a, b in zip(line.text, new_line_text) if a != b) + abs(len(line.text) - len(new_line_text))
        conf_penalty = min(0.40, char_diff * 0.05)
        new_conf = round(max(0.40, line.confidence - conf_penalty), 4)

        corrupted_word_boxes: list[WordBox] = []
        if line.words:
            for w in line.words:
                new_w_text = corrupt_text_string(w.text, error_rate=error_rate, rng=rng)
                w_conf = round(max(0.40, w.confidence - conf_penalty), 4)
                w_box = WordBox(
                    text=new_w_text,
                    bbox=w.bbox,
                    confidence=w_conf,
                    page=w.page,
                )
                corrupted_word_boxes.append(w_box)
                corrupted_words.append(w_box)
        else:
            # Reconstruct word boxes from line text if line.words was empty
            words = new_line_text.split()
            x0, y0, x1, y1 = line.bbox
            w_span = (x1 - x0) / max(len(words), 1)
            for idx, wt in enumerate(words):
                w_box = WordBox(
                    text=wt,
                    bbox=[round(x0 + idx * w_span, 2), y0, round(x0 + (idx + 1) * w_span, 2), y1],
                    confidence=new_conf,
                    page=line.page,
                )
                corrupted_word_boxes.append(w_box)
                corrupted_words.append(w_box)

        corrupted_line = LineBox(
            text=new_line_text,
            bbox=line.bbox,
            confidence=new_conf,
            page=line.page,
            words=corrupted_word_boxes,
        )
        corrupted_lines.append(corrupted_line)

    metadata = dict(clean_record.metadata)
    metadata.update({
        "track": "ocr_text",
        "degradation_type": "ocr_character_corruption",
        "error_rate": error_rate,
        "seed": seed,
    })

    return DocumentOCRRecord(
        doc_id=new_doc_id,
        source_path=clean_record.source_path,
        engine=f"corrupted_{clean_record.engine}",
        device=clean_record.device,
        num_pages=clean_record.num_pages,
        lines=corrupted_lines,
        words=corrupted_words,
        metadata=metadata,
    )
