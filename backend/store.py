"""
Writing detections to the database from the live stream.

Two jobs, both on the hot path, so both are deliberately cheap:

1. **Cooldown (plan §4.3).** A 20 fps stream would otherwise insert ~20 rows a
   second per visible object. We keep at most one row per FOD class per
   COOLDOWN_SECONDS, and within a frame only the highest-confidence box of each
   class.
2. **Severity cache.** Severity comes from `fod_classes`, which changes only
   when an admin edits a weight — so we cache it instead of querying per frame,
   and let the PATCH endpoint invalidate.

`persist_detections()` is called through `run_in_executor`, never inline, so a
slow INSERT cannot add latency to inference.
"""

import threading
import time
from datetime import datetime

import numpy as np

import risk
from config import settings
from database import SessionLocal
from inference import save_snapshot
from models import FodClass, FodDetection, Inspection, RiskAssessment

# ------------------------------------------------------------------ cooldown -
_cooldown_lock = threading.Lock()
_last_saved: dict[int, float] = {}          # {class_id: monotonic seconds}


def should_save(class_id: int, now: float | None = None) -> bool:
    """True at most once per class per `cooldown_seconds`."""
    now = time.monotonic() if now is None else now
    with _cooldown_lock:
        if now - _last_saved.get(class_id, 0.0) < settings.cooldown_seconds:
            return False
        _last_saved[class_id] = now
        return True


def reset_cooldown() -> None:
    """Called when a stream starts so a fresh session records immediately."""
    with _cooldown_lock:
        _last_saved.clear()


# ----------------------------------------------------------- severity cache -
_sev_lock = threading.Lock()
_sev_cache: dict[int, int] = {}
_sev_loaded_at = 0.0
_SEV_TTL = 60.0


def invalidate_severity_cache() -> None:
    global _sev_loaded_at
    with _sev_lock:
        _sev_cache.clear()
        _sev_loaded_at = 0.0


def _severity_map() -> dict[int, int]:
    global _sev_loaded_at
    now = time.monotonic()
    with _sev_lock:
        if _sev_cache and now - _sev_loaded_at < _SEV_TTL:
            return dict(_sev_cache)

    with SessionLocal() as db:
        rows = db.query(FodClass.id, FodClass.severity_weight).all()

    fresh = {int(cid): int(weight) for cid, weight in rows}
    with _sev_lock:
        _sev_cache.clear()
        _sev_cache.update(fresh)
        _sev_loaded_at = time.monotonic()
    return fresh


# --------------------------------------------------------------- persistence -
def _best_per_class(detections: list[dict]) -> list[dict]:
    """Keep only the highest-confidence box of each class in this frame."""
    best: dict[int, dict] = {}
    for det in detections:
        cid = int(det["class_id"])
        if cid not in best or det["conf"] > best[cid]["conf"]:
            best[cid] = det
    return list(best.values())


def persist_detections(
    frame: np.ndarray,
    detections: list[dict],
    camera_label: str | None,
) -> list[dict]:
    """Save the frame's noteworthy detections.

    Returns one {class_id, risk_level, risk_score} per *saved* row so the caller
    can echo the risk badge back over the WebSocket. Never raises: a DB hiccup
    must not kill the live stream.
    """
    if not detections:
        return []

    severities = _severity_map()
    saved: list[dict] = []
    now_wall = datetime.now()

    try:
        with SessionLocal() as db:
            for det in _best_per_class(detections):
                class_id = int(det["class_id"])
                if not should_save(class_id):
                    continue

                assessment = risk.assess(
                    det["class_name"], det["conf"], severities.get(class_id, 3)
                )
                image_path = save_snapshot(
                    frame, det, assessment.risk_level, when=now_wall
                )

                row = FodDetection(
                    class_id=class_id,
                    class_name=det["class_name"],
                    confidence=float(det["conf"]),
                    x1=float(det["x1"]), y1=float(det["y1"]),
                    x2=float(det["x2"]), y2=float(det["y2"]),
                    camera_label=(camera_label or None),
                    image_path=image_path,
                    detected_at=now_wall,
                )
                db.add(row)
                db.flush()                      # need row.id for the children

                db.add(RiskAssessment(
                    detection_id=row.id,
                    likelihood=assessment.likelihood,
                    severity=assessment.severity,
                    risk_score=assessment.risk_score,
                    risk_level=assessment.risk_level,
                    recommendation=assessment.recommendation,
                    created_at=now_wall,
                ))
                # Every detection opens exactly one inspection (plan §3.5).
                db.add(Inspection(
                    detection_id=row.id,
                    status="open",
                    created_at=now_wall,
                    updated_at=now_wall,
                ))

                saved.append({
                    "class_id": class_id,
                    "detection_id": row.id,
                    "risk_level": assessment.risk_level,
                    "risk_score": assessment.risk_score,
                })
            db.commit()
    except Exception as exc:  # noqa: BLE001 — logged, never fatal
        print(f"[store] failed to persist detections: {exc}")
        return []

    return saved


def annotate_risk(detections: list[dict]) -> list[dict]:
    """Add risk_level/risk_score to *every* live box, saved or not.

    Mutates in place (and returns the list) so the WS payload carries a badge
    per box. Read-only and cache-backed, so it costs nothing per frame.
    """
    severities = _severity_map()
    for det in detections:
        a = risk.assess(
            det["class_name"], det["conf"], severities.get(int(det["class_id"]), 3)
        )
        det["risk_level"] = a.risk_level
        det["risk_score"] = a.risk_score
    return detections
