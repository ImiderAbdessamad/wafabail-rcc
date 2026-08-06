/* Hooks transverses : debounce, focus piégé, virtualisation, suivi de job. */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { followJob } from "../lib/api.js";

/**
 * Planifie un travail d'affichage sur la prochaine frame.
 *
 * `requestAnimationFrame` est gelé quand l'onglet est masqué : une liste
 * virtualisée resterait figée à mi-chemin. On aligne sur la frame quand c'est
 * possible, avec un filet de sécurité en `setTimeout`.
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

/** Valeur retardée — évite une requête par frappe. */
export function useDebouncedValue(value, delay = 280) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/** Version stable d'un callback, utilisable dans des effets sans les relancer. */
export function useEvent(handler) {
  const ref = useRef(handler);
  useLayoutEffect(() => {
    ref.current = handler;
  });
  return useCallback((...args) => ref.current?.(...args), []);
}

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * Piège le focus dans un conteneur (modales) et le restaure à la fermeture.
 * `onEscape` est appelé sur Échap.
 */
export function useFocusTrap(active, onEscape) {
  const ref = useRef(null);
  const escapeRef = useRef(onEscape);
  useLayoutEffect(() => {
    escapeRef.current = onEscape;
  });

  useEffect(() => {
    if (!active) return undefined;
    const container = ref.current;
    if (!container) return undefined;

    const previouslyFocused = document.activeElement;
    const focusables = () =>
      Array.from(container.querySelectorAll(FOCUSABLE)).filter(
        (node) => node.offsetParent !== null || node === document.activeElement
      );

    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        escapeRef.current?.();
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
      const target = container.querySelector("[data-autofocus]") || focusables()[0] || container;
      target.focus?.();
    });

    return () => {
      container.removeEventListener("keydown", onKeyDown);
      if (previouslyFocused?.isConnected) previouslyFocused.focus?.();
    };
  }, [active]);

  return ref;
}

/** Bloque le défilement de la page tant que `active` est vrai. */
export function useScrollLock(active) {
  useEffect(() => {
    if (!active) return undefined;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [active]);
}

/**
 * Fenêtre de lignes visibles pour une liste virtualisée à hauteur fixe.
 *
 * @returns {{ ref, start, end, offsetY, totalHeight }}
 */
export function useVirtualRows(count, rowHeight, { overscan = 8 } = {}) {
  const ref = useRef(null);
  const [range, setRange] = useState({ start: 0, end: Math.min(count, 20) });

  const measure = useEvent(() => {
    const node = ref.current;
    if (!node) return;
    const { scrollTop } = node;
    const height = node.clientHeight || 480;
    const start = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan);
    const end = Math.min(count, Math.ceil((scrollTop + height) / rowHeight) + overscan);
    setRange((current) =>
      current.start === start && current.end === end ? current : { start, end }
    );
  });

  useEffect(() => {
    measure();
    const node = ref.current;
    if (!node) return undefined;
    const onScroll = () => schedule(measure);
    node.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      node.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [measure, count, rowHeight]);

  return {
    ref,
    start: range.start,
    end: range.end,
    offsetY: range.start * rowHeight,
    totalHeight: count * rowHeight,
  };
}

/**
 * Suit un job d'extraction (SSE + repli polling) et coupe proprement le suivi
 * au démontage du composant.
 */
export function useJobFollower() {
  const followers = useRef(new Set());

  useEffect(
    () => () => {
      for (const follower of followers.current) follower.cancel();
      followers.current.clear();
    },
    []
  );

  return useCallback(async (jobId, { onProgress } = {}) => {
    const follower = followJob(jobId, { onProgress });
    followers.current.add(follower);
    try {
      return await follower.promise;
    } finally {
      followers.current.delete(follower);
    }
  }, []);
}
