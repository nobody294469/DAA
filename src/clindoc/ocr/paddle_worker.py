"""Standalone PaddleOCR worker executed inside the isolated .venv_ocr environment."""

import argparse
import json
from pathlib import Path
import time

import fitz  # PyMuPDF
from paddleocr import PaddleOCR


def run_paddleocr(input_path: str, output_json: str, dpi: int = 150) -> dict:
    """Run PaddleOCR on PDF or image file and output DocumentOCRRecord JSON."""
    start_time = time.perf_counter()
    input_p = Path(input_path)
    doc_id = input_p.stem

    ocr = PaddleOCR(use_angle_cls=False, lang="en", use_gpu=False, show_log=False)
    
    scale = 72.0 / dpi  # Scale pixel coords back to PDF point coords
    lines_output = []
    words_output = []
    
    if input_p.suffix.lower() == ".pdf":
        pdf_doc = fitz.open(str(input_p))
        num_pages = len(pdf_doc)
        
        for page_idx in range(num_pages):
            page_num = page_idx + 1
            page = pdf_doc[page_idx]
            pix = page.get_pixmap(dpi=dpi)
            
            # Save temporary image
            tmp_img = input_p.parent / f"_tmp_{doc_id}_p{page_num}.png"
            pix.save(str(tmp_img))
            
            try:
                ocr_res = ocr.ocr(str(tmp_img), cls=False)
            finally:
                if tmp_img.exists():
                    tmp_img.unlink()
                    
            if ocr_res and len(ocr_res) > 0 and ocr_res[0]:
                for item in ocr_res[0]:
                    box, (text, conf) = item
                    # box is [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
                    xs = [pt[0] for pt in box]
                    ys = [pt[1] for pt in box]
                    x0 = round(min(xs) * scale, 2)
                    y0 = round(min(ys) * scale, 2)
                    x1 = round(max(xs) * scale, 2)
                    y1 = round(max(ys) * scale, 2)
                    conf_f = round(float(conf), 4)
                    
                    # Synthesize word boxes from line text
                    words_in_line = text.split()
                    w_boxes = []
                    if words_in_line:
                        w_width = (x1 - x0) / max(len(words_in_line), 1)
                        for w_idx, w_text in enumerate(words_in_line):
                            wx0 = round(x0 + w_idx * w_width, 2)
                            wx1 = round(wx0 + w_width, 2)
                            w_box = {
                                "text": w_text,
                                "bbox": [wx0, y0, wx1, y1],
                                "confidence": conf_f,
                                "page": page_num,
                            }
                            w_boxes.append(w_box)
                            words_output.append(w_box)
                            
                    lines_output.append({
                        "text": text,
                        "bbox": [x0, y0, x1, y1],
                        "confidence": conf_f,
                        "page": page_num,
                        "words": w_boxes,
                    })
    else:
        # Single image
        num_pages = 1
        ocr_res = ocr.ocr(str(input_p), cls=False)
        if ocr_res and len(ocr_res) > 0 and ocr_res[0]:
            for item in ocr_res[0]:
                box, (text, conf) = item
                xs = [pt[0] for pt in box]
                ys = [pt[1] for pt in box]
                x0 = round(min(xs), 2)
                y0 = round(min(ys), 2)
                x1 = round(max(xs), 2)
                y1 = round(max(ys), 2)
                conf_f = round(float(conf), 4)
                
                words_in_line = text.split()
                w_boxes = []
                if words_in_line:
                    w_width = (x1 - x0) / max(len(words_in_line), 1)
                    for w_idx, w_text in enumerate(words_in_line):
                        wx0 = round(x0 + w_idx * w_width, 2)
                        wx1 = round(wx0 + w_width, 2)
                        w_box = {
                            "text": w_text,
                            "bbox": [wx0, y0, wx1, y1],
                            "confidence": conf_f,
                            "page": 1,
                        }
                        w_boxes.append(w_box)
                        words_output.append(w_box)
                        
                lines_output.append({
                    "text": text,
                    "bbox": [x0, y0, x1, y1],
                    "confidence": conf_f,
                    "page": 1,
                    "words": w_boxes,
                })

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)

    doc_record = {
        "doc_id": doc_id,
        "source_path": str(input_p),
        "engine": "paddleocr",
        "device": "cpu",
        "num_pages": num_pages,
        "lines": lines_output,
        "words": words_output,
        "metadata": {
            "processing_time_ms": elapsed_ms,
            "dpi": dpi,
            "scale_factor": scale if input_p.suffix.lower() == ".pdf" else 1.0,
        },
    }

    out_p = Path(output_json)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(doc_record, indent=2), encoding="utf-8")
    return doc_record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PaddleOCR on document")
    parser.add_argument("--input", required=True, help="Input PDF or image path")
    parser.add_argument("--output", required=True, help="Output JSON path")
    parser.add_argument("--dpi", type=int, default=150, help="Rendering DPI for PDF pages")
    args = parser.parse_args()

    run_paddleocr(args.input, args.output, args.dpi)
