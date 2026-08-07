"""The aggregator: filters, counts, and the shape of the report it emits."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from exif_atlas.analyze import Aggregator, Options, format_shutter
from exif_atlas.exif import Photo, ScanResult


def frame(**kwargs):
    """One scanned frame, with plausible defaults."""
    settings = dict(
        path="/photos/x.jpg", container="jpeg", camera="FUJIFILM X-T4",
        lens="XF23mmF1.4 R", focal=23.0, fnumber=1.4, exposure=1 / 250.0,
        iso=400, taken=datetime(2024, 6, 21, 7, 30), tz_minutes=60,
    )
    settings.update(kwargs)
    return ScanResult(settings["path"], "ok", 512, settings["container"],
                      photo=Photo(**settings))


def fold(results, options=None):
    aggregator = Aggregator(options or Options())
    for result in results:
        aggregator.add_result(result)
    return aggregator


def report_of(results, options=None):
    return fold(results, options).report({"version": "0.1.0"})


# --- counting ---------------------------------------------------------------

def test_counts_add_up():
    aggregator = fold([
        frame(),
        frame(),
        ScanResult("/photos/a.png", "no-exif", 40, "png"),
        ScanResult("/photos/b.cr3", "unsupported", 0, "cr3"),
        ScanResult("/photos/c.jpg", "unreadable", 0, "unknown"),
    ])
    assert aggregator.files_seen == 5
    assert aggregator.photos == 2
    assert aggregator.no_exif == 1
    assert aggregator.unsupported == 1
    assert aggregator.unreadable == 1


def test_unparsed_formats_are_named_not_hidden():
    """A gap in the numbers has to be visible or the report is a lie."""
    report = report_of([
        frame(),
        ScanResult("/p/a.cr3", "unsupported", 0, "cr3"),
        ScanResult("/p/b.cr3", "unsupported", 0, "cr3"),
        ScanResult("/p/c.raf", "unsupported", 0, "raf"),
    ])
    scan = report["scan"]
    assert scan["unsupported"] == 3
    assert scan["unsupported_formats"] == {"cr3": 2, "raf": 1}


def test_containers_are_counted_by_kind():
    report = report_of([frame(container="jpeg"), frame(container="tiff"),
                        frame(container="tiff")])
    assert report["scan"]["containers"] == {"jpeg": 1, "tiff": 2}


# --- filters ----------------------------------------------------------------

def test_since_filter_drops_earlier_frames():
    results = [frame(taken=datetime(2023, 1, 1)),
               frame(taken=datetime(2024, 6, 1))]
    aggregator = fold(results, Options(since=date(2024, 1, 1)))
    assert aggregator.photos == 1
    assert aggregator.filtered == 1


def test_until_filter_drops_later_frames():
    results = [frame(taken=datetime(2023, 1, 1)),
               frame(taken=datetime(2024, 6, 1))]
    aggregator = fold(results, Options(until=date(2023, 12, 31)))
    assert aggregator.photos == 1


def test_camera_filter_is_a_case_insensitive_substring():
    results = [frame(camera="FUJIFILM X-T4"), frame(camera="NIKON Z 6")]
    assert fold(results, Options(camera="x-t4")).photos == 1
    assert fold(results, Options(camera="nikon")).photos == 1
    assert fold(results, Options(camera="leica")).photos == 0


def test_lens_filter_is_a_case_insensitive_substring():
    results = [frame(lens="XF23mmF1.4 R"), frame(lens="XF56mmF1.2 R")]
    assert fold(results, Options(lens="23mm")).photos == 1


def test_a_frame_with_no_date_survives_a_since_filter_only_if_undated():
    """Undated frames cannot be excluded by date without inventing one."""
    aggregator = fold([frame(taken=None)], Options(since=date(2024, 1, 1)))
    assert aggregator.photos + aggregator.filtered == 1


# --- gear -------------------------------------------------------------------

def test_gear_records_the_date_range_each_body_was_used():
    report = report_of([
        frame(camera="FUJIFILM X-T4", taken=datetime(2022, 3, 1)),
        frame(camera="FUJIFILM X-T4", taken=datetime(2023, 9, 1)),
        frame(camera="FUJIFILM X-H2", taken=datetime(2024, 1, 1)),
    ])
    bodies = {row["name"]: row for row in report["gear"]["cameras"]}
    assert bodies["FUJIFILM X-T4"]["count"] == 2
    assert bodies["FUJIFILM X-T4"]["first"].startswith("2022-03-01")
    assert bodies["FUJIFILM X-T4"]["last"].startswith("2023-09-01")
    assert bodies["FUJIFILM X-H2"]["count"] == 1


def test_body_and_lens_pairs_are_counted():
    report = report_of([
        frame(camera="A", lens="L1"), frame(camera="A", lens="L1"),
        frame(camera="A", lens="L2"),
    ])
    pairs = {(row["camera"], row["lens"]): row["count"]
             for row in report["gear"]["pairs"]}
    assert pairs[("A", "L1")] == 2
    assert pairs[("A", "L2")] == 1


def zoom_frames(focals, low=24.0, high=70.0, each=25):
    return [frame(lens="%g-%g" % (low, high), focal=float(f),
                  lens_min=low, lens_max=high)
            for f in focals for _ in range(each)]


def test_a_zoom_used_only_at_its_ends_is_described_as_such():
    results = zoom_frames([24.0, 25.0, 66.0, 70.0])
    report = report_of(results)
    zoom = report["focal"]["zooms"][0]
    assert zoom["lens"] == "24-70"
    assert zoom["wide_end"] + zoom["long_end"] > 0.9
    assert zoom["verdict"] == "shot at both ends"


def test_a_zoom_used_across_its_range_is_not_flagged_as_ends_only():
    results = [frame(lens="24-70", focal=float(f), lens_min=24.0,
                     lens_max=70.0)
               for f in range(24, 71) for _ in range(3)]
    zoom = report_of(results)["focal"]["zooms"][0]
    assert zoom["middle"] > 0.5
    assert zoom["verdict"] == "uses the range"


# --- distributions ----------------------------------------------------------

def test_focal_band_covering_half_the_frames_is_reported():
    results = [frame(focal=35.0)] * 60 + [frame(focal=200.0)] * 40
    band = report_of(results)["focal"]["band"]
    assert band["low"] == band["high"] == 35.0
    assert band["share"] >= 0.5


def test_focal_top_list_keeps_the_focal_that_was_recorded():
    """A 23mm prime must not be reported back as 24mm."""
    top = report_of([frame(focal=23.0)] * 40)["focal"]["top"]
    assert top[0]["value"] == 23.0


def habit_text(report):
    return " ".join("%s %s" % (row["title"], row["detail"])
                    for row in report["habits"]).lower()


def test_shooting_wide_open_is_flagged():
    results = [frame(fnumber=1.4, lens="XF23mmF1.4 R")] * 95 \
        + [frame(fnumber=8.0, lens="XF23mmF1.4 R")] * 5
    report = report_of(results)
    assert "wide open" in habit_text(report)
    assert report["aperture"]["wide_open"]["share"] > 0.9


def test_a_varied_aperture_habit_is_not_flagged_as_wide_open():
    results = [frame(fnumber=f) for f in (1.4, 2.8, 4.0, 5.6, 8.0, 11.0)] * 20
    assert "wide open" not in habit_text(report_of(results))


def test_iso_ceiling_is_the_high_percentile_not_the_maximum():
    """One accidental frame at ISO 25600 is not a working ceiling."""
    results = [frame(iso=400)] * 990 + [frame(iso=25600)] * 10
    iso = report_of(results)["iso"]
    assert iso["max"] == 25600
    assert iso["ceiling"] < 25600


def test_shutter_distribution_is_present():
    results = [frame(exposure=1 / 250.0)] * 30 + [frame(exposure=1.0)] * 10
    shutter = report_of(results)["shutter"]
    assert shutter["available"] is True
    assert sum(row["count"] for row in shutter["histogram"]) == 40


def test_hours_are_counted_on_the_local_clock():
    results = [frame(taken=datetime(2024, 6, 21, 6, 15))] * 5 \
        + [frame(taken=datetime(2024, 6, 21, 20, 45))] * 3
    hours = {row["hour"]: row["count"]
             for row in report_of(results)["time_of_day"]["hours"]}
    assert hours[6] == 5
    assert hours[20] == 3
    assert hours[13] == 0


def test_calendar_covers_every_year_in_the_span():
    """A year with nothing in it is a finding, so it keeps its row."""
    results = [frame(taken=datetime(2022, 5, 1)),
               frame(taken=datetime(2024, 8, 3))]
    calendar = report_of(results)["calendar"]
    assert [row["year"] for row in calendar["years"]] == [2022, 2023, 2024]
    fallow = calendar["years"][1]
    assert fallow["active_days"] == 0
    assert fallow["total"] == 0


def test_calendar_counts_distinct_shooting_days():
    results = [frame(taken=datetime(2024, 5, 1, 9)),
               frame(taken=datetime(2024, 5, 1, 17)),
               frame(taken=datetime(2024, 5, 2, 9))]
    assert report_of(results)["calendar"]["days_shot"] == 2


# --- GPS handling in the report ---------------------------------------------

def test_locations_are_rounded_by_default():
    results = [frame(latitude=51.507351, longitude=-0.127758)] * 5
    report = report_of(results)["locations"]
    assert report["mode"] == "round"
    assert report["decimal_places"] == 2
    place = report["clusters"][0]
    assert place["latitude"] == pytest.approx(51.51, abs=1e-9)
    assert place["longitude"] == pytest.approx(-0.13, abs=1e-9)
    assert place["label"] == "51.51 N, 0.13 W"


def test_the_exact_coordinate_never_reaches_the_report():
    """The rounded value is what is stored, not merely what is displayed."""
    results = [frame(latitude=51.507351, longitude=-0.127758)] * 5
    text = repr(report_of(results))
    assert "51.507351" not in text
    assert "0.127758" not in text


def test_precise_mode_keeps_more_digits():
    results = [frame(latitude=51.507351, longitude=-0.127758)] * 5
    report = report_of(results, Options(gps="precise"))["locations"]
    assert report["mode"] == "precise"
    assert report["decimal_places"] == 5
    assert report["clusters"][0]["latitude"] == pytest.approx(51.50735,
                                                              abs=1e-9)
    assert "front door" in report["note"]


def test_gps_off_removes_location_entirely():
    results = [frame(latitude=51.5, longitude=-0.12)] * 5
    report = report_of(results, Options(gps="off"))
    assert report["locations"]["clusters"] == []
    assert report["locations"]["photos"] == 0
    text = repr(report)
    assert "51.5," not in text and "-0.12" not in text


def test_no_gps_tags_means_no_places():
    report = report_of([frame()] * 3)["locations"]
    assert report["clusters"] == []
    assert "GPS tag" in report["note"]


def test_twilight_is_marked_when_a_fix_exists():
    results = [frame(latitude=51.5074, longitude=-0.1278,
                     taken=datetime(2024, 6, 21, 5, 0))] * 5
    window = report_of(results)["time_of_day"]["twilight"]
    assert window is not None
    assert 0 <= window["dawn_mean"] <= 24


def test_twilight_uses_a_coarse_position(tmp_path=None):
    """Even the sun calculation must not carry a precise coordinate."""
    results = [frame(latitude=51.507351, longitude=-0.127758)] * 5
    window = report_of(results)["time_of_day"]["twilight"]
    assert window["latitude"] == pytest.approx(51.51, abs=0.01)


def test_no_twilight_without_a_fix():
    assert report_of([frame()] * 3)["time_of_day"]["twilight"] is None


# --- absence ----------------------------------------------------------------

def test_absence_needs_enough_frames_to_be_worth_saying():
    small = report_of([frame(focal=24.0), frame(focal=70.0)])
    assert small["absences"]["enough_data"] is False
    assert small["absences"]["focal_lengths"] == []


def test_absence_reports_unused_focal_lengths():
    results = [frame(focal=24.0)] * 50 + [frame(focal=70.0)] * 50
    absences = report_of(results)["absences"]
    assert absences["enough_data"] is True
    assert 35 in {item["value"] for item in absences["focal_lengths"]}


def test_absence_reports_quiet_hours():
    results = [frame(taken=datetime(2024, 6, 1, 9))] * 100
    absences = report_of(results)["absences"]
    assert absences["hours"]
    assert 3 in absences["quiet_hours"]


def test_a_zoom_range_never_reached_is_reported():
    """Owning a 70-200 and never passing 100mm is a finding."""
    results = [frame(lens="70-200", focal=70.0, lens_min=70.0,
                     lens_max=200.0)] * 100
    unreached = report_of(results)["absences"]["unreached_focal_range"]
    assert unreached
    assert unreached[0]["lens"] == "70-200"


# --- report shape -----------------------------------------------------------

def test_report_has_every_section():
    report = report_of([frame()] * 5)
    for key in ("meta", "scan", "span", "gear", "focal", "aperture",
                "shutter", "iso", "time_of_day", "calendar", "locations",
                "absences", "habits", "filters"):
        assert key in report


def test_report_survives_an_empty_scan():
    report = report_of([])
    assert report["scan"]["photos"] == 0
    assert report["focal"]["available"] is False


def test_report_survives_frames_with_almost_no_metadata():
    bare = ScanResult("/p/x.jpg", "ok", 100, "jpeg",
                      photo=Photo(path="/p/x.jpg", container="jpeg",
                                  camera="Some Body"))
    report = report_of([bare] * 3)
    assert report["scan"]["photos"] == 3
    assert report["focal"]["available"] is False


def test_report_is_json_serialisable():
    import json
    report = report_of([frame(latitude=51.5, longitude=-0.12)] * 5)
    assert json.loads(json.dumps(report, default=str))


# --- formatting -------------------------------------------------------------

def test_shutter_speeds_read_the_way_a_camera_shows_them():
    assert format_shutter(1 / 250.0) == "1/250"
    assert format_shutter(1 / 60.0) == "1/60"
    assert format_shutter(1.0) == "1s"
    assert format_shutter(30.0) == "30s"
    assert format_shutter(2.5) == "2.5s"
    assert format_shutter(1 / 8000.0) == "1/8000"
    assert format_shutter(0) == "?"


def test_focal_histogram_value_agrees_with_its_label():
    """A row that says 23mm must not also say its value is 24."""
    aggregator = Aggregator()
    for _ in range(60):
        aggregator.add_result(frame(focal=23.0))
    rows = aggregator.report()["focal"]["histogram"]
    row = next(r for r in rows if r["count"] == 60)
    assert row["label"] == "23mm"
    assert row["value"] == 23.0


def test_focal_histogram_keeps_the_grouping_bucket():
    """The canonical series entry stays available for lining rows up."""
    aggregator = Aggregator()
    for _ in range(60):
        aggregator.add_result(frame(focal=23.0))
    rows = aggregator.report()["focal"]["histogram"]
    row = next(r for r in rows if r["count"] == 60)
    assert row["bucket"] == 24


def test_a_scattered_bucket_falls_back_to_the_canonical_name():
    """Zoom positions with no dominant value keep the series marking."""
    aggregator = Aggregator()
    for focal in (23.0, 23.5, 24.0, 24.5, 25.0):
        for _ in range(10):
            aggregator.add_result(frame(focal=focal))
    rows = aggregator.report()["focal"]["histogram"]
    row = next(r for r in rows if r["bucket"] == 24)
    assert row["value"] == row["bucket"]
    assert row["label"] == "24mm"
