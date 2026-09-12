"""Diagnostic Docling local, sans Ollama ni envoi réseau."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.direct_financial_extraction_pipeline import render_pdf_pages_png
from app.services.docling_financial_adapter import (
    candidates_from_docling_markdown,
    convert_pdf_with_docling,
)
from app.services.financial_document_router import (
    classify_document_source,
    normalized_pages_to_pdf,
    prepare_financial_page,
)
from app.services.financial_page_classifier import classify_from_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--max-pages", type=int, default=4)
    args = parser.parse_args()

    pdf_path = args.pdf.resolve()
    pdf_bytes = pdf_path.read_bytes()
    pages = render_pdf_pages_png(pdf_bytes, dpi=180, max_pages=args.max_pages)
    source_kind = classify_document_source([page.native_text for page in pages])
    prepared = [
        prepare_financial_page(
            page_number=page.page_number,
            image_bytes=page.image_bytes,
            native_text=page.native_text,
            source_kind=source_kind,
        )
        for page in pages
    ]
    scanned = source_kind == "image_only"
    input_pdf = normalized_pages_to_pdf(prepared) if scanned else pdf_bytes
    result = convert_pdf_with_docling(
        input_pdf,
        filename=pdf_path.name,
        page_count=len(pages),
        force_full_page_ocr=scanned,
        source_mode="normalized_scan_pdf" if scanned else "original_pdf",
    )
    page_types = {
        page.page_number: classify_from_text(
            "\n".join(
                value
                for value in (
                    page.native_text,
                    prepared[index].local_ocr_text,
                    result.markdown_by_page.get(page.page_number, ""),
                )
                if value
            )
        )
        or "AUTRE"
        for index, page in enumerate(pages)
    }
    candidates = candidates_from_docling_markdown(
        result.markdown_by_page,
        page_types=page_types,
        orientation_by_page={item.page_number: item.orientation.selected_angle for item in prepared},
        scanned=scanned,
    )
    print(
        json.dumps(
            {
                "document": str(pdf_path),
                "source_kind": source_kind,
                "status": result.status,
                "latency_ms": result.latency_ms,
                "tables_count": result.tables_count,
                "source_mode": result.source_mode,
                "error": result.error,
                "page_types": page_types,
                "markdown_chars_by_page": {
                    page: len(markdown) for page, markdown in result.markdown_by_page.items()
                },
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
