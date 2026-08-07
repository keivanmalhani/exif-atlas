"""Every field extractor, exercised on tag dictionaries directly.

These run without touching the filesystem: the extractors take a parsed tag
dictionary, so each one can be pinned down on its own.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from exif_atlas import exif


# --- camera -----------------------------------------------------------------

def test_camera_drops_a_redundant_make_prefix():
    assert exif.extract_camera(
        {"Make": "Canon", "Model": "Canon EOS R6"}) == "Canon EOS R6"


def test_camera_prepends_the_make_when_the_model_stands_alone():
    assert exif.extract_camera(
        {"Make": "FUJIFILM", "Model": "X-T4"}) == "FUJIFILM X-T4"


def test_camera_uses_only_the_first_word_of_a_long_make():
    assert exif.extract_camera(
        {"Make": "NIKON CORPORATION", "Model": "Z 6"}) == "NIKON Z 6"


def test_camera_falls_back_to_the_dng_unique_model():
    assert exif.extract_camera({"UniqueCameraModel": "DJI FC3411"}) \
        == "DJI FC3411"


def test_camera_is_none_when_nothing_identifies_the_body():
    assert exif.extract_camera({}) is None


def test_camera_rejects_placeholder_strings():
    assert exif.extract_camera({"Make": "unknown", "Model": "n/a"}) is None


def test_camera_collapses_padding_and_nulls():
    assert exif.extract_camera(
        {"Make": "SONY\x00", "Model": "  ILCE-7M4  "}) == "SONY ILCE-7M4"


# --- lens -------------------------------------------------------------------

def test_lens_uses_the_model_when_present():
    assert exif.extract_lens({"LensModel": "XF16-55mmF2.8 R LM WR"}) \
        == "XF16-55mmF2.8 R LM WR"


def test_lens_prefixes_the_make_when_the_model_omits_it():
    assert exif.extract_lens(
        {"LensMake": "Sigma", "LensModel": "35mm F1.4 DG HSM"}) \
        == "Sigma 35mm F1.4 DG HSM"


def test_lens_falls_back_to_the_declared_zoom_range():
    tags = {"LensSpecification": [(24, 1), (70, 1), (28, 10), (28, 10)]}
    assert exif.extract_lens(tags) == "24-70mm lens"


def test_lens_fallback_reads_a_prime_as_a_single_focal():
    tags = {"LensSpecification": [(50, 1), (50, 1), (18, 10), (18, 10)]}
    assert exif.extract_lens(tags) == "50mm lens"


def test_lens_is_none_when_nothing_is_recorded():
    assert exif.extract_lens({}) is None


def test_lens_range_is_ordered_low_to_high():
    tags = {"LensSpecification": [(200, 1), (70, 1), (28, 10), (28, 10)]}
    assert exif.extract_lens_range(tags) == (70.0, 200.0)


def test_lens_range_rejects_absurd_values():
    tags = {"LensSpecification": [(1, 1), (99999, 1)]}
    assert exif.extract_lens_range(tags) is None


def test_lens_range_rejects_zero():
    tags = {"LensSpecification": [(0, 1), (0, 1), (0, 1), (0, 1)]}
    assert exif.extract_lens_range(tags) is None


# --- focal length -----------------------------------------------------------

def test_focal_reads_a_rational():
    assert exif.extract_focal({"FocalLength": (2300, 100)}) == pytest.approx(23)


def test_focal_rejects_zero_and_nonsense():
    assert exif.extract_focal({"FocalLength": (0, 1)}) is None
    assert exif.extract_focal({"FocalLength": (600000, 1)}) is None
    assert exif.extract_focal({}) is None


def test_focal_rejects_a_zero_denominator():
    assert exif.extract_focal({"FocalLength": (23, 0)}) is None


def test_focal35_is_separate_from_the_real_focal():
    tags = {"FocalLength": (2300, 100), "FocalLengthIn35mmFilm": 35}
    assert exif.extract_focal(tags) == pytest.approx(23)
    assert exif.extract_focal35(tags) == pytest.approx(35)


# --- aperture ---------------------------------------------------------------

def test_fnumber_reads_a_rational():
    assert exif.extract_fnumber({"FNumber": (18, 10)}) == pytest.approx(1.8)


def test_fnumber_falls_back_to_the_apex_aperture_value():
    # APEX 2 is f/2, since f = 2 ** (Av / 2).
    assert exif.extract_fnumber({"ApertureValue": (2, 1)}) == pytest.approx(2.0)


def test_fnumber_rejects_values_outside_any_real_lens():
    assert exif.extract_fnumber({"FNumber": (1, 10)}) is None
    assert exif.extract_fnumber({"FNumber": (500, 1)}) is None
    assert exif.extract_fnumber({}) is None


# --- shutter ----------------------------------------------------------------

def test_exposure_reads_a_rational():
    assert exif.extract_exposure({"ExposureTime": (1, 250)}) \
        == pytest.approx(1 / 250)


def test_exposure_falls_back_to_the_apex_shutter_speed():
    # APEX 8 is 1/256s, since t = 2 ** -Tv.
    assert exif.extract_exposure({"ShutterSpeedValue": (8, 1)}) \
        == pytest.approx(1 / 256)


def test_exposure_accepts_a_long_exposure():
    assert exif.extract_exposure({"ExposureTime": (30, 1)}) == pytest.approx(30)


def test_exposure_rejects_the_impossible():
    assert exif.extract_exposure({"ExposureTime": (7200, 1)}) is None
    assert exif.extract_exposure({"ExposureTime": (0, 1)}) is None
    assert exif.extract_exposure({}) is None


# --- ISO --------------------------------------------------------------------

def test_iso_reads_the_common_tag():
    assert exif.extract_iso({"ISOSpeedRatings": 6400}) == 6400


def test_iso_takes_the_first_entry_of_a_list():
    assert exif.extract_iso({"ISOSpeedRatings": [800, 0]}) == 800


def test_iso_skips_the_65535_escape_hatch():
    tags = {"ISOSpeedRatings": 65535, "ISOSpeed": 25600}
    assert exif.extract_iso(tags) == 25600


def test_iso_is_none_when_absent():
    assert exif.extract_iso({}) is None


# --- timestamps -------------------------------------------------------------

def test_parse_datetime_reads_the_exif_form():
    assert exif.parse_datetime("2024:06:21 07:30:00") \
        == datetime(2024, 6, 21, 7, 30, 0)


def test_parse_datetime_tolerates_dashes_and_a_t_separator():
    assert exif.parse_datetime("2024-06-21T07:30:00") \
        == datetime(2024, 6, 21, 7, 30, 0)


def test_parse_datetime_tolerates_subsecond_digits():
    assert exif.parse_datetime("2024:06:21 07:30:00.482") \
        == datetime(2024, 6, 21, 7, 30, 0)


def test_parse_datetime_rejects_the_all_zero_placeholder():
    assert exif.parse_datetime("0000:00:00 00:00:00") is None


def test_parse_datetime_rejects_an_impossible_day():
    assert exif.parse_datetime("2024:02:31 10:00:00") is None


def test_parse_datetime_rejects_a_year_before_photography():
    assert exif.parse_datetime("1800:01:01 00:00:00") is None


def test_parse_datetime_folds_a_leap_second():
    assert exif.parse_datetime("2016:12:31 23:59:60").second == 59


def test_parse_datetime_rejects_non_strings():
    assert exif.parse_datetime(None) is None
    assert exif.parse_datetime(20240621) is None


def test_extract_datetime_prefers_the_original():
    tags = {"DateTime": "2024:01:01 00:00:00",
            "DateTimeOriginal": "2023:05:05 12:00:00"}
    assert exif.extract_datetime(tags).year == 2023


def test_parse_tz_offset_handles_both_signs():
    assert exif.parse_tz_offset("+05:30") == 330
    assert exif.parse_tz_offset("-08:00") == -480
    assert exif.parse_tz_offset("+00:00") == 0


def test_parse_tz_offset_rejects_junk():
    assert exif.parse_tz_offset("05:30") is None
    assert exif.parse_tz_offset("+25:00") is None
    assert exif.parse_tz_offset("") is None
    assert exif.parse_tz_offset(None) is None


# --- odds and ends ----------------------------------------------------------

def test_dimensions_prefer_the_exif_pixel_tags():
    tags = {"PixelXDimension": 6240, "PixelYDimension": 4160,
            "ImageWidth": 160, "ImageLength": 120}
    assert exif.extract_dimensions(tags) == (6240, 4160)


def test_dimensions_fall_back_to_the_tiff_tags():
    assert exif.extract_dimensions(
        {"ImageWidth": 5472, "ImageLength": 3648}) == (5472, 3648)


def test_dimensions_are_none_when_unrecorded():
    assert exif.extract_dimensions({}) is None


def test_orientation_is_range_checked():
    assert exif.extract_orientation({"Orientation": 6}) == 6
    assert exif.extract_orientation({"Orientation": 9}) is None
    assert exif.extract_orientation({}) is None


def test_flash_reads_only_the_fired_bit():
    assert exif.extract_flash({"Flash": 0}) is False
    assert exif.extract_flash({"Flash": 1}) is True
    assert exif.extract_flash({"Flash": 16}) is False   # present, did not fire
    assert exif.extract_flash({"Flash": 25}) is True    # fired, auto mode
    assert exif.extract_flash({}) is None
