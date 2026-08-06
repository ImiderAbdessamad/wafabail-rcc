/* Client HTTP de l'API RCC. Une seule couche d'erreurs pour toute l'interface. */

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(message, { status = 0, body = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
  get isAuth() { return this.status === 401; }
  get isConflict() { return this.status === 409; }
}

/** Handler installé par app.js : bascule sur l'écran de connexion en cas de 401. */
let onUnauthorized = null;
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

function detailToMessage(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    // Erreurs de validation Pydantic
    const first = detail[0];
    if (first?.msg) return `${first.msg}${first.loc ? ` (${first.loc.at(-1)})` : ""}`;
  }
  return fallback;
}

async function request(path, { method = "GET", body, signal, isForm = false, authChallenge = true } = {}) {
  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      signal,
      credentials: "same-origin",
      headers: isForm || body == null ? undefined : { "Content-Type": "application/json" },
      body: isForm ? body : body == null ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    if (error.name === "AbortError") throw error;
    throw new ApiError(
      "Serveur injoignable. Vérifiez que l'API est démarrée puis réessayez.",
      { status: 0 }
    );
  }

  // `authChallenge: false` sur /auth/login : un 401 y signifie « mauvais
  // identifiants », pas « session expirée ».
  if (response.status === 401 && authChallenge) {
    onUnauthorized?.();
    throw new ApiError("Session expirée — reconnectez-vous.", { status: 401 });
  }

  if (response.status === 204) return null;

  const isJson = (response.headers.get("content-type") || "").includes("application/json");
  const payload = isJson ? await response.json().catch(() => null) : null;

  if (!response.ok) {
    throw new ApiError(
      detailToMessage(payload?.detail, `Erreur ${response.status} — ${response.statusText}`),
      { status: response.status, body: payload }
    );
  }
  return payload;
}

/* ------------------------------------------------------------ Session --- */

export const auth = {
  me: () => request("/auth/me"),
  login: (username, password) =>
    request("/auth/login", {
      method: "POST",
      body: { username, password },
      authChallenge: false,
    }),
  logout: () => request("/auth/logout", { method: "POST" }),
};

/* ----------------------------------------------------------- Dossiers --- */

export const dossiers = {
  list: ({ status, search, signal } = {}) => {
    const params = new URLSearchParams();
    if (status && status !== "all") params.set("status", status);
    if (search) params.set("search", search);
    const query = params.toString();
    return request(`/rcc/dossiers${query ? `?${query}` : ""}`, { signal });
  },
  get: (id, { signal } = {}) => request(`/rcc/dossiers/${encodeURIComponent(id)}`, { signal }),
  create: (payload) => request("/rcc/dossiers", { method: "POST", body: payload }),
  patch: (id, payload) =>
    request(`/rcc/dossiers/${encodeURIComponent(id)}`, { method: "PATCH", body: payload }),
  saveOverrides: (id, overrides) =>
    request(`/rcc/dossiers/${encodeURIComponent(id)}/overrides`, {
      method: "PUT",
      body: { overrides },
    }),
  attach: (id, payload) =>
    request(`/rcc/dossiers/${encodeURIComponent(id)}/attach`, { method: "POST", body: payload }),
  audit: (id, { signal } = {}) =>
    request(`/rcc/dossiers/${encodeURIComponent(id)}/audit`, { signal }),
  fileUrl: (id) => `${BASE}/rcc/dossiers/${encodeURIComponent(id)}/file`,
};

export const audit = {
  all: ({ signal } = {}) => request("/rcc/audit", { signal }),
};

/* --------------------------------------------------------------- Jobs --- */

