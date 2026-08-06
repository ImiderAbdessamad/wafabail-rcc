/* Groupes de postes RCC et lignes de saisie.

   Chaque ligne est mémoïsée et détient l'état de sa propre saisie : taper dans
   un champ ne provoque pas le rendu des dix-neuf autres. */

import { memo, useEffect, useRef, useState } from "react";
import { FORMULAS, GROUPS, LOCKED_CODES, TAG_CODES, fieldState } from "../../lib/fields.js";
import { formatAmount, formatDateTime, parseAmount } from "../../lib/format.js";
import Icon, { ICONS } from "../Icon.jsx";
import { Badge } from "../States.jsx";

export default function FieldGroups({
  fields,
  overrides,
  activeCode,
  savingCodes,
  targetCode,
  onEdit,
  onCommit,
  onVerify,
  onFocusField,
  registerRow,
}) {
  const byCode = new Map(fields.map((field) => [field.code, field]));

  return (
    <>
      {GROUPS.map((group) => {
        let toCheck = 0;
        let blocked = 0;

        const rows = group.codes.map((code) => {
          const field = byCode.get(code) || {
            code, label: code, value: null, status: "missing", confidence: 0, evidence: [],
          };
          const override = overrides.get(code) || null;
          const meta = fieldState(field, override);
          if (meta.state === "low") toCheck += 1;
          if (meta.state === "conflict" || meta.state === "missing") blocked += 1;
          return { field, override, meta };
        });

        const stateLabel = blocked
          ? `${blocked} poste(s) à reprendre`
          : toCheck
            ? `${toCheck} poste(s) à vérifier`
            : "Contrôlé";
        const stateStyle = blocked
          ? { color: "var(--bad-text)", background: "var(--bad-soft)" }
          : toCheck
            ? { color: "var(--warn-text)", background: "var(--warn-soft-2)" }
            : { color: "var(--ok-text)", background: "var(--ok-soft)" };

        return (
          <section className="group" key={group.key} aria-label={group.title}>
            <div className="group-head">
              <span className="group-letter" aria-hidden="true">{group.letter}</span>
              <h3 className="group-title">{group.title}</h3>
              <span className="group-subtitle">{group.subtitle}</span>
              <span className="group-state" style={stateStyle}>{stateLabel}</span>
            </div>

            <div className="group-body">
              {rows.map(({ field, override, meta }) => (
                <FieldRow
                  key={field.code}
                  field={field}
                  override={override}
                  meta={meta}
                  active={activeCode === field.code}
                  saving={savingCodes.has(field.code)}
                  isTarget={targetCode === field.code}
                  onEdit={onEdit}
                  onCommit={onCommit}
                  onVerify={onVerify}
                  onFocusField={onFocusField}
                  registerRow={registerRow}
                />
              ))}
            </div>
          </section>
        );
      })}
    </>
  );
}

