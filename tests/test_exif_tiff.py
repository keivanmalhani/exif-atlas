"""TIFF and DNG parsing, plus the other containers that wrap a TIFF block."""

from __future__ import annotations

import struct

import pytest

from exif_atlas import exif
from tests import fixtures as fx


def read(tmp_path, name, data):
    return exif.read_exif_tags(fx.write(str(tmp_path), name, data))


def test_little_endian_tiff(tmp_path):
    ifd0, ifd = fx.standard_tags()
    tags, container, _ = read(tmp_path, "a.tif",
                              fx.tiff(fx.build_exif_block(ifd0, ifd)))
    assert container == "tiff"
    assert tags["Make"] == "FUJIFILM"


def test_big_endian_tiff(tmp_path):
    ifd0, ifd = fx.standard_tags(camera=("PENTAX", "K-1"))
    block = fx.build_exif_block(ifd0, ifd, order=fx.BIG)
    assert block[:2] == b"MM"
    tags, _, _ = read(tmp_path, "b.tif", fx.tiff(block))
    assert tags["Model"] == "K-1"


def test_dng_is_read_as_tiff(tmp_path):
    """DNG is a TIFF with a different extension and a longer tail."""
    ifd0, ifd = fx.standard_tags(camera=("Leica", "Q2"), lens="Summilux 28mm")
    data = fx.tiff(fx.build_exif_block(ifd0, ifd), trailing=200_000)
    tags, container, used = read(tmp_path, "c.dng", data)
    assert container == "tiff"
    assert tags["Model"] == "Q2"
    assert used <= 8192


def test_tiff_larger_than_the_head_cache_still_resolves_offsets(tmp_path):
    """Values can sit past the cached head; the reader must seek for them."""
    ifd0, ifd = fx.standard_tags()
    block = fx.build_exif_block(ifd0, ifd)
    padding = b"\x00" * (exif.FILE_HEAD_CACHE + 1024)
    tags, _, _ = read(tmp_path, "d.dng", block + padding)
    assert tags["LensModel"] == "XF23mmF1.4 R"


def test_vendor_tiff_magic_is_accepted(tmp_path):
    """Panasonic RW2 uses magic 0x55 where a plain TIFF uses 42."""
    ifd0, ifd = fx.standard_tags(camera=("Panasonic", "DC-S5"))
    block = fx.build_exif_block(ifd0, ifd, magic=0x0055)
    tags, _, _ = read(tmp_path, "e.rw2", fx.tiff(block))
    assert tags["Model"] == "DC-S5"


def test_unknown_tiff_magic_is_rejected(tmp_path):
    ifd0, ifd = fx.standard_tags()
    block = bytearray(fx.build_exif_block(ifd0, ifd))
    block[2:4] = struct.pack("<H", 1234)
    with pytest.raises(exif.ExifError):
        read(tmp_path, "f.tif", bytes(block))


def test_png_exif_chunk(tmp_path):
    ifd0, ifd = fx.standard_tags(camera=("Apple", "iPhone 15 Pro"))
    tags, container, _ = read(tmp_path, "g.png",
                              fx.png(fx.build_exif_block(ifd0, ifd)))
    assert container == "png"
    assert tags["Model"] == "iPhone 15 Pro"


def test_png_without_exif_chunk(tmp_path):
    with pytest.raises(exif.ExifError):
        read(tmp_path, "h.png", fx.png(None))


def test_webp_exif_chunk(tmp_path):
    ifd0, ifd = fx.standard_tags(camera=("Google", "Pixel 8"))
    tags, container, _ = read(tmp_path, "i.webp",
                              fx.webp(fx.build_exif_block(ifd0, ifd)))
    assert container == "webp"
    assert tags["Model"] == "Pixel 8"


def test_heif_exif_item(tmp_path):
    ifd0, ifd = fx.standard_tags(camera=("Apple", "iPhone 14"))
    tags, container, _ = read(tmp_path, "j.heic",
                              fx.heif(fx.build_exif_block(ifd0, ifd)))
    assert container == "heif"
    assert tags["Model"] == "iPhone 14"


