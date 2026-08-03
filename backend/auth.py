"""
Password hashing, JWT issuing, and the FastAPI auth dependencies.

The token travels in an httpOnly cookie (plan §1.3 decision 3) so it is sent
automatically on page navigation *and* on the WebSocket handshake — no manual
Authorization header plumbing in the frontend.

We call `bcrypt` directly instead of going through passlib: passlib 1.7.4 reads
`bcrypt.__about__`, which was removed in bcrypt 4.1+, and this machine has
bcrypt 5.0.0.
"""

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Response, WebSocket, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import User

# bcrypt hashes at most 72 bytes and (in 5.x) raises on longer input.
_BCRYPT_MAX_BYTES = 72


def _clip(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_clip(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_clip(password), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None


def set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expire_hours * 3600,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.cookie_name, path="/")


_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="Belum login atau sesi kedaluwarsa"
)


def _user_from_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    return user


def get_current_user(
    db: Session = Depends(get_db),
    fod_token: str | None = Cookie(default=None, alias=settings.cookie_name),
) -> User:
    user = _user_from_token(db, fod_token)
    if user is None:
        raise _UNAUTHORIZED
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Hanya administrator yang boleh mengakses ini",
        )
    return user


def authenticate_ws(ws: WebSocket, db: Session) -> User | None:
    """Validate the handshake cookie. Caller closes with 4401 when None."""
    return _user_from_token(db, ws.cookies.get(settings.cookie_name))
