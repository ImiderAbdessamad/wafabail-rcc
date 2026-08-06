/* Panneau « Conformité RCC » — restitution du calcul serveur, sans recalcul. */

const SEVERITY_COLOR = {
  blocked: "var(--bad-text)",
  warn: "var(--warn-text)",
  ok: "var(--ok-text)",
};

export default function CompliancePanel({ compliance, onFocusField }) {
  const color = compliance.blockers
    ? "var(--bad-text)"
    : compliance.warnings
      ? "var(--warn-text)"
      : "var(--ok-text)";

  return (
    <div className="panel panel-pad" style={{ marginBottom: 14 }}>
      <div className="comp-head">
        <div>
          <h2 className="comp-title">Conformité RCC du dossier</h2>
          <p className="comp-sub">{compliance.summary}</p>
        </div>
        <div className="comp-score">
          <div className="comp-pct" style={{ color }}>{`${compliance.pct} %`}</div>
          <div className="comp-ratio">
            {`${compliance.rules_ok}/${compliance.rules_total} règles conformes`}
          </div>
        </div>
      </div>

      {compliance.sections.map((section) => (
        <div className="comp-section" key={section.title}>
          <div className="comp-section-head">
            <span className="comp-section-title">{section.title}</span>
            <span className="comp-section-line" aria-hidden="true" />
            <span
              className="comp-section-state"
              style={{ color: SEVERITY_COLOR[section.severity] }}
            >
              {section.state}
            </span>
          </div>

          {section.rules.map((rule, index) => {
            const canFocus = !rule.ok && rule.affected_fields?.length > 0;
            const markStyle = rule.ok
              ? { background: "var(--ok-soft)", color: "var(--ok-text)" }
              : rule.blocking
                ? { background: "var(--bad-soft)", color: "var(--bad-text)" }
                : { background: "var(--warn-soft-2)", color: "var(--warn-text)" };

            return (
              <div className="comp-rule" key={`${section.title}-${index}`}>
                <span className="comp-mark" style={markStyle}>
                  {rule.ok ? "✓" : rule.blocking ? "!" : "~"}
                </span>

                {canFocus ? (
                  <button
                    type="button"
                    className="comp-label"
                    style={{ textAlign: "left", textDecoration: "underline", textUnderlineOffset: 2 }}
                    onClick={() => onFocusField(rule.affected_fields[0], { openDoc: true })}
                  >
                    {rule.label}
                  </button>
                ) : (
                  <span className="comp-label">{rule.label}</span>
                )}

                {!rule.ok && rule.blocking ? (
                  <span className="comp-blocking">bloquant</span>
                ) : null}

                <span className="comp-detail" title={rule.detail}>{rule.detail}</span>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
