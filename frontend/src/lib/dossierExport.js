/* Exports analyste : tableur pour retraitement et rapport PDF prêt au partage. */

import { formatAmount, formatAmountMad, formatDate, formatDateTime } from "./format.js";

const cleanFilePart = (value) => String(value || "dossier")
  .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
  .replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");

function effectiveField(field, override) {
  return override ? { ...field, value: override.corrected_value, override } : field;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url; anchor.download = name;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1200);
}

function csvCell(value) { return `"${String(value ?? "").replace(/"/g, '""')}"`; }

export function exportDossierCsv({ dossier, compliance, statusLabel }) {
  const overrides = new Map((dossier.overrides || []).map((item) => [item.field_code, item]));
  const fields = (dossier.result?.fields || []).map((field) => effectiveField(field, overrides.get(field.code)));
  const info = [
    ["Dossier RCC", dossier.id], ["Client", dossier.client_name], ["ICE", dossier.ice],
    ["Exercice", formatDate(dossier.exercice_date)], ["Crédit demandé", dossier.credit_amount],
    ["Statut", statusLabel], ["Complétude OCR (%)", dossier.completeness_pct],
    ["Exporté le", formatDateTime(new Date().toISOString())], [],
  ];
  const headers = ["Code RCC", "Libellé", "Valeur effective (MAD)", "Valeur N-1 (MAD)", "Statut OCR", "Confiance (%)", "Statut analyste", "Page source", "Libellé source", "Valeur source"];
  const rows = fields.map((field) => {
    const evidence = field.evidence?.[0] || {};
    return [field.code, field.label, field.value, field.value_n1, field.status,
      field.confidence == null ? "" : Math.round(field.confidence * 100),
      field.override ? (field.override.verified ? "Confirmé" : "Corrigé") : "OCR",
      evidence.page_number, evidence.raw_label, evidence.raw_value];
  });
  const controlRows = (dossier.result?.controls || []).map((control) => [control.code, control.label, control.status, control.message]);
  const lines = [
    "sep=,",
    ...info.map((row) => row.map(csvCell).join(",")),
    headers.map(csvCell).join(","),
    ...rows.map((row) => row.map(csvCell).join(",")),
    "", ["Contrôles", "Libellé", "Statut", "Détail"].map(csvCell).join(","),
    ...controlRows.map((row) => row.map(csvCell).join(",")),
    "", ["Conformité", "Valeur"].map(csvCell).join(","),
    ["Décision de validation possible", compliance?.can_validate ? "Oui" : "Non"].map(csvCell).join(","),
    ["Bloquants", compliance?.blockers ?? 0].map(csvCell).join(","),
    ["Avertissements", compliance?.warnings ?? 0].map(csvCell).join(","),
  ];
  download(new Blob([`\uFEFF${lines.join("\r\n")}`], { type: "text/csv;charset=utf-8" }), `RCC-${cleanFilePart(dossier.id)}-${cleanFilePart(dossier.client_name)}.csv`);
}

