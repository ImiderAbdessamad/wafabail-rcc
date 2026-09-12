"""Diagnostic local du prétraitement RCC, sans appel GLM ni envoi réseau.

Usage:
    python scripts/diagnose_hybrid_pipeline.py path/to/document.pdf --max-pages 4
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.direct_financial_extraction_pipeline import render_pdf_pages_png
from app.services.financial_document_router import (
    classify_document_source,
    prepare_financial_page,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--dpi", type=int, default=180)
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    if not pdf_path.exists():
        parser.error(f"PDF introuvable: {pdf_path}")
    pages = render_pdf_pages_png(
        pdf_path.read_bytes(),
        dpi=args.dpi,
        max_pages=args.max_pages,
    )
    source_kind = classify_document_source([page.native_text for page in pages])
    rows = []
    for page in pages:
        prepared = prepare_financial_page(
            page_number=page.page_number,
            image_bytes=page.image_bytes,
            native_text=page.native_text,
            source_kind=source_kind,
        )
        rows.append(
            {
                "page": page.page_number,
                "source_kind": source_kind,
                "route": prepared.route,
                "native_chars": len(page.native_text.strip()),
                "local_ocr_chars": len(prepared.local_ocr_text.strip()),
                "local_ocr_confidence": prepared.local_ocr_confidence,
                "local_ocr_status": prepared.local_ocr_status,
                "orientation": prepared.orientation.selected_angle,
                "orientation_method": prepared.orientation.method,
                "orientation_confidence": prepared.orientation.confidence,
                "orientation_scores": prepared.orientation.scores,
                "inverted": prepared.visual.inverted,
                "contrast": prepared.visual.contrast,
                "sharpness": prepared.visual.sharpness,
                "extraction_image_bytes": len(prepared.extraction_image),
                "warnings": list(prepared.warnings),
            }
        )
    print(json.dumps({"document": str(pdf_path), "pages": rows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
