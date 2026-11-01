"""Born-digital PDF text and layout extraction using PyMuPDF."""

from collections import defaultdict
from pathlib import Path
import time
import pymupdf

from clindoc.ocr.schema import DocumentOCRRecord, LineBox, WordBox


def extract_born_digital_pdf(pdf_path: str | Path, doc_id: str | None = None) -> DocumentOCRRecord:
    """Extract text, line bounding boxes, and word tokens from born-digital PDF."""
    start_time = time.perf_counter()
    pdf_path = Path(pdf_path)
    if doc_id is None:
        doc_id = pdf_path.stem

    doc = pymupdf.open(str(pdf_path))
    num_pages = len(doc)

    all_lines: list[LineBox] = []
    all_words: list[WordBox] = []

    for page_idx in range(num_pages):
        page_num = page_idx + 1
        page = doc[page_idx]

        # Extract words: (x0, y0, x1, y1, "word", block_no, line_no, word_no)
        raw_words = page.get_text("words")

        # Group words by (block_no, line_no)
        lines_dict: dict[tuple[int, int], list[WordBox]] = defaultdict(list)

        for w in raw_words:
            x0, y0, x1, y1, word_text, b_no, l_no, _ = w
            if not word_text.strip():
                continue
            word_box = WordBox(
                text=word_text.strip(),
                bbox=[round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                confidence=1.0,
                page=page_num,
            )
            all_words.append(word_box)
            lines_dict[(b_no, l_no)].append(word_box)

        page_lines: list[LineBox] = []
        for (b_no, l_no), words in lines_dict.items():
            words_sorted = sorted(words, key=lambda w: w.bbox[0])
            line_text = " ".join(w.text for w in words_sorted)
            line_x0 = min(w.bbox[0] for w in words_sorted)
            line_y0 = min(w.bbox[1] for w in words_sorted)
            line_x1 = max(w.bbox[2] for w in words_sorted)
            line_y1 = max(w.bbox[3] for w in words_sorted)

            page_lines.append(
                LineBox(
                    text=line_text,
                    bbox=[round(line_x0, 2), round(line_y0, 2), round(line_x1, 2), round(line_y1, 2)],
                    confidence=1.0,
                    page=page_num,
                    words=words_sorted,
                )
            )

        # Apply robust 2D layout analysis to sort page lines into clinical reading order
        from clindoc.ocr.layout import sort_page_lines_reading_order
        p_width = float(page.rect.width) if hasattr(page, "rect") else 612.0
        p_height = float(page.rect.height) if hasattr(page, "rect") else 792.0
        page_lines_ordered = sort_page_lines_reading_order(page_lines, page_width=p_width, page_height=p_height)
        all_lines.extend(page_lines_ordered)

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return DocumentOCRRecord(
        doc_id=doc_id,
        source_path=str(pdf_path),
        engine="pymupdf_digital",
        device="cpu",
        num_pages=num_pages,
        lines=all_lines,
        words=all_words,
        metadata={"processing_time_ms": elapsed_ms},
    )
