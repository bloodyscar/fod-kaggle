"""User CRUD + FOD class severity weights. Admin only, enforced server-side."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from auth import get_current_user, hash_password, require_admin
from database import get_db
from models import FodClass, User
from schemas import FodClassOut, FodClassUpdate, UserCreate, UserOut, UserUpdate
from store import invalidate_severity_cache

router = APIRouter(tags=["users"])


# --------------------------------------------------------------------- users -
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username sudah dipakai"
        )
    user = User(
        username=payload.username,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=payload.is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")

    # Don't let an admin lock themselves out mid-session.
    if user.id == admin.id:
        if payload.is_active is False:
            raise HTTPException(
                status_code=400, detail="Tidak boleh menonaktifkan akun sendiri"
            )
        if payload.role is not None and payload.role != "admin":
            raise HTTPException(
                status_code=400, detail="Tidak boleh menurunkan peran akun sendiri"
            )

    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.password:
        user.password_hash = hash_password(payload.password)
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Tidak boleh menghapus akun sendiri")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Pengguna tidak ditemukan")
    db.delete(user)
    db.commit()


# --------------------------------------------------------------- fod classes -
@router.get("/fod-classes", response_model=list[FodClassOut])
def list_fod_classes(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    # Readable by anyone logged in — the detections table renders severity too.
    return db.scalars(select(FodClass).order_by(FodClass.id)).all()


@router.patch("/fod-classes/{class_id}", response_model=FodClassOut)
def update_fod_class(
    class_id: int,
    payload: FodClassUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    row = db.get(FodClass, class_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Kelas FOD tidak ditemukan")
    row.severity_weight = payload.severity_weight
    db.commit()
    db.refresh(row)
    # The live stream caches severity — drop it so the next frame uses the edit.
    invalidate_severity_cache()
    return row
