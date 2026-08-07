"""GPS: sign handling and rounding.

Coordinates carry their hemisphere in a separate tag, and the magnitude is
always positive. Every bug in this area comes from the same two places: a
reader that ignores the reference tag, and a rounding step that truncates
towards zero and so drags southern and western points back towards the
equator and the meridian.
"""

from __future__ import annotations

import pytest

from exif_atlas import exif
from exif_atlas.analyze import (Cell, Options, cluster_locations,
                                format_coordinate, round_coord)
from tests import fixtures as fx


# --- reading the sign out of the reference tags ------------------------------

CITIES = {
    "north east": (48.8584, 2.2945),      # Paris
    "north west": (40.6892, -74.0445),    # New York
    "south east": (-33.8568, 151.2153),   # Sydney
    "south west": (-22.9519, -43.2105),   # Rio de Janeiro
}


@pytest.mark.parametrize("name", sorted(CITIES))
def test_hemispheres_survive_a_round_trip(tmp_path, name):
    latitude, longitude = CITIES[name]
    ifd0, ifd = fx.standard_tags()
    block = fx.build_exif_block(ifd0, ifd, fx.gps_tags(latitude, longitude))
    path = fx.write(str(tmp_path), "%s.jpg" % name.replace(" ", "_"),
                    fx.jpeg(block))
    photo = exif.read_photo(path).photo
    assert photo.latitude == pytest.approx(latitude, abs=1e-4)
    assert photo.longitude == pytest.approx(longitude, abs=1e-4)


def test_south_reference_makes_latitude_negative():
    tags = {"GPSLatitude": [(33, 1), (51, 1), (24, 1)], "GPSLatitudeRef": "S",
            "GPSLongitude": [(151, 1), (12, 1), (55, 1)],
            "GPSLongitudeRef": "E"}
    latitude, longitude = exif.extract_gps(tags)
    assert latitude < 0
    assert longitude > 0


def test_west_reference_makes_longitude_negative():
    tags = {"GPSLatitude": [(40, 1), (41, 1), (21, 1)], "GPSLatitudeRef": "N",
            "GPSLongitude": [(74, 1), (2, 1), (40, 1)],
            "GPSLongitudeRef": "W"}
    latitude, longitude = exif.extract_gps(tags)
    assert latitude > 0
    assert longitude < 0


def test_a_negative_magnitude_does_not_flip_the_sign_twice():
    """Some writers emit a negative degree value as well as a ref of S."""
    tags = {"GPSLatitude": [(-33, 1), (51, 1), (24, 1)],
            "GPSLatitudeRef": "S",
            "GPSLongitude": [(151, 1), (12, 1), (55, 1)],
            "GPSLongitudeRef": "E"}
    latitude, _ = exif.extract_gps(tags)
    assert latitude == pytest.approx(-33.856667, abs=1e-5)


def test_lowercase_reference_letters_are_accepted():
    tags = {"GPSLatitude": [(10, 1), (0, 1), (0, 1)], "GPSLatitudeRef": "s",
            "GPSLongitude": [(20, 1), (0, 1), (0, 1)], "GPSLongitudeRef": "w"}
    assert exif.extract_gps(tags) == (-10.0, -20.0)


def test_missing_reference_letters_are_read_as_positive():
    tags = {"GPSLatitude": [(10, 1)], "GPSLongitude": [(20, 1)]}
    assert exif.extract_gps(tags) == (10.0, 20.0)


def test_a_nonsense_reference_letter_is_refused():
    tags = {"GPSLatitude": [(10, 1)], "GPSLatitudeRef": "Q",
            "GPSLongitude": [(20, 1)], "GPSLongitudeRef": "E"}
    assert exif.extract_gps(tags) is None


def test_the_null_island_fix_is_discarded():
    """0,0 is what a camera writes when it has no fix at all."""
    tags = {"GPSLatitude": [(0, 1), (0, 1), (0, 1)], "GPSLatitudeRef": "N",
            "GPSLongitude": [(0, 1), (0, 1), (0, 1)], "GPSLongitudeRef": "E"}
    assert exif.extract_gps(tags) is None


def test_out_of_range_coordinates_are_discarded():
    tags = {"GPSLatitude": [(100, 1)], "GPSLatitudeRef": "N",
            "GPSLongitude": [(20, 1)], "GPSLongitudeRef": "E"}
    assert exif.extract_gps(tags) is None


