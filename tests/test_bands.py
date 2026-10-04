import pytest

from traceguard.bands import BANDS, band_for, describe


@pytest.mark.parametrize("count, expected", [(0, "A"), (2, "A"), (3, "B"), (9, "B"), (10, "C"), (24, "C"),
                                              (25, "D"), (49, "D"), (50, "E"), (93, "E")])
def test_band_boundaries(count, expected):
    assert band_for(count, "high") == expected


def test_no_band_for_low_confidence_or_missing_count():
    assert band_for(12, "low") is None
    assert band_for(12, None) is None
    assert band_for(None, "high") is None
    assert band_for(12, "medium") == "C"


def test_bands_are_contiguous_and_cover_every_count():
    for (_, _, high), (_, next_low, _) in zip(BANDS, BANDS[1:]):
        assert high + 1 == next_low
    assert BANDS[0][1] == 0 and BANDS[-1][2] is None


def test_describe_gives_readable_ranges():
    assert [r["range"] for r in describe()] == ["0–2", "3–9", "10–24", "25–49", "50+"]
