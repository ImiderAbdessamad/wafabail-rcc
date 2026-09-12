"""Deterministic, visually verified PDF report for an RCC dossier.

The renderer uses explicit page coordinates instead of HTML pagination.  This
keeps long financial labels, repeated table headers, and page breaks stable
across PyMuPDF releases.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import fitz

from app.schemas.dossier import DossierDetail, FieldOverride
from app.services.rcc_compliance import ComplianceReport


DARK = (0.10, 0.08, 0.07)
ORANGE = (0.91, 0.36, 0.05)
ORANGE_DARK = (0.73, 0.26, 0.03)
INK = (0.10, 0.09, 0.08)
MUTED = (0.39, 0.43, 0.50)
LINE = (0.86, 0.88, 0.91)
SURFACE = (0.96, 0.97, 0.98)
WARN_BG = (1.00, 0.95, 0.92)
OK_BG = (0.93, 0.98, 0.95)
WHITE = (1.0, 1.0, 1.0)


def _clean(value: Any) -> str:
    return (
        str(value if value is not None else "-")
        .replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u2264", "<=")
        .replace("\u2248", "~")
        .replace("\u00b7", "|")
    )


def _number(value: Any, *, suffix: str = "") -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _clean(value)
    if abs(number - round(number)) < 1e-9:
        rendered = f"{number:,.0f}"
    else:
        rendered = f"{number:,.2f}".rstrip("0").rstrip(".")
    return rendered.replace(",", " ") + suffix


def _effective_value(value: Any, override: FieldOverride | None) -> Any:
    if override is not None and override.corrected_value is not None:
        return override.corrected_value
    return value


class _ReportCanvas:
    width = fitz.paper_rect("a4").width
    height = fitz.paper_rect("a4").height
    left = 34.0
    right = width - 34.0
    bottom = height - 42.0

    def __init__(self, dossier_id: str) -> None:
        self.document = fitz.open()
        self.dossier_id = dossier_id
        self.page: fitz.Page
        self.y = 0.0
        self._new_page(first=True)

    @staticmethod
    def _font(bold: bool) -> str:
        return "hebo" if bold else "helv"

    def _new_page(self, *, first: bool = False) -> None:
        self.page = self.document.new_page(width=self.width, height=self.height)
        if first:
            self.y = 0
            return
        self.page.draw_rect(fitz.Rect(0, 0, self.width, 28), fill=DARK, color=DARK)
        self.page.draw_rect(fitz.Rect(0, 28, self.width, 31), fill=ORANGE, color=ORANGE)
        self.page.insert_textbox(
            fitz.Rect(self.left, 7, self.right, 22),
            f"WAFABAIL RCC | {self.dossier_id} | RAPPORT D'EXTRACTION",
            fontsize=7.3,
            fontname="hebo",
            color=WHITE,
        )
        self.y = 43

    def ensure(self, height: float) -> bool:
        if self.y + height <= self.bottom:
            return False
        self._new_page()
        return True

    def _split_long_token(self, token: str, width: float, size: float, bold: bool) -> list[str]:
        fontname = self._font(bold)
        parts: list[str] = []
        current = ""
        for char in token:
            candidate = current + char
            if current and fitz.get_text_length(candidate, fontname=fontname, fontsize=size) > width:
                parts.append(current)
                current = char
            else:
                current = candidate
        if current:
            parts.append(current)
        return parts or [""]

    def wrap(self, value: Any, width: float, size: float, *, bold: bool = False) -> list[str]:
        text = _clean(value).strip() or "-"
        fontname = self._font(bold)
        lines: list[str] = []
        for paragraph in text.splitlines() or [text]:
            words: list[str] = []
            for token in paragraph.split() or [""]:
                if fitz.get_text_length(token, fontname=fontname, fontsize=size) <= width:
                    words.append(token)
                else:
                    words.extend(self._split_long_token(token, width, size, bold))
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if current and fitz.get_text_length(candidate, fontname=fontname, fontsize=size) > width:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            lines.append(current or "-")
        return lines

    def text_box(
        self,
        rect: fitz.Rect,
        text: Any,
        *,
        size: float = 8,
        bold: bool = False,
        color: tuple[float, float, float] = INK,
        align: int = fitz.TEXT_ALIGN_LEFT,
    ) -> None:
        self.page.insert_textbox(
            rect,
            _clean(text),
            fontsize=size,
            fontname=self._font(bold),
            color=color,
            align=align,
            lineheight=1.12,
        )

    def section(self, title: str, note: str | None = None, *, min_follow: float = 0) -> None:
        block_height = 31 if note else 22
        self.ensure(block_height + min_follow)
        self.text_box(
            fitz.Rect(self.left, self.y, self.right, self.y + 18),
            title,
            size=12,
            bold=True,
        )
        self.y += 19
        if note:
            self.text_box(
                fitz.Rect(self.left, self.y, self.right, self.y + 14),
                note,
                size=7.2,
                color=MUTED,
            )
            self.y += 14

    def notice(self, text: str, *, ok: bool = False) -> None:
        lines = self.wrap(text, self.right - self.left - 24, 8)
        height = max(30, len(lines) * 10 + 14)
        self.ensure(height + 5)
        bg = OK_BG if ok else WARN_BG
        accent = (0.08, 0.48, 0.30) if ok else ORANGE_DARK
        self.page.draw_rect(fitz.Rect(self.left, self.y, self.right, self.y + height), fill=bg, color=bg)
        self.page.draw_rect(fitz.Rect(self.left, self.y, self.left + 3, self.y + height), fill=accent, color=accent)
        self.text_box(
            fitz.Rect(self.left + 12, self.y + 7, self.right - 7, self.y + height - 4),
            "\n".join(lines),
            size=8,
        )
        self.y += height + 6

    def table(
        self,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
        widths: Sequence[float],
        *,
        aligns: Sequence[int] | None = None,
        size: float = 7.1,
        row_min: float = 20,
        wrap_margin: float = 5,
    ) -> None:
        total_width = self.right - self.left
        pixel_widths = [total_width * fraction for fraction in widths]
        header_height = 23.0
        line_height = size * 1.28
        padding_x = 5.0
        padding_y = 4.0

        def draw_header() -> None:
            self.ensure(header_height + row_min)
            x = self.left
            for index, header in enumerate(headers):
                cell_width = pixel_widths[index]
                rect = fitz.Rect(x, self.y, x + cell_width, self.y + header_height)
                self.page.draw_rect(rect, fill=DARK, color=WHITE, width=0.35)
                self.text_box(
                    fitz.Rect(x + padding_x, self.y + 5, x + cell_width - padding_x, self.y + header_height - 2),
                    header,
                    size=7,
                    bold=True,
                    color=WHITE,
                    align=(aligns[index] if aligns else fitz.TEXT_ALIGN_LEFT),
                )
                x += cell_width
            self.y += header_height

        draw_header()
        for row_index, row in enumerate(rows):
            cell_lines = [
                # Keep a small safety margin because MuPDF's textbox wrapping
                # is slightly more conservative than get_text_length() for
                # accented French labels.
                self.wrap(value, pixel_widths[index] - 2 * padding_x - wrap_margin, size)
                for index, value in enumerate(row)
            ]
            row_height = max(row_min, max(len(lines) for lines in cell_lines) * line_height + 2 * padding_y)
            if self.y + row_height > self.bottom:
                self._new_page()
                draw_header()
            fill = SURFACE if row_index % 2 else WHITE
            x = self.left
            for index, lines in enumerate(cell_lines):
                cell_width = pixel_widths[index]
                rect = fitz.Rect(x, self.y, x + cell_width, self.y + row_height)
                self.page.draw_rect(rect, fill=fill, color=LINE, width=0.35)
                self.text_box(
                    fitz.Rect(x + padding_x, self.y + padding_y, x + cell_width - padding_x, self.y + row_height - 2),
                    "\n".join(lines),
                    size=size,
                    align=(aligns[index] if aligns else fitz.TEXT_ALIGN_LEFT),
                )
                x += cell_width
            self.y += row_height
        self.y += 8

    def finish(self) -> bytes:
        page_count = self.document.page_count
        for page_number, page in enumerate(self.document, start=1):
            y = self.height - 27
            page.draw_line((self.left, y - 6), (self.right, y - 6), color=LINE, width=0.55)
            page.insert_textbox(
                fitz.Rect(self.left, y, self.right - 110, y + 16),
                "Wafabail RCC - rapport interne",
                fontsize=6.8,
                fontname="helv",
                color=MUTED,
            )
            page.insert_textbox(
                fitz.Rect(self.right - 100, y, self.right, y + 16),
                f"Page {page_number} / {page_count}",
                fontsize=6.8,
                fontname="helv",
                color=MUTED,
                align=fitz.TEXT_ALIGN_RIGHT,
            )
        payload = self.document.tobytes(garbage=4, deflate=True)
        self.document.close()
        return payload


def build_rcc_pdf(detail: DossierDetail, compliance: ComplianceReport) -> bytes:
    """Build the current RCC state as a polished, paginated PDF report."""

    canvas = _ReportCanvas(detail.id)
    result = detail.result
    overrides = {item.field_code: item for item in detail.overrides}
    status_text = "Prêt à valider" if compliance.can_validate else "Revue requise"
    exported_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

    # First-page identity band.
    canvas.page.draw_rect(fitz.Rect(0, 0, canvas.width, 94), fill=DARK, color=DARK)
    canvas.page.draw_rect(fitz.Rect(0, 91, canvas.width, 95), fill=ORANGE, color=ORANGE)
    canvas.text_box(
        fitz.Rect(canvas.left, 18, canvas.right, 33),
        f"WAFABAIL - VALIDATION RCC | {status_text.upper()}",
        size=7.2,
        bold=True,
        color=(0.96, 0.57, 0.30),
    )
    canvas.text_box(
        fitz.Rect(canvas.left, 35, canvas.right, 69),
        "Rapport d'extraction financière",
        size=19,
        bold=True,
        color=WHITE,
    )
    canvas.text_box(
        fitz.Rect(canvas.left, 72, canvas.right, 88),
        f"Dossier {detail.id} - exporté le {exported_at}",
        size=7.8,
        color=(0.84, 0.82, 0.79),
    )
    canvas.y = 111

    canvas.text_box(
        fitz.Rect(canvas.left, canvas.y, canvas.right, canvas.y + 31),
        detail.client_name or "Client à identifier",
        size=17,
        bold=True,
    )
    canvas.y += 36

    meta = [
        ("ICE", detail.ice or "Non détecté"),
        ("Exercice", detail.exercice_date or "Non détecté"),
        ("Crédit demandé", _number(detail.credit_amount, suffix=" MAD")),
        ("Statut", detail.status_label),
    ]
    card_gap = 7
    card_width = (canvas.right - canvas.left - card_gap * 3) / 4
    for index, (label, value) in enumerate(meta):
        x = canvas.left + index * (card_width + card_gap)
        canvas.page.draw_rect(fitz.Rect(x, canvas.y, x + card_width, canvas.y + 42), fill=SURFACE, color=LINE, width=0.5)
        canvas.page.draw_rect(fitz.Rect(x, canvas.y, x + 2.5, canvas.y + 42), fill=ORANGE, color=ORANGE)
        canvas.text_box(fitz.Rect(x + 9, canvas.y + 6, x + card_width - 5, canvas.y + 17), label.upper(), size=6.2, bold=True, color=MUTED)
        canvas.text_box(fitz.Rect(x + 9, canvas.y + 20, x + card_width - 5, canvas.y + 37), value, size=8.5)
    canvas.y += 52

    pages_total = result.document.pages_total if result else 0
    pages_processed = result.document.pages_processed if result else 0
    metrics = [
        ("Complétude RCC", _number(detail.completeness_pct, suffix=" %")),
        ("Conformité", f"{compliance.pct} %"),
        ("Bloquants", str(compliance.blockers)),
        ("Pages traitées", f"{pages_processed} / {pages_total}"),
    ]
    for index, (label, value) in enumerate(metrics):
        x = canvas.left + index * (card_width + card_gap)
        canvas.page.draw_rect(fitz.Rect(x, canvas.y, x + card_width, canvas.y + 49), fill=(0.95, 0.96, 0.97), color=LINE, width=0.5)
        canvas.text_box(fitz.Rect(x + 9, canvas.y + 7, x + card_width - 5, canvas.y + 18), label.upper(), size=6.2, bold=True, color=MUTED)
        canvas.text_box(fitz.Rect(x + 9, canvas.y + 23, x + card_width - 5, canvas.y + 43), value, size=14, bold=True, color=ORANGE_DARK)
    canvas.y += 58
    canvas.notice(f"{status_text}. {compliance.summary}", ok=compliance.can_validate)

    field_rows: list[list[Any]] = []
    if result:
        review_labels = {"verified": "Vérifié", "corrected": "Corrigé"}
        for field in result.fields:
            override = overrides.get(field.code)
            review = review_labels.get("verified" if override and override.verified else "corrected" if override else "", "OCR")
            evidence = field.evidence[0] if field.evidence else None
            field_rows.append(
                [
                    field.number,
                    field.label,
                    _number(_effective_value(field.value, override)),
                    _number(field.value_n1),
                    _number(field.confidence * 100, suffix=" %"),
                    review,
                    f"p. {evidence.page_number}" if evidence and evidence.page_number else "-",
                ]
            )
    canvas.section(
        "Postes RCC - valeurs effectives",
        "Les corrections et confirmations analyste sont superposées à l'OCR sans modifier la source.",
    )
    canvas.table(
        ["N°", "Poste RCC", "Exercice N", "N-1", "Confiance", "Revue", "Source"],
        field_rows or [["-", "Aucune donnée RCC", "-", "-", "-", "-", "-"]],
        [0.05, 0.37, 0.14, 0.12, 0.10, 0.12, 0.10],
        aligns=[fitz.TEXT_ALIGN_LEFT, fitz.TEXT_ALIGN_LEFT, fitz.TEXT_ALIGN_RIGHT, fitz.TEXT_ALIGN_RIGHT, fitz.TEXT_ALIGN_RIGHT, fitz.TEXT_ALIGN_LEFT, fitz.TEXT_ALIGN_LEFT],
        size=7.0,
    )

    canvas.section("Contrôles comptables", min_follow=48)
    control_rows = []
    if result:
        for control in result.controls:
            verdict = {"passed": "Conforme", "failed": "Écart", "not_testable": "Non testable"}.get(control.status, control.status)
            control_rows.append([control.label, verdict, control.message or "-"])
    canvas.table(
        ["Contrôle", "Verdict", "Observation"],
        control_rows or [["Aucun contrôle disponible", "Non testable", "-"]],
        [0.35, 0.16, 0.49],
        size=7.2,
    )

    canvas.section("Conformité RCC", min_follow=48)
    compliance_rows = [
        [
            section.title,
            rule.label,
            "Conforme" if rule.ok else "Bloquant" if rule.blocking else "Avertissement",
            rule.detail,
        ]
        for section in compliance.sections
        for rule in section.rules
    ]
    canvas.table(
        ["Section", "Règle", "État", "Détail"],
        compliance_rows,
        [0.20, 0.37, 0.14, 0.29],
        size=6.8,
        row_min=21,
        wrap_margin=10,
    )

    scoring = result.scoring if result else None
    if scoring:
        canvas.section("Ratios de scoring financier", min_follow=72)
        canvas.notice(
            "Score final non calculé. La politique de pondération reste non approuvée; les ratios sont fournis à titre analytique et auditable.",
        )
        ratio_rows = [
            [
                ratio.label,
                _number(ratio.value, suffix=f" {ratio.unit}" if ratio.unit else ""),
                ratio.status.replace("_", " "),
                ratio.formula,
            ]
            for ratio in scoring.ratios
        ]
        canvas.table(
            ["Ratio", "Valeur", "Statut", "Formule"],
            ratio_rows or [["Aucun ratio calculable", "-", "non calculable", "-"]],
            [0.27, 0.16, 0.17, 0.40],
            size=6.8,
        )

    canvas.section("Audit du pipeline d'extraction", min_follow=72)
    source = result.extraction.source_kind if result else None
    engines = ", ".join(result.extraction.engines) if result else ""
    canvas.notice(f"Source: {source or 'non renseignée'} | Moteurs: {engines or 'non renseignés'}", ok=True)
    audit_rows = []
    if result:
        for audit in result.extraction.page_audit:
            audit_rows.append(
                [
                    audit.page_number,
                    audit.detected_type,
                    audit.source_kind or "-",
                    audit.route or audit.extraction_strategy,
                    f"{audit.orientation}°",
                    audit.local_ocr_status or "not_run",
                    audit.docling_status or "not_run",
                ]
            )
    canvas.table(
        ["Page", "Type", "Source", "Route", "Orientation", "OCR local", "Docling"],
        audit_rows or [["-", "-", "-", "-", "-", "non exécuté", "non exécuté"]],
        [0.07, 0.18, 0.13, 0.25, 0.11, 0.13, 0.13],
        size=6.5,
    )

    warnings = list(dict.fromkeys((result.warnings + result.extraction.warnings) if result else []))
    if warnings:
        canvas.section("Avertissements techniques")
        for warning in warnings:
            canvas.notice(warning)

    return canvas.finish()