export const jobs = {
  /** Envoi du PDF avec progression d'upload (fetch n'expose pas onprogress). */
  create(file, { onUploadProgress } = {}) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append("file", file);

      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/rcc/jobs`);
      xhr.withCredentials = true;

      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          onUploadProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      });

      xhr.addEventListener("load", () => {
        let payload = null;
        try { payload = JSON.parse(xhr.responseText); } catch { /* réponse non JSON */ }
        if (xhr.status === 401) {
          onUnauthorized?.();
          reject(new ApiError("Session expirée — reconnectez-vous.", { status: 401 }));
          return;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload);
          return;
        }
        reject(new ApiError(
          detailToMessage(payload?.detail, `Erreur ${xhr.status} lors de l'envoi du fichier.`),
          { status: xhr.status, body: payload }
        ));
      });

      xhr.addEventListener("error", () =>
        reject(new ApiError("Envoi interrompu — vérifiez votre connexion.", { status: 0 })));
      xhr.addEventListener("abort", () =>
        reject(new ApiError("Envoi annulé.", { status: 0 })));

      xhr.send(form);
    });
  },

  progress: (jobId, { signal } = {}) =>
    request(`/rcc/jobs/${encodeURIComponent(jobId)}`, { signal }),

  result: (jobId, { signal } = {}) =>
    request(`/rcc/jobs/${encodeURIComponent(jobId)}/result`, { signal }),

  streamUrl: (jobId) => `${BASE}/rcc/jobs/${encodeURIComponent(jobId)}/stream`,
};

/**
 * Suit un job jusqu'à son terme : SSE en priorité, repli sur du polling si le
 * flux se coupe (proxy, veille de l'onglet…).
 *
 * @returns {{ promise: Promise<object>, cancel: () => void }}
 */
export function followJob(jobId, { onProgress } = {}) {
  let source = null;
  let cancelled = false;
  let pollTimer = null;
  let settle = { resolve: null, reject: null };

  const cleanup = () => {
    source?.close();
    source = null;
    clearTimeout(pollTimer);
  };

  const finish = async () => {
    if (cancelled) return;
    cleanup();
    try {
      settle.resolve(await jobs.result(jobId));
    } catch (error) {
      settle.reject(error);
    }
  };

  const fail = (message) => {
    if (cancelled) return;
    cleanup();
    settle.reject(new ApiError(message, { status: 422 }));
  };

  // Un job peut disparaître (redémarrage du serveur, expiration du TTL) :
  // sans plafond, le client interrogerait indéfiniment un job inexistant.
  const MAX_CONSECUTIVE_FAILURES = 4;
  let consecutiveFailures = 0;

  const poll = async () => {
    if (cancelled) return;
    try {
      const progress = await jobs.progress(jobId);
      consecutiveFailures = 0;
      onProgress?.(progress);
      if (progress.status === "completed") return finish();
      if (progress.status === "failed") return fail(progress.error || "L'extraction a échoué.");
    } catch (error) {
      if (error.isAuth) { cleanup(); settle.reject(error); return; }
      if (error.status === 404) {
        return fail(
          "Le job d'extraction n'existe plus (serveur redémarré ou délai dépassé). "
          + "Relancez l'import."
        );
      }
      consecutiveFailures += 1;
      if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
        return fail("Suivi de l'extraction interrompu — le serveur ne répond plus.");
      }
    }
    pollTimer = setTimeout(poll, 2500);
  };

  const promise = new Promise((resolve, reject) => {
    settle = { resolve, reject };

    try {
      source = new EventSource(jobs.streamUrl(jobId), { withCredentials: true });
    } catch {
      poll();
      return;
    }

    const handleProgress = (event) => {
      try { onProgress?.(JSON.parse(event.data)); } catch { /* évènement non JSON */ }
    };

    for (const name of [
      "job_status", "pdf_validated", "job_started", "pages_rendered",
      "page_classified", "page_extracted", "page_skipped", "page_failed",
      "resolving_fields", "running_controls",
    ]) {
      source.addEventListener(name, handleProgress);
    }
    source.onmessage = handleProgress;

    source.addEventListener("result_ready", (event) => {
      handleProgress(event);
      finish();
    });
    source.addEventListener("job_failed", (event) => {
      let message = "L'extraction a échoué.";
      try {
        const data = JSON.parse(event.data);
        message = data.error || data.message || message;
      } catch { /* évènement non JSON */ }
      fail(message);
    });

    source.onerror = () => {
      // EventSource retente seul ; s'il a définitivement fermé, on bascule en polling.
      if (!source || source.readyState === EventSource.CLOSED) {
        cleanup();
        poll();
      }
    };
  });

  return {
    promise,
    cancel() {
      cancelled = true;
      cleanup();
    },
  };
}
