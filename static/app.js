/*
 * Validation RCC — point d'entrée.
 * Coque applicative : session, routage par hash, montage des écrans.
 */

import * as api from "./js/api.js";
import { $, $$, announce, initials, schedule, toast } from "./js/util.js";
import { createListView } from "./js/views/list.js";
import { createDetailView } from "./js/views/detail.js";
import { createImportView } from "./js/views/import.js";
import { createAuditView } from "./js/views/audit.js";

const VIEWS = ["list", "detail", "import", "audit"];

const screenLogin = $("#screenLogin");
const screenApp = $("#screenApp");
const mainRegion = $("#mainRegion");

let currentUser = null;
let currentView = null;
let views = null;

/* ============================================================== routage === */

function parseHash() {
  const raw = (location.hash || "").replace(/^#\/?/, "");
  const [name, param] = raw.split("/");
  if (!VIEWS.includes(name)) return { name: "list", param: null };
  return { name, param: param ? decodeURIComponent(param) : null };
}

function setHash(view, param) {
  const next = `#/${view}${param ? `/${encodeURIComponent(param)}` : ""}`;
  if (location.hash !== next) location.hash = next;
  else handleRoute();
}

function setActiveNav(view) {
  for (const button of $$("[data-nav]")) {
    const isActive = button.dataset.nav === view;
    if (isActive) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}

function showView(name) {
  for (const section of $$(".view")) {
    const isTarget = section.dataset.view === name;
    section.hidden = !isTarget;
    section.classList.toggle("is-entering", isTarget);
  }
  setActiveNav(name);
  currentView = name;
}

async function handleRoute() {
  if (!currentUser) return;
  const { name, param } = parseHash();

  // On quitte le détail : les saisies en attente partent au serveur.
  if (currentView === "detail" && name !== "detail") views.detail.flushPending();
  if (currentView === "import" && name !== "import") views.import.hide();

  if (name === "detail" && !param) {
    setHash("list");
    return;
  }

  showView(name);

  switch (name) {
    case "list":
      await views.list.show();
      break;
    case "detail":
      enableDetailNav(true);
      await views.detail.open(param);
      views.list.setActive(param);
      break;
    case "import":
      views.import.show();
      break;
    case "audit":
      await views.audit.show();
      break;
  }
  announce(`Écran ${name} affiché.`);
  mainRegion.scrollTop = 0;
}

function enableDetailNav(enabled) {
  for (const button of $$('[data-nav="detail"]')) {
    button.disabled = !enabled;
    if (enabled) button.removeAttribute("data-disabled-reason");
  }
}

/* ============================================================== session === */

function showLogin({ message } = {}) {
  currentUser = null;
  screenApp.hidden = true;
  screenLogin.hidden = false;
  document.title = "Connexion — Validation RCC";
  if (message) {
    const errorNode = $("#loginError");
    errorNode.textContent = message;
    errorNode.hidden = false;
  }
  schedule(() => $("#loginUser").focus());
}

async function showApp(user) {
  currentUser = user;
  screenLogin.hidden = true;
  screenApp.hidden = false;
  document.title = "Validation RCC — Wafabail";

  $("#userInitials").textContent = user.initials || initials(user.display_name);
  $("#userTip").textContent = `${user.display_name} — valideur RCC`;
  $("#userMenuName").textContent = user.display_name;

  if (!views) views = buildViews();
  if (!location.hash) setHash("list");
  else await handleRoute();
}

function buildViews() {
  const list = createListView({
    onOpenDossier: (id) => setHash("detail", id),
    onNavigate: (view) => setHash(view),
  });

  const detail = createDetailView({
    onBack: () => setHash("list"),
    onDossierChanged: (dossier) => list.patchLocal(dossier),
  });

  const importView = createImportView({
    onOpenDossier: (id) => setHash("detail", id),
    onDossierCreated: () => list.refresh(),
  });

  const audit = createAuditView({
    onOpenDossier: (id) => setHash("detail", id),
  });

  return { list, detail, import: importView, audit };
}

/* ========================================================== connexion === */

const loginForm = $("#loginForm");
const loginSubmit = $("#loginSubmit");
const loginError = $("#loginError");

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const username = $("#loginUser").value.trim();
  const password = $("#loginPwd").value;

  loginError.hidden = true;
  $("#loginUser").removeAttribute("aria-invalid");
  $("#loginPwd").removeAttribute("aria-invalid");

  if (!username || !password) {
    loginError.textContent = "Renseignez votre identifiant et votre mot de passe.";
    loginError.hidden = false;
    (username ? $("#loginPwd") : $("#loginUser")).setAttribute("aria-invalid", "true");
    (username ? $("#loginPwd") : $("#loginUser")).focus();
    return;
  }

  loginSubmit.classList.add("is-busy");
  loginSubmit.disabled = true;
  try {
    const user = await api.auth.login(username, password);
    $("#loginPwd").value = "";
    await showApp(user);
    toast(`Bienvenue, ${user.display_name}.`, { title: "Connecté", type: "ok", timeout: 3600 });
  } catch (error) {
    loginError.textContent = error.message;
    loginError.hidden = false;
    $("#loginPwd").setAttribute("aria-invalid", "true");
    $("#loginPwd").focus();
  } finally {
    loginSubmit.classList.remove("is-busy");
    loginSubmit.disabled = false;
  }
});

/* ============================================================ menu user === */

const userBtn = $("#userBtn");
const userMenu = $("#userMenu");

function closeUserMenu() {
  userMenu.hidden = true;
  userBtn.setAttribute("aria-expanded", "false");
}

userBtn.addEventListener("click", (event) => {
  event.stopPropagation();
  const open = userMenu.hidden;
  userMenu.hidden = !open;
  userBtn.setAttribute("aria-expanded", String(open));
  if (open) $("#logoutBtn").focus();
});

document.addEventListener("click", (event) => {
  if (!userMenu.hidden && !userMenu.contains(event.target)) closeUserMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !userMenu.hidden) {
    closeUserMenu();
    userBtn.focus();
  }
});

$("#logoutBtn").addEventListener("click", async () => {
  closeUserMenu();
  try { await api.auth.logout(); } catch { /* la session est de toute façon abandonnée */ }
  views?.import.hide();
  views?.detail.destroy();
  location.hash = "";
  showLogin();
  toast("Vous êtes déconnecté.", { title: "Session close", type: "info", timeout: 3000 });
});

/* ========================================================== navigation === */

for (const button of $$("[data-nav]")) {
  button.addEventListener("click", () => {
    if (button.disabled) return;
    const target = button.dataset.nav;
    if (target === "detail") {
      const id = views?.detail.currentId;
      if (id) setHash("detail", id);
      return;
    }
    setHash(target);
  });
}

window.addEventListener("hashchange", handleRoute);

// Enregistre les corrections en attente si l'onglet se ferme.
window.addEventListener("beforeunload", () => views?.detail.flushPending());
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") views?.detail.flushPending();
});

/* ============================================================== amorçage === */

api.setUnauthorizedHandler(() => {
  if (!currentUser) return;
  currentUser = null;
  views?.import.hide();
  views?.detail.destroy();
  showLogin({ message: "Votre session a expiré. Reconnectez-vous pour continuer." });
});

(async function bootstrap() {
  try {
    const user = await api.auth.me();
    await showApp(user);
  } catch {
    showLogin();
  }
})();
