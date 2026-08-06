"""Authentification analyste (session cookie, rôle unique)."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response

from app.config import SESSION_COOKIE_NAME
from app.schemas.dossier import LoginRequest, SessionUser
from app.services.auth import (
    clear_session_cookie,
    create_session,
    destroy_session,
    purge_expired_sessions,
    require_analyst,
    set_session_cookie,
    verify_credentials,
)

router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post("/login", response_model=SessionUser)
async def login(payload: LoginRequest, response: Response) -> SessionUser:
    user = verify_credentials(payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Identifiant ou mot de passe incorrect.")
    purge_expired_sessions()
    token, expires = create_session(user)
    set_session_cookie(response, token, expires)
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> Response:
    destroy_session(session_token)
    clear_session_cookie(response)
    response.status_code = 204
    return response


@router.get("/me", response_model=SessionUser)
async def me(user: SessionUser = Depends(require_analyst)) -> SessionUser:
    return user
