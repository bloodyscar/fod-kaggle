"""
The 5 tables (plan §3) — one file on purpose.

users · fod_classes · fod_detections · risk_assessments · inspections
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# TINYINT on MySQL, SMALLINT elsewhere — keeps the schema faithful to the plan
# while staying portable.
TinyInt = SmallInteger().with_variant(mysql.TINYINT(unsigned=True), "mysql")

# BIGINT on MySQL. SQLite only auto-increments a plain INTEGER primary key
# (it must alias rowid), so the dev fallback URL needs the variant.
BigIntKey = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="petugas")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class FodClass(Base):
    """Severity weight per FOD class. `id` == the model's class_id (0-30)."""

    __tablename__ = "fod_classes"

    # NOT autoincrement — the id is dictated by the model's class order.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    severity_weight: Mapped[int] = mapped_column(TinyInt, nullable=False, default=3)


class FodDetection(Base):
    __tablename__ = "fod_detections"

    id: Mapped[int] = mapped_column(BigIntKey, primary_key=True, autoincrement=True)
    class_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fod_classes.id"), nullable=False, index=True
    )
    # Denormalised so the history table renders without a JOIN, and so rows
    # keep their original label even if an admin renames a class later.
    class_name: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Normalised 0-1 bbox — same convention as the existing WS payload.
    x1: Mapped[float] = mapped_column(Float, nullable=False)
    y1: Mapped[float] = mapped_column(Float, nullable=False)
    x2: Mapped[float] = mapped_column(Float, nullable=False)
    y2: Mapped[float] = mapped_column(Float, nullable=False)

    camera_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), index=True
    )

    risk: Mapped["RiskAssessment | None"] = relationship(
        back_populates="detection",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    inspection: Mapped["Inspection | None"] = relationship(
        back_populates="detection",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(BigIntKey, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(
        BigIntKey,
        ForeignKey("fod_detections.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    likelihood: Mapped[int] = mapped_column(TinyInt, nullable=False)
    severity: Mapped[int] = mapped_column(TinyInt, nullable=False)
    risk_score: Mapped[int] = mapped_column(TinyInt, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    detection: Mapped["FodDetection"] = relationship(back_populates="risk")

    __table_args__ = (Index("ix_risk_assessments_risk_level", "risk_level"),)


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[int] = mapped_column(BigIntKey, primary_key=True, autoincrement=True)
    detection_id: Mapped[int] = mapped_column(
        BigIntKey,
        ForeignKey("fod_detections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    handled_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # started_at - detection.detected_at, filled when status first moves to 'proses'
    response_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    detection: Mapped["FodDetection"] = relationship(back_populates="inspection")
    handler: Mapped["User | None"] = relationship()

    __table_args__ = (Index("ix_inspections_status", "status"),)

    @property
    def handler_name(self) -> str | None:
        """Display name for the officer, so the API never exposes only an id."""
        if self.handler is None:
            return None
        return self.handler.full_name or self.handler.username
