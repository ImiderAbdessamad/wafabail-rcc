/* Modales : overlay, piège de focus, fermeture Échap / clic extérieur. */

import { el, trapFocus } from "./util.js";

let openCount = 0;

/**
 * Ouvre une modale.
 *
 * @param {(close: Function) => HTMLElement} render  construit le contenu
 * @param {{ labelledBy?: string, onClose?: Function, dismissible?: boolean }} options
 * @returns {Function} fermeture programmatique
 */
export function openModal(render, { labelledBy, onClose, dismissible = true } = {}) {
  const root = document.getElementById("modalRoot");
  let releaseFocus = null;
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    releaseFocus?.();
    overlay.remove();
    openCount = Math.max(0, openCount - 1);
    if (openCount === 0) document.body.style.removeProperty("overflow");
    onClose?.();
  };

  const dialog = el("div", {
    class: "modal",
    role: "dialog",
    "aria-modal": "true",
    ...(labelledBy ? { "aria-labelledby": labelledBy } : {}),
    onClick: (event) => event.stopPropagation(),
  });
  dialog.appendChild(render(close));

  const overlay = el("div", {
    class: "overlay",
    onClick: () => { if (dismissible) close(); },
  }, [dialog]);

  root.appendChild(overlay);
  openCount += 1;
  document.body.style.overflow = "hidden";
  releaseFocus = trapFocus(dialog, { onEscape: () => { if (dismissible) close(); } });

  return close;
}

/** Confirmation simple, promesse résolue avec true/false. */
export function confirmModal({ title, text, confirmLabel = "Confirmer", tone = "danger" }) {
  return new Promise((resolve) => {
    openModal(
      (close) =>
        el("div", {}, [
          el("h2", { class: "modal-title", id: "confirmTitle", text: title }),
          el("p", { class: "modal-sub", text }),
          el("div", { class: "modal-actions" }, [
            el("button", {
              type: "button", class: "btn btn-ghost", text: "Annuler",
              onClick: () => { close(); resolve(false); },
            }),
            el("button", {
              type: "button", autofocus: true,
              class: `btn ${tone === "danger" ? "btn-danger-solid" : "btn-primary"}`,
              text: confirmLabel,
              onClick: () => { close(); resolve(true); },
            }),
          ]),
        ]),
      { labelledBy: "confirmTitle", onClose: () => resolve(false) }
    );
  });
}
