"""
Risk engine + decision engine (plan §4.1-4.2).

    Risk = Likelihood x Severity     ->  1..25  ->  5 levels  ->  recommendation

Likelihood comes from the detector's confidence; Severity is the per-class
weight stored in `fod_classes` (admin editable).
"""

from typing import NamedTuple

# Level -> hex colour, mirrored in the frontend CSS badge classes.
RISK_COLORS: dict[str, str] = {
    "Critical": "#ff5457",
    "High": "#ff8a3d",
    "Medium": "#ffb627",
    "Low": "#35e0c7",
    "Very Low": "#8b93a3",
}

# Ordered worst -> best; used for chart axes and summary dicts.
RISK_LEVELS: list[str] = ["Critical", "High", "Medium", "Low", "Very Low"]

# Level -> action text. A plain dict, not a rule engine (plan §4.2).
RECOMMENDATIONS: dict[str, str] = {
    "Very Low": "Pantau pada inspeksi terjadwal berikutnya.",
    "Low": "Pantau pada inspeksi terjadwal berikutnya.",
    "Medium": "Lakukan verifikasi/inspeksi visual lapangan secepatnya.",
    "High": "Lakukan pembersihan runway sebelum penerbangan berikutnya.",
    "Critical": "PEMBERSIHAN DARURAT SEGERA sebelum penerbangan berikutnya.",
}

# Levels that raise a notification badge in the dashboard.
ALERT_LEVELS: tuple[str, ...] = ("High", "Critical")


class Assessment(NamedTuple):
    likelihood: int
    severity: int
    risk_score: int
    risk_level: str
    recommendation: str


def likelihood(conf: float) -> int:
    """Confidence -> Likelihood 1-5."""
    if conf >= 0.85:
        return 5
    if conf >= 0.70:
        return 4
    if conf >= 0.55:
        return 3
    if conf >= 0.45:
        return 2
    return 1


def classify(score: int) -> str:
    """risk_score 1-25 -> level name."""
    if score >= 20:
        return "Critical"
    if score >= 13:
        return "High"
    if score >= 7:
        return "Medium"
    if score >= 3:
        return "Low"
    return "Very Low"


def recommend(class_name: str, score: int, level: str) -> str:
    action = RECOMMENDATIONS.get(level, RECOMMENDATIONS["Medium"])
    return f"{class_name} terdeteksi (skor {score}/25 — {level}). {action}"


def assess(class_name: str, conf: float, severity: int) -> Assessment:
    """The one entry point: confidence + class severity -> full assessment."""
    sev = max(1, min(5, int(severity)))
    lik = likelihood(float(conf))
    score = lik * sev
    level = classify(score)
    return Assessment(
        likelihood=lik,
        severity=sev,
        risk_score=score,
        risk_level=level,
        recommendation=recommend(class_name, score, level),
    )
