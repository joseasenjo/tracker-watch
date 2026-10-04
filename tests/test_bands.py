import pytest

from traceguard.bands import BANDS, band_for, describe


@pytest.mark.parametrize("count, expected", [(0, "A"), (2, "A"), (3, "B"), (5, "B"), (6, "C"), (9, "C"),
                                              (10, "D"), (14, "D"), (15, "E"), (40, "E")])
def test_band_boundaries(count, expected):
    assert band_for(count, "high") == expected


def test_no_band_for_low_confidence_or_missing_count():
    assert band_for(12, "low") is None
    assert band_for(12, None) is None
    assert band_for(None, "high") is None
    assert band_for(12, "medium") == "D"


def test_bands_are_contiguous_and_cover_every_count():
    for (_, _, high), (_, next_low, _) in zip(BANDS, BANDS[1:]):
        assert high + 1 == next_low
    assert BANDS[0][1] == 0 and BANDS[-1][2] is None


def test_describe_gives_readable_ranges():
    assert [r["range"] for r in describe()] == ["0–2", "3–5", "6–9", "10–14", "15+"]
