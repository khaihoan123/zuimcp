import datetime

from fastapi import HTTPException, Request
from sqlalchemy import text

from core.db import SessionLocal


def get_current_user_id(request: Request) -> str:
    """Identifies the logged-in dashboard user from Better Auth's session
    cookie. Used by REST endpoints the browser calls directly (connections
    CRUD, etc.) — NOT by MCP tools, which authenticate via OAuth bearer
    tokens instead (see services/email/service.py's _get_user_id)."""
    cookie = request.cookies.get("better-auth.session_token")
    if not cookie:
        raise HTTPException(status_code=401, detail="Not logged in")

    # Better Auth's cookie value is "<token>.<signature>" — the session
    # table's `token` column stores just the token part.
    raw_token = cookie.split(".")[0]

    db = SessionLocal()
    try:
        row = db.execute(
            text('SELECT "userId", "expiresAt" FROM session WHERE token = :token'),
            {"token": raw_token},
        ).first()
    finally:
        db.close()

    if not row:
        raise HTTPException(status_code=401, detail="Session not found")

    user_id, expires_at = row
    if expires_at < datetime.datetime.now(datetime.timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")

    return user_id
