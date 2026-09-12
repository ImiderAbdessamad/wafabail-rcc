"""Adaptateur Docling local et conversion prudente des tables en candidats RCC."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import DOCLING_MAX_PAGES
from app.schemas.direct_financial_extraction import (
    DirectFinancialCandidate,
    DirectFinancialEvidence,
)
from app.services.direct_financial_resolver import dedupe_direct_candidates
from app.services.financial_normalizer import parse_decimal_amount

logger = logging.getLogger(__name__)

# Évite torch.compile / cl.exe sous Windows. Doit être posé avant l'import Docling.
os.environ.setdefault("DOCLING_INFERENCE_COMPILE_TORCH_MODELS", "false")


@dataclass(frozen=True)
class DoclingDocumentResult:
    status: Literal["success", "not_installed", "disabled", "error"]
    markdown_by_page: dict[int, str] = field(default_factory=dict)
    latency_ms: int | None = None
    tables_count: int = 0
    source_mode: str = "original_pdf"
    error: str | None = None


def docling_available() -> bool:
    return importlib.util.find_spec("docling") is not None


_CONVERTERS: dict[bool, Any] = {}


def _build_converter(force_full_page_ocr: bool) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        OcrMode,
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    ocr_options = RapidOcrOptions(
        lang=["fr"],
        backend="onnxruntime",
        mode=OcrMode.FULL_PAGE if force_full_page_ocr else OcrMode.DEFAULT,
        scale=3.0,
    )
    options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        ocr_options=ocr_options,
    )
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    options.table_structure_options.do_cell_matching = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def convert_pdf_with_docling(
    pdf_bytes: bytes,
    *,
    filename: str,
    page_count: int,
    force_full_page_ocr: bool,
    source_mode: str,
) -> DoclingDocumentResult:
    if not docling_available():
        return DoclingDocumentResult(
            status="not_installed",
            source_mode=source_mode,
            error="Installer les dépendances de requirements-ocr.txt pour activer Docling.",
        )
    started = time.perf_counter()
    try:
        from docling.datamodel.document import DocumentStream

        converter = _CONVERTERS.setdefault(
            force_full_page_ocr,
            _build_converter(force_full_page_ocr),
        )
        max_pages = min(page_count, DOCLING_MAX_PAGES)
        stream = DocumentStream(name=filename, stream=io.BytesIO(pdf_bytes))
        converted = converter.convert(
            stream,
            page_range=(1, max_pages),
        )
        markdown_by_page = {
            page_number: converted.document.export_to_markdown(
                page_no=page_number,
                page_break_placeholder=None,
            )
            for page_number in range(1, max_pages + 1)
        }
        return DoclingDocumentResult(
            status="success",
            markdown_by_page=markdown_by_page,
            latency_ms=round((time.perf_counter() - started) * 1000),
            tables_count=len(getattr(converted.document, "tables", []) or []),
            source_mode=source_mode,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Docling a échoué sur %s", filename)
        return DoclingDocumentResult(
            status="error",
            latency_ms=round((time.perf_counter() - started) * 1000),
            source_mode=source_mode,
            error=f"{type(exc).__name__}: {exc}",
        )


async def convert_pdf_with_docling_async(**kwargs: Any) -> DoclingDocumentResult:
    return await asyncio.to_thread(convert_pdf_with_docling, **kwargs)


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9'\s+\-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _cells(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [cell.strip() for cell in stripped.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(not cell or set(cell) <= {"-", ":", " "} for cell in cells)


def _is_amount(value: str) -> bool:
    return parse_decimal_amount(value) is not None


def _field_for_label(label: str, page_type: str) -> tuple[str | None, str]:
    text = _fold(label)
    is_total = "total" in text
    nature = "GRAND_TOTAL" if "total general" in text else "SECTION_TOTAL" if is_total else "DETAIL"

    if page_type == "BILAN_ACTIF":
        if "total general" in text:
            return "TOTAL_ACTIF", nature
        if re.search(r"\btotal\s+i\b", text) and "ii" not in text and "iii" not in text:
            return "ACTIFS_IMMOBILISES", nature
        if re.search(r"\btotal\s+ii\b", text) and "iii" not in text:
            return "ACTIF_CIRCULANT", nature
        if re.search(r"\btotal\s+iii\b", text) or "tresorerie actif" in text:
            return "TRESORERIE_ACTIF", nature
        if "stocks" in text and (is_total or text.endswith("stocks")):
            return "STOCKS", nature
        if "clients et comptes rattaches" in text:
            return "CLIENTS", nature
        if "caisses" in text or text == "caisse":
            return "CAISSE", nature

    if page_type == "BILAN_PASSIF":
        if "total general" in text:
            return "TOTAL_PASSIF", nature
        if "total des capitaux propres" in text:
            return "FONDS_PROPRES", nature
        if "fournisseurs et comptes rattaches" in text:
            return "FOURNISSEURS", nature
        if (
            "comptes d associes" in text
            or "comptes d'associes" in text
            or "compte courant associes" in text
        ):
            return "COMPTE_COURANT_ASSOCIES", nature
        if "dettes de financement" in text and (is_total or text.endswith(" c")):
            return "DETTES_FINANCIERES", nature
        if text.count("dettes du passif circulant") >= 2 or (
            "passif circulant" in text and is_total
        ):
            return "PASSIF_CIRCULANT", "SECTION_TOTAL"
        if re.search(r"\btotal\s+iii\b", text) or "tresorerie passif" in text:
            return "TRESORERIE_PASSIF", nature
        if "resultat net de l exercice" in text:
            return "RESULTAT_NET", nature

    if page_type in {"CPC", "DETAIL_CPC"}:
        if "chiffre d affaires" in text or "ventes de biens et services produits" in text:
            return "CHIFFRE_AFFAIRES", nature
        if "achats revendus" in text:
            return "ACHATS_REVENDUS", nature
        if "achats consommes" in text:
            return "ACHATS_CONSOMMES", nature
        if "autres charges externes" in text:
            return "AUTRES_CHARGES_EXTERNES", nature
        if "charges d interets" in text or "interets des emprunts" in text:
            return "CHARGES_INTERETS", nature
        if "resultat net" in text:
            return "RESULTAT_NET_XVI", nature
        if "resultat d exploitation" in text:
            return "RESULTAT_EXPLOITATION", nature
        if "charges financieres" in text and is_total:
            return "CHARGES_FINANCIERES", nature

    return None, nature


def _column_roles(headers: list[str], page_type: str) -> list[tuple[str, str]]:
    """Renvoie (période, rôle) pour chaque colonne du tableau."""
    roles: list[tuple[str, str]] = []
    for header in headers:
        folded = _fold(header)
        if "precedent" in folded or "n-1" in folded or "n 1" in folded:
            roles.append(("N_MINUS_1", "EXERCICE_N1"))
        elif "amort" in folded or "provision" in folded:
            roles.append(("N", "AMORT_PROV"))
        elif "brut" in folded:
            roles.append(("N", "BRUT"))
        elif "net" in folded and page_type == "BILAN_ACTIF":
            roles.append(("N", "NET_N"))
        elif "exercice" in folded or "montant" in folded or "net" in folded:
            roles.append(("N", "EXERCICE_N"))
        else:
            roles.append(("N", "UNKNOWN"))
    return roles


def _is_financial_header(cells: list[str], page_type: str) -> bool:
    """Require explicit period/value semantics before trusting column position."""
    roles = _column_roles(cells, page_type)
    value_roles = [role for _period, role in roles if role != "UNKNOWN"]
    if not value_roles:
        return False
    if page_type == "BILAN_ACTIF":
        return "NET_N" in value_roles or "EXERCICE_N1" in value_roles
    return any(role in {"EXERCICE_N", "EXERCICE_N1"} for role in value_roles)


def candidates_from_docling_markdown(
    markdown_by_page: dict[int, str],
    *,
    page_types: dict[int, str],
    orientation_by_page: dict[int, int] | None = None,
    scanned: bool = False,
) -> list[DirectFinancialCandidate]:
    """Transforme seulement les lignes/colonnes non ambiguës en observations.

    Les sorties Docling restent des candidats concurrents. Elles ne remplacent
    jamais silencieusement GLM et sont arbitrées ensuite par le résolveur et les
    contrôles comptables.
    """
    orientation_by_page = orientation_by_page or {}
    confidence = 0.68 if scanned else 0.86
    candidates: list[DirectFinancialCandidate] = []

    for page_number, markdown in sorted(markdown_by_page.items()):
        page_type = page_types.get(page_number, "AUTRE")
        if page_type not in {"BILAN_ACTIF", "BILAN_PASSIF", "CPC", "DETAIL_CPC"}:
            continue
        lines = markdown.splitlines()
        headers: list[str] = []
        roles: list[tuple[str, str]] = []
        for line in lines:
            if not line.strip().startswith("|"):
                headers = []
                roles = []
                continue
            cells = _cells(line)
            if not cells or _is_separator(cells):
                continue
            if not headers and _is_financial_header(cells, page_type):
                headers = cells
                roles = _column_roles(headers, page_type)
                continue
            if not headers:
                continue

            # Period assignment by column position is safe only when the data
            # row and the verified header have identical geometry.
            if len(cells) != len(headers):
                continue
            value_indexes = [
                index
                for index, (_period, role) in enumerate(roles)
                if role != "UNKNOWN"
            ]
            label_cells = [
                cell
                for index, cell in enumerate(cells)
                if index not in value_indexes and cell
            ]
            label = " ".join(dict.fromkeys(label_cells)).strip()
            if not label:
                continue
            field_code, nature = _field_for_label(label, page_type)
            if field_code is None:
                continue

            amount_indexes = [index for index in value_indexes if _is_amount(cells[index])]
            for index in amount_indexes:
                period, role = roles[index]
                # Actif : Brut / amortissements ne sont pas des valeurs finales RCC.
                if page_type == "BILAN_ACTIF" and role in {"BRUT", "AMORT_PROV"}:
                    continue
                raw_value = cells[index]
                try:
                    candidates.append(
                        DirectFinancialCandidate(
                            field_code=field_code,
                            raw_value=raw_value,
                            period=period,  # type: ignore[arg-type]
                            nature=nature,  # type: ignore[arg-type]
                            confidence=confidence,
                            evidence=DirectFinancialEvidence(
                                page_number=page_number,
                                page_type=page_type,  # type: ignore[arg-type]
                                raw_label=label[:180],
                                column_name=headers[index][:100] if index < len(headers) else None,
                                column_role=role,  # type: ignore[arg-type]
                                source_excerpt=line[:240],
                                orientation=orientation_by_page.get(page_number, 0),  # type: ignore[arg-type]
                                extraction_method="docling_table",
                                engine="docling+tableformer",
                            ),
                            warnings=[
                                "Observation locale Docling; géométrie ligne/colonnes vérifiée, "
                                "puis valeur soumise au consensus et aux contrôles."
                            ],
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Ligne Docling ignorée page=%d: %s", page_number, exc)
    return dedupe_direct_candidates(candidates)
