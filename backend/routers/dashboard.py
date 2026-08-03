"""Dashboard KPIs, charts, notifications, and the weather proxy."""

from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import weather
from auth import get_current_user
from database import get_db
from models import FodDetection, Inspection, RiskAssessment, User
from risk import ALERT_LEVELS, RISK_LEVELS
from schemas import (
    ChartsOut,
    DashboardSummary,
    NotificationItem,
    NotificationsOut,
    WeatherOut,
)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    today_start = datetime.combine(date.today(), time.min)

    total_today = db.scalar(
        select(func.count(FodDetection.id)).where(
            FodDetection.detected_at >= today_start
        )
    ) or 0
    total_all = db.scalar(select(func.count(FodDetection.id))) or 0

    level_rows = db.execute(
        select(RiskAssessment.risk_level, func.count(RiskAssessment.id))
        .group_by(RiskAssessment.risk_level)
    ).all()
    by_level = {level: 0 for level in RISK_LEVELS}
    for level, count in level_rows:
        by_level[level] = count

    open_inspections = db.scalar(
        select(func.count(Inspection.id)).where(Inspection.status == "open")
    ) or 0

    critical_open = db.scalar(
        select(func.count(Inspection.id))
        .join(RiskAssessment, RiskAssessment.detection_id == Inspection.detection_id)
        .where(Inspection.status == "open", RiskAssessment.risk_level == "Critical")
    ) or 0

    # Average over inspections that actually got started.
    avg_response = db.scalar(
        select(func.avg(Inspection.response_time_seconds)).where(
            Inspection.response_time_seconds.isnot(None)
        )
    )

    return DashboardSummary(
        total_today=total_today,
        total_all=total_all,
        by_level=by_level,
        open_inspections=open_inspections,
        critical_open=critical_open,
        avg_response_seconds=round(float(avg_response), 1) if avg_response is not None else None,
    )


@router.get("/dashboard/charts", response_model=ChartsOut)
def charts(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Bar (last 7 days) + doughnut (risk distribution) in one request."""
    today = date.today()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    window_start = datetime.combine(days[0], time.min)

    # Group in SQL by date, then fill gaps in Python so empty days show as 0.
    rows = db.execute(
        select(
            func.date(FodDetection.detected_at).label("d"),
            func.count(FodDetection.id),
        )
        .where(FodDetection.detected_at >= window_start)
        .group_by(func.date(FodDetection.detected_at))
    ).all()

    counts: dict[str, int] = {}
    for d, count in rows:
        # MySQL returns a date object; SQLite returns an ISO string.
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)
        counts[key] = count

    level_rows = db.execute(
        select(RiskAssessment.risk_level, func.count(RiskAssessment.id))
        .group_by(RiskAssessment.risk_level)
    ).all()
    level_map = {level: count for level, count in level_rows}

    return ChartsOut(
        daily_labels=[d.strftime("%d %b") for d in days],
        daily_counts=[counts.get(d.isoformat(), 0) for d in days],
        level_labels=RISK_LEVELS,
        level_counts=[level_map.get(level, 0) for level in RISK_LEVELS],
    )


@router.get("/notifications", response_model=NotificationsOut)
def notifications(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """High/Critical detections whose inspection is still open (plan §4.5).

    No notifications table and no is_read column: marking the inspection
    'proses' or 'selesai' is what makes the alert disappear.
    """
    stmt = (
        select(FodDetection, RiskAssessment, Inspection)
        .join(RiskAssessment, RiskAssessment.detection_id == FodDetection.id)
        .join(Inspection, Inspection.detection_id == FodDetection.id)
        .where(
            RiskAssessment.risk_level.in_(ALERT_LEVELS),
            Inspection.status == "open",
        )
        .order_by(FodDetection.detected_at.desc())
    )

    rows = db.execute(stmt.limit(10)).all()
    items = [
        NotificationItem(
            detection_id=det.id,
            inspection_id=insp.id,
            class_name=det.class_name,
            risk_level=r.risk_level,
            risk_score=r.risk_score,
            recommendation=r.recommendation,
            detected_at=det.detected_at,
        )
        for det, r, insp in rows
    ]

    unread_count = db.scalar(
        select(func.count(Inspection.id))
        .join(RiskAssessment, RiskAssessment.detection_id == Inspection.detection_id)
        .where(
            Inspection.status == "open",
            RiskAssessment.risk_level.in_(ALERT_LEVELS),
        )
    ) or 0

    return NotificationsOut(
        items=items,
        unread_count=unread_count,
        critical_count=sum(1 for i in items if i.risk_level == "Critical"),
    )


@router.get("/weather", response_model=WeatherOut)
def current_weather(_: User = Depends(get_current_user)):
    # Cached 10 min in weather.py; always 200 so the widget can render "data lama".
    return WeatherOut(**weather.get_weather())
