"""Civil twilight, and the timezone guess that places it on a local clock."""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest

from exif_atlas.analyze import (civil_twilight, estimate_tz_minutes,
                                twilight_window)


def local(latitude, longitude, when, offset_hours):
    dawn, dusk = civil_twilight(latitude, longitude, when)
    return (dawn + offset_hours) % 24.0, (dusk + offset_hours) % 24.0


# Published civil twilight, local clock, from the 2024 almanac. Held to a
# quarter of an hour, which is well inside what the report draws but well
# outside what a sign error or a wrong day number would survive.
ALMANAC = [
    ("London midsummer", 51.5074, -0.1278, date(2024, 6, 21), 1,
     3 + 56 / 60, 22 + 8 / 60),
    ("London midwinter", 51.5074, -0.1278, date(2024, 12, 21), 0,
     7 + 23 / 60, 16 + 34 / 60),
    ("New York equinox", 40.7128, -74.0060, date(2024, 3, 21), -4,
     6 + 30 / 60, 19 + 36 / 60),
    ("Sydney midsummer", -33.8688, 151.2093, date(2024, 12, 21), 11,
     5 + 12 / 60, 20 + 35 / 60),
]


@pytest.mark.parametrize("name,lat,lon,day,offset,dawn,dusk", ALMANAC,
                         ids=[row[0] for row in ALMANAC])
def test_against_the_almanac(name, lat, lon, day, offset, dawn, dusk):
    got_dawn, got_dusk = local(lat, lon, day, offset)
    assert got_dawn == pytest.approx(dawn, abs=0.25)
    assert got_dusk == pytest.approx(dusk, abs=0.25)


def test_london_summer_nights_are_shorter_than_winter_nights():
    summer = local(51.5074, -0.1278, date(2024, 6, 21), 1)
    winter = local(51.5074, -0.1278, date(2024, 12, 21), 0)
    assert (summer[1] - summer[0]) > (winter[1] - winter[0]) + 8


def test_sydney_midsummer_is_the_southern_summer():
    """The seasons invert below the equator, which a sign error hides."""
    dawn_dec, dusk_dec = local(-33.8688, 151.2093, date(2024, 12, 21), 11)
    dawn_jun, dusk_jun = local(-33.8688, 151.2093, date(2024, 6, 21), 10)
    assert (dusk_dec - dawn_dec) > (dusk_jun - dawn_jun)


def test_quito_days_barely_move():
    """On the equator the length of the day is nearly constant."""
    spans = []
    for month in (3, 6, 9, 12):
        dawn, dusk = local(-0.1807, -78.4678, date(2024, month, 21), -5)
        spans.append(dusk - dawn)
    assert max(spans) - min(spans) < 0.5


def test_dusk_is_always_after_dawn_in_utc_hours():
    for latitude in (-60.0, -30.0, 0.0, 30.0, 60.0):
        for month in range(1, 13):
            result = civil_twilight(latitude, 0.0, date(2024, month, 21))
            assert result is not None
            assert result[1] > result[0]


def test_polar_summer_has_no_civil_night():
    assert civil_twilight(78.2, 15.6, date(2024, 6, 21)) is None


def test_polar_winter_has_no_civil_day():
    assert civil_twilight(78.2, 15.6, date(2024, 12, 21)) is None


def test_just_below_the_arctic_circle_still_resolves():
    assert civil_twilight(60.0, 25.0, date(2024, 6, 21)) is not None


def test_longitude_shifts_the_clock_by_four_minutes_a_degree():
    east = civil_twilight(51.5, 0.0, date(2024, 3, 21))
    west = civil_twilight(51.5, 15.0, date(2024, 3, 21))
    assert east[0] - west[0] == pytest.approx(1.0, abs=0.02)


def test_out_of_range_inputs_are_refused():
    with pytest.raises(ValueError):
        civil_twilight(91.0, 0.0, date(2024, 1, 1))
    with pytest.raises(ValueError):
        civil_twilight(0.0, 181.0, date(2024, 1, 1))


def test_twilight_window_spans_a_year():
    window = twilight_window(51.5074, -0.1278, 0)
    assert window["dawn_earliest"] < window["dawn_latest"]
    assert window["dusk_earliest"] < window["dusk_latest"]
    assert window["samples"] == 12
    assert window["polar_samples"] == 0


