"""
SQLAlchemy engine + session factory.

Sync engine on purpose (see plan §1.3 decision 2): FastAPI runs plain `def`
endpoints in a threadpool, so blocking DB calls never stall the event loop and
we avoid an async MySQL driver.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,      # survive MySQL's idle connection timeout
    pool_recycle=3600,
    echo=settings.sql_echo,
    future=True,
)

# SQLite ignores ON DELETE CASCADE unless foreign keys are switched on
# per-connection. Harmless no-op for MySQL; keeps the fallback URL usable.
if settings.database_url.startswith("sqlite"):
    @event.listens_for(Engine, "connect")
    def _sqlite_fk_on(dbapi_conn, _record):  # pragma: no cover - dev fallback
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency — one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    """Create any missing table. No Alembic by design (plan §1.3)."""
    import models  # noqa: F401  — registers the mappers before create_all

    Base.metadata.create_all(bind=engine)
