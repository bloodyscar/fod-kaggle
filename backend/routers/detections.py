"""FOD detection history: list + filters, detail, runway map points, delete."""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user, require_admin
from config import BASE_DIR
from database import get_db
from models import FodDetection, Inspection, RiskAssessment, User
from schemas import DetectionOut, DetectionPage, MapPoint

router = APIRouter(prefix="/detections", tags=["detections"])


def _apply_filters(stmt, *, date_from, date_to, class_id, risk_level, status_filter):
    if date_from:
        stmt = stmt.where(FodDetection.detected_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(FodDetection.detected_at <= datetime.combine(date_to, time.max))
    if class_id is not None:
        stmt = stmt.where(FodDetection.class_id == class_id)
    if risk_level:
        stmt = stmt.where(RiskAssessment.risk_level == risk_level)
    if status_filter:
        stmt = stmt.where(Inspection.status == status_filter)
    return stmt


@router.get("", response_model=DetectionPage)
def list_detections(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    date_from: date | None = None,
    date_to: date | None = None,
    class_id: int | None = None,
    risk_level: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=200),
):
    # Outer joins so a detection still lists even if its risk/inspection row is
    # somehow missing; they're only inner-ish when a filter needs them.
    base = (
        select(FodDetection)
        .outerjoin(RiskAssessment, RiskAssessment.detection_id == FodDetection.id)
        .outerjoin(Inspection, Inspection.detection_id == FodDetection.id)
    )
    base = _apply_filters(
        base,
        date_from=date_from, date_to=date_to, class_id=class_id,
        risk_level=risk_level, status_filter=status_filter,
    )

    count_stmt = (
        select(func.count(FodDetection.id))
        .outerjoin(RiskAssessment, RiskAssessment.detection_id == FodDetection.id)
        .outerjoin(Inspection, Inspection.detection_id == FodDetection.id)
    )
    count_stmt = _apply_filters(
        count_stmt,
        date_from=date_from, date_to=date_to, class_id=class_id,
        risk_level=risk_level, status_filter=status_filter,
    )
    total = db.scalar(count_stmt) or 0

    rows = db.scalars(
        base.options(
            joinedload(FodDetection.risk),
            joinedload(FodDetection.inspection).joinedload(Inspection.handler),
        )
        .order_by(FodDetection.detected_at.desc(), FodDetection.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).unique().all()

    return DetectionPage(
        items=[DetectionOut.model_validate(r) for r in rows],
        total=total,
        page=page,
        per_page=per_page,
        pages=max(1, -(-total // per_page)),   # ceil
    )


# Registered before /{detection_id} so "map" is never parsed as an id.
@router.get("/map", response_model=list[MapPoint])
def detection_map(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    hours: int = Query(default=24, ge=1, le=24 * 30),
):
    since = datetime.now() - timedelta(hours=hours)
    rows = db.execute(
        select(
            FodDetection.id,
            FodDetection.class_name,
            FodDetection.x1, FodDetection.y1, FodDetection.x2, FodDetection.y2,
            FodDetection.detected_at,
            RiskAssessment.risk_level,
            RiskAssessment.risk_score,
        )
        .outerjoin(RiskAssessment, RiskAssessment.detection_id == FodDetection.id)
        .where(FodDetection.detected_at >= since)
        .order_by(FodDetection.detected_at.desc())
        .limit(300)
    ).all()

    return [
        MapPoint(
            id=r.id,
            class_name=r.class_name,
            cx=(r.x1 + r.x2) / 2,
            cy=(r.y1 + r.y2) / 2,
            risk_level=r.risk_level or "Very Low",
            risk_score=r.risk_score or 0,
            detected_at=r.detected_at,
        )
        for r in rows
    ]


@router.get("/{detection_id}/snapshot")
def get_snapshot(
    detection_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Serve the evidence JPEG behind auth.

    Deliberately not a StaticFiles mount: snapshots are operational evidence and
    shouldn't be readable by anyone who guesses a filename.
    """
    row = db.get(FodDetection, detection_id)
    if row is None or not row.image_path:
        raise HTTPException(status_code=404, detail="Snapshot tidak tersedia")

    path = (BASE_DIR / row.image_path).resolve()
    # Defence in depth: never serve outside storage/, whatever is in the column.
    if not str(path).startswith(str((BASE_DIR / "storage").resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail="Snapshot tidak tersedia")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/{detection_id}", response_model=DetectionOut)
def get_detection(
    detection_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = db.scalar(
        select(FodDetection)
        .options(
            joinedload(FodDetection.risk),
            joinedload(FodDetection.inspection).joinedload(Inspection.handler),
        )
        .where(FodDetection.id == detection_id)
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Deteksi tidak ditemukan")
    return row


@router.delete("/{detection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_detection(
    detection_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    row = db.get(FodDetection, detection_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Deteksi tidak ditemukan")

    # Remove the evidence JPEG too, otherwise storage/ grows forever.
    if row.image_path:
        snapshot = BASE_DIR / row.image_path
        try:
            snapshot.unlink(missing_ok=True)
        except OSError:
            pass   # a locked/missing file must not block the delete

    db.delete(row)     # risk_assessments + inspections cascade
    db.commit()
