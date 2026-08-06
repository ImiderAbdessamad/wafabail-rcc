/* Bandeau d'information contextuel (équilibre comptable, liasse manquante…). */

import Icon, { ICONS } from "../Icon.jsx";

const TONE_ICON = {
  ok: ICONS.check,
  bad: ICONS.warning,
  warn: ICONS.warning,
  neutral: ICONS.info,
};

export default function Banner({ tone = "neutral", title, text, children }) {
  return (
    <div className={`banner banner-${tone}`}>
      <Icon paths={TONE_ICON[tone]} size={17} width={2} />
      <div className="banner-body">
        <p className="banner-title">{title}</p>
        {text ? <p className="banner-text">{text}</p> : null}
      </div>
      {children}
    </div>
  );
}
