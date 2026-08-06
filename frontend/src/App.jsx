/* Racine applicative : session, routage, mémoire du dernier dossier ouvert. */

import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import AppShell from "./components/AppShell.jsx";
import Login from "./screens/Login.jsx";
import DossierList from "./screens/DossierList.jsx";
import DossierDetail from "./screens/DossierDetail.jsx";
import Import from "./screens/Import.jsx";
import Audit from "./screens/Audit.jsx";
import { useSession } from "./hooks/useSession.jsx";

export default function App() {
  const { user, booting } = useSession();
  const [lastDossierId, setLastDossierId] = useState(null);
  // Incrémenté à chaque changement de dossier : force le rechargement de la
  // liste quand on y revient après une validation ou un rejet.
  const [listVersion, setListVersion] = useState(0);
  const location = useLocation();

  const onDossierChanged = useCallback(() => setListVersion((v) => v + 1), []);

  // La session perdue vide la mémoire de navigation.
  useEffect(() => {
    if (!user) setLastDossierId(null);
  }, [user]);

  if (booting) {
    return (
      <div className="app-booting" role="status" aria-live="polite">
        <div className="app-booting-card">
          <span className="app-booting-mark" aria-hidden="true">W</span>
          <div>
            <p className="app-booting-title">Validation RCC</p>
            <p className="app-booting-copy">Vérification de votre session sécurisée…</p>
          </div>
          <span className="app-booting-spinner" aria-hidden="true" />
        </div>
        <span className="sr-only">Chargement de la session…</span>
      </div>
    );
  }

  if (!user) return <Login />;

  const activeId = location.pathname.startsWith("/validation/")
    ? decodeURIComponent(location.pathname.split("/")[2] || "")
    : null;

  return (
    <AppShell lastDossierId={lastDossierId}>
      <Routes>
        <Route path="/" element={<Navigate to="/dossiers" replace />} />
        <Route
          path="/dossiers"
          element={<DossierList key={listVersion} activeId={activeId} />}
        />
        <Route
          path="/validation/:dossierId"
          element={
            <DetailRoute
              onDossierChanged={onDossierChanged}
              onOpened={setLastDossierId}
            />
          }
        />
        <Route path="/validation" element={<Navigate to="/dossiers" replace />} />
        <Route path="/import" element={<Import onDossierCreated={onDossierChanged} />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="*" element={<Navigate to="/dossiers" replace />} />
      </Routes>
    </AppShell>
  );
}

/** Mémorise le dossier ouvert pour réactiver l'entrée « Validation » du rail. */
function DetailRoute({ onDossierChanged, onOpened }) {
  const { dossierId } = useParams();

  useEffect(() => {
    onOpened(dossierId);
  }, [dossierId, onOpened]);

  return <DossierDetail key={dossierId} onDossierChanged={onDossierChanged} />;
}
