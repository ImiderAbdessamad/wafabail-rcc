/* Lecteur PDF contrôlé : navigation page par page et surlignage des preuves OCR. */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import Icon, { ICONS } from "../Icon.jsx";

GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

const fold = (value) => String(value || "").normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "").replace(/\s+/g, " ").trim().toLowerCase();

function terms(evidence) {
  return [evidence.raw_label, evidence.raw_value].map(fold).filter((term) => term.length >= 3);
}

export default function PdfEvidenceViewer({ fileUrl, filename, fields, activeCode, activeEvidencePage, onFocusField }) {
  const canvasRef = useRef(null);
  const hostRef = useRef(null);
  const pdfRef = useRef(null);
  const renderRef = useRef(null);
  const [pageCount, setPageCount] = useState(0);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageWidth, setPageWidth] = useState(0);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState(null);
  const [showEvidence, setShowEvidence] = useState(true);
  const [importantCodes, setImportantCodes] = useState(() => new Set());
  const [rectangles, setRectangles] = useState([]);

  const evidence = useMemo(() => (fields || []).flatMap((field) =>
    (field.evidence || []).map((item) => ({ ...item, code: field.code, label: field.label }))
  ), [fields]);
  const selectedEvidence = useMemo(() => evidence.find((item) => item.code === activeCode), [activeCode, evidence]);

  useEffect(() => {
    const targetPage = activeEvidencePage || selectedEvidence?.page_number;
    if (targetPage) setPageNumber(targetPage);
  }, [activeEvidencePage, selectedEvidence]);

  useEffect(() => {
    let disposed = false;
    const task = getDocument({ url: fileUrl, withCredentials: true });
    setStatus("loading"); setError(null); setPageCount(0);
    task.promise.then((pdf) => {
      if (disposed) { pdf.destroy(); return; }
      pdfRef.current = pdf; setPageCount(pdf.numPages); setStatus("ready");
    }).catch((reason) => {
      if (!disposed) { setError(reason?.message || "Le document ne peut pas être affiché."); setStatus("error"); }
    });
    return () => {
      disposed = true;
      renderRef.current?.cancel?.();
      if (typeof pdfRef.current?.destroy === "function") pdfRef.current.destroy();
      pdfRef.current = null;
      if (typeof task.destroy === "function") task.destroy();
    };
  }, [fileUrl]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return undefined;
    const update = () => setPageWidth(Math.max(260, host.clientWidth - 24));
    update();
    const observer = new ResizeObserver(update); observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const pdf = pdfRef.current; const canvas = canvasRef.current;
    if (!pdf || !canvas || !pageWidth || status !== "ready") return undefined;
    let cancelled = false;
    async function render() {
      try {
        renderRef.current?.cancel();
        const page = await pdf.getPage(pageNumber);
        const baseViewport = page.getViewport({ scale: 1 });
        const viewport = page.getViewport({ scale: pageWidth / baseViewport.width });
        const ratio = window.devicePixelRatio || 1;
        const context = canvas.getContext("2d", { alpha: false });
        canvas.width = Math.floor(viewport.width * ratio); canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${Math.floor(viewport.width)}px`; canvas.style.height = `${Math.floor(viewport.height)}px`;
        const task = page.render({ canvasContext: context, viewport, transform: [ratio, 0, 0, ratio, 0, 0] });
        renderRef.current = task; await task.promise;
        if (cancelled) return;
        const text = await page.getTextContent();
        const onPage = evidence.filter((item) => item.page_number === pageNumber);
        const found = [];
        for (const item of text.items) {
          const value = fold(item.str);
          const matches = onPage.filter((source) => terms(source).some((term) => value.includes(term) || term.includes(value)));
          if (!value || !matches.length) continue;
          const matrix = viewport.transform;
          const left = matrix[0] * item.transform[4] + matrix[2] * item.transform[5] + matrix[4];
          const bottom = matrix[1] * item.transform[4] + matrix[3] * item.transform[5] + matrix[5];
          const height = Math.max(9, Math.abs(item.transform[3] * viewport.scale));
          found.push({ key: `${item.transform[4]}-${item.transform[5]}-${matches.map((match) => match.code).join("-")}`, left, top: bottom - height, width: Math.max(12, item.width * viewport.scale), height, codes: matches.map((match) => match.code), label: matches[0].label });
        }
        if (!cancelled) setRectangles(found);
      } catch (reason) {
        if (!cancelled && reason?.name !== "RenderingCancelledException") { setError(reason?.message || "Le rendu de cette page a échoué."); setStatus("error"); }
      }
    }
    render();
    return () => { cancelled = true; };
  }, [evidence, pageNumber, pageWidth, status]);

  const toggleImportant = useCallback(() => {
    if (!activeCode) return;
    setImportantCodes((current) => { const next = new Set(current); next.has(activeCode) ? next.delete(activeCode) : next.add(activeCode); return next; });
  }, [activeCode]);
  const activeIsImportant = activeCode && importantCodes.has(activeCode);

  return <div className="pdf-viewer">
    <div className="viewer-toolbar">
      <span className="viewer-file" title={filename}>{filename || "Liasse"}</span>
      <span className="viewer-page-count" aria-live="polite">{pageCount ? `Page ${pageNumber} / ${pageCount}` : "Chargement du document…"}</span>
      <span style={{ flex: 1 }} />
      <button type="button" className={`btn btn-ghost btn-sm${showEvidence ? " is-selected" : ""}`} onClick={() => setShowEvidence((value) => !value)}><Icon paths={ICONS.highlight} size={13} width={2} />{showEvidence ? "Masquer les preuves" : "Afficher les preuves"}</button>
      <button type="button" className="btn btn-soft btn-sm" disabled={!activeCode} onClick={toggleImportant}><Icon paths={ICONS.highlight} size={13} width={2} />{activeIsImportant ? "Retirer l'important" : "Marquer important"}</button>
      <a className="btn btn-ghost btn-sm" href={fileUrl} target="_blank" rel="noopener noreferrer">Ouvrir</a>
    </div>
    <div className="pdf-canvas-scroll pane-scroll" ref={hostRef}>
      <div className="pdf-stage">
        {status === "loading" ? <p className="pdf-status">Chargement du document…</p> : null}
        {status === "error" ? <p className="pdf-status pdf-status-error">{error}</p> : null}
        <div className="pdf-page" hidden={status !== "ready"}><canvas ref={canvasRef} />
          {showEvidence ? <div className="pdf-highlights" aria-label="Preuves OCR surlignées">{rectangles.map((rect) => {
            const active = rect.codes.includes(activeCode); const important = rect.codes.some((code) => importantCodes.has(code));
            return <button key={rect.key} type="button" className={`pdf-highlight${active ? " is-active" : ""}${important ? " is-important" : ""}`} style={{ left: rect.left, top: rect.top, width: rect.width, height: rect.height }} title={`Preuve OCR : ${rect.label}`} aria-label={`Voir ${rect.label} dans le formulaire`} onClick={() => onFocusField(rect.codes[0], { openDoc: false, pageNumber })} />;
          })}</div> : null}
        </div>
      </div>
    </div>
    <div className="pdf-pagination" aria-label="Navigation du document">
      <button type="button" className="btn btn-ghost btn-sm btn-icon" aria-label="Page précédente" disabled={pageNumber <= 1} onClick={() => setPageNumber((page) => Math.max(1, page - 1))}><Icon paths={ICONS.chevronLeft} size={15} width={2} /></button>
      <span>{pageCount ? `Page ${pageNumber}` : ""}</span>
      <button type="button" className="btn btn-ghost btn-sm btn-icon" aria-label="Page suivante" disabled={!pageCount || pageNumber >= pageCount} onClick={() => setPageNumber((page) => Math.min(pageCount, page + 1))}><Icon paths={ICONS.chevronRight} size={15} width={2} /></button>
    </div>
  </div>;
}
