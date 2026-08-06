/* États transverses : vide, erreur, squelettes de chargement. */

import Icon, { ICONS } from "./Icon.jsx";

export function EmptyState({ icon = ICONS.inbox, title, text, children, tone = "neutral" }) {
  return (
    <div className={`empty${tone === "bad" ? " empty-bad" : ""}`}>
      <div className="empty-icon">
        <Icon paths={icon} size={20} width={1.6} />
      </div>
      <h3>{title}</h3>
      {text ? <p>{text}</p> : null}
      {children ? (
        <div style={{ display: "flex", gap: 9, flexWrap: "wrap", justifyContent: "center" }}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

export function ErrorState({ title = "Chargement impossible", message, onRetry, children }) {
  return (
    <EmptyState icon={ICONS.warning} tone="bad" title={title} text={message}>
      {children}
      {onRetry ? (
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          Réessayer
        </button>
      ) : null}
    </EmptyState>
  );
}

export function SkeletonRows({ count = 6, widths = [90, 190, 110, 120, 130] }) {
  return (
    <>
      {Array.from({ length: count }, (_, row) => (
        <div className="sk-row" key={row} aria-hidden="true">
          {widths.map((width, index) => (
            <div
              key={`${row}-${index}`}
              className={`skeleton ${index === 1 ? "sk-title" : "sk-line"}`}
              style={{ width }}
            />
          ))}
        </div>
      ))}
    </>
  );
}

/** Pastille de statut réutilisable. */
export function Badge({ tone = "neutral", dot = false, children, className = "", ...props }) {
  return (
    <span className={`badge badge-${tone} ${className}`.trim()} {...props}>
      {dot ? <span className="badge-dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
