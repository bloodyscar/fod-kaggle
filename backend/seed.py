"""
Database seeding — safe to run repeatedly.

    python seed.py            # tables + default users + 31 fod_classes
    python seed.py --demo     # ...plus 7 days of realistic demo detections
    python seed.py --demo --reset-demo   # wipe previous demo rows first

Idempotent by design: `fod_classes` rows are only INSERTed when the id is
missing, so a severity weight an admin has tuned is never overwritten.
"""

import argparse
import random
import sys
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

import risk
from classes import FOD_CLASSES, severity_rows
from auth import hash_password
from database import SessionLocal, create_all
from models import FodClass, FodDetection, Inspection, RiskAssessment, User

DEFAULT_USERS = [
    # username, full name, password, role
    ("admin", "Administrator Sistem", "admin123", "admin"),
    ("petugas", "Petugas Airside", "petugas123", "petugas"),
]


def seed_fod_classes(db) -> tuple[int, int]:
    """Insert missing classes only. Returns (inserted, kept)."""
    existing = {row for row in db.scalars(select(FodClass.id)).all()}
    inserted = 0
    for class_id, name, weight in severity_rows():
        if class_id in existing:
            continue
        db.add(FodClass(id=class_id, name=name, severity_weight=weight))
        inserted += 1
    db.commit()
    return inserted, len(existing)


def seed_users(db) -> int:
    created = 0
    for username, full_name, password, role in DEFAULT_USERS:
        if db.scalar(select(User).where(User.username == username)):
            continue
        db.add(User(
            username=username,
            full_name=full_name,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        ))
        created += 1
    db.commit()
    return created


# --------------------------------------------------------------------- demo --
# Weighted so the demo looks like a real runway: lots of small hardware, the
# occasional dropped tool, rarely a big metal sheet.
DEMO_WEIGHTS = {
    "Bolt": 9, "Nut": 8, "Screw": 8, "Washer": 6, "Nail": 6, "Wire": 5,
    "PaintChip": 7, "Rock": 6, "PlasticPart": 5, "MetalPart": 4, "Tape": 4,
    "Label": 4, "SodaCan": 3, "LuggageTag": 3, "Wood": 3, "BoltWasher": 3,
    "Pen": 2, "Hose": 2, "ClampPart": 2, "MetalSheet": 1, "Wrench": 1,
    "Screwdriver": 1, "Pliers": 1, "Hammer": 1, "Cutter": 1, "Battery": 1,
    "FuelCap": 1, "LuggagePart": 1, "BoltNutSet": 2, "AdjustableClamp": 1,
    "AdjustableWrench": 1,
}
DEMO_CAMERAS = [
    "Integrated Webcam",
    "Logitech C920 HD Pro (external)",
    "USB2.0 HD UVC WebCam (external)",
]
DEMO_MARKER = "[demo]"


def _demo_datetime(rng: random.Random, day_offset: int) -> datetime:
    """A plausible inspection-hours timestamp on the given day."""
    day = datetime.now().replace(microsecond=0) - timedelta(days=day_offset)
    hour = rng.choice([6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18])
    return day.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60))


