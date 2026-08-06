/* Modale : overlay, focus piégé, Échap, clic extérieur. */

import { createPortal } from "react-dom";
import { useFocusTrap, useScrollLock } from "../hooks/index.js";

export default function Modal({
  open,
  onClose,
  labelledBy,
  dismissible = true,
  className = "",
  children,
}) {
  const close = dismissible ? onClose : undefined;
  const ref = useFocusTrap(open, close);
  useScrollLock(open);

  if (!open) return null;

  return createPortal(
    <div
      className="overlay"
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose?.();
      }}
    >
      <div
        ref={ref}
        className={`modal ${className}`.trim()}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
      >
        {children}
      </div>
    </div>,
    document.body
  );
}

/** Bouton avec état occupé (spinner intégré). */
export function BusyButton({ busy, children, className = "btn", ...props }) {
  return (
    <button {...props} className={`${className}${busy ? " is-busy" : ""}`} disabled={busy || props.disabled}>
      <span className="btn-label">{children}</span>
      <span className="btn-spinner" aria-hidden="true" />
    </button>
  );
}
