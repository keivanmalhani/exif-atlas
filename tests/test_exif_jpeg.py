"""JPEG APP1 parsing, against fixtures built byte by byte."""

from __future__ import annotations

import os

import pytest

from exif_atlas import exif
from tests import fixtures as fx


def read(tmp_path, name, data):
    path = fx.write(str(tmp_path), name, data)
    return exif.read_exif_tags(path)


def test_jpeg_app1_is_found(tmp_path):
    ifd0, ifd = fx.standard_tags()
    tags, container, _ = read(tmp_path, "a.jpg",
                              fx.jpeg(fx.build_exif_block(ifd0, ifd)))
    assert container == "jpeg"
    assert tags["Model"] == "X-T4"


def test_jpeg_app1_after_other_segments(tmp_path):
    """APP1 is not required to be the first segment, and often is not."""
    ifd0, ifd = fx.standard_tags()
    data = fx.jpeg(fx.build_exif_block(ifd0, ifd),
                   extra_app=b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00",
                   comment=b"made by a camera")
    tags, _, _ = read(tmp_path, "b.jpg", data)
    assert tags["Model"] == "X-T4"


def test_jpeg_padded_marker_run(tmp_path):
    """A run of 0xFF fill bytes before a marker is legal and must be skipped."""
    ifd0, ifd = fx.standard_tags()
    good = fx.jpeg(fx.build_exif_block(ifd0, ifd))
    padded = good[:2] + b"\xff\xff\xff" + good[2:]
    tags, _, _ = read(tmp_path, "c.jpg", padded)
    assert tags["Model"] == "X-T4"


def test_jpeg_without_exif_raises(tmp_path):
    with pytest.raises(exif.ExifError):
        read(tmp_path, "d.jpg", fx.jpeg(None))


def test_jpeg_app1_that_is_not_exif_is_ignored(tmp_path):
    """APP1 also carries XMP. A reader that assumes otherwise misparses it."""
    xmp = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>"
    data = bytearray(b"\xff\xd8")
    data += b"\xff\xe1" + len(xmp).to_bytes(2, "big", signed=False)
    data = bytearray(b"\xff\xd8")
    payload = xmp
    data += b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    data += b"\xff\xda" + (8).to_bytes(2, "big") + b"\x01\x01\x00\x00\x3f\x00"
    data += b"\xff\xd9"
    with pytest.raises(exif.ExifError):
        read(tmp_path, "e.jpg", bytes(data))


def test_jpeg_stops_at_start_of_scan(tmp_path):
    """Pixel data must never be searched for metadata.

    The scan below contains a byte sequence that looks exactly like an EXIF
    APP1 segment. A reader that keeps walking past SOS finds it.
    """
    ifd0, ifd = fx.standard_tags(camera=("NIKON", "Z6"))
    decoy = b"\xff\xe1\x00\x10Exif\x00\x00II\x2a\x00\x08\x00\x00\x00"
    data = fx.jpeg(None, pixel_bytes=0)
    data = data[:-2] + decoy + b"\xff\xd9"
    with pytest.raises(exif.ExifError):
        read(tmp_path, "f.jpg", data)
    assert ifd0  # fixture built, deliberately not written into the file


def test_jpeg_big_endian_exif(tmp_path):
    ifd0, ifd = fx.standard_tags(camera=("Canon", "Canon EOS R6"))
    block = fx.build_exif_block(ifd0, ifd, order=fx.BIG)
    tags, _, _ = read(tmp_path, "g.jpg", fx.jpeg(block))
    assert tags["Model"] == "Canon EOS R6"
    assert tags["ISOSpeedRatings"] == 400


def test_jpeg_gps_ifd_is_walked(tmp_path):
    ifd0, ifd = fx.standard_tags()
    block = fx.build_exif_block(ifd0, ifd, fx.gps_tags(51.5074, -0.1278))
    tags, _, _ = read(tmp_path, "h.jpg", fx.jpeg(block))
    assert tags["GPSLatitudeRef"] == "N"
    assert tags["GPSLongitudeRef"] == "W"
    assert exif.extract_gps(tags) is not None


def test_jpeg_overflow_values_are_followed(tmp_path):
    """Values longer than four bytes live outside the directory entry."""
    long_name = "SIGMA 100-400mm F5-6.3 DG DN OS | Contemporary 021"
    ifd0, ifd = fx.standard_tags(lens=long_name)
    tags, _, _ = read(tmp_path, "i.jpg", fx.jpeg(fx.build_exif_block(ifd0, ifd)))
    assert tags["LensModel"] == long_name


def test_jpeg_reports_bytes_read(tmp_path):
    ifd0, ifd = fx.standard_tags()
    _, _, used = read(tmp_path, "j.jpg",
                      fx.jpeg(fx.build_exif_block(ifd0, ifd),
                              pixel_bytes=400_000))
    assert 0 < used < 64 * 1024


def test_read_photo_populates_every_field(tmp_path):
    path = fx.write(str(tmp_path), "k.jpg",
                    fx.simple_jpeg(gps=(48.8584, 2.2945)))
    result = exif.read_photo(path)
    assert result.status == "ok"
    photo = result.photo
    assert photo.camera == "FUJIFILM X-T4"
    assert photo.lens == "XF23mmF1.4 R"
    assert photo.focal == pytest.approx(23.0)
    assert photo.fnumber == pytest.approx(1.4)
    assert photo.exposure == pytest.approx(1 / 250.0)
    assert photo.iso == 400
    assert photo.taken.year == 2024
    assert photo.tz_minutes == 60
    assert photo.width == 6240 and photo.height == 4160
    assert photo.megapixels == pytest.approx(25.9584)
    assert photo.latitude == pytest.approx(48.8584, abs=1e-4)


def test_read_photo_on_missing_file_is_unreadable(tmp_path):
    result = exif.read_photo(os.path.join(str(tmp_path), "nope.jpg"))
    assert result.status == "unreadable"
    assert result.photo is None


def test_read_photo_never_raises_on_random_bytes(tmp_path):
    path = fx.write(str(tmp_path), "l.jpg", os.urandom(9000))
    result = exif.read_photo(path)
    assert result.status in ("no-exif", "unsupported", "unreadable")


def test_taken_utc_applies_the_offset(tmp_path):
    path = fx.write(str(tmp_path), "m.jpg",
                    fx.simple_jpeg(taken="2024:06:21 07:30:00",
                                   offset="+05:30"))
    photo = exif.read_photo(path).photo
    utc = photo.taken_utc()
    assert (utc.hour, utc.minute) == (2, 0)
