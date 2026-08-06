/* Écran « Import & extraction OCR ». */

import { useCallback } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../lib/api.js";
import { TopBar } from "../components/AppShell.jsx";
import Banner from "../components/detail/Banner.jsx";
import ImportPanel from "../components/ImportPanel.jsx";
import { useToasts } from "../hooks/useToasts.jsx";

export default function Import({ onDossierCreated }) {
  const navigate = useNavigate();
  const { toast } = useToasts();

  const onCompleted = useCallback(
    async (jobId) => {
      try {
        const created = await api.dossiers.create({ job_id: jobId });
        const dossierId = created.dossier.id;
        toast(`Dossier ${dossierId} créé et placé dans la file de validation.`, {
          title: "Extraction terminée",
          type: "ok",
        });
        onDossierCreated?.(created.dossier);
        return {
          dossierId,
          onOpen: () => navigate(`/validation/${encodeURIComponent(dossierId)}`),
        };
      } catch (err) {
        if (!err.isAuth) {
          toast(err.message, { title: "Création du dossier impossible", type: "bad" });
        }
        return null;
      }
    },
    [navigate, onDossierCreated, toast]
  );

  return (
    <section className="view is-entering">
      <TopBar
        title="Import & extraction OCR"
        subtitle="Dépôt des liasses fiscales et suivi de l'extraction des postes RCC."
      />

      <div className="scroll">
        <div className="wrap wrap-narrow">
          <ImportPanel onCompleted={onCompleted} />

          <div style={{ marginTop: 18 }}>
            <Banner
              tone="neutral"
              title="Comment se déroule l'extraction"
              text="Chaque page est classée puis lue par le modèle de vision, les postes RCC sont résolus, puis les contrôles comptables sont exécutés. Un dossier est créé automatiquement en fin de traitement et rejoint la file de validation."
            />
          </div>
        </div>
      </div>
    </section>
  );
}
