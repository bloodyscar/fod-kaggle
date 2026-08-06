"""
Sample index for the FOD-A dataset gallery (VOC2007/JPEGImages, 33.793 frames).

The gallery never scans or serves the whole dataset. Instead a background
thread walks the frames, runs `best.onnx` on each one and keeps the first
`dataset_per_class` frames per FOD class — 20 x 31 = 620 samples. The labels
are therefore *our model's* labels, not the dataset's VOC XML (which is ignored
on purpose): the page doubles as a sanity check on what best.onnx recognises.

Two things keep this affordable:

* **Early exit.** Indexing stops as soon as every class is full, or after
  `dataset_scan_limit` frames — whichever comes first. A full 33.793-frame pass
  would take hours at ~0.2 s per inference.
* **Strided visit order.** Consecutive filenames are consecutive video frames of
  the *same* object (the VOC XML carries a `track_id`), so scanning 000000,
  000001, 000002… would fill one bucket and starve the rest. We walk the file
  list in a fixed coprime stride instead, which spreads the scan across the
  whole dataset while staying deterministic and resumable.

The result is cached as JSON in `storage/dataset_index.json`, so a restart
reuses the work; a partial index resumes from where it stopped.
"""

import json
import math
import os
import threading
import time
from datetime import datetime

import cv2

from classes import FOD_CLASSES
from config import (
    DATASET_IMAGES_DIR,
    DATASET_INDEX_PATH,
    settings,
)

# Coprime with 33.793, so `(k * STRIDE) % n` visits every frame exactly once
# before repeating — a permutation, not a sample with gaps.
STRIDE = 9973

# Guard both the state dict and the buckets: FastAPI serves sync endpoints from
# a threadpool, so readers really do run while the indexer thread writes.
_lock = threading.Lock()

_state: dict = {
    "status": "idle",          # idle | indexing | ready | error | missing
    "scanned": 0,              # frames actually run through the model
    "labelled": 0,             # frames filed under some class
    "total_images": 0,         # frames in JPEGImages (the "33 ribu")
    "cursor": 0,               # position in the strided walk, for resuming
    "error": None,
    "updated_at": None,
}
# class_name -> [{file, class_id, class_name, conf, box, width, height}]
_buckets: dict[str, list[dict]] = {name: [] for name in FOD_CLASSES}
_thread: threading.Thread | None = None


# ---------------------------------------------------------------- storage ----


def _persist_locked() -> None:
    """Write the index atomically. Caller must hold `_lock`."""
    payload = {
        "state": {**_state, "status": _state["status"]},
        "buckets": _buckets,
        "per_class": settings.dataset_per_class,
    }
    try:
        DATASET_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = DATASET_INDEX_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, DATASET_INDEX_PATH)
    except Exception as exc:  # noqa: BLE001
        # A cache we cannot write is not fatal — we just re-index next boot.
        print(f"[dataset] WARNING could not save index: {exc}")