/** Classeur Excel natif : aucun problème d'encodage ou de séparateur régional. */
export async function exportDossierWorkbook({ dossier, compliance, statusLabel }) {
  const XLSX = await import("xlsx");
  const overrides = new Map((dossier.overrides || []).map((item) => [item.field_code, item]));
  const fields = (dossier.result?.fields || []).map((field) => effectiveField(field, overrides.get(field.code)));
  const book = XLSX.utils.book_new();
  const addSheet = (name, rows, widths) => {
    const sheet = XLSX.utils.aoa_to_sheet(rows);
    sheet["!cols"] = widths.map((width) => ({ wch: width }));
    sheet["!freeze"] = { xSplit: 0, ySplit: 1 };
    XLSX.utils.book_append_sheet(book, sheet, name);
  };

  addSheet("Synthèse", [
    ["RAPPORT RCC - SYNTHÈSE"], [],
    ["Dossier RCC", dossier.id], ["Client", dossier.client_name || "Client à identifier"],
    ["ICE", dossier.ice || "Non détecté"], ["Exercice", formatDate(dossier.exercice_date)],
    ["Crédit demandé (MAD)", dossier.credit_amount], ["Statut", statusLabel],
    ["Complétude OCR (%)", dossier.completeness_pct], ["Décision de validation possible", compliance?.can_validate ? "Oui" : "Non"],
    ["Contrôles bloquants", compliance?.blockers ?? 0], ["Avertissements", compliance?.warnings ?? 0],
    ["Corrections / vérifications analyste", dossier.overrides?.length ?? 0], [],
    ["Exporté le", formatDateTime(new Date().toISOString())],
  ], [34, 72]);

  addSheet("Données RCC", [
    ["Code RCC", "Libellé", "Valeur effective (MAD)", "Valeur N-1 (MAD)", "Statut OCR", "Confiance (%)", "Revue analyste", "Page source"],
    ...fields.map((field) => {
      const evidence = field.evidence?.[0] || {};
      return [field.code, field.label, field.value, field.value_n1, field.status,
        field.confidence == null ? "" : Math.round(field.confidence * 100),
        field.override ? (field.override.verified ? "Confirmé" : "Corrigé") : "OCR", evidence.page_number || ""];
    }),
  ], [28, 43, 25, 21, 18, 15, 22, 14]);

  addSheet("Zones extraites", [
    ["Code RCC", "Poste RCC", "Page", "Libellé lu par OCR", "Valeur lue par OCR", "Colonne", "Extrait source", "Confiance (%)"],
    ...fields.flatMap((field) => (field.evidence || []).map((evidence) => [
      field.code, field.label, evidence.page_number || "", evidence.raw_label || "", evidence.raw_value || "",
      evidence.column_name || "", evidence.source_excerpt || "", evidence.confidence == null ? (field.confidence == null ? "" : Math.round(field.confidence * 100)) : Math.round(evidence.confidence * 100),
    ])),
  ], [28, 40, 10, 40, 24, 22, 65, 15]);

  addSheet("Contrôles", [
    ["Code", "Contrôle", "Statut", "Observation"],
    ...(dossier.result?.controls || []).map((control) => [control.code, control.label, control.status, control.message || ""]),
  ], [28, 48, 18, 90]);

  XLSX.writeFile(book, `RCC-${cleanFilePart(dossier.id)}-${cleanFilePart(dossier.client_name)}.xlsx`, { compression: true });
}

