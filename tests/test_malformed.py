"""Damaged, truncated and empty files.

A photo library accumulates half copied files, interrupted card reads and
sidecars with the wrong extension. None of them may crash the scan, and
none of them may be dropped without appearing in the counts.
"""

from __future__ import annotations

import os

import pytest

from exif_atlas import exif
from tests import fixtures as fx


def build(tmp_path, name, data):
    return fx.write(str(tmp_path), name, data)


def test_empty_file(tmp_path):
    result = exif.read_photo(build(tmp_path, "a.jpg", b""))
    assert result.status == "no-exif"
    assert "empty" in result.detail


def test_two_byte_file(tmp_path):
    result = exif.read_photo(build(tmp_path, "b.jpg", b"\xff\xd8"))
    assert result.status == "no-exif"


@pytest.mark.parametrize("keep", [4, 10, 24, 40, 64, 96, 128, 200])
def test_jpeg_truncated_at_many_points(tmp_path, keep):
    """Cutting the file anywhere must produce a status, never a traceback."""
    whole = fx.simple_jpeg()
    result = exif.read_photo(build(tmp_path, "c%d.jpg" % keep, whole[:keep]))
    assert result.status in ("ok", "no-exif", "unsupported", "unreadable")


@pytest.mark.parametrize("keep", [8, 12, 20, 30, 44, 60, 90, 140])
def test_tiff_truncated_at_many_points(tmp_path, keep):
    whole = fx.tiff(fx.build_exif_block(*fx.standard_tags()))
    result = exif.read_photo(build(tmp_path, "d%d.tif" % keep, whole[:keep]))
    assert result.status in ("ok", "no-exif", "unsupported", "unreadable")


def test_app1_segment_length_runs_past_the_end(tmp_path):
    """A lying segment length is the classic way to walk off a buffer."""
    whole = bytearray(fx.simple_jpeg())
    marker = whole.index(b"\xff\xe1")
    whole[marker + 2:marker + 4] = (60000).to_bytes(2, "big")
    result = exif.read_photo(build(tmp_path, "e.jpg", bytes(whole)))
    assert result.status in ("ok", "no-exif")


def test_zero_length_jpeg_segment(tmp_path):
    data = b"\xff\xd8" + b"\xff\xe1" + b"\x00\x00" + b"\xff\xd9"
    result = exif.read_photo(build(tmp_path, "f.jpg", data))
    assert result.status == "no-exif"


def test_ifd_offset_points_outside_the_block(tmp_path):
    block = bytearray(fx.build_exif_block(*fx.standard_tags()))
    block[4:8] = (0x7FFFFFF0).to_bytes(4, "little")
    result = exif.read_photo(build(tmp_path, "g.jpg", fx.jpeg(bytes(block))))
    assert result.status == "no-exif"


def test_ifd_claims_an_impossible_entry_count(tmp_path):
    block = bytearray(fx.build_exif_block(*fx.standard_tags()))
    block[8:10] = (65535).to_bytes(2, "little")
    result = exif.read_photo(build(tmp_path, "h.jpg", fx.jpeg(bytes(block))))
    assert result.status in ("ok", "no-exif")


def test_value_offset_points_past_the_end(tmp_path):
    """An overflow pointer into nowhere must yield nothing, not garbage."""
    ifd0 = [(0x0110, fx.ASCII, "a model name long enough to overflow")]
    block = bytearray(fx.build_exif_block(ifd0, None))
    # The single entry's value offset sits at 8 + 2 + 8.
    block[18:22] = (0x0FFFFFFF).to_bytes(4, "little")
    result = exif.read_photo(build(tmp_path, "i.jpg", fx.jpeg(bytes(block))))
    assert result.status == "no-exif"


def test_self_referential_ifd_chain_terminates(tmp_path):
    """IFD0's next pointer aimed at itself is an infinite loop invitation."""
    ifd0, ifd = fx.standard_tags()
    block = bytearray(fx.build_exif_block(ifd0, ifd))
    count = int.from_bytes(block[8:10], "little")
    next_field = 8 + 2 + 12 * count
    block[next_field:next_field + 4] = (8).to_bytes(4, "little")
    result = exif.read_photo(build(tmp_path, "j.jpg", bytes(block)))
    assert result.status in ("ok", "no-exif")