def test_heif_exif_item_when_meta_follows_mdat(tmp_path):
    """Apple writes ftyp, free, mdat, meta. The reader must not stop at mdat.

    The scan used to break out of the top level box walk the moment it saw
    mdat, on the assumption that nothing useful follows the image data. On a
    real iPhone library that assumption drops 72 percent of the HEIC files
    with "no meta box" while their EXIF sits a few boxes further on. Walking
    past mdat costs eight bytes per box header because the source seeks
    rather than reads.
    """
    ifd0, ifd = fx.standard_tags(camera=("Apple", "iPhone 13 Pro Max"))
    tags, container, used = read(tmp_path, "apple.heic",
                                 fx.heif_meta_last(
                                     fx.build_exif_block(ifd0, ifd)))
    assert container == "heif"
    assert tags["Model"] == "iPhone 13 Pro Max"
    assert used < exif.MAX_HEADER_BYTES


def test_heif_without_exif_item(tmp_path):
    with pytest.raises(exif.ExifError):
        read(tmp_path, "k.heic", fx.heif(None))


def test_container_is_sniffed_not_trusted_from_the_extension(tmp_path):
    """A JPEG named .dng is still a JPEG."""
    ifd0, ifd = fx.standard_tags()
    tags, container, _ = read(tmp_path, "l.dng",
                              fx.jpeg(fx.build_exif_block(ifd0, ifd)))
    assert container == "jpeg"
    assert tags["Model"] == "X-T4"


def test_named_unsupported_format_is_counted_not_skipped(tmp_path):
    """CR3 has a private layout. It must be reported, not silently dropped."""
    path = fx.write(str(tmp_path), "m.cr3", b"\x00\x00\x00\x18ftypcrx ")
    result = exif.read_photo(path)
    assert result.status == "unsupported"
    assert result.container == "cr3"
    assert result.detail


def test_unknown_container_is_reported_as_unsupported(tmp_path):
    path = fx.write(str(tmp_path), "n.raw", b"NOTANIMAGE" * 40)
    result = exif.read_photo(path)
    assert result.status == "unsupported"
    assert result.container == "unknown"


def test_sub_ifd_pointers_are_followed(tmp_path):
    """Some raw files park the body name one level down, in a SubIFD.

    IFD0 here carries no Make and no Model, which is the only condition
    under which the reader descends. The frame would otherwise be filed
    under an unknown camera.
    """
    order = fx.LITTLE
    ifd0 = [(0x0112, fx.SHORT, [1])]
    exif_ifd = [(0x9003, fx.ASCII, "2024:03:03 12:00:00"),
                (0x920A, fx.RATIONAL, [(65, 1)])]
    header = struct.pack(order + "2sHI", b"II", 42, 8)
    entries = list(ifd0) + [(fx.TAG_EXIF_IFD, fx.LONG, [0]),
                            (0x014A, fx.LONG, [0])]
    sized, sized_over = fx.pack_ifd(entries, order, 8)
    exif_start = 8 + len(sized) + len(sized_over)
    exif_bytes, exif_over = fx.pack_ifd(exif_ifd, order, exif_start)
    sub_start = exif_start + len(exif_bytes) + len(exif_over)
    sub_bytes, sub_over = fx.pack_ifd(
        [(0x010F, fx.ASCII, "Hasselblad"), (0x0110, fx.ASCII, "X2D"),
         (0x0100, fx.LONG, [11656]), (0x0101, fx.LONG, [8742])],
        order, sub_start)
    entries = list(ifd0) + [(fx.TAG_EXIF_IFD, fx.LONG, [exif_start]),
                            (0x014A, fx.LONG, [sub_start])]
    ifd0_bytes, ifd0_over = fx.pack_ifd(entries, order, 8)
    block = (header + ifd0_bytes + ifd0_over + exif_bytes + exif_over
             + sub_bytes + sub_over)
    tags, _, _ = read(tmp_path, "o.dng", fx.tiff(block))
    assert tags["Model"] == "X2D"
    assert exif.extract_dimensions(tags) == (11656, 8742)


def test_iter_image_files_walks_recursively(tmp_path):
    root = str(tmp_path)
    fx.write(root, "one.jpg", b"x")
    fx.write(root, "sub/two.dng", b"x")
    fx.write(root, "sub/deeper/three.heic", b"x")
    fx.write(root, "notes.txt", b"x")
    fx.write(root, ".hidden.jpg", b"x")
    fx.write(root, "__pycache__/four.jpg", b"x")
    found = {p.rsplit("/", 1)[-1] for p in exif.iter_image_files(root)}
    assert found == {"one.jpg", "two.dng", "three.heic"}