def test_twilight_window_moves_with_the_offset():
    at_utc = twilight_window(51.5074, -0.1278, 0)
    at_plus_two = twilight_window(51.5074, -0.1278, 120)
    assert at_plus_two["dawn_mean"] - at_utc["dawn_mean"] \
        == pytest.approx(2.0, abs=0.01)


def test_twilight_window_counts_the_polar_samples():
    window = twilight_window(78.2, 15.6, 60)
    assert window is not None
    assert window["polar_samples"] > 0
    assert window["samples"] < 12


def test_twilight_window_is_none_where_it_never_resolves():
    assert twilight_window(89.9, 0.0, 0) is None


def test_timezone_comes_from_the_tag_when_present():
    offsets = Counter({60: 900, 0: 12})
    minutes, source = estimate_tz_minutes(offsets, -0.12)
    assert minutes == 60
    assert "OffsetTime" in source


def test_timezone_falls_back_to_longitude():
    minutes, source = estimate_tz_minutes(Counter(), 151.2)
    assert minutes == 600
    assert "longitude" in source


def test_timezone_falls_back_to_longitude_in_the_west():
    minutes, _ = estimate_tz_minutes(Counter(), -74.0)
    assert minutes == -300


def test_timezone_with_nothing_to_go_on_assumes_utc():
    minutes, source = estimate_tz_minutes(Counter(), None)
    assert minutes == 0
    assert "UTC" in source


# ---------------------------------------------------------------------------
# Why the band is missing, when it is missing
# ---------------------------------------------------------------------------


def _report(latitude=None, longitude=None, gps="round"):
    from datetime import datetime

    from exif_atlas.analyze import Aggregator, Options
    from exif_atlas.exif import Photo, ScanResult

    aggregator = Aggregator(Options(gps=gps))
    for hour in range(24):
        photo = Photo(path="/p/%d.jpg" % hour, container="jpeg",
                      camera="X-T4", lens="XF23mmF1.4 R", focal=23.0,
                      fnumber=2.0, exposure=1 / 250.0, iso=200,
                      taken=datetime(2024, 6, 21, hour, 0),
                      latitude=latitude, longitude=longitude)
        aggregator.add_result(
            ScanResult(photo.path, "ok", 512, "jpeg", photo=photo))
    return aggregator.report()["time_of_day"]


def test_a_library_with_no_coordinate_says_so():
    assert _report()["twilight_missing"] == "no-coordinate"


def test_no_gps_mode_is_reported_as_omitted_not_as_missing():
    """--no-gps is a choice, not an absence, and should not read as one."""
    section = _report(51.5074, -0.1278, gps="off")
    assert section["twilight_missing"] == "omitted"


def test_a_polar_library_is_not_blamed_on_missing_gps():
    """At the pole no sample day resolves, but the GPS was there."""
    section = _report(89.5, 15.65)
    assert section["twilight"] is None
    assert section["twilight_missing"] == "undefined"
    assert section["twilight_latitude"] == pytest.approx(89.5)


def test_a_high_arctic_library_still_gets_a_band_from_the_days_that_resolve():
    """At 78N most of the year has no civil night, but the rest does."""
    section = _report(78.22, 15.65)
    assert section["twilight"] is not None
    assert section["twilight"]["polar_samples"] > 0


def test_a_temperate_library_still_gets_its_band():
    section = _report(51.5074, -0.1278)
    assert section["twilight"] is not None
    assert section["twilight_missing"] is None


def test_the_polar_explanation_reaches_the_page():
    from exif_atlas.analyze import Aggregator, Options
    from exif_atlas.exif import Photo, ScanResult
    from exif_atlas.render import render_html
    from datetime import datetime

    aggregator = Aggregator(Options())
    for hour in range(24):
        photo = Photo(path="/p/%d.jpg" % hour, container="jpeg",
                      camera="X-T4", lens="L", focal=23.0, fnumber=2.0,
                      exposure=1 / 250.0, iso=200,
                      taken=datetime(2024, 6, 21, hour, 0),
                      latitude=89.5, longitude=15.65)
        aggregator.add_result(
            ScanResult(photo.path, "ok", 512, "jpeg", photo=photo))
    html = render_html(aggregator.report())
    assert "Civil twilight does not resolve" in html
    assert "No GPS coordinate was available" not in html


def test_greenwich_meridian_is_not_treated_as_a_missing_fix():
    """Longitude zero is London, not a camera without a fix."""
    section = _report(51.4779, 0.0)
    assert section["twilight"] is not None
