"""
The 31 FOD-A classes — the single source of truth for class order.

⚠️  ORDER IS A CONTRACT WITH best.onnx: the list index *is* the class_id the
model emits. Never reorder or insert entries without retraining/replacing the
model, or every historical `fod_detections.class_id` becomes mislabelled.

This lives in its own tiny module (not in inference.py) so `seed.py` can import
the class list without loading onnxruntime and building an inference session.
"""

FOD_CLASSES: list[str] = [
    'AdjustableClamp', 'AdjustableWrench', 'Battery', 'Bolt', 'BoltNutSet',
    'BoltWasher', 'ClampPart', 'Cutter', 'FuelCap', 'Hammer', 'Hose',
    'Label', 'LuggagePart', 'LuggageTag', 'MetalPart', 'MetalSheet',
    'Nail', 'Nut', 'PaintChip', 'Pen', 'PlasticPart', 'Pliers', 'Rock',
    'Screw', 'Screwdriver', 'SodaCan', 'Tape', 'Washer', 'Wire', 'Wood',
    'Wrench',
]
assert len(FOD_CLASSES) == 31, "FOD_CLASSES must stay at 31 entries"

# {class_id: name} — what inference.py and the WS payload use.
CLASS_NAMES: dict[int, str] = dict(enumerate(FOD_CLASSES))

# Default Severity weight (1-5). 5 = hardest / most damaging to engines and
# tyres (ICAO/FAA reasoning: hard metal >> plastic or paper).
# Admins can override these per class in the DB; this is only the seed value.
DEFAULT_SEVERITY: dict[str, int] = {
    # 5 — hard metal tools & fasteners
    'AdjustableWrench': 5, 'Bolt': 5, 'BoltNutSet': 5, 'BoltWasher': 5,
    'Hammer': 5, 'MetalPart': 5, 'MetalSheet': 5, 'Pliers': 5,
    'Screwdriver': 5, 'Wrench': 5,
    # 4 — smaller metal / dense debris
    'AdjustableClamp': 4, 'Battery': 4, 'ClampPart': 4, 'Cutter': 4,
    'Nail': 4, 'Nut': 4, 'Rock': 4, 'Screw': 4, 'Wire': 4,
    # 3 — bulky but softer
    'FuelCap': 3, 'Hose': 3, 'SodaCan': 3, 'Washer': 3, 'Wood': 3,
    # 2 — light plastic
    'LuggagePart': 2, 'Pen': 2, 'PlasticPart': 2,
    # 1 — paper / film / chips
    'Label': 1, 'LuggageTag': 1, 'PaintChip': 1, 'Tape': 1,
}
assert set(DEFAULT_SEVERITY) == set(FOD_CLASSES), "severity map must cover every class"


def severity_rows() -> list[tuple[int, str, int]]:
    """[(class_id, name, severity_weight)] ready for seeding."""
    return [(i, name, DEFAULT_SEVERITY[name]) for i, name in enumerate(FOD_CLASSES)]
