"""The "you never shoot this" section.

Absence is only interesting when the gear could have reached the value.
Telling a photographer who owns one 23mm prime that they never shoot at
400mm is noise; telling the owner of a 24-70 that the middle of the zoom is
untouched is the finding.
"""

from __future__ import annotations

from collections import Counter

import pytest

from exif_atlas.analyze import (STANDARD_FOCALS, STANDARD_FSTOPS,
                                contiguous_runs, find_absences,
                                half_coverage_band, median_from_counter,
                                percentile_from_counter, snap_focal,
                                snap_fstop, snap_iso)


def test_a_value_never_used_inside_the_range_is_reported():
    counter = Counter({24: 500, 70: 500})
    absent = find_absences(counter, STANDARD_FOCALS, total=1000)
    values = {item["value"] for item in absent}
    assert 35 in values
    assert 50 in values


def test_values_outside_the_reachable_range_are_not_reported():
    """Nobody needs telling they own no 600mm lens."""
    counter = Counter({24: 500, 70: 500})
    absent = find_absences(counter, STANDARD_FOCALS, total=1000)
    values = {item["value"] for item in absent}
    assert 600 not in values
    assert 14 not in values


def test_values_actually_used_are_not_reported_as_absent():
    counter = Counter({24: 400, 35: 300, 50: 300})
    absent = find_absences(counter, STANDARD_FOCALS, total=1000)
    values = {item["value"] for item in absent}
    assert values.isdisjoint({24, 35, 50})


def test_a_near_miss_counts_as_use():
    """Shooting 34mm is not avoiding 35mm."""
    counter = Counter({24: 500, 34: 400, 70: 100})
    absent = find_absences(counter, STANDARD_FOCALS, total=1000)
    assert 35 not in {item["value"] for item in absent}


def test_something_used_rarely_is_still_reported():
    counter = Counter({24: 990, 35: 5, 70: 5})
    absent = find_absences(counter, STANDARD_FOCALS, total=1000)
    entry = next(i for i in absent if i["value"] == 35)
    assert entry["count"] == 5
    assert entry["share"] == pytest.approx(0.005)


def test_the_threshold_is_adjustable():
    counter = Counter({24: 900, 35: 50, 70: 50})
    loose = find_absences(counter, STANDARD_FOCALS, total=1000, threshold=0.1)
    tight = find_absences(counter, STANDARD_FOCALS, total=1000, threshold=0.01)
    assert 35 in {i["value"] for i in loose}
    assert 35 not in {i["value"] for i in tight}


def test_unbounded_mode_considers_every_candidate():
    counter = Counter({5.6: 1000})
    absent = find_absences(counter, STANDARD_FSTOPS, total=1000, bounded=False)
    values = {item["value"] for item in absent}
    assert 1.4 in values
    assert 22.0 in values


def test_no_photos_means_no_findings():
    assert find_absences(Counter(), STANDARD_FOCALS, total=0) == []


def test_no_observations_in_bounded_mode_means_no_findings():
    assert find_absences(Counter(), STANDARD_FOCALS, total=100) == []


def test_a_single_focal_length_yields_nothing_to_say():
    """One prime, one value. There is no gap inside a point."""
    absent = find_absences(Counter({23: 1000}), STANDARD_FOCALS, total=1000)
    assert absent == []


# --- runs of empty hours -----------------------------------------------------

def test_runs_group_consecutive_hours():
    assert contiguous_runs([1, 2, 3, 7, 8]) == [(1, 3), (7, 8)]


def test_runs_wrap_around_midnight():
    """22:00 to 03:00 is one quiet stretch, not two."""
    assert contiguous_runs([0, 1, 2, 3, 22, 23], modulus=24) == [(22, 3)]


def test_runs_do_not_wrap_when_the_ends_are_not_both_present():
    assert contiguous_runs([0, 1, 2, 20], modulus=24) == [(0, 2), (20, 20)]


def test_runs_of_everything_stay_one_run():
    assert contiguous_runs(list(range(24)), modulus=24) == [(0, 23)]


def test_runs_of_nothing():
    assert contiguous_runs([]) == []


def test_runs_deduplicate():
    assert contiguous_runs([5, 5, 6]) == [(5, 6)]


# --- the band that covers half the frames ------------------------------------

def test_half_coverage_band_on_a_prime_is_a_point():
    band = half_coverage_band(Counter({23: 1000}))
    assert (band["low"], band["high"]) == (23, 23)
    assert band["share"] == 1.0


def test_half_coverage_band_finds_the_narrowest_run():
    """A zoom used only at its ends must not be described by its midpoint."""
    counter = Counter({24: 450, 25: 100, 70: 450})
    band = half_coverage_band(counter)
    assert (band["low"], band["high"]) == (24, 25)
    assert band["count"] == 550


def test_half_coverage_band_widens_when_use_is_spread():
    """The band is the point of the section: a median alone hides this."""
    concentrated = half_coverage_band(Counter({33: 100, 35: 800, 37: 100}))
    spread = half_coverage_band(
        Counter({focal: 100 for focal in (16, 24, 35, 50, 85, 135)}))
    assert concentrated["high"] - concentrated["low"] == 0
    assert spread["high"] - spread["low"] > 15


def test_half_coverage_band_of_nothing_is_none():
    assert half_coverage_band(Counter()) is None


def test_percentile_walks_the_keys_in_order():
    counter = Counter({100: 10, 200: 10, 400: 10, 800: 10})
    assert percentile_from_counter(counter, 0.25) == 100
    assert percentile_from_counter(counter, 0.5) == 200
    assert percentile_from_counter(counter, 0.95) == 800


def test_percentile_of_nothing_is_none():
    assert percentile_from_counter(Counter(), 0.5) is None


def test_median_is_the_fifty_percent_point():
    assert median_from_counter(Counter({1: 1, 2: 1, 3: 1})) == 2


# --- snapping ----------------------------------------------------------------

def test_focal_snaps_to_the_nearest_marked_length():
    assert snap_focal(23.4) == 24
    assert snap_focal(33.0) == 35
    assert snap_focal(51.2) == 50


def test_focal_beyond_the_series_is_kept_as_measured():
    assert snap_focal(1200.0) == 1200


def test_focal_below_the_series_is_pulled_up_to_the_first_entry():
    assert snap_focal(3.0) == STANDARD_FOCALS[0]


def test_focal_of_zero_is_zero():
    assert snap_focal(0.0) == 0


def test_fstop_snaps_to_a_third_stop():
    assert snap_fstop(1.79) == pytest.approx(1.8)
    assert snap_fstop(5.5) == pytest.approx(5.6)
    assert snap_fstop(11.4) == pytest.approx(11.0)


def test_fstop_above_the_series_is_clamped():
    assert snap_fstop(200.0) == STANDARD_FSTOPS[-1]


def test_iso_snaps_to_the_marked_stops():
    assert snap_iso(160) == 160
    assert snap_iso(1234) == 1250
    assert snap_iso(0) == 0
