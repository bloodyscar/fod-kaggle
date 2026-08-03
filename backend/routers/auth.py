"""Login / logout / whoami."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import (
    clear_auth_cookie,
    create_token,
    get_current_user,
    set_auth_cookie,
    verify_password,
)
from database import get_db
from models import User
from schemas import LoginRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))

    # Same message for unknown user, wrong password, and disabled account —
    # don't leak which usernames exist.
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password salah",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Akun ini dinonaktifkan"
        )

    set_auth_cookie(response, create_token(user))
    return user


@router.post("/logout")
def logout(response: Response, _: User = Depends(get_current_user)):
    clear_auth_cookie(response)
    return {"detail": "Berhasil keluar"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