def test_exif_pointer_that_loops_back_to_ifd0(tmp_path):
    ifd0 = [(0x0110, fx.ASCII, "X-T4"), (fx.TAG_EXIF_IFD, fx.LONG, [8])]
    block = fx.build_exif_block(ifd0, None)
    result = exif.read_photo(build(tmp_path, "k.jpg", fx.jpeg(block)))
    assert result.status in ("ok", "no-exif")


def test_unknown_value_type_is_skipped(tmp_path):
    """Type 99 does not exist. The entry must be dropped, not guessed at."""
    ifd0 = [(0x0110, fx.ASCII, "X-T4")]
    block = bytearray(fx.build_exif_block(ifd0, None))
    block[12:14] = (99).to_bytes(2, "little")
    tags, _, _ = exif.read_exif_tags(
        build(tmp_path, "l.jpg", fx.jpeg(bytes(block))))
    assert "Model" not in tags


def test_rational_with_a_zero_denominator(tmp_path):
    ifd0, ifd = fx.standard_tags()
    ifd = [entry for entry in ifd if entry[0] != 0x920A]
    ifd.append((0x920A, fx.RATIONAL, [(23, 0)]))
    path = build(tmp_path, "m.jpg", fx.jpeg(fx.build_exif_block(ifd0, ifd)))
    photo = exif.read_photo(path).photo
    assert photo.focal is None


def test_exif_present_but_carrying_nothing_useful(tmp_path):
    """A block with only a copyright string is not a photograph record."""
    block = fx.build_exif_block([(0x8298, fx.ASCII, "(c) somebody")], None)
    result = exif.read_photo(build(tmp_path, "n.jpg", fx.jpeg(block)))
    assert result.status == "no-exif"
    assert result.detail == "EXIF present but empty"


def test_bigtiff_is_declined_by_name(tmp_path):
    block = bytearray(fx.build_exif_block(*fx.standard_tags()))
    block[2:4] = (43).to_bytes(2, "little")
    result = exif.read_photo(build(tmp_path, "o.tif", bytes(block)))
    assert result.status == "no-exif"
    assert "BigTIFF" in result.detail


def test_ascii_value_with_no_terminator(tmp_path):
    ifd0 = [(0x0110, fx.UNDEFINED, b"X-T4")]
    block = bytearray(fx.build_exif_block(ifd0, None))
    block[12:14] = (2).to_bytes(2, "little")  # relabel the type as ASCII
    tags, _, _ = exif.read_exif_tags(
        build(tmp_path, "p.jpg", fx.jpeg(bytes(block))))
    assert tags.get("Model") == "X-T4"


def test_a_directory_named_like_an_image_is_not_read(tmp_path):
    os.makedirs(os.path.join(str(tmp_path), "album.jpg"))
    found = list(exif.iter_image_files(str(tmp_path)))
    assert found == []


def test_unreadable_file_is_reported(tmp_path):
    path = build(tmp_path, "q.jpg", fx.simple_jpeg())
    os.chmod(path, 0o000)
    try:
        result = exif.read_photo(path)
    finally:
        os.chmod(path, 0o644)
    assert result.status in ("unreadable", "ok")


def test_gps_ifd_pointing_at_rubbish(tmp_path):
    ifd0, ifd = fx.standard_tags()
    gps = fx.gps_tags(51.5, -0.12)
    block = bytearray(fx.build_exif_block(ifd0, ifd, gps))
    marker = block.find((0x8825).to_bytes(2, "little"))
    block[marker + 8:marker + 12] = (0x0FFFFFF0).to_bytes(4, "little")
    photo = exif.read_photo(build(tmp_path, "r.jpg",
                                  fx.jpeg(bytes(block)))).photo
    assert photo is not None
    assert photo.latitude is None
