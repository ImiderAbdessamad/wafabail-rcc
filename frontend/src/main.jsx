import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App.jsx";
import { SessionProvider } from "./hooks/useSession.jsx";
import { ToastProvider } from "./hooks/useToasts.jsx";
import "./styles.css";

/*
 * Routage par hash : FastAPI monte StaticFiles sur "/", il n'y a donc pas de
 * repli côté serveur pour les chemins d'application. Le hash garde les liens
 * profonds fonctionnels sans toucher à main.py.
 */
createRoot(document.getElementById("root")).render(
  <StrictMode>
    <HashRouter>
      <ToastProvider>
        <SessionProvider>
          <App />
        </SessionProvider>
      </ToastProvider>
    </HashRouter>
  </StrictMode>
);
