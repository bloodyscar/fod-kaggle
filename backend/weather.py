"""
Open-Meteo proxy for the dashboard weather widget (plan §4.7).

Why proxy at all: one server-side cache keeps us to ~144 upstream calls a day
no matter how many dashboards are open, and the browser never has to reach a
third-party host (which also keeps the CSP/offline story simple).

No API key — Open-Meteo is free for non-commercial use.
"""

import threading
import time

import httpx

from config import settings

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

_CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,"
    "weather_code,wind_speed_10m,wind_direction_10m,visibility"
)

# WMO weather interpretation codes -> Indonesian UI text.
# Open-Meteo sends only the numeric code, unlike OpenWeatherMap.
WMO_CODES: dict[int, str] = {
    0: "Cerah",
    1: "Cerah berawan",
    2: "Berawan",
    3: "Berawan tebal",
    45: "Berkabut",
    48: "Kabut beku",
    51: "Gerimis ringan",
    53: "Gerimis sedang",
    55: "Gerimis lebat",
    56: "Gerimis beku ringan",
    57: "Gerimis beku lebat",
    61: "Hujan ringan",
    63: "Hujan sedang",
    65: "Hujan lebat",
    66: "Hujan beku ringan",
    67: "Hujan beku lebat",
    71: "Salju ringan",
    73: "Salju sedang",
    75: "Salju lebat",
    77: "Butiran salju",
    80: "Hujan lokal ringan",
    81: "Hujan lokal sedang",
    82: "Hujan lokal lebat",
    85: "Hujan salju ringan",
    86: "Hujan salju lebat",
    95: "Badai petir",
    96: "Badai petir + hujan es ringan",
    99: "Badai petir + hujan es lebat",
}

# Guard the cache: FastAPI runs sync endpoints in a threadpool, so two
# dashboards refreshing at once really can land here concurrently.
_lock = threading.Lock()
_cache: dict = {"data": None, "ts": 0.0}


def _params() -> dict:
    return {
        "latitude": settings.weather_lat,
        "longitude": settings.weather_lon,
        "current": _CURRENT_FIELDS,
        "wind_speed_unit": "ms",              # m/s — aviation convention
        "timezone": settings.weather_timezone,
    }


def _fetch() -> dict:
    resp = httpx.get(
        OPEN_METEO_URL, params=_params(), timeout=settings.weather_timeout_seconds
    )
    resp.raise_for_status()
    cur = resp.json()["current"]

    visibility = cur.get("visibility")
    return {
        "temperature": cur.get("temperature_2m"),
        "feels_like": cur.get("apparent_temperature"),
        "humidity": cur.get("relative_humidity_2m"),
        "wind_speed": cur.get("wind_speed_10m"),
        "wind_direction": cur.get("wind_direction_10m"),
        "precipitation": cur.get("precipitation"),
        # Open-Meteo reports visibility in metres.
        "visibility_km": round(visibility / 1000, 1) if visibility is not None else None,
        "condition": WMO_CODES.get(cur.get("weather_code"), "Tidak diketahui"),
        "observed_at": cur.get("time"),
    }


def get_weather() -> dict:
    """Fresh reading, or the last good one flagged `stale`, or an error dict."""
    now = time.monotonic()
    with _lock:
        cached = _cache["data"]
        if cached and now - _cache["ts"] < settings.weather_ttl_seconds:
            return {**cached, "stale": False}

    try:
        data = _fetch()
    except Exception:
        # Offline / upstream down: better a slightly old reading than nothing.
        with _lock:
            cached = _cache["data"]
        if cached:
            return {**cached, "stale": True}
        return {"error": "unavailable", "stale": True}

    with _lock:
        _cache["data"] = data
        _cache["ts"] = time.monotonic()
    return {**data, "stale": False}