const FieldRow = memo(function FieldRow({
  field, override, meta, active, saving, isTarget,
  onEdit, onCommit, onVerify, onFocusField, registerRow,
}) {
  const code = field.code;
  const isTag = TAG_CODES.has(code);
  const locked = LOCKED_CODES.has(code);
  const serverValue = override ? override.corrected_value : field.value;

  const [draft, setDraft] = useState(serverValue != null ? formatAmount(serverValue) : "");
  const [showTip, setShowTip] = useState(false);
  const dirty = useRef(false);
  const rowRef = useRef(null);

  // Le serveur fait autorité, sauf pendant que l'analyste tape.
  useEffect(() => {
    if (dirty.current) return;
    setDraft(serverValue != null ? formatAmount(serverValue) : "");
  }, [serverValue]);

  useEffect(() => {
    registerRow(code, rowRef.current);
    return () => registerRow(code, null);
  }, [code, registerRow]);

  const inputId = `f-${code}`;
  const state = saving ? "saving" : meta.state;

  return (
    <div
      ref={rowRef}
      className={`frow${isTarget ? " is-target" : ""}`}
      data-code={code}
      data-state={state}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      <div className="frow-main">
        <div className="frow-head">
          <label className="frow-label" htmlFor={isTag ? undefined : inputId}>
            {field.label}
          </label>

          {override ? (
            <span
              className={`frow-flag frow-flag-edit hint`}
              data-hint={`${override.verified ? "Valeur OCR confirmée" : "Corrigé"} par ${override.edited_by}`}
            >
              <Icon paths={override.verified ? ICONS.check : ICONS.pencil} size={11} width={2.2} />
            </span>
          ) : null}

          {locked ? (
            <span className="frow-flag frow-flag-lock hint" data-hint="Calculé par le pipeline — non modifiable">
              <Icon paths={ICONS.lock} size={11} width={2} />
            </span>
          ) : null}

          {field.evidence?.length ? (
            <button
              type="button"
              className={`locate${active ? " is-active" : ""}`}
              aria-label={`Localiser ${field.label} dans le document`}
              onClick={() => onFocusField(code, { openDoc: true })}
            >
              <Icon paths={ICONS.highlight} size={9} width={2.4} />
              voir
            </button>
          ) : null}

          {meta.state === "low" ? (
            <button
              type="button"
              className="locate hint"
              data-hint="Confirmer la valeur lue par l'OCR sans la modifier"
              aria-label={`Marquer ${field.label} comme vérifié`}
              onClick={() => onVerify(code)}
            >
              <Icon paths={ICONS.check} size={9} width={2.6} />
              marquer vérifié
            </button>
          ) : null}

          {field.note ? <Badge className="badge-xs">{field.note}</Badge> : null}
        </div>

        <p className="frow-formula">{FORMULAS[code] || ""}</p>
      </div>

      {field.value_n1 != null ? <PreviousYear field={field} current={serverValue} /> : null}

      {isTag ? (
        <div className="frow-input" style={{ justifyContent: "flex-end" }}>
          <TypeResultatTag value={field.note} />
        </div>
      ) : (
        <div className="frow-input">
          {saving ? <span className="frow-save-spinner" aria-hidden="true" /> : null}
          <div className="frow-input-wrap">
            <input
              id={inputId}
              type="text"
              inputMode="decimal"
              autoComplete="off"
              value={draft}
              readOnly={locked}
              placeholder={meta.state === "missing" ? "non lu" : ""}
              aria-describedby={`${inputId}-conf`}
              aria-invalid={meta.state === "conflict" || meta.state === "missing" ? "true" : undefined}
              onChange={(event) => {
                dirty.current = true;
                setDraft(event.target.value);
                onEdit(code, parseAmount(event.target.value));
              }}
              onFocus={() => onFocusField(code, { openDoc: false, scroll: false })}
              onBlur={() => {
                dirty.current = false;
                const parsed = parseAmount(draft);
                if (parsed != null) setDraft(formatAmount(parsed));
                onCommit(code);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  event.currentTarget.blur();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  dirty.current = false;
                  setDraft(serverValue != null ? formatAmount(serverValue) : "");
                  onEdit(code, null, { cancel: true });
                }
              }}
            />
            <span className="frow-unit" aria-hidden="true">MAD</span>
          </div>
          <span id={`${inputId}-conf`} className={`frow-conf ${meta.confClass}`}>
            {meta.confLabel}
          </span>
        </div>
      )}

      {showTip && override ? (
        <div className="audit-tip" role="note">
          <p className="audit-tip-head">Piste d'audit</p>
          <p>
            Valeur OCR d'origine : <b>{`${formatAmount(override.original_value)} MAD`}</b>
          </p>
          <p className="audit-tip-meta">
            {`${override.verified ? "Confirmée" : "Corrigée"} par ${override.edited_by} le ${formatDateTime(override.edited_at)}`}
          </p>
        </div>
      ) : null}
    </div>
  );
});

function PreviousYear({ field, current }) {
  const previous = field.value_n1;
  const variation =
    current != null && previous !== 0 ? ((current - previous) / Math.abs(previous)) * 100 : null;
  const color =
    variation == null ? "var(--muted-2)"
      : Math.abs(variation) > 40 ? "var(--bad-text)"
      : Math.abs(variation) > 20 ? "var(--warn-text)"
      : variation >= 0 ? "var(--ok-text)" : "var(--muted)";

  return (
    <div className="frow-prev hint" data-hint="Exercice N-1 extrait de la liasse">
      <div className="frow-prev-val">{formatAmount(previous)}</div>
      <div className="frow-prev-var" style={{ color }}>
        {variation == null
          ? ""
          : `${variation >= 0 ? "+" : ""}${variation.toFixed(1).replace(".", ",")} %`}
      </div>
    </div>
  );
}

function TypeResultatTag({ value }) {
  const label = value || "Non déterminé";
  const tone = label === "Bénéficiaire" ? "ok" : label === "Déficitaire" ? "bad" : "neutral";
  return (
    <Badge tone={tone} style={{ fontSize: "12.5px", padding: "7px 16px" }}>
      {label}
    </Badge>
  );
}
