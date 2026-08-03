"""Inspection handling — the only table users update by hand (besides users)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from auth import get_current_user
from database import get_db
from models import FodDetection, Inspection, RiskAssessment, User
from schemas import InspectionRow, InspectionUpdate

router = APIRouter(prefix="/inspections", tags=["inspections"])


def _to_row(insp: Inspection, class_name: str, detected_at: datetime,
            risk_level: str | None, risk_score: int | None) -> InspectionRow:
    return InspectionRow(
        id=insp.id,
        detection_id=insp.detection_id,
        status=insp.status,
        handled_by=insp.handled_by,
        handler_name=insp.handler_name,
        notes=insp.notes,
        started_at=insp.started_at,
        completed_at=insp.completed_at,
        response_time_seconds=insp.response_time_seconds,
        created_at=insp.created_at,
        updated_at=insp.updated_at,
        class_name=class_name,
        detected_at=detected_at,
        risk_level=risk_level,
        risk_score=risk_score,
    )


@router.get("", response_model=list[InspectionRow])
def list_inspections(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = (
        select(Inspection, FodDetection, RiskAssessment)
        .join(FodDetection, FodDetection.id == Inspection.detection_id)
        .outerjoin(RiskAssessment, RiskAssessment.detection_id == FodDetection.id)
        .options(joinedload(Inspection.handler))
        .order_by(FodDetection.detected_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Inspection.status == status_filter)

    return [
        _to_row(insp, det.class_name, det.detected_at,
                r.risk_level if r else None, r.risk_score if r else None)
        for insp, det, r in db.execute(stmt).unique().all()
    ]


@router.patch("/{inspection_id}", response_model=InspectionRow)
def update_inspection(
    inspection_id: int,
    payload: InspectionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(status_code=404, detail="Inspeksi tidak ditemukan")

    detection = db.get(FodDetection, insp.detection_id)
    if detection is None:
        raise HTTPException(status_code=409, detail="Deteksi terkait sudah dihapus")

    if payload.notes is not None:
        insp.notes = payload.notes

    if payload.handled_by is not None:
        if db.get(User, payload.handled_by) is None:
            raise HTTPException(status_code=400, detail="Petugas tidak ditemukan")
        insp.handled_by = payload.handled_by

    if payload.status is not None and payload.status != insp.status:
        now = datetime.now()
        insp.status = payload.status

        # Whoever moves it off 'open' owns it, unless a handler was named.
        if payload.status in ("proses", "selesai") and insp.handled_by is None:
            insp.handled_by = user.id

        if payload.status == "proses":
            if insp.started_at is None:
                insp.started_at = now
            insp.completed_at = None
        elif payload.status == "selesai":
            # Going straight open -> selesai still needs a start time so the
            # response-time KPI isn't silently null.
            if insp.started_at is None:
                insp.started_at = now
            insp.completed_at = now
        else:   # back to 'open' — reopen cleanly
            insp.started_at = None
            insp.completed_at = None
            insp.response_time_seconds = None

        if insp.started_at is not None:
            delta = (insp.started_at - detection.detected_at).total_seconds()
            insp.response_time_seconds = max(0, int(delta))

    insp.updated_at = datetime.now()
    db.commit()
    db.refresh(insp)

    risk_row = db.scalar(
        select(RiskAssessment).where(RiskAssessment.detection_id == detection.id)
    )
    return _to_row(
        insp, detection.class_name, detection.detected_at,
        risk_row.risk_level if risk_row else None,
        risk_row.risk_score if risk_row else None,
    )
