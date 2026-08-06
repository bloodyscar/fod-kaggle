"""
Application settings — read once from backend/.env via pydantic-settings.

Every tunable lives here so no other module has to know about os.environ.
Paths are derived from this file's location, so the app can be started from
any working directory.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"
MODEL_PATH = BASE_DIR / "best.onnx"
STORAGE_DIR = BASE_DIR / "storage" / "detections"

# FOD-A Pascal-VOC dataset (33.793 frames), downloaded from Kaggle:
#   imenesabeur/dataset-for-foreign-object-debris-in-airports
# Only JPEGImages is read; the VOC XML labels are ignored on purpose — the
# Dataset page labels each frame with best.onnx, so what it shows is what our
# own model sees. Gitignored: 412 MB is not repo material.
DATASET_DIR = BASE_DIR.parent / "VOC2007"
DATASET_IMAGES_DIR = DATASET_DIR / "JPEGImages"
DATASET_INDEX_PATH = BASE_DIR / "storage" / "dataset_index.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Database ----------------------------------------------------------
    database_url: str = (
        "mysql+pymysql://root@127.0.0.1:3306/fod_sentry?charset=utf8mb4"
    )
    sql_echo: bool = False

    # ---- Auth --------------------------------------------------------------
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 8
    cookie_name: str = "fod_token"
    # Set true only when serving over HTTPS — a Secure cookie is dropped on http://
    cookie_secure: bool = False

    # ---- CORS --------------------------------------------------------------
    # Comma separated. Must NOT be "*" because we send credentials (cookies).
    allowed_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # ---- Inference / storage ----------------------------------------------
    input_size: int = 960
    default_conf: float = 0.35
    default_iou: float = 0.45
    # Anti-duplicate window: at most one saved row per FOD class per N seconds.
    cooldown_seconds: int = 10
    save_snapshots: bool = True

    # ---- Weather (Open-Meteo — no API key) ---------------------------------
    weather_lat: float = -3.3667          # Bandara Douw Aturure, Nabire
    weather_lon: float = 135.4833
    weather_timezone: str = "Asia/Jayapura"
    weather_ttl_seconds: int = 600         # 10 minutes
    weather_timeout_seconds: float = 6.0

    # ---- Dataset gallery ---------------------------------------------------
    # How many sample frames to keep per FOD class. 20 x 31 = 620 thumbnails,
    # which is plenty to eyeball a class and cheap to serve.
    dataset_per_class: int = 20
    # Hard stop on the indexer: it runs best.onnx on one frame at a time, so
    # scanning all 33.793 would take hours. It quits early anyway once every
    # class is full.
    dataset_scan_limit: int = 6000
    # A frame is only filed under a class if the model is at least this sure.
    dataset_min_conf: float = 0.45
    # Breather between frames so indexing never starves live detection.
    dataset_index_sleep: float = 0.01

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
