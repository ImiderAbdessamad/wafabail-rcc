/* Écran de connexion. */

import { useEffect, useRef, useState } from "react";
import { useSession } from "../hooks/useSession.jsx";
import { useToasts } from "../hooks/useToasts.jsx";

export default function Login() {
  const { login, expiredMessage } = useSession();
  const { toast } = useToasts();

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(expiredMessage);
  const [invalid, setInvalid] = useState(null); // "user" | "password"
  const [busy, setBusy] = useState(false);

  const userRef = useRef(null);
  const passwordRef = useRef(null);

  useEffect(() => {
    userRef.current?.focus();
  }, []);

  useEffect(() => {
    if (expiredMessage) setError(expiredMessage);
  }, [expiredMessage]);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setInvalid(null);

    if (!username.trim() || !password) {
      setError("Renseignez votre identifiant et votre mot de passe.");
      const field = username.trim() ? "password" : "user";
      setInvalid(field);
      (field === "user" ? userRef : passwordRef).current?.focus();
      return;
    }

    setBusy(true);
    try {
      const me = await login(username.trim(), password);
      setPassword("");
      toast(`Bienvenue, ${me.display_name}.`, { title: "Connecté", type: "ok", timeout: 3600 });
    } catch (err) {
      setError(err.message);
      setInvalid("password");
      passwordRef.current?.focus();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login-brand">
        <div className="login-logo">
          <span className="login-logo-mark" aria-hidden="true">E</span>
          <span className="login-logo-text">
            EKIP<span className="muted"> · Validation RCC</span>
          </span>
        </div>

        <div className="login-pitch">
          <p className="login-eyebrow">Risque de contrepartie</p>
          <h1>Contrôle humain des bilans extraits par OCR.</h1>
          <p className="login-lede">
            Vérifiez les postes financiers extraits de la liasse fiscale, corrigez les
            valeurs mal reconnues et validez la cohérence comptable avant enrichissement
            du modèle EKIP.
          </p>
        </div>

        <dl className="login-stats">
          <div>
            <dt>20</dt>
            <dd>postes RCC contrôlés</dd>
          </div>
          <div>
            <dt>8</dt>
            <dd>contrôles comptables</dd>
          </div>
          <div>
            <dt>100 %</dt>
            <dd>piste d'audit</dd>
          </div>
        </dl>

        <span className="login-ring login-ring-a" aria-hidden="true" />
        <span className="login-ring login-ring-b" aria-hidden="true" />
      </div>

      <div className="login-panel">
        <form className="login-form" onSubmit={onSubmit} noValidate>
          <h2>Se connecter</h2>
          <p className="login-sub">Accès réservé aux valideurs RCC habilités.</p>

          <div className="field-group">
            <label htmlFor="loginUser">Identifiant</label>
            <input
              id="loginUser"
              ref={userRef}
              type="email"
              name="username"
              autoComplete="username"
              placeholder="prenom.nom@wafabail.ma"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              aria-describedby="loginError"
              aria-invalid={invalid === "user" ? "true" : undefined}
            />
          </div>

          <div className="field-group">
            <label htmlFor="loginPwd">Mot de passe</label>
            <input
              id="loginPwd"
              ref={passwordRef}
              type="password"
              name="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              aria-describedby="loginError"
              aria-invalid={invalid === "password" ? "true" : undefined}
            />
          </div>

          <p id="loginError" className="form-error" role="alert" hidden={!error}>
            {error}
          </p>

          <button type="submit" className={`btn btn-primary btn-block${busy ? " is-busy" : ""}`} disabled={busy}>
            <span className="btn-label">Se connecter</span>
            <span className="btn-spinner" aria-hidden="true" />
          </button>

          <p className="login-note">
            Toute action de validation est horodatée et tracée au nom de l'utilisateur
            connecté.
          </p>
        </form>
      </div>
    </div>
  );
}