export async function exportDossierPdf({ dossier, compliance, statusLabel }) {
  const [{ jsPDF }, { default: autoTable }] = await Promise.all([
    import("jspdf"),
    import("jspdf-autotable"),
  ]);
  const overrides = new Map((dossier.overrides || []).map((item) => [item.field_code, item]));
  const fields = (dossier.result?.fields || []).map((field) => effectiveField(field, overrides.get(field.code)));
  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const pageWidth = doc.internal.pageSize.getWidth();
  const accent = [192, 74, 8]; const ink = [21, 17, 14]; const muted = [100, 116, 139];
  const statusTone = dossier.status === "validated" ? [21, 128, 61] : dossier.status === "rejected" ? [185, 28, 28] : [180, 83, 9];

  doc.setFillColor(...ink); doc.rect(0, 0, pageWidth, 34, "F");
  doc.setFillColor(...accent); doc.roundedRect(14, 10, 14, 14, 3, 3, "F");
  doc.setTextColor(255, 255, 255); doc.setFont("helvetica", "bold"); doc.setFontSize(13); doc.text("W", 19, 19.5);
  doc.setFontSize(15); doc.text("Rapport de validation RCC", 34, 16);
  doc.setFont("helvetica", "normal"); doc.setFontSize(8.5); doc.setTextColor(222, 214, 204); doc.text("Données d'analyse - usage interne", 34, 22);
  doc.setFillColor(...statusTone); doc.roundedRect(pageWidth - 48, 11, 34, 8, 4, 4, "F");
  doc.setFont("helvetica", "bold"); doc.setFontSize(7.5); doc.setTextColor(255, 255, 255); doc.text(statusLabel.toUpperCase(), pageWidth - 31, 16.2, { align: "center" });

  doc.setTextColor(...ink); doc.setFont("helvetica", "bold"); doc.setFontSize(17); doc.text(dossier.client_name || "Client à identifier", 14, 46);
  doc.setFont("helvetica", "normal"); doc.setFontSize(9); doc.setTextColor(...muted); doc.text(`Dossier ${dossier.id}  |  Exporté le ${formatDateTime(new Date().toISOString())}`, 14, 52);
  autoTable(doc, {
    startY: 58, theme: "plain", margin: { left: 14, right: 14 }, tableWidth: "auto",
    body: [["ICE", dossier.ice || "Non détecté", "Exercice", formatDate(dossier.exercice_date)], ["Crédit demandé", formatAmountMad(dossier.credit_amount), "Complétude OCR", `${formatAmount(dossier.completeness_pct, { decimals: true })} %`]],
    styles: { font: "helvetica", fontSize: 8.5, cellPadding: 3, textColor: ink },
    columnStyles: { 0: { fontStyle: "bold", textColor: muted }, 2: { fontStyle: "bold", textColor: muted } },
  });

  const summaryY = doc.lastAutoTable.finalY + 8;
  const cards = [["POSTES RCC", String(fields.length)], ["CONTRÔLES BLOQUANTS", String(compliance?.blockers ?? 0)], ["CORRECTIONS ANALYSTE", String(dossier.overrides?.length ?? 0)]];
  cards.forEach(([label, value], index) => {
    const x = 14 + index * 61;
    doc.setFillColor(248, 249, 250); doc.roundedRect(x, summaryY, 57, 19, 2.5, 2.5, "F");
    doc.setTextColor(...muted); doc.setFont("helvetica", "bold"); doc.setFontSize(6.7); doc.text(label, x + 4, summaryY + 6);
    doc.setTextColor(...accent); doc.setFontSize(15); doc.text(value, x + 4, summaryY + 14);
  });

  autoTable(doc, {
    startY: summaryY + 28, margin: { left: 14, right: 14 },
    head: [["Code", "Poste RCC", "Valeur effective", "Confiance", "Revue", "Source"]],
    body: fields.map((field) => {
      const evidence = field.evidence?.[0];
      return [field.code, field.label, formatAmountMad(field.value), field.confidence == null ? "-" : `${Math.round(field.confidence * 100)} %`, field.override ? (field.override.verified ? "Confirmé" : "Corrigé") : "OCR", evidence?.page_number ? `p. ${evidence.page_number}` : "-"];
    }),
    headStyles: { fillColor: ink, textColor: [255, 255, 255], fontSize: 7.5, fontStyle: "bold" },
    bodyStyles: { fontSize: 7.4, textColor: ink, cellPadding: 2.5 }, alternateRowStyles: { fillColor: [248, 249, 250] },
    columnStyles: { 0: { cellWidth: 29 }, 1: { cellWidth: 60 }, 2: { cellWidth: 32, halign: "right" }, 3: { cellWidth: 18, halign: "right" }, 4: { cellWidth: 20 }, 5: { cellWidth: 16, halign: "right" } },
  });

  let nextY = doc.lastAutoTable.finalY + 10;
  const controls = dossier.result?.controls || [];
  if (nextY > 250) { doc.addPage(); nextY = 18; }
  doc.setTextColor(...ink); doc.setFont("helvetica", "bold"); doc.setFontSize(11); doc.text("Contrôles de cohérence", 14, nextY);
  autoTable(doc, {
    startY: nextY + 4, margin: { left: 14, right: 14 }, head: [["Contrôle", "Statut", "Observation"]],
    body: controls.map((control) => [control.label, control.status === "passed" ? "Conforme" : control.status === "failed" ? "Écart" : "Non testable", control.message || "-"]),
    headStyles: { fillColor: [71, 85, 105], fontSize: 7.5 }, bodyStyles: { fontSize: 7.4, cellPadding: 2.5 },
    columnStyles: { 0: { cellWidth: 53 }, 1: { cellWidth: 26 }, 2: { cellWidth: 101 } },
  });

  const total = doc.internal.getNumberOfPages();
  for (let page = 1; page <= total; page += 1) {
    doc.setPage(page); doc.setDrawColor(230, 232, 235); doc.line(14, 288, pageWidth - 14, 288);
    doc.setTextColor(...muted); doc.setFont("helvetica", "normal"); doc.setFontSize(7.5);
    doc.text(`Wafabail RCC - ${dossier.id}`, 14, 293); doc.text(`Page ${page} / ${total}`, pageWidth - 14, 293, { align: "right" });
  }
  doc.save(`RCC-${cleanFilePart(dossier.id)}-${cleanFilePart(dossier.client_name)}.pdf`);
}
