/* Notifications applicatives + annonces aux lecteurs d'écran. */

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
} from "react";

const ToastContext = createContext(null);

let nextId = 0;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [announcement, setAnnouncement] = useState("");
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((current) =>
      current.map((t) => (t.id === id ? { ...t, leaving: true } : t))
    );
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.set(
      id,
      setTimeout(() => {
        setToasts((current) => current.filter((t) => t.id !== id));
        timers.current.delete(id);
      }, 320)
    );
  }, []);

  const toast = useCallback(
    (message, { title = "", type = "info", timeout = 5200 } = {}) => {
      const id = ++nextId;
      setToasts((current) => [...current, { id, message, title, type }]);
      setAnnouncement(`${title ? `${title}. ` : ""}${message}`);
      if (timeout) {
        timers.current.set(id, setTimeout(() => dismiss(id), timeout));
      }
      return id;
    },
    [dismiss]
  );

  /** Message poussé dans la zone aria-live sans notification visuelle. */
  const announce = useCallback((message) => setAnnouncement(message), []);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const value = useMemo(() => ({ toast, announce }), [toast, announce]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>
      <div className="toasts" role="region" aria-label="Notifications">
        {toasts.map((item) => (
          <div
            key={item.id}
            className={`toast toast-${item.type}${item.leaving ? " is-leaving" : ""}`}
            role={item.type === "bad" ? "alert" : "status"}
          >
            <div className="toast-body">
              {item.title ? <p className="toast-title">{item.title}</p> : null}
              <p className="toast-text">{item.message}</p>
            </div>
            <button
              type="button"
              className="toast-close"
              aria-label="Fermer la notification"
              onClick={() => dismiss(item.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToasts() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToasts doit être utilisé dans un ToastProvider.");
  return context;
}
