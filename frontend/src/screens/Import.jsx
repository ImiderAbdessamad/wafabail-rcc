/* Écran « Import & extraction OCR ». */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../lib/api.js";
import { TopBar } from "../components/AppShell.jsx";
import Banner from "../components/detail/Banner.jsx";
import ImportPanel from "../components/ImportPanel.jsx";
import { useToasts } from "../hooks/useToasts.jsx";
import Icon, { ICONS } from "../components/Icon.jsx";

const WORKFLOW = [
  {
    number: "01", title: "Contrôle du document", summary: "Format, taille et lisibilité de la liasse",
    detail: "Le fichier est vérifié avant traitement pour éviter les PDF vides, corrompus ou trop volumineux.",
    output: "Document conforme et prêt pour la lecture",
  },
  {
    number: "02", title: "Lecture intelligente", summary: "Classification des pages et extraction OCR",
    detail: "Chaque page est orientée, classée puis lue avec le modèle le plus adapté à son contenu financier.",
    output: "Valeurs, libellés, pages sources et confiance OCR",
  },
  {
    number: "03", title: "Contrôles RCC", summary: "Résolution des postes et cohérence comptable",
    detail: "Les valeurs candidates sont rapprochées des 20 postes RCC puis soumises aux contrôles arithmétiques.",
    output: "Dossier structuré avec anomalies signalées",
  },
  {
    number: "04", title: "Validation analyste", summary: "Revue des preuves avant transmission",
    detail: "L’analyste compare chaque valeur à sa preuve, corrige si nécessaire et prend la décision finale.",
    output: "Décision traçable et prête pour EKIP",
  },
];

export default function Import({ onDossierCreated }) {
  const navigate = useNavigate();
  const { toast } = useToasts();
  const [engine, setEngine] = useState({ status: "checking", label: "Vérification du moteur OCR" });
  const [activeStep, setActiveStep] = useState(0);
  const selectedStep = WORKFLOW[activeStep];

  const checkEngine = useCallback(async () => {
    setEngine((current) => ({ ...current, status: "checking", label: "Vérification du moteur OCR" }));
    try {
      setEngine(await api.system.ocrHealth());
    } catch {
      setEngine({ status: "offline", label: "Moteur OCR en attente", models_count: 0 });
    }
  }, []);

  useEffect(() => { checkEngine(); }, [checkEngine]);

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
        title="Centre d’import"
        subtitle="Préparez une liasse fiscale pour la validation RCC."
      >
        <div className={`engine-pill is-${engine.status}`} role="status" aria-live="polite">
          <span className="engine-dot" aria-hidden="true" />
          <span><strong>{engine.label}</strong>{engine.status === "online" ? `${engine.models_count || 0} modèle(s) · ${engine.latency_ms} ms` : "Vérifiez l’instance GPU avant un nouvel import"}</span>
          <button type="button" onClick={checkEngine} aria-label="Actualiser l’état du moteur OCR">Actualiser</button>
        </div>
      </TopBar>

      <div className="scroll">
        <div className="wrap import-workspace">
          <section className="import-hero">
            <div>
              <span className="import-eyebrow">Extraction documentaire assistée</span>
              <h2>Transformez une liasse fiscale en dossier RCC contrôlable</h2>
              <p>Le moteur identifie les états financiers, extrait les postes utiles et prépare les preuves nécessaires à une décision rapide et traçable.</p>
            </div>
            <div className="import-hero-metric">
              <span>20</span>
              <small>postes RCC recherchés automatiquement</small>
            </div>
          </section>

          <div className="import-grid">
            <div className="import-primary">
              {engine.status === "offline" ? (
                <Banner tone="warn" title="Moteur OCR non disponible" text="Vous pouvez préparer vos fichiers, mais attendez que l’indicateur passe au vert avant de lancer une extraction." />
              ) : null}
              <ImportPanel onCompleted={onCompleted} onWorkflowStepChange={setActiveStep} />
              <div className="import-checklist">
                <div><Icon paths={ICONS.check} size={16} /><span><strong>Avant de déposer</strong><small>Vérifiez que le document est complet et lisible.</small></span></div>
                <div><Icon paths={ICONS.file} size={16} /><span><strong>Un PDF par société</strong><small>Les annexes peuvent rester dans le même fichier.</small></span></div>
                <div><Icon paths={ICONS.lock} size={16} /><span><strong>Contrôle humain obligatoire</strong><small>Aucune valeur n’est transmise sans validation.</small></span></div>
              </div>
            </div>

            <aside className="workflow-card" aria-label="Parcours d’extraction">
              <div className="workflow-head">
                <span>Parcours automatisé</span>
                <strong>De la liasse à la décision</strong>
              </div>
              <ol>
                {WORKFLOW.map((step, index) => (
                  <li key={step.number} className={`${index === activeStep ? "is-active" : ""}${index < activeStep ? " is-complete" : ""}`}>
                    <button
                      type="button"
                      aria-current={index === activeStep ? "step" : undefined}
                      aria-controls="workflow-step-detail"
                      onClick={() => setActiveStep(index)}
                    >
                      <span className="workflow-number">{index < activeStep ? "✓" : step.number}</span>
                      <span><strong>{step.title}</strong><small>{step.summary}</small></span>
                      <span className="workflow-chevron" aria-hidden="true">›</span>
                    </button>
                  </li>
                ))}
              </ol>
              <div className="workflow-detail" id="workflow-step-detail" aria-live="polite">
                <span className="workflow-detail-label">Étape {selectedStep.number}</span>
                <strong>{selectedStep.title}</strong>
                <p>{selectedStep.detail}</p>
                <div><span>Résultat attendu</span><b>{selectedStep.output}</b></div>
              </div>
              <button type="button" className="workflow-link" onClick={() => navigate("/dossiers")}>Consulter la file de validation <span>→</span></button>
            </aside>
          </div>
        </div>
      </div>
    </section>
  );
}
