/* Modales de l'écran de validation : rejet, arbitrage, confirmation. */

import { useState } from "react";
import { REJECT_MOTIFS } from "../../lib/fields.js";
import Icon, { ICONS } from "../Icon.jsx";
import Modal, { BusyButton } from "../Modal.jsx";

export function RejectModal({ open, dossier, onClose, onConfirm }) {
  const [motif, setMotif] = useState(REJECT_MOTIFS[0]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    const ok = await onConfirm({ motif, comment: comment.trim() || null });
    setBusy(false);
    if (ok) onClose();
  }

  return (
    <Modal open={open} onClose={onClose} labelledBy="rejectTitle">
      <h2 className="modal-title" id="rejectTitle">{`Rejeter le dossier ${dossier.id}`}</h2>
      <p className="modal-sub">
        Le dossier retourne au gestionnaire avec le motif choisi. Aucune donnée n'est
        transmise au modèle EKIP.
      </p>

      <fieldset className="radio-list">
        {REJECT_MOTIFS.map((label, index) => (
          <label className={`radio-item${motif === label ? " is-checked" : ""}`} key={label}>
            <input
              type="radio"
              name="motif"
              value={label}
              checked={motif === label}
              data-autofocus={index === 0 ? "" : undefined}
              onChange={() => setMotif(label)}
            />
            {label}
          </label>
        ))}
      </fieldset>

      <textarea
        placeholder="Commentaire à l'attention du gestionnaire (facultatif)…"
        aria-label="Commentaire de rejet"
        value={comment}
        onChange={(event) => setComment(event.target.value)}
      />

      <div className="modal-actions">
        <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
          Annuler
        </button>
        <BusyButton busy={busy} className="btn btn-danger-solid" onClick={confirm}>
          Confirmer le rejet
        </BusyButton>
      </div>
    </Modal>
  );
}

export function EscalateModal({ open, compliance, onClose, onConfirm }) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  async function confirm() {
    setBusy(true);
    const ok = await onConfirm({ comment: comment.trim() || null });
    setBusy(false);
    if (ok) onClose();
  }

  return (
    <Modal open={open} onClose={onClose} labelledBy="escalateTitle">
      <h2 className="modal-title" id="escalateTitle">Demander un arbitrage superviseur</h2>
      <p className="modal-sub">
        Le dossier reste en attente et sort de votre file. Un superviseur RCC tranche sur
        les points signalés.
      </p>

      <div className="modal-recap">
        <p className="modal-recap-title">Points transmis automatiquement</p>
        <ul>
          <li>{`· Règles de conformité bloquantes : ${compliance.blockers}`}</li>
          <li>{`· Postes en incohérence d'extraction : ${compliance.conflicting_fields.length}`}</li>
          <li>{`· Postes sous le seuil de confiance : ${compliance.low_confidence_fields.length}`}</li>
          <li>{`· Postes non lus : ${compliance.missing_fields.length}`}</li>
        </ul>
      </div>

      <textarea
        placeholder="Question posée au superviseur…"
        aria-label="Question au superviseur"
        data-autofocus=""
        value={comment}
        onChange={(event) => setComment(event.target.value)}
      />

      <div className="modal-actions">
        <button type="button" className="btn btn-ghost" onClick={onClose} disabled={busy}>
          Annuler
        </button>
        <BusyButton busy={busy} className="btn btn-dark" onClick={confirm}>
          Transmettre au superviseur
        </BusyButton>
      </div>
    </Modal>
  );
}

export function ValidatedModal({ open, dossier, compliance, onClose }) {
  return (
    <Modal open={open} onClose={onClose} labelledBy="validatedTitle" className="modal-center">
      <div className="modal-check">
        <Icon paths={ICONS.check} size={26} width={2.6} style={{ stroke: "var(--ok)" }} />
      </div>
      <h2 className="modal-title" id="validatedTitle">Dossier validé</h2>
      <p className="modal-sub">
        Les postes RCC contrôlés du dossier <b className="mono">{dossier.id}</b> ont été
        transmis au modèle EKIP. La piste d'audit des corrections est archivée.
      </p>

      <dl className="modal-stats">
        <div>
          <dt>{dossier.overrides.length}</dt>
          <dd>postes contrôlés</dd>
        </div>
        <div>
          <dt style={{ color: "var(--ok-text)" }}>
            {`${compliance.rules_ok}/${compliance.rules_total}`}
          </dt>
          <dd>règles conformes</dd>
        </div>
        <div>
          <dt style={{ fontSize: 13 }}>{dossier.decided_by || "—"}</dt>
          <dd>valideur</dd>
        </div>
      </dl>

      <button
        type="button"
        className="btn btn-primary btn-block"
        data-autofocus=""
        style={{ marginTop: 18 }}
        onClick={onClose}
      >
        Retour aux dossiers
      </button>
    </Modal>
  );
}
