/* Coque applicative : rail latéral, barre d'onglets mobile, menu utilisateur. */

import { useEffect, useRef, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import Icon, { ICONS } from "./Icon.jsx";
import { initials } from "../lib/format.js";
import { useSession } from "../hooks/useSession.jsx";
import { useToasts } from "../hooks/useToasts.jsx";

const NAV = [
  { to: "/dossiers", key: "list", label: "Dossiers", tip: "Dossiers RCC", icon: ICONS.folder },
  { to: "/validation", key: "detail", label: "Validation", tip: "Validation des bilans", icon: ICONS.clipboardCheck },
  { to: "/import", key: "import", label: "Import", tip: "Import & extraction OCR", icon: ICONS.upload },
  { to: "/audit", key: "audit", label: "Audit", tip: "Historique & piste d'audit", icon: ICONS.history },
];

export default function AppShell({ lastDossierId, children }) {
  const { user, logout } = useSession();
  const { toast } = useToasts();
  const navigate = useNavigate();
  const location = useLocation();

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  const buttonRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    const onClick = (event) => {
      if (!menuRef.current?.contains(event.target) && !buttonRef.current?.contains(event.target)) {
        setMenuOpen(false);
      }
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("click", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  const detailPath = lastDossierId ? `/validation/${encodeURIComponent(lastDossierId)}` : null;
  const isDetailActive = location.pathname.startsWith("/validation");

  function navTarget(item) {
    return item.key === "detail" ? detailPath : item.to;
  }

  function onNavClick(event, item) {
    const target = navTarget(item);
    if (!target) {
      event.preventDefault();
      return;
    }
    event.preventDefault();
    navigate(target);
  }

  async function onLogout() {
    setMenuOpen(false);
    await logout();
    toast("Vous êtes déconnecté.", { title: "Session close", type: "info", timeout: 3000 });
  }

  const userInitials = user?.initials || initials(user?.display_name);

  return (
    <div className="app">
      <nav className="rail" aria-label="Navigation principale">
        <div className="rail-logo" aria-hidden="true">W</div>
        <div className="rail-sep" aria-hidden="true" />

        {NAV.map((item) => {
          const target = navTarget(item);
          const disabled = !target;
          const active = item.key === "detail" ? isDetailActive : location.pathname.startsWith(item.to);
          return (
            <button
              key={item.key}
              type="button"
              className="rail-btn"
              disabled={disabled}
              aria-current={active ? "page" : undefined}
              onClick={(event) => onNavClick(event, item)}
            >
              <Icon paths={item.icon} size={18} width={1.8} />
              <span className="rail-label">{item.label}</span>
              <span className="rail-tip" role="tooltip">
                {disabled ? "Ouvrez un dossier depuis la file" : item.tip}
              </span>
            </button>
          );
        })}

        <button
          ref={buttonRef}
          type="button"
          className="rail-user"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((open) => !open)}
        >
          <span>{userInitials}</span>
          <span className="rail-tip" role="tooltip">
            {user ? `${user.display_name} — valideur RCC` : "Session"}
          </span>
        </button>

        {menuOpen ? (
          <div className="user-menu" role="menu" ref={menuRef}>
            <p className="user-menu-name">{user?.display_name}</p>
            <p className="user-menu-role">Valideur RCC</p>
            <button type="button" role="menuitem" className="user-menu-item" onClick={onLogout}>
              Se déconnecter
            </button>
          </div>
        ) : null}
      </nav>

      <main className="main">{children}</main>

      <nav className="tabbar" aria-label="Navigation">
        {NAV.map((item) => {
          const target = navTarget(item);
          const active = item.key === "detail" ? isDetailActive : location.pathname.startsWith(item.to);
          return (
            <button
              key={item.key}
              type="button"
              className="tabbar-btn"
              disabled={!target}
              aria-current={active ? "page" : undefined}
              onClick={(event) => onNavClick(event, item)}
            >
              <Icon paths={item.icon} size={18} width={1.8} />
              {item.label}
            </button>
          );
        })}
      </nav>
    </div>
  );
}

/** Barre de titre commune aux écrans pleine largeur. */
export function TopBar({ title, subtitle, children }) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <h1>{title}</h1>
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
      {children ? <div className="topbar-actions">{children}</div> : null}
    </header>
  );
}

export { NavLink };
