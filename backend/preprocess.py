"""
Optional image pre-processing (plan §4.4) — toggled per-connection from the
live page via the existing WS `config` message.

Order matters: denoise first (remove speckle), then CLAHE (lift local
contrast), then sharpen last (so we don't amplify noise we were about to
remove). All three operate on the BGR frame in place-ish and are cheap enough
to stay in the inference thread.
"""

import cv2
import numpy as np

_clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def denoise(img: np.ndarray) -> np.ndarray:
    """Speckle / rain-drop removal. medianBlur, not fastNlMeans (too slow)."""
    return cv2.medianBlur(img, 3)


def enhance_contrast(img: np.ndarray) -> np.ndarray:
    """CLAHE on the L channel of LAB — lifts shadows without shifting colour."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    lab = cv2.merge((_clahe.apply(l), a, b))
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def sharpen(img: np.ndarray) -> np.ndarray:
    """Unsharp mask — recovers edge definition on soft / glary frames."""
    blurred = cv2.GaussianBlur(img, (0, 0), 3)
    return cv2.addWeighted(img, 1.5, blurred, -0.5, 0)


def apply_chain(
    img: np.ndarray,
    *,
    use_denoise: bool = False,
    use_clahe: bool = False,
    use_sharpen: bool = False,
) -> np.ndarray:
    if use_denoise:
        img = denoise(img)
    if use_clahe:
        img = enhance_contrast(img)
    if use_sharpen:
        img = sharpen(img)
    return img
