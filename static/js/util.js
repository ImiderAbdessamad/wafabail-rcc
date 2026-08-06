/* Utilitaires partagés : DOM, formatage, accessibilité, notifications. */

/* ---------------------------------------------------------------- DOM --- */

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Crée un élément. `html` n'est utilisé que pour du contenu construit ici. */
export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Remplace le contenu d'un nœud en une seule écriture. */
export function replaceChildren(node, ...children) {
  node.replaceChildren(...children.flat().filter(Boolean));
}

/* --------------------------------------------------------- Formatage --- */

const NUMBER_FMT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const DECIMAL_FMT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

/** Espaces insécables normalisés en espaces simples (lisibilité tabulaire). */
export function formatAmount(value, { decimals = false } = {}) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  const fmt = decimals ? DECIMAL_FMT : NUMBER_FMT;
  return fmt.format(Number(value)).replace(/[  ]/g, " ");
}

export function formatAmountMad(value) {
  const formatted = formatAmount(value);
  return formatted === "—" ? "—" : `${formatted} MAD`;
}

/** Parse une saisie utilisateur « 12 345,67 » ou « 12345.67 ». */
export function parseAmount(raw) {
  if (raw == null) return null;
  const cleaned = String(raw)
    .replace(/[\s  ]/g, "")
    .replace(/[^\d,.\-]/g, "")
    .replace(/\.(?=\d{3}\b)/g, "")
    .replace(",", ".");
  if (cleaned === "" || cleaned === "-" || cleaned === ".") return null;
  const num = Number(cleaned);
  return Number.isFinite(num) ? num : null;
}

export function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} o`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} Ko`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`;
}

export function formatDateTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date
    .toLocaleString("fr-FR", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    })
    .replace(",", "");
}

export function formatDate(value) {
  if (!value) return "—";
  // Le pipeline renvoie souvent déjà « 31/12/2025 »
  if (/^\d{2}\/\d{2}\/\d{4}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("fr-FR");
}

export function formatPct(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${Number(value).toFixed(digits).replace(".", ",")} %`;
}

export function initials(name) {
  const parts = String(name || "").replace(/[._]/g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count > 1 ? plural : singular}`;
}

/* ------------------------------------------------------ Asynchronisme --- */

export function debounce(fn, delay = 280) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
  wrapped.cancel = () => clearTimeout(timer);
  wrapped.flush = (...args) => { clearTimeout(timer); fn(...args); };
  return wrapped;
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Planifie un travail d'affichage sur la prochaine frame.
 *
 * `requestAnimationFrame` est gelé par le navigateur quand l'onglet est masqué :
 * un rendu par lots ou une liste virtualisée resterait alors figé à mi-chemin.
 * On aligne donc sur la frame quand c'est possible, avec un filet de sécurité
 * en `setTimeout` pour que le travail se termine dans tous les cas.
 */
export function schedule(fn) {
  let done = false;
  const run = () => {
    if (done) return;
    done = true;
    fn();
  };
  requestAnimationFrame(run);
  setTimeout(run, 32);
}

/* --------------------------------------------------- Accessibilité --- */

let announcerNode = null;
/** Annonce un message aux lecteurs d'écran (aria-live). */
export function announce(message) {
  announcerNode ||= document.getElementById("srAnnouncer");
  if (!announcerNode) return;
  // Réécriture forcée pour que la même chaîne soit re-annoncée
  announcerNode.textContent = "";
  schedule(() => { announcerNode.textContent = message; });
}

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * Piège le focus dans un conteneur (modales). Renvoie la fonction de libération,
 * qui restaure le focus sur l'élément d'origine.
 */
export function trapFocus(container, { onEscape } = {}) {
  const previouslyFocused = document.activeElement;

  const focusables = () =>
    $$(FOCUSABLE, container).filter((node) => node.offsetParent !== null || node === document.activeElement);

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onEscape?.();
      return;
    }
    if (event.key !== "Tab") return;
    const items = focusables();
    if (!items.length) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  container.addEventListener("keydown", onKeyDown);
  schedule(() => {
    const target = container.querySelector("[autofocus]") || focusables()[0] || container;
    target.focus?.();
  });

  return () => {
    container.removeEventListener("keydown", onKeyDown);
    if (previouslyFocused?.isConnected) previouslyFocused.focus?.();
  };
}

/* ------------------------------------------------------ Notifications --- */

const TOAST_ICONS = { ok: "✓", bad: "!", warn: "!", info: "i" };

export function toast(message, { title = "", type = "info", timeout = 5200 } = {}) {
  const root = document.getElementById("toasts");
  if (!root) return () => {};

  const node = el("div", { class: `toast toast-${type}`, role: type === "bad" ? "alert" : "status" }, [
    el("div", { class: "toast-body" }, [
      title ? el("p", { class: "toast-title", text: title }) : null,
      el("p", { class: "toast-text", text: message }),
    ]),
    el("button", {
      class: "toast-close", type: "button", "aria-label": "Fermer la notification",
      text: "×", onClick: () => dismiss(),
    }),
  ]);

  let timer = null;
  function dismiss() {
    clearTimeout(timer);
    node.classList.add("is-leaving");
    node.addEventListener("transitionend", () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 400);
  }

  root.appendChild(node);
  announce(`${title ? `${title}. ` : ""}${message}`);
  if (timeout) timer = setTimeout(dismiss, timeout);
  return dismiss;
}

/* --------------------------------------------------------- Squelettes --- */

export function skeletonRows(count = 5, widths = [90, 190, 110, 120, 130]) {
  return Array.from({ length: count }, () =>
    el("div", { class: "sk-row", "aria-hidden": "true" },
      widths.map((width, index) =>
        el("div", {
          class: `skeleton ${index === 1 ? "sk-title" : "sk-line"}`,
          style: `width:${width}px`,
        })
      )
    )
  );
}

export function loadingBlock(label = "Chargement…") {
  return el("div", { class: "empty", role: "status" }, [
    el("div", { class: "skeleton", style: "width:180px;height:12px" }),
    el("p", { class: "sr-only", text: label }),
  ]);
}

/* --------------------------------------------------------------- SVG --- */

/** Icône inline depuis un chemin SVG (évite les requêtes réseau). */
export function icon(paths, { size = 16, stroke = "currentColor", width = 2 } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", stroke);
  svg.setAttribute("stroke-width", width);
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  for (const d of [].concat(paths)) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", d);
    svg.appendChild(path);
  }
  return svg;
}

export const ICONS = {
  warning: ["M12 9v4", "M12 17h.01", "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"],
  check: ["M20 6 9 17l-5-5"],
  chevronLeft: ["m15 18-6-6 6-6"],
  chevronRight: ["m9 18 6-6-6-6"],
  eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z", "M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"],
  pencil: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"],
  lock: ["M7 11V7a5 5 0 0 1 10 0v4", "M5 11h14a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8a1 1 0 0 1 1-1Z"],
  highlight: ["m9 11-6 6v3h3l6-6", "m17 3 4 4-9 9-4-4Z"],
  upload: ["M12 16V4", "m7 9 5-5 5 5", "M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"],
  file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", "M14 2v6h6"],
  inbox: ["M22 12h-6l-2 3h-4l-2-3H2", "M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11Z"],
  info: ["M12 16v-4", "M12 8h.01", "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z"],
  clock: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Z", "M12 7v5l3 2"],
};
