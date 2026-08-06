"""Pydantic request/response models — one file (plan §2.1)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["admin", "petugas"]
InspectionStatus = Literal["open", "proses", "selesai"]
RiskLevel = Literal["Very Low", "Low", "Medium", "High", "Critical"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth ------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class UserOut(ORMModel):
    id: int
    username: str
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    full_name: str = Field(default="", max_length=100)
    password: str = Field(min_length=6, max_length=128)
    role: Role = "petugas"
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, min_length=6, max_length=128)
    role: Role | None = None
    is_active: bool | None = None


# ---------------------------------------------------------- fod classes -----
class FodClassOut(ORMModel):
    id: int
    name: str
    severity_weight: int


class FodClassUpdate(BaseModel):
    severity_weight: int = Field(ge=1, le=5)


# ------------------------------------------------------------ detections ----
class RiskOut(ORMModel):
    likelihood: int
    severity: int
    risk_score: int
    risk_level: str
    recommendation: str


class InspectionOut(ORMModel):
    id: int
    detection_id: int
    status: str
    handled_by: int | None
    handler_name: str | None = None
    notes: str | None
    started_at: datetime | None
    completed_at: datetime | None
    response_time_seconds: int | None
    created_at: datetime
    updated_at: datetime


class DetectionOut(ORMModel):
    id: int
    class_id: int
    class_name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    camera_label: str | None
    image_path: str | None
    detected_at: datetime
    risk: RiskOut | None = None
    inspection: InspectionOut | None = None


class DetectionPage(BaseModel):
    items: list[DetectionOut]
    total: int
    page: int
    per_page: int
    pages: int


class MapPoint(BaseModel):
    id: int
    class_name: str
    # bbox centre, still normalised 0-1 — the UI maps it linearly onto the SVG
    cx: float
    cy: float
    risk_level: str
    risk_score: int
    detected_at: datetime


class InspectionUpdate(BaseModel):
    status: InspectionStatus | None = None
    notes: str | None = Field(default=None, max_length=2000)
    handled_by: int | None = None


class InspectionRow(InspectionOut):
    class_name: str
    detected_at: datetime
    risk_level: str | None = None
    risk_score: int | None = None


# ------------------------------------------------------------- dashboard ----
class DashboardSummary(BaseModel):
    total_today: int
    total_all: int
    by_level: dict[str, int]
    open_inspections: int
    critical_open: int
    avg_response_seconds: float | None


class ChartsOut(BaseModel):
    daily_labels: list[str]
    daily_counts: list[int]
    level_labels: list[str]
    level_counts: list[int]


class NotificationItem(BaseModel):
    detection_id: int
    inspection_id: int
    class_name: str
    risk_level: str
    risk_score: int
    recommendation: str
    detected_at: datetime


class NotificationsOut(BaseModel):
    items: list[NotificationItem]
    unread_count: int
    critical_count: int


# --------------------------------------------------------------- weather ----
class WeatherOut(BaseModel):
    temperature: float | None = None
    feels_like: float | None = None
    humidity: float | None = None
    wind_speed: float | None = None
    wind_direction: float | None = None
    precipitation: float | None = None
    visibility_km: float | None = None
    condition: str | None = None
    observed_at: str | None = None
    stale: bool = False
    error: str | None = None


# --------------------------------------------------------------- dataset ----
# The FOD-A gallery. Labels come from best.onnx, not from the dataset's VOC XML.
class DatasetSample(BaseModel):
    file: str
    class_id: int
    class_name: str
    conf: float
    box: list[float] = Field(default_factory=list)   # x1,y1,x2,y2 normalised 0-1
    width: int
    height: int
    objects: int = 0


class DatasetClassCount(BaseModel):
    class_id: int
    class_name: str
    count: int


class DatasetStatus(BaseModel):
    status: str                    # idle | indexing | ready | error | missing
    scanned: int = 0               # frames run through the model so far
    labelled: int = 0
    total_images: int = 0          # frames in the dataset (33.793)
    sampled: int = 0               # frames kept in the gallery
    per_class: int = 20
    scan_limit: int = 0
    min_conf: float = 0.0
    error: str | None = None
    updated_at: str | None = None


class DatasetPage(BaseModel):
    items: list[DatasetSample]
    total: int
    limit: int
    offset: int
    status: DatasetStatus
    classes: list[DatasetClassCount]