def seed_demo(db, days: int = 7, rng: random.Random | None = None) -> int:
    rng = rng or random.Random(20260803)

    severities = {
        name: weight
        for _, name, weight in [
            (c.id, c.name, c.severity_weight)
            for c in db.scalars(select(FodClass)).all()
        ]
    }
    class_ids = {name: i for i, name in enumerate(FOD_CLASSES)}
    names = list(DEMO_WEIGHTS)
    weights = [DEMO_WEIGHTS[n] for n in names]

    # Look up real ids rather than assuming 1/2 — the users may already exist
    # with different ids, and handled_by is a real FK.
    handler_ids = db.scalars(
        select(User.id).where(User.username.in_(["petugas", "admin"])).order_by(User.id)
    ).all()

    created = 0
    for day_offset in range(days - 1, -1, -1):
        # Fewer findings today (the day is still in progress).
        n_events = rng.randint(1, 4) if day_offset == 0 else rng.randint(3, 9)
        for _ in range(n_events):
            name = rng.choices(names, weights=weights, k=1)[0]
            class_id = class_ids[name]
            conf = round(rng.uniform(0.38, 0.97), 4)
            detected_at = _demo_datetime(rng, day_offset)

            # A small, plausible bbox somewhere on the strip.
            w = rng.uniform(0.03, 0.12)
            h = rng.uniform(0.03, 0.12)
            x1 = rng.uniform(0.05, 0.95 - w)
            y1 = rng.uniform(0.15, 0.85 - h)

            assessment = risk.assess(name, conf, severities.get(name, 3))

            det = FodDetection(
                class_id=class_id,
                class_name=name,
                confidence=conf,
                x1=round(x1, 4), y1=round(y1, 4),
                x2=round(x1 + w, 4), y2=round(y1 + h, 4),
                camera_label=f"{rng.choice(DEMO_CAMERAS)} {DEMO_MARKER}",
                image_path=None,          # no real snapshot for synthetic rows
                detected_at=detected_at,
            )
            db.add(det)
            db.flush()

            db.add(RiskAssessment(
                detection_id=det.id,
                likelihood=assessment.likelihood,
                severity=assessment.severity,
                risk_score=assessment.risk_score,
                risk_level=assessment.risk_level,
                recommendation=assessment.recommendation,
                created_at=detected_at,
            ))

            # Older + higher-risk findings are more likely to be handled.
            handled_chance = 0.85 if day_offset > 1 else 0.45
            if assessment.risk_level in ("High", "Critical"):
                handled_chance += 0.10

            insp = Inspection(
                detection_id=det.id,
                status="open",
                created_at=detected_at,
                updated_at=detected_at,
            )
            if rng.random() < handled_chance:
                response = rng.randint(120, 3600)
                started = detected_at + timedelta(seconds=response)
                insp.started_at = started
                insp.response_time_seconds = response
                insp.handled_by = rng.choice(handler_ids) if handler_ids else None
                if rng.random() < 0.8:
                    insp.status = "selesai"
                    insp.completed_at = started + timedelta(minutes=rng.randint(5, 90))
                    insp.notes = rng.choice([
                        "FOD diambil dan runway dibersihkan.",
                        "Objek diamankan, area diverifikasi bersih.",
                        "Sudah ditangani, tidak ada temuan tambahan.",
                    ])
                else:
                    insp.status = "proses"
                    insp.notes = "Petugas sedang menuju lokasi."
                insp.updated_at = insp.completed_at or started
            db.add(insp)
            created += 1

    db.commit()
    return created


def reset_demo(db) -> int:
    """Delete only rows this script generated (matched by the camera marker)."""
    ids = db.scalars(
        select(FodDetection.id).where(
            FodDetection.camera_label.like(f"%{DEMO_MARKER}%")
        )
    ).all()
    if not ids:
        return 0
    # Delete children explicitly: a bulk DELETE bypasses ORM cascade, and the
    # DB-level ON DELETE CASCADE needs InnoDB which we can't assume elsewhere.
    db.execute(delete(Inspection).where(Inspection.detection_id.in_(ids)))
    db.execute(delete(RiskAssessment).where(RiskAssessment.detection_id.in_(ids)))
    db.execute(delete(FodDetection).where(FodDetection.id.in_(ids)))
    db.commit()
    return len(ids)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the FOD Sentry database")
    parser.add_argument("--demo", action="store_true",
                        help="also generate 7 days of demo detections")
    parser.add_argument("--days", type=int, default=7,
                        help="how many days of demo data (default 7)")
    parser.add_argument("--reset-demo", action="store_true",
                        help="delete previously generated demo rows first")
    args = parser.parse_args()

    print("• creating tables (if missing)…")
    create_all()

    with SessionLocal() as db:
        inserted, kept = seed_fod_classes(db)
        total_classes = db.scalar(select(func.count(FodClass.id)))
        print(f"• fod_classes: +{inserted} baru, {kept} dipertahankan → total {total_classes}")
        if total_classes != 31:
            print(f"  ⚠ expected 31 rows, found {total_classes}")

        created_users = seed_users(db)
        print(f"• users: +{created_users} baru")
        if created_users:
            for username, _, password, role in DEFAULT_USERS:
                print(f"    {username} / {password}  ({role})")

        if args.reset_demo:
            removed = reset_demo(db)
            print(f"• demo lama dihapus: {removed} deteksi")

        if args.demo:
            existing_demo = db.scalar(
                select(func.count(FodDetection.id)).where(
                    FodDetection.camera_label.like(f"%{DEMO_MARKER}%")
                )
            )
            if existing_demo:
                print(f"• demo dilewati — sudah ada {existing_demo} baris demo "
                      f"(pakai --reset-demo untuk regenerasi)")
            else:
                created = seed_demo(db, days=args.days)
                print(f"• demo: +{created} deteksi selama {args.days} hari")

    print("✓ selesai")
    return 0


if __name__ == "__main__":
    sys.exit(main())