def load_cached() -> None:
    """Restore a previous run's index, if any. Safe to call at startup."""
    if not DATASET_INDEX_PATH.is_file():
        return
    try:
        payload = json.loads(DATASET_INDEX_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[dataset] WARNING ignoring unreadable index: {exc}")
        return

    saved_per_class = payload.get("per_class")
    with _lock:
        for name, items in (payload.get("buckets") or {}).items():
            if name in _buckets and isinstance(items, list):
                _buckets[name] = items
        for key in ("scanned", "labelled", "total_images", "cursor"):
            value = (payload.get("state") or {}).get(key)
            if isinstance(value, int):
                _state[key] = value
        # "indexing" in a saved file means the process died mid-run; treat it
        # as resumable rather than pretending it is still going.
        _state["status"] = "ready" if _is_complete_locked() else "idle"
        _state["updated_at"] = (payload.get("state") or {}).get("updated_at")

    if saved_per_class and saved_per_class != settings.dataset_per_class:
        print(
            f"[dataset] index was built for {saved_per_class}/class, "
            f"now configured for {settings.dataset_per_class} — "
            "POST /api/dataset/reindex to rebuild"
        )
    print(f"[dataset] index loaded: {total_labelled()} samples, {_state['status']}")


# ------------------------------------------------------------------ state ----


def _is_complete_locked() -> bool:
    per_class = settings.dataset_per_class
    return all(len(items) >= per_class for items in _buckets.values())


def total_labelled() -> int:
    return sum(len(items) for items in _buckets.values())


def class_counts() -> list[dict]:
    """Per-class sample counts, in the canonical class_id order."""
    with _lock:
        return [
            {"class_id": cid, "class_name": name, "count": len(_buckets[name])}
            for cid, name in enumerate(FOD_CLASSES)
        ]


def snapshot() -> dict:
    """Indexer progress for the UI's status strip."""
    with _lock:
        return {
            **{k: _state[k] for k in ("status", "scanned", "labelled", "error", "updated_at")},
            "total_images": _state["total_images"] or _count_images(),
            "per_class": settings.dataset_per_class,
            "scan_limit": settings.dataset_scan_limit,
            "min_conf": settings.dataset_min_conf,
            "sampled": total_labelled(),
        }


def samples(class_name: str | None = None) -> list[dict]:
    """Flat sample list, grouped by class in class_id order."""
    with _lock:
        if class_name:
            return list(_buckets.get(class_name, []))
        out: list[dict] = []
        for name in FOD_CLASSES:
            out.extend(_buckets[name])
        return out


# ---------------------------------------------------------------- indexing ----


def _count_images() -> int:
    """Cheap count of JPEGImages — no listing kept, just the number."""
    if not DATASET_IMAGES_DIR.is_dir():
        return 0
    try:
        with os.scandir(DATASET_IMAGES_DIR) as it:
            return sum(
                1 for e in it if e.is_file() and e.name.lower().endswith((".jpg", ".jpeg"))
            )
    except OSError:
        return 0


def _image_files() -> list[str]:
    with os.scandir(DATASET_IMAGES_DIR) as it:
        names = [
            e.name
            for e in it
            if e.is_file() and e.name.lower().endswith((".jpg", ".jpeg"))
        ]
    names.sort()
    return names


def _worker() -> None:
    # Imported here, not at module scope: this module is imported by the router,
    # and only the indexer needs an ONNX session in hand.
    from inference import run_inference

    try:
        files = _image_files()
    except OSError as exc:
        with _lock:
            _state["status"] = "error"
            _state["error"] = f"Tidak bisa membaca folder dataset: {exc}"
        return

    n = len(files)
    if not n:
        with _lock:
            _state["status"] = "missing"
            _state["error"] = "Folder JPEGImages kosong."
        return

    stride = STRIDE if math.gcd(STRIDE, n) == 1 else 1
    per_class = settings.dataset_per_class
    min_conf = settings.dataset_min_conf

    with _lock:
        _state["total_images"] = n
        _state["status"] = "indexing"
        _state["error"] = None
        start_cursor = _state["cursor"]
        scanned = _state["scanned"]

    # Frames already filed — skip them on a resumed run.
    with _lock:
        seen = {item["file"] for items in _buckets.values() for item in items}

    budget = settings.dataset_scan_limit
    processed_this_run = 0
    dirty = 0

    for k in range(start_cursor, n):
        with _lock:
            if _state["status"] != "indexing":     # reindex/reset asked us to stop
                return
            complete = _is_complete_locked()
        if complete or processed_this_run >= budget:
            break

        name = files[(k * stride) % n]
        if name in seen:
            continue

        path = DATASET_IMAGES_DIR / name
        frame = cv2.imread(str(path))
        if frame is None:
            continue

        processed_this_run += 1
        scanned += 1
        try:
            dets = run_inference(frame, min_conf, settings.default_iou)
        except Exception as exc:  # noqa: BLE001
            print(f"[dataset] inference failed on {name}: {exc}")
            dets = []

        # run_inference returns highest-confidence first; that box decides the
        # class this frame is filed under.
        top = dets[0] if dets else None
        if top and top["conf"] >= min_conf:
            cls = top["class_name"]
            h, w = frame.shape[:2]
            with _lock:
                bucket = _buckets.get(cls)
                if bucket is not None and len(bucket) < per_class:
                    bucket.append({
                        "file": name,
                        "class_id": top["class_id"],
                        "class_name": cls,
                        "conf": round(top["conf"], 4),
                        "box": [
                            round(top["x1"], 4), round(top["y1"], 4),
                            round(top["x2"], 4), round(top["y2"], 4),
                        ],
                        "width": w,
                        "height": h,
                        "objects": len(dets),
                    })
                    _state["labelled"] += 1
                    dirty += 1

        with _lock:
            _state["scanned"] = scanned
            _state["cursor"] = k + 1
            _state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if dirty >= 25:
                _persist_locked()
                dirty = 0

        if settings.dataset_index_sleep:
            time.sleep(settings.dataset_index_sleep)

    with _lock:
        if _state["status"] == "indexing":
            _state["status"] = "ready"
        _state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _persist_locked()
    print(
        f"[dataset] indexing stopped: {total_labelled()} samples "
        f"after {_state['scanned']} frames"
    )


def ensure_index() -> dict:
    """Start indexing if it has not run yet. Returns the current progress."""
    global _thread

    if not DATASET_IMAGES_DIR.is_dir():
        with _lock:
            _state["status"] = "missing"
            _state["error"] = (
                f"Folder dataset tidak ditemukan: {DATASET_IMAGES_DIR}. "
                "Unduh dataset FOD-A dari Kaggle lalu letakkan VOC2007/ di root proyek."
            )
        return snapshot()

    with _lock:
        busy = _thread is not None and _thread.is_alive()
        status = _state["status"]
        complete = _is_complete_locked()
        if not busy and status in ("idle", "missing", "error") and not complete:
            _state["status"] = "indexing"
            _state["error"] = None
            start = True
        else:
            start = False

    if start:
        _thread = threading.Thread(target=_worker, name="dataset-index", daemon=True)
        _thread.start()

    return snapshot()


def reindex() -> dict:
    """Throw the index away and scan again from the top (admin action)."""
    global _thread

    with _lock:
        _state["status"] = "idle"          # signals a running worker to bail out
    running = _thread
    if running is not None and running.is_alive():
        running.join(timeout=5.0)

    with _lock:
        for name in _buckets:
            _buckets[name] = []
        _state.update({
            "scanned": 0, "labelled": 0, "cursor": 0, "error": None,
            "updated_at": None,
        })
        _persist_locked()

    return ensure_index()
