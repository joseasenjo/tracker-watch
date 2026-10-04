"""Fixed, published bands for the number of tracking services contacted before any interaction.

The band is a labelled range of a count, not a verdict on privacy or legality. Thresholds are
public (method page) and change only with a note. Results with low confidence get no band.
"""
from __future__ import annotations

# (label, lowest count, highest count or None for open-ended)
BANDS = (("A", 0, 2), ("B", 3, 5), ("C", 6, 9), ("D", 10, 14), ("E", 15, None))
BANDED_CONFIDENCE = ("high", "medium")


def band_for(count: int | None, confidence: str | None = "high") -> str | None:
    if count is None or confidence not in BANDED_CONFIDENCE:
        return None
    for label, low, high in BANDS:
        if count >= low and (high is None or count <= high):
            return label
    return None


def describe() -> list[dict]:
    rows = []
    for label, low, high in BANDS:
        rows.append({"label": label, "range": f"{low}+" if high is None else (str(low) if low == high else f"{low}–{high}")})
    return rows
