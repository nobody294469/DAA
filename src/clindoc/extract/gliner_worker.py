"""Worker script running inside .venv_ner for GLiNER-BioMed entity extraction."""

import argparse
import json
from pathlib import Path
import sys
import time

from gliner import GLiNER

LABELS = [
    "medication",
    "dosage",
    "dose unit",
    "dosage form",
    "route",
    "frequency",
    "duration",
]


def main():
    parser = argparse.ArgumentParser(description="GLiNER-BioMed NER Worker")
    parser.add_argument("--input", type=str, required=True, help="Path to input JSON with list of texts")
    parser.add_argument("--output", type=str, required=True, help="Path to output JSON")
    parser.add_argument("--model-name", type=str, default="Ihor/gliner-biomed-base-v1.0", help="Hugging Face model ID")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    texts = data.get("texts", [])

    t0 = time.time()
    model = GLiNER.from_pretrained(args.model_name)
    load_time = time.time() - t0

    t1 = time.time()
    all_results = []
    for text in texts:
        entities = model.predict_entities(text, LABELS, threshold=0.3)
        formatted = [
            {
                "label": ent["label"],
                "text": ent["text"],
                "start": ent["start"],
                "end": ent["end"],
                "score": float(ent["score"]),
            }
            for ent in entities
        ]
        all_results.append(formatted)

    infer_time = time.time() - t1

    output = {
        "model_name": args.model_name,
        "load_time_seconds": round(load_time, 3),
        "inference_time_seconds": round(infer_time, 3),
        "total_texts": len(texts),
        "results": all_results,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)


if __name__ == "__main__":
    main()
