"""Auditable CSV and PDF exports for an RCC dossier.

The export layer intentionally consumes :class:`DossierDetail` and the
server-side compliance report.  It never recalculates RCC values in the
browser and never mutates the stored OCR snapshot.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from html import escape
from io import BytesIO, StringIO
from typing import Any, Iterable

import fitz

from app.schemas.dossier import DossierDetail, FieldOverride
from app.schemas.rcc import RccField
from app.services.rcc_compliance import ComplianceReport


CSV_COLUMNS = [
    "record_type",
    "dossier_id",
    "client_name",
    "ice",
    "code",
    "label",
    "period",
    "value",
    "unit",
    "status",
    "confidence_pct",
    "review_status",
    "page_number",
    "engine",
    "extraction_method",
    "source_label",
    "source_value",
    "expected",
    "observed",
    "difference",
    "details",
]


def safe_export_stem(detail: DossierDetail) -> str:
    """Return a portable, predictable filename stem."""

    client = re.sub(r"[^A-Za-z0-9_-]+", "-", detail.client_name or "client")
    client = client.strip("-")[:60] or "client"
    dossier_id = re.sub(r"[^A-Za-z0-9_-]+", "-", detail.id).strip("-")
    return f"RCC-{dossier_id}-{client}"


def _override_map(detail: DossierDetail) -> dict[str, FieldOverride]:
    return {item.field_code: item for item in detail.overrides}


def _effective_value(field: RccField, override: FieldOverride | None) -> Any:
    # A verification normally persists the source value as corrected_value.
    # The fallback keeps exports correct for legacy rows that stored NULL.
    if override is not None and override.corrected_value is not None:
        return override.corrected_value
    return field.value


def _review_status(override: FieldOverride | None) -> str:
    if override is None:
        return "ocr"
    return "verified" if override.verified else "corrected"


def _confidence_pct(value: Any) -> float | str:
    if value is None:
        return ""
    return round(float(value) * 100, 2)


def _base_row(detail: DossierDetail, **values: Any) -> dict[str, Any]:
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "dossier_id": detail.id,
            "client_name": detail.client_name,
            "ice": detail.ice or "",
        }
    )
    row.update(values)
    return row


def _csv_rows(
    detail: DossierDetail, compliance: ComplianceReport
) -> Iterable[dict[str, Any]]:
    exported_at = datetime.now(timezone.utc).isoformat()
    metadata = [
        ("exercise_date", "Date de clôture", detail.exercice_date or ""),
        ("credit_amount", "Crédit demandé", detail.credit_amount or ""),
        ("dossier_status", "Statut du dossier", detail.status_label),
        ("completeness_pct", "Complétude RCC (%)", detail.completeness_pct),
        ("compliance_pct", "Conformité RCC (%)", compliance.pct),
        ("compliance_blockers", "Règles bloquantes", compliance.blockers),
        ("exported_at_utc", "Exporté le (UTC)", exported_at),
    ]
    for code, label, value in metadata:
        yield _base_row(
            detail,
            record_type="metadata",
            code=code,
            label=label,
            value=value,
            status="current",
        )

    overrides = _override_map(detail)
    if detail.result:
        for field in detail.result.fields:
            evidence = field.evidence[0] if field.evidence else None
            common = {
                "record_type": "rcc_field",
                "code": field.code,
                "label": field.label,
                "unit": field.unit,
                "status": field.status,
                "confidence_pct": _confidence_pct(field.confidence),
                "review_status": _review_status(overrides.get(field.code)),
                "page_number": evidence.page_number if evidence else "",
                "engine": evidence.engine if evidence and evidence.engine else "",
                "extraction_method": (
                    evidence.extraction_method if evidence else ""
                ),
                "source_label": evidence.raw_label if evidence else "",
                "source_value": evidence.raw_value if evidence else "",
                "details": field.note or "",
            }
            yield _base_row(
                detail,
                **common,
                period="N",
                value=_effective_value(field, overrides.get(field.code)),
            )
            if field.value_n1 is not None:
                prior_period = {**common, "review_status": "source_n1"}
                yield _base_row(
                    detail,
                    **prior_period,
                    period="N-1",
                    value=field.value_n1,
                )

        for control in detail.result.controls:
            yield _base_row(
                detail,
                record_type="accounting_control",
                code=control.code,
                label=control.label,
                status=control.status,
                expected=control.expected if control.expected is not None else "",
                observed=control.observed if control.observed is not None else "",
                difference=control.difference if control.difference is not None else "",
                details=control.message,
            )

        for section in compliance.sections:
            for rule in section.rules:
                yield _base_row(
                    detail,
                    record_type="compliance_rule",
                    code=section.title,
                    label=rule.label,
                    status="passed" if rule.ok else "blocked" if rule.blocking else "warning",
                    details=rule.detail,
                )

        scoring = detail.result.scoring
        if scoring:
            for ratio in scoring.ratios:
                yield _base_row(
                    detail,
                    record_type="financial_ratio",
                    code=ratio.code,
                    label=ratio.label,
                    value=ratio.value if ratio.value is not None else "",
                    unit=ratio.unit,
                    status=ratio.status,
                    details=ratio.formula,
                )

        for audit in detail.result.extraction.page_audit:
            route = audit.route or audit.extraction_strategy
            audit_details = (
                f"type={audit.detected_type}; route={route}; orientation={audit.orientation}; "
                f"local_ocr_chars={audit.local_ocr_chars}; "
                f"local_ocr_status={audit.local_ocr_status or 'not_run'}; "
                f"docling_status={audit.docling_status or 'not_run'}"
            )
            yield _base_row(
                detail,
                record_type="page_audit",
                code=f"PAGE_{audit.page_number}",
                label=f"Page {audit.page_number}",
                status=audit.extraction_status,
                confidence_pct=_confidence_pct(audit.local_ocr_confidence),
                page_number=audit.page_number,
                extraction_method=route,
                details=audit_details,
            )

        warnings = list(dict.fromkeys(detail.result.warnings + detail.result.extraction.warnings))
        for index, warning in enumerate(warnings, start=1):
            yield _base_row(
                detail,
                record_type="warning",
                code=f"WARNING_{index}",
                label="Avertissement d'extraction",
                status="warning",
                details=warning,
            )


def build_rcc_csv(detail: DossierDetail, compliance: ComplianceReport) -> bytes:
    """Build an Excel-friendly UTF-8 CSV with a stable, rectangular schema."""

    stream = StringIO(newline="")
    # French Excel installations commonly expect a semicolon.  The hint keeps
    # double-click opening deterministic while DictReader still sees a normal
    # rectangular dataset after the first line.
    stream.write("sep=;\r\n")
    writer = csv.DictWriter(
        stream,
        fieldnames=CSV_COLUMNS,
        delimiter=";",
        quotechar='"',
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(_csv_rows(detail, compliance))
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _fmt_number(value: Any, *, suffix: str = "") -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(number - round(number)) < 1e-9:
        rendered = f"{number:,.0f}"
    else:
        rendered = f"{number:,.2f}".rstrip("0").rstrip(".")
    return rendered.replace(",", " ") + suffix


def _table(
    headers: list[str],
    rows: Iterable[Iterable[Any]],
    css_class: str = "",
    widths: list[int] | None = None,
    *,
    break_first_column: bool = False,
) -> str:
    head = "".join(f"<td>{escape(str(item))}</td>" for item in headers)
    body_parts: list[str] = []
    for row in rows:
        cells: list[str] = []
        for index, cell in enumerate(row):
            rendered = escape(str(cell))
            if index == 0 and break_first_column:
                rendered = rendered.replace("_", "_<wbr>").replace("/", "/<wbr>")
            cells.append(f"<td>{rendered}</td>")
        body_parts.append("<tr>" + "".join(cells) + "</tr>")
    columns = ""
    if widths:
        columns = "<colgroup>" + "".join(
            f'<col style="width:{width}%">' for width in widths
        ) + "</colgroup>"
    return f'<table class="{css_class}">{columns}<tbody><tr class="table-header">{head}</tr>{"".join(body_parts)}</tbody></table>'


def _render_story(html: str, css: str) -> bytes:
    output = BytesIO()
    writer = fitz.DocumentWriter(output)
    story = fitz.Story(html=html, user_css=css, em=10)
    mediabox = fitz.paper_rect("a4")
    content = fitz.Rect(34, 32, mediabox.width - 34, mediabox.height - 44)
    more = 1
    while more:
        device = writer.begin_page(mediabox)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()

    document = fitz.open(stream=output.getvalue(), filetype="pdf")
    for index, page in enumerate(document, start=1):
        y = page.rect.height - 25
        page.draw_line((34, y - 6), (page.rect.width - 34, y - 6), color=(0.86, 0.88, 0.91), width=0.6)
        page.insert_text((34, y + 5), "Wafabail RCC - rapport interne", fontsize=7, color=(0.38, 0.43, 0.50))
        page.insert_text(
            (page.rect.width - 82, y + 5),
            f"Page {index} / {document.page_count}",
            fontsize=7,
            color=(0.38, 0.43, 0.50),
        )
    result = document.tobytes(garbage=4, deflate=True)
    document.close()
    return result


def _build_rcc_pdf_story_legacy(detail: DossierDetail, compliance: ComplianceReport) -> bytes:
    """Build a paginated analyst report using the same resolved RCC dataset."""

    result = detail.result
    overrides = _override_map(detail)
    fields = result.fields if result else []
    field_rows = []
    for field in fields:
        evidence = field.evidence[0] if field.evidence else None
        field_rows.append(
            [
                field.number,
                field.label,
                _fmt_number(_effective_value(field, overrides.get(field.code))),
                _fmt_number(field.value_n1),
                _fmt_number(_confidence_pct(field.confidence), suffix=" %"),
                {"ocr": "OCR", "verified": "Vérifié", "corrected": "Corrigé"}[
                    _review_status(overrides.get(field.code))
                ],
                f"p. {evidence.page_number}" if evidence and evidence.page_number else "-",
            ]
        )

    control_rows = []
    if result:
        for control in result.controls:
            verdict = {
                "passed": "Conforme",
                "failed": "Écart",
                "not_testable": "Non testable",
            }.get(control.status, control.status)
            control_rows.append([control.label, verdict, control.message or "-"])

    scoring = result.scoring if result else None
    ratio_rows = []
    if scoring:
        for ratio in scoring.ratios:
            ratio_rows.append(
                [
                    ratio.label,
                    _fmt_number(ratio.value, suffix=f" {ratio.unit}" if ratio.unit else ""),
                    ratio.status.replace("_", " "),
                    ratio.formula,
                ]
            )

    page_rows = []
    if result:
        for audit in result.extraction.page_audit:
            page_rows.append(
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

    exported_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    status_class = "ok" if compliance.can_validate else "blocked"
    status_text = "Prêt à valider" if compliance.can_validate else "Revue requise"
    source_kind = result.extraction.source_kind if result else None
    engines = ", ".join(result.extraction.engines) if result else ""
    pages = result.document.pages_total if result else 0
    processed = result.document.pages_processed if result else 0

    field_headers = ["N°", "Poste RCC", "Exercice N", "N-1", "Confiance", "Revue", "Source"]
    field_widths = [5, 37, 14, 12, 10, 12, 10]
    first_field_page = _table(field_headers, field_rows[:15], "financial", field_widths)
    remaining_field_page = ""
    if len(field_rows) > 15:
        remaining_field_page = (
            '<div class="page-break"></div><h2>Postes RCC - suite</h2>'
            + _table(field_headers, field_rows[15:], "financial", field_widths)
        )

    html = f"""
    <header class="hero">
      <div class="eyebrow">WAFABAIL - VALIDATION RCC &nbsp; | &nbsp; {escape(status_text.upper())}</div>
      <h1>Rapport d'extraction financière</h1>
      <p>Dossier {escape(detail.id)} - exporté le {escape(exported_at)}</p>
    </header>
    <section class="identity">
      <h2>{escape(detail.client_name or "Client à identifier")}</h2>
      <table class="meta"><tr>
        <td><b>ICE</b><br>{escape(detail.ice or "Non détecté")}</td>
        <td><b>Exercice</b><br>{escape(detail.exercice_date or "Non détecté")}</td>
        <td><b>Crédit demandé</b><br>{escape(_fmt_number(detail.credit_amount, suffix=" MAD"))}</td>
        <td><b>Statut</b><br>{escape(detail.status_label)}</td>
      </tr></table>
    </section>
    <table class="metrics"><tr>
      <td><small>COMPLÉTUDE RCC</small><strong>{_fmt_number(detail.completeness_pct, suffix=" %")}</strong></td>
      <td><small>CONFORMITÉ</small><strong>{compliance.pct} %</strong></td>
      <td><small>BLOQUANTS</small><strong>{compliance.blockers}</strong></td>
      <td><small>PAGES TRAITÉES</small><strong>{processed} / {pages}</strong></td>
    </tr></table>
    <div class="notice {status_class}"><b>{escape(status_text)}.</b> {escape(compliance.summary)}</div>

    <h2>Postes RCC - valeurs effectives</h2>
    <p class="section-note">Les corrections et confirmations analyste sont superposées à l'OCR sans modifier la source.</p>
    {first_field_page}
    {remaining_field_page}

    <h2>Contrôles comptables</h2>
    {_table(["Contrôle", "Verdict", "Observation"], control_rows or [["Aucun contrôle disponible", "Non testable", "-"]], "controls", [34, 16, 50])}

    <h2>Conformité RCC</h2>
    {_table(["Section", "Règle", "État", "Détail"], [
        [section.title, rule.label, "Conforme" if rule.ok else "Bloquant" if rule.blocking else "Avertissement", rule.detail]
        for section in compliance.sections for rule in section.rules
    ], "compliance", [18, 39, 14, 29])}
    """

    html += '<div class="page-break"></div>'
    if scoring:
        html += f"""
        <h2>Ratios de scoring financier</h2>
        <div class="notice neutral"><b>Score final non calculé.</b> La politique de pondération reste non approuvée; les ratios sont fournis à titre analytique et auditable.</div>
        {_table(["Ratio", "Valeur", "Statut", "Formule"], ratio_rows or [["Aucun ratio calculable", "-", "non calculable", "-"]], "ratios", [27, 16, 16, 41])}
        """

    html += f"""
    <h2>Audit du pipeline d'extraction</h2>
    <p class="section-note"><b>Source:</b> {escape(source_kind or "non renseignée")} &nbsp; <b>Moteurs:</b> {escape(engines or "non renseignés")}</p>
    {_table(["Page", "Type", "Source", "Route", "Orientation", "OCR local", "Docling"], page_rows or [["-", "-", "-", "-", "-", "non exécuté", "non exécuté"]], "audit", [7, 18, 13, 25, 11, 13, 13])}
    """

    warnings = list(dict.fromkeys((result.warnings + result.extraction.warnings) if result else []))
    if warnings:
        html += "<h2>Avertissements techniques</h2><ul>" + "".join(
            f"<li>{escape(warning)}</li>" for warning in warnings
        ) + "</ul>"

    css = """
    * { box-sizing: border-box; }
    body { font-family: sans-serif; color: #171411; font-size: 8.3pt; line-height: 1.35; }
    h1 { font-size: 19pt; color: #ffffff; margin: 2px 0 4px; }
    h2 { font-size: 12pt; margin: 18px 0 6px; color: #171411; }
    p { margin: 2px 0; }
    .hero { background: #191512; color: #ffffff; padding: 15px 17px; border-bottom: 5px solid #e85d0c; }
    .identity table { width: 100%; border: 0; }
    .eyebrow { color: #f6a469; font-size: 7pt; font-weight: bold; letter-spacing: 1px; }
    .hero p { color: #d9d2cc; }
    .identity { padding: 12px 2px 5px; }
    .identity h2 { font-size: 17pt; margin: 0 0 8px; }
    .meta td { width: 25%; padding: 5px 8px; border-left: 2px solid #e85d0c; background: #f8f7f5; }
    .meta b { color: #6b625a; font-size: 7pt; text-transform: uppercase; }
    .metrics { width: 100%; border-spacing: 7px; margin: 7px 0 10px; }
    .metrics td { width: 25%; background: #f4f6f8; border: 1px solid #dfe3e8; padding: 8px; }
    .metrics small { display: block; color: #667085; font-size: 6.5pt; font-weight: bold; }
    .metrics strong { display: block; color: #c04a08; font-size: 14pt; margin-top: 3px; }
    .notice { margin: 8px 0 12px; padding: 8px 10px; border-left: 3px solid #b94a19; background: #fff3ed; }
    .notice.ok { border-color: #168252; background: #edf9f3; }
    .notice.neutral { border-color: #697386; background: #f4f6f8; }
    .section-note { color: #667085; margin-bottom: 6px; }
    table { border-collapse: collapse; table-layout: fixed; width: 100%; }
    td { overflow-wrap: anywhere; border-bottom: 1px solid #e1e4e8; padding: 4px 5px; vertical-align: top; }
    tr { break-inside: avoid; page-break-inside: avoid; }
    .table-header td { background: #25201c; color: #ffffff; font-weight: bold; padding: 5px; text-align: left; }
    tbody tr:nth-child(even) { background: #f8f9fa; }
    .financial { font-size: 7.4pt; }
    .financial td:nth-child(3), .financial td:nth-child(4), .financial td:nth-child(5) { text-align: right; }
    .controls, .compliance, .ratios { font-size: 7.5pt; }
    .audit { font-size: 7.1pt; }
    .page-break { break-before: page; page-break-before: always; height: 0; }
    ul { margin: 4px 0 10px 16px; padding: 0; } li { margin: 3px 0; }
    """
    return _render_story(html, css)
