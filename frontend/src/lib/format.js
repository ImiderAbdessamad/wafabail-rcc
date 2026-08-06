/* Formatage et petits utilitaires — sans dépendance au DOM ni à React. */

const NUMBER_FMT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const DECIMAL_FMT = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });

/** Espaces insécables normalisés en espaces simples (lisibilité tabulaire). */
export function formatAmount(value, { decimals = false } = {}) {
  if (value == null || value === "" || Number.isNaN(Number(value))) return "—";
  const fmt = decimals ? DECIMAL_FMT : NUMBER_FMT;
  return fmt.format(Number(value)).replace(/[  ]/g, " ");
}

export function formatAmountMad(value) {
  const formatted = formatAmount(value);
  return formatted === "—" ? "—" : `${formatted} MAD`;
}

/** Parse une saisie utilisateur « 12 345,67 » ou « 12345.67 ». */
export function parseAmount(raw) {
  if (raw == null) return null;
  const cleaned = String(raw)
    .replace(/[\s  ]/g, "")
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

export function initials(name) {
  const parts = String(name || "").replace(/[._]/g, " ").split(/\s+/).filter(Boolean);
  if (!parts.length) return "??";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count > 1 ? plural : singular}`;
}
