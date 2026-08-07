"""The byte budget.

The whole claim of this tool is that it reads metadata and not photographs.
That claim is only worth making if it is measured, so these tests count the
bytes that actually cross the file boundary rather than trusting the number
the reader reports about itself.
"""

from __future__ import annotations

import builtins
import io
import os

import pytest

from exif_atlas import exif
from tests import fixtures as fx

# Deliberately larger than any header could be, and larger than the reader's
# own ceiling, so a regression that slurps the file cannot hide.
BIG = 24 * 1024 * 1024
BUDGET = 256 * 1024


REAL_OPEN = builtins.open


class CountingFile(io.RawIOBase):
    """A read-only file wrapper that tallies every byte handed out."""

    def __init__(self, path):
        self._fh = REAL_OPEN(path, "rb")
        self.total = 0

    def read(self, size=-1):
        data = self._fh.read(size)
        self.total += len(data)
        return data

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, offset, whence=os.SEEK_SET):
        return self._fh.seek(offset, whence)

    def tell(self):
        return self._fh.tell()

    def fileno(self):
        return self._fh.fileno()

    def close(self):
        self._fh.close()


@pytest.fixture
def counted(monkeypatch):
    """Replace open() so every byte read during a scan is counted."""
    handles = []

    def spy(path, mode="r", *args, **kwargs):
        if "b" in mode and "r" in mode:
            handle = CountingFile(path)
            handles.append(handle)
            return handle
        return REAL_OPEN(path, mode, *args, **kwargs)

    monkeypatch.setattr(exif, "open", spy, raising=False)
    monkeypatch.setattr(builtins, "open", spy)
    yield handles
    monkeypatch.undo()


def total(handles):
    return sum(handle.total for handle in handles)


def test_large_jpeg_costs_only_its_header(tmp_path, counted):
    path = fx.write(str(tmp_path), "big.jpg", fx.simple_jpeg(pixel_bytes=BIG))
    assert os.path.getsize(path) > BIG

    result = exif.read_photo(path)

    assert result.status == "ok"
    assert result.photo.camera == "FUJIFILM X-T4"
    assert total(counted) < BUDGET
    # And well under it: the header of this file is a few hundred bytes.
    assert total(counted) < 32 * 1024


def test_large_dng_costs_only_its_header(tmp_path, counted):
    block = fx.build_exif_block(*fx.standard_tags())
    path = fx.write(str(tmp_path), "big.dng", fx.tiff(block, trailing=BIG))
    assert os.path.getsize(path) > BIG

    result = exif.read_photo(path)

    assert result.status == "ok"
    assert total(counted) < BUDGET


def test_large_png_costs_only_its_header(tmp_path, counted):
    block = fx.build_exif_block(*fx.standard_tags())
    path = fx.write(str(tmp_path), "big.png", fx.png(block, pixel_bytes=BIG))
    assert os.path.getsize(path) > BIG
    assert exif.read_photo(path).status == "ok"
    assert total(counted) < BUDGET


def test_large_heic_costs_only_its_header(tmp_path, counted):
    block = fx.build_exif_block(*fx.standard_tags())
    path = fx.write(str(tmp_path), "big.heic", fx.heif(block, pixel_bytes=BIG))
    assert os.path.getsize(path) > BIG
    assert exif.read_photo(path).status == "ok"
    assert total(counted) < BUDGET


def test_a_jpeg_with_no_exif_at_all_still_stops_early(tmp_path, counted):
    """The expensive mistake is scanning a whole file to find nothing."""
    path = fx.write(str(tmp_path), "none.jpg", fx.jpeg(None, pixel_bytes=BIG))
    assert exif.read_photo(path).status == "no-exif"
    assert total(counted) < BUDGET


def test_the_cost_does_not_grow_with_the_file(tmp_path, counted):
    """Ten times the pixel data, byte for byte the same work.

    Both files here are far larger than the header cache, so the only thing
    varying between them is the amount of image data. If the reader touched
    any of it the two figures would differ.
    """
    small = fx.write(str(tmp_path), "s.jpg",
                     fx.simple_jpeg(pixel_bytes=2 * 1024 * 1024))
    large = fx.write(str(tmp_path), "l.jpg", fx.simple_jpeg(pixel_bytes=BIG))
    assert os.path.getsize(large) > 10 * os.path.getsize(small)

    exif.read_photo(small)
    for_small = total(counted)
    exif.read_photo(large)
    for_large = total(counted) - for_small

    assert for_large == for_small


def test_a_whole_folder_of_large_files_stays_within_budget(tmp_path, counted):
    root = str(tmp_path)
    for index in range(8):
        fx.write(root, "f%d.jpg" % index,
                 fx.simple_jpeg(pixel_bytes=2 * 1024 * 1024))
    on_disk = sum(os.path.getsize(p) for p in exif.iter_image_files(root))
    results = [exif.read_photo(p) for p in exif.iter_image_files(root)]

    assert all(r.status == "ok" for r in results)
    assert on_disk > 16 * 1024 * 1024
    assert total(counted) < 8 * BUDGET
    assert total(counted) < on_disk / 100


def test_the_reported_figure_is_not_an_understatement(tmp_path, counted):
    """read_photo publishes a byte count; it must not flatter itself."""
    path = fx.write(str(tmp_path), "r.jpg", fx.simple_jpeg(pixel_bytes=BIG))
    result = exif.read_photo(path)
    assert result.bytes_read >= total(counted)


def test_the_source_refuses_to_exceed_its_ceiling():
    """The guard itself, independent of any container."""
    source = exif._Source(io.BytesIO(b"\x00" * 4096), limit=1024)
    source.read(1024)
    with pytest.raises(exif.ExifError):
        source.read(1)


def test_the_declared_ceiling_is_the_one_in_use():
    assert exif.MAX_HEADER_BYTES == BUDGET