def test_degrees_and_decimal_minutes_form():
    """Two element coordinates, no seconds, are legal and common."""
    tags = {"GPSLatitude": [(51, 1), (30265, 1000)], "GPSLatitudeRef": "N",
            "GPSLongitude": [(0, 1), (7668, 1000)], "GPSLongitudeRef": "W"}
    latitude, longitude = exif.extract_gps(tags)
    assert latitude == pytest.approx(51.50442, abs=1e-4)
    assert longitude == pytest.approx(-0.1278, abs=1e-4)


def test_gps_absent_gives_none():
    assert exif.extract_gps({}) is None
    assert exif.extract_gps({"GPSLatitude": [(51, 1)]}) is None


# --- rounding ---------------------------------------------------------------

def test_rounding_is_symmetric_about_the_equator():
    assert round_coord(33.8688) == 33.87
    assert round_coord(-33.8688) == -33.87


def test_rounding_is_symmetric_about_the_meridian():
    assert round_coord(151.2093) == 151.21
    assert round_coord(-151.2093) == -151.21


@pytest.mark.parametrize("value", [
    0.005, -0.005, 1.005, -1.005, 12.345, -12.345, 89.995, -89.995,
])
def test_halfway_values_round_away_from_zero(value):
    """The case binary floating point gets wrong if you do not go via str."""
    rounded = round_coord(value)
    assert abs(rounded) == pytest.approx(abs(round_coord(-value)))
    assert (rounded >= 0) == (value >= 0)


def test_rounding_never_moves_a_point_by_more_than_a_cell():
    for value in (-179.999, -45.678, -0.001, 0.0, 0.001, 45.678, 179.999):
        assert abs(round_coord(value) - value) <= 0.005 + 1e-9


def test_rounding_does_not_produce_negative_zero():
    result = round_coord(-0.001)
    assert result == 0.0
    assert str(result) == "0.0"


def test_rounding_to_five_places_is_the_precise_mode():
    assert round_coord(-33.856784321, places=5) == -33.85678


def test_precise_option_asks_for_five_places():
    assert Options(gps="precise").gps_places == 5
    assert Options(gps="round").gps_places == 2
    assert Options(gps="off").gps_places == 2


def test_rounding_rejects_infinity():
    with pytest.raises(ValueError):
        round_coord(float("inf"))


def test_two_decimals_is_about_a_kilometre():
    """The claim in the help text, checked rather than asserted in prose."""
    from exif_atlas.analyze import _haversine_km
    assert _haversine_km(51.50, 0.0, 51.51, 0.0) == pytest.approx(1.11, abs=0.02)


# --- clustering -------------------------------------------------------------

def test_nearby_cells_become_one_place():
    cells = {(51.50, -0.12): Cell(count=10), (51.51, -0.13): Cell(count=5)}
    clusters = cluster_locations(cells, radius_km=25.0)
    assert len(clusters) == 1
    assert clusters[0]["count"] == 15


def test_distant_cells_stay_apart():
    cells = {(51.50, -0.12): Cell(count=10), (-33.86, 151.21): Cell(count=5)}
    clusters = cluster_locations(cells, radius_km=25.0)
    assert len(clusters) == 2
    assert [c["count"] for c in clusters] == [10, 5]


def test_clusters_are_ordered_by_size():
    cells = {(10.0, 10.0): Cell(count=3), (40.0, 40.0): Cell(count=30),
             (-20.0, -20.0): Cell(count=12)}
    clusters = cluster_locations(cells, radius_km=25.0)
    assert [c["count"] for c in clusters] == [30, 12, 3]


def test_cluster_centre_is_weighted_by_frame_count():
    cells = {(50.0, 0.0): Cell(count=9), (50.1, 0.0): Cell(count=1)}
    clusters = cluster_locations(cells, radius_km=50.0)
    assert clusters[0]["latitude"] == pytest.approx(50.01, abs=1e-6)


def test_clustering_nothing_gives_nothing():
    assert cluster_locations({}) == []


def test_formatted_coordinates_name_the_hemisphere():
    assert format_coordinate(-33.86, 151.21) == "33.86 S, 151.21 E"
    assert format_coordinate(40.69, -74.04) == "40.69 N, 74.04 W"
