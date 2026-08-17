"""Exécute le pipeline RCC sur un PDF local, sans passer par l'API.

    py -3 scripts/run_extraction.py chemin/vers/liasse.pdf [--max-pages 6]

Affiche les 20 postes RCC, les contrôles comptables et les avertissements —
utile pour valider une évolution du moteur avant de la brancher sur l'API.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.services.rcc_lab_pipeline import analyze_rcc_document  # noqa: E402


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", " ")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Extraction RCC sur un PDF local")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--max-pages", type=int, default=None)
    parser.add_argument("--json", type=Path, help="écrit le résultat complet en JSON")
    parser.add_argument("--quiet", action="store_true", help="masque la progression")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.pdf.exists():
        print(f"PDF introuvable : {args.pdf}")
        return 2

    def emit(event: str, data: dict) -> None:
        if args.quiet:
            return
        page = data.get("page")
        prefix = f"[page {page}] " if page else ""
        print(f"  {prefix}{event} {data.get('page_type', '') or ''}".rstrip())

    result = await analyze_rcc_document(
        args.pdf.read_bytes(), args.pdf.name, max_pages=args.max_pages, emit=emit
    )

    print("\n=== Document ===")
    doc = result.document
    print(f"{doc.filename} — {doc.pages_total} pages "
          f"({doc.pages_processed} traitées, {doc.pages_skipped} ignorées, {doc.pages_failed} en échec)")
    print(f"Société : {doc.company.raison_sociale or '—'} · ICE {doc.company.ice or '—'}")
    print(f"Exercice : {doc.exercise.label or '—'}")

    print("\n=== Postes RCC ===")
    for field in result.fields:
        value = field.note if field.code == "TYPE_RESULTAT" else _fmt(field.value)
        print(
            f"{field.number:>2}. {field.label:<32} {str(value or '—'):>20}  "
            f"{field.status:<12} conf={field.confidence:.2f}  {field.note or ''}"
        )
    print(f"\nComplétude : {result.completeness_pct} %")

    print("\n=== Contrôles ===")
    for control in result.controls:
        print(f"[{control.status:<6}] {control.label} — {control.message}")

    if result.warnings:
        print("\n=== Avertissements ===")
        for warning in result.warnings:
            print(f"- {warning}")

    if args.json:
        args.json.write_text(
            json.dumps(result.model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\nRésultat complet écrit dans {args.json}")

    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(main()))
