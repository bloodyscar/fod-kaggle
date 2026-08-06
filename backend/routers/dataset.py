"""
FOD-A dataset gallery: model-labelled sample frames + the frame JPEGs.

The dataset on disk holds 33.793 frames, so nothing here ever returns the whole
thing. `dataset_index` keeps a small sample per FOD class (20 by default,
labelled by best.onnx) and this router pages through that sample with
limit/offset. The frame count is reported so the UI can say how much is *not*
being shown.
"""

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

import dataset_index
from auth import get_current_user, require_admin
from classes import FOD_CLASSES
from config import DATASET_IMAGES_DIR
from models import User
from schemas import DatasetClassCount, DatasetPage, DatasetSample, DatasetStatus

router = APIRouter(prefix="/dataset", tags=["dataset"])

# VOC frame names are six digits, but stay lenient — and strict enough that no
# separator, dot-dot or drive letter can get through to the filesystem.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,64}\.(jpg|jpeg)$", re.IGNORECASE)


def _envelope(rows: list[dict], limit: int, offset: int, status: dict) -> DatasetPage:
    return DatasetPage(
        items=[DatasetSample(**row) for row in rows[offset:offset + limit]],
        total=len(rows),
        limit=limit,
        offset=offset,
        status=DatasetStatus(**status),
        classes=[DatasetClassCount(**c) for c in dataset_index.class_counts()],
    )


@router.get("", response_model=DatasetPage)
def list_samples(
    _: User = Depends(get_current_user),
    class_name: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """One page of sample frames — all classes, or just one.

    Indexing starts lazily on the first request (same idea as the weather
    cache) and keeps running in the background, so an early call legitimately
    returns few items with `status.status == "indexing"`.
    """
    if class_name and class_name not in FOD_CLASSES:
        raise HTTPException(status_code=404, detail="Kelas FOD tidak dikenal")

    status = dataset_index.ensure_index()
    return _envelope(dataset_index.samples(class_name), limit, offset, status)


@router.get("/status", response_model=DatasetStatus)
def index_status(_: User = Depends(get_current_user)):
    """Cheap progress poll — no image list attached."""
    return DatasetStatus(**dataset_index.ensure_index())


@router.post("/reindex", response_model=DatasetStatus)
def reindex(_: User = Depends(require_admin)):
    """Drop the sample index and scan again (e.g. after replacing best.onnx)."""
    return DatasetStatus(**dataset_index.reindex())


@router.get("/image/{filename}")
def get_frame(filename: str, _: User = Depends(get_current_user)):
    """Serve one dataset frame behind auth.

    Not a StaticFiles mount: that would expose the Annotations XML and the
    ImageSets splits too, and we only ever want the JPEGs.
    """
    if not _SAFE_NAME.match(filename):
        raise HTTPException(status_code=404, detail="Gambar tidak ditemukan")

    root = DATASET_IMAGES_DIR.resolve()
    path = (root / filename).resolve()
    # Defence in depth: the regex already blocks traversal, this catches the rest.
    if path.parent != root or not path.is_file():
        raise HTTPException(status_code=404, detail="Gambar tidak ditemukan")

    # Dataset frames never change, so let the browser keep them for a day.
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
