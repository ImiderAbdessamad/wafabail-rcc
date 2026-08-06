"""Authentification par session — rôle unique « valideur RCC ».

Volontairement minimal : un seul compte analyste configuré par variables
d'environnement, jeton opaque en cookie HttpOnly. Pas de RBAC à ce stade.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException, Response

from app.config import (
    ANALYST_DISPLAY_NAME,
    ANALYST_PASSWORD,
    ANALYST_USERNAME,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    SESSION_TTL_HOURS,
)
from app.db import cursor
from app.schemas.dossier import SessionUser


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _initials(display_name: str) -> str:
    parts = [p for p in display_name.replace(".", " ").split() if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def verify_credentials(username: str, password: str) -> SessionUser | None:
    """Comparaison à temps constant sur les deux champs."""
    ok_user = hmac.compare_digest(username.strip().lower(), ANALYST_USERNAME.lower())
    ok_pwd = hmac.compare_digest(password, ANALYST_PASSWORD)
    if not (ok_user and ok_pwd):
        return None
    return SessionUser(
        username=ANALYST_USERNAME,
        display_name=ANALYST_DISPLAY_NAME,
        initials=_initials(ANALYST_DISPLAY_NAME),
    )


def create_session(user: SessionUser) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(hours=SESSION_TTL_HOURS)
    with cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO sessions (token, username, display_name, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (token, user.username, user.display_name, _iso(now), _iso(expires)),
        )
    return token, expires


def purge_expired_sessions() -> None:
    with cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE expires_at < ?", (_iso(_now()),))


def resolve_session(token: str | None) -> SessionUser | None:
    if not token:
        return None
    with cursor() as cur:
        cur.execute(
            "SELECT username, display_name, expires_at FROM sessions WHERE token = ?",
            (token,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    try:
        expires = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    if expires <= _now():
        destroy_session(token)
        return None
    return SessionUser(
        username=row["username"],
        display_name=row["display_name"],
        initials=_initials(row["display_name"]),
    )


def destroy_session(token: str | None) -> None:
    if not token:
        return
    with cursor(commit=True) as cur:
        cur.execute("DELETE FROM sessions WHERE token = ?", (token,))


def set_session_cookie(response: Response, token: str, expires: datetime) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=SESSION_COOKIE_SECURE,
        max_age=SESSION_TTL_HOURS * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


async def require_analyst(
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> SessionUser:
    """Dépendance FastAPI — 401 si la session est absente ou expirée."""
    user = resolve_session(session_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Session expirée ou absente.")
    return user
