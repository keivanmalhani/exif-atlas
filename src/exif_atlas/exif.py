"""Read EXIF metadata straight out of image file headers.

Only the standard library is used, and only header bytes are read. Image
pixel data is never touched: for every container we understand, the reader
seeks to the metadata block, reads it, and stops. The number of bytes each
call consumed is reported on the result so callers (and tests) can prove it.

Containers understood here:

  JPEG      APP1 segment carrying the "Exif\\x00\\x00" marker
  TIFF      little and big endian, including the TIFF derived raw formats
            (DNG, NEF, ARW, CR2, ORF, RW2 and friends)
  HEIF      HEIC/HEIF/AVIF, via the ISO base media file format meta box
  PNG       eXIf chunk
  WebP      EXIF chunk inside the RIFF container

Anything else is reported as an unrecognised container rather than being
quietly dropped. Formats such as Canon CR3, Fujifilm RAF and Sigma X3F use
private layouts that are not implemented; they are counted and named in the
report so a gap in the numbers is always visible.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Sequence

__all__ = [
    "ExifError",
    "Photo",
    "ScanResult",
    "read_photo",
    "read_exif_tags",
    "iter_image_files",
    "parse_datetime",
    "parse_tz_offset",
    "extract_camera",
    "extract_lens",
    "extract_lens_range",
    "extract_focal",
    "extract_focal35",
    "extract_fnumber",
    "extract_exposure",
    "extract_iso",
    "extract_datetime",
    "extract_tz_offset",
    "extract_gps",
    "extract_dimensions",
    "extract_orientation",
    "extract_flash",
    "IMAGE_EXTENSIONS",
    "UNSUPPORTED_EXTENSIONS",
    "MAX_HEADER_BYTES",
]


class ExifError(Exception):
    """Raised when a header cannot be understood."""


# ---------------------------------------------------------------------------
# Budgets. These are deliberately tight: the point of the tool is that it
# reads headers, not photographs.
# ---------------------------------------------------------------------------

MAX_HEADER_BYTES = 256 * 1024   # hard ceiling on bytes read for one file
MAX_SEGMENT_BYTES = 128 * 1024  # largest single metadata block we will load
MAX_IFD_ENTRIES = 512           # sane cap on directory entries
MAX_IFD_DEPTH = 4               # ExifIFD inside IFD0 inside ... stop somewhere
MAX_VALUE_BYTES = 4096          # no single tag value we want is larger
MAX_JPEG_SEGMENTS = 64          # markers scanned before giving up
MAX_CHUNKS = 64                 # PNG / RIFF / BMFF boxes scanned
FILE_HEAD_CACHE = 4096          # first bytes of a TIFF kept after one read


# ---------------------------------------------------------------------------
# Extensions. Detection is by magic bytes; extensions only decide which files
# are worth opening and how to name a format in the report.
# ---------------------------------------------------------------------------

JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".jfif"}
TIFF_EXTENSIONS = {
    ".tif", ".tiff", ".dng", ".nef", ".nrw", ".arw", ".sr2", ".srf", ".cr2",
    ".pef", ".ptx", ".srw", ".orf", ".rw2", ".rwl", ".raw", ".iiq", ".3fr",
    ".fff", ".dcr", ".kdc", ".mos", ".erf", ".mef", ".gpr",
}
HEIF_EXTENSIONS = {".heic", ".heif", ".hif", ".avif"}
PNG_EXTENSIONS = {".png"}
WEBP_EXTENSIONS = {".webp"}

# Real photo formats with private container layouts we do not implement.
UNSUPPORTED_EXTENSIONS = {
    ".cr3", ".raf", ".x3f", ".mrw", ".bay", ".rwz", ".cap", ".eip", ".jxl",
}

IMAGE_EXTENSIONS = (
    JPEG_EXTENSIONS
    | TIFF_EXTENSIONS
    | HEIF_EXTENSIONS
    | PNG_EXTENSIONS
    | WEBP_EXTENSIONS
    | UNSUPPORTED_EXTENSIONS
)

SKIP_DIRECTORIES = {
    ".git", ".svn", ".hg", "__pycache__", ".cache", "@eaDir",
    ".Trashes", "$RECYCLE.BIN", "Thumbs", ".thumbnails",
}


# ---------------------------------------------------------------------------
# Byte sources
# ---------------------------------------------------------------------------


class _Source:
    """A file handle that counts every byte it hands out."""

    __slots__ = ("_fh", "bytes_read", "limit")

    def __init__(self, fh, limit: int = MAX_HEADER_BYTES) -> None:
        self._fh = fh
        self.bytes_read = 0
        self.limit = limit

    def _charge(self, n: int) -> None:
        self.bytes_read += n
        if self.bytes_read > self.limit:
            raise ExifError("header byte budget exceeded")

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        data = self._fh.read(n)
        self._charge(len(data))
        return data

    def pread(self, offset: int, n: int) -> bytes:
        if n <= 0 or offset < 0:
            return b""
        self._fh.seek(offset)
        return self.read(n)

    def skip(self, n: int) -> None:
        # Seeking is free: no bytes cross the boundary.
        self._fh.seek(n, os.SEEK_CUR)

    def tell(self) -> int:
        return self._fh.tell()


class _Window:
    """A view of TIFF structured bytes addressed from the TIFF header."""

    def get(self, offset: int, size: int) -> bytes:  # pragma: no cover - iface
        raise NotImplementedError


class _MemoryWindow(_Window):
    """A metadata block already resident in memory."""

    __slots__ = ("_data",)

    def __init__(self, data: bytes) -> None:
        self._data = data

    def get(self, offset: int, size: int) -> bytes:
        if offset < 0 or size <= 0 or offset >= len(self._data):
            return b""
        return self._data[offset:offset + size]


class _FileWindow(_Window):
    """A TIFF file read in place, with the first pages kept after one read."""

    __slots__ = ("_src", "_base", "_head")

    def __init__(self, src: _Source, base: int, head: bytes) -> None:
        self._src = src
        self._base = base
        self._head = head

    def get(self, offset: int, size: int) -> bytes:
        if offset < 0 or size <= 0:
            return b""
        end = offset + size
        if end <= len(self._head):
            return self._head[offset:end]
        return self._src.pread(self._base + offset, size)


# ---------------------------------------------------------------------------
# TIFF tag dictionary
# ---------------------------------------------------------------------------

TYPE_SIZES = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    5: 8,   # RATIONAL
    6: 1,   # SBYTE
    7: 1,   # UNDEFINED
    8: 2,   # SSHORT
    9: 4,   # SLONG
    10: 8,  # SRATIONAL
    11: 4,  # FLOAT
    12: 8,  # DOUBLE
}

_SIGNED_TYPES = {6, 8, 9, 10}

IFD0_TAGS = {
    0x0100: "ImageWidth",
    0x0101: "ImageLength",
    0x010F: "Make",
    0x0110: "Model",
    0x0112: "Orientation",
    0x0132: "DateTime",
    0x013B: "Artist",
    0x8298: "Copyright",
    0xC614: "UniqueCameraModel",
    0xC615: "LocalizedCameraModel",
}

EXIF_TAGS = {
    0x829A: "ExposureTime",
    0x829D: "FNumber",
    0x8822: "ExposureProgram",
    0x8827: "ISOSpeedRatings",
    0x8830: "SensitivityType",
    0x8832: "RecommendedExposureIndex",
    0x8833: "ISOSpeed",
    0x9003: "DateTimeOriginal",
    0x9004: "DateTimeDigitized",
    0x9010: "OffsetTime",
    0x9011: "OffsetTimeOriginal",
    0x9012: "OffsetTimeDigitized",
    0x9201: "ShutterSpeedValue",
    0x9202: "ApertureValue",
    0x9204: "ExposureBiasValue",
    0x9205: "MaxApertureValue",
    0x9207: "MeteringMode",
    0x9209: "Flash",
    0x920A: "FocalLength",
    0xA002: "PixelXDimension",
    0xA003: "PixelYDimension",
    0xA402: "ExposureMode",
    0xA403: "WhiteBalance",
    0xA405: "FocalLengthIn35mmFilm",
    0xA406: "SceneCaptureType",
    0xA430: "CameraOwnerName",
    0xA431: "BodySerialNumber",
    0xA432: "LensSpecification",
    0xA433: "LensMake",
    0xA434: "LensModel",
    0xA435: "LensSerialNumber",
}

GPS_TAGS = {
    0x0000: "GPSVersionID",
    0x0001: "GPSLatitudeRef",
    0x0002: "GPSLatitude",
    0x0003: "GPSLongitudeRef",
    0x0004: "GPSLongitude",
    0x0005: "GPSAltitudeRef",
    0x0006: "GPSAltitude",
    0x0007: "GPSTimeStamp",
    0x0012: "GPSMapDatum",
    0x001D: "GPSDateStamp",
}

TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825
TAG_SUB_IFDS = 0x014A


# ---------------------------------------------------------------------------
# TIFF walking
# ---------------------------------------------------------------------------

# TIFF magic numbers in the wild. 42 is the standard; the rest are vendor
# variations on an otherwise ordinary TIFF header.
TIFF_MAGICS = {
    42,      # TIFF, DNG, NEF, ARW, CR2, PEF, ...
    0x4F52,  # Olympus ORF ("IIRO")
    0x5352,  # Olympus ORF ("IIRS")
    0x004F,  # Olympus ORF variant
    0x0055,  # Panasonic RW2/RAW
}
BIGTIFF_MAGIC = 43


def _decode_value(win: _Window, order: str, typ: int, count: int,
                  inline: bytes):
    """Turn one IFD entry payload into a Python value."""
    unit = TYPE_SIZES.get(typ)
    if unit is None:
        return None
    size = unit * count
    if size <= 0 or size > MAX_VALUE_BYTES:
        return None
    if size <= 4:
        data = inline[:size]
    else:
        if len(inline) < 4:
            return None
        offset = struct.unpack(order + "I", inline)[0]
        data = win.get(offset, size)
    if len(data) < size:
        # Truncated value. Use what is there if it is a string, else drop it.
        if typ != 2:
            return None
        size = len(data)
        count = size

    if typ == 2:
        raw = data.split(b"\x00", 1)[0]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        text = text.strip()
        return text or None

    if typ in (1, 7):
        values = list(data)
    elif typ == 6:
        values = list(struct.unpack(order + "%db" % count, data))
    elif typ == 3:
        values = list(struct.unpack(order + "%dH" % count, data))
    elif typ == 8:
        values = list(struct.unpack(order + "%dh" % count, data))
    elif typ == 4:
        values = list(struct.unpack(order + "%dI" % count, data))
    elif typ == 9:
        values = list(struct.unpack(order + "%di" % count, data))
    elif typ in (5, 10):
        code = "I" if typ == 5 else "i"
        flat = struct.unpack(order + "%d%s" % (count * 2, code), data)
        values = [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
    elif typ == 11:
        values = list(struct.unpack(order + "%df" % count, data))
    elif typ == 12:
        values = list(struct.unpack(order + "%dd" % count, data))
    else:  # pragma: no cover - guarded by TYPE_SIZES lookup
        return None

    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return values


def _walk_ifd(win: _Window, order: str, offset: int, names: dict,
              out: dict, seen: set, depth: int = 0,
              follow: bool = True) -> None:
    """Read one IFD, storing wanted tags into out."""
    if depth > MAX_IFD_DEPTH or offset <= 0 or offset in seen:
        return
    seen.add(offset)

    head = win.get(offset, 2)
    if len(head) < 2:
        return
    entries = struct.unpack(order + "H", head)[0]
    if entries == 0:
        return
    entries = min(entries, MAX_IFD_ENTRIES)

    body = win.get(offset + 2, entries * 12)
    usable = len(body) // 12
    if usable == 0:
        return

    pending_pointers: list[tuple[int, dict]] = []
    for index in range(usable):
        chunk = body[index * 12:index * 12 + 12]
        tag, typ, count = struct.unpack(order + "HHI", chunk[:8])
        inline = chunk[8:12]

        if follow and tag == TAG_EXIF_IFD:
            value = _decode_value(win, order, typ, count, inline)
            if isinstance(value, int) and value > 0:
                pending_pointers.append((value, EXIF_TAGS))
            continue
        if follow and tag == TAG_GPS_IFD:
            value = _decode_value(win, order, typ, count, inline)
            if isinstance(value, int) and value > 0:
                pending_pointers.append((value, GPS_TAGS))
            continue

        name = names.get(tag)
        if name is None or name in out:
            # Unwanted tags are skipped without reading their value, which
            # is what keeps the byte count down on raw files.
            continue
        value = _decode_value(win, order, typ, count, inline)
        if value is not None:
            out[name] = value

    for pointer, table in pending_pointers:
        _walk_ifd(win, order, pointer, table, out, seen, depth + 1,
                  follow=False)


def _read_tiff_structure(win: _Window, src: _Source | None = None) -> dict:
    """Parse a TIFF header and the directories that matter."""
    header = win.get(0, 8)
    if len(header) < 8:
        raise ExifError("truncated TIFF header")
    if header[:2] == b"II":
        order = "<"
    elif header[:2] == b"MM":
        order = ">"
    else:
        raise ExifError("not a TIFF byte order mark")

    magic = struct.unpack(order + "H", header[2:4])[0]
    if magic == BIGTIFF_MAGIC:
        raise ExifError("BigTIFF is not supported")
    if magic not in TIFF_MAGICS:
        raise ExifError("unexpected TIFF magic %d" % magic)

    first_ifd = struct.unpack(order + "I", header[4:8])[0]
    tags: dict = {}
    seen: set = set()
    _walk_ifd(win, order, first_ifd, IFD0_TAGS, tags, seen)

    if "Make" not in tags and "Model" not in tags:
        # Some raw files park the camera identity in a SubIFD. Look one level
        # down before giving up, but only for the identity tags.
        sub = _find_sub_ifds(win, order, first_ifd)
        for candidate in sub[:4]:
            _walk_ifd(win, order, candidate, IFD0_TAGS, tags, seen, depth=1)
            if "Model" in tags:
                break

    tags["_byte_order"] = "little" if order == "<" else "big"
    return tags


def _find_sub_ifds(win: _Window, order: str, offset: int) -> list[int]:
    head = win.get(offset, 2)
    if len(head) < 2:
        return []
    entries = min(struct.unpack(order + "H", head)[0], MAX_IFD_ENTRIES)
    body = win.get(offset + 2, entries * 12)
    found: list[int] = []
    for index in range(len(body) // 12):
        chunk = body[index * 12:index * 12 + 12]
        tag, typ, count = struct.unpack(order + "HHI", chunk[:8])
        if tag != TAG_SUB_IFDS:
            continue
        value = _decode_value(win, order, typ, count, chunk[8:12])
        if isinstance(value, int):
            found.append(value)
        elif isinstance(value, list):
            found.extend(v for v in value if isinstance(v, int))
    return [v for v in found if v > 0]


# ---------------------------------------------------------------------------
# Container handling
# ---------------------------------------------------------------------------


def _exif_from_jpeg(src: _Source) -> dict:
    marker_head = src.read(2)
    if marker_head != b"\xff\xd8":
        raise ExifError("not a JPEG")

    for _ in range(MAX_JPEG_SEGMENTS):
        head = src.read(2)
        if len(head) < 2:
            raise ExifError("truncated JPEG")
        if head[0] != 0xFF:
            raise ExifError("lost JPEG marker alignment")
        marker = head[1]
        while marker == 0xFF:
            filler = src.read(1)
            if not filler:
                raise ExifError("truncated JPEG")
            marker = filler[0]

        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD9, 0xDA):
            break  # end of image, or start of the pixels we refuse to read

        size_bytes = src.read(2)
        if len(size_bytes) < 2:
            raise ExifError("truncated JPEG segment")
        length = struct.unpack(">H", size_bytes)[0]
        if length < 2:
            raise ExifError("bad JPEG segment length")
        payload_len = length - 2

        if marker == 0xE1 and payload_len >= 6:
            take = min(payload_len, MAX_SEGMENT_BYTES)
            payload = src.read(take)
            if payload[:6] == b"Exif\x00\x00":
                return _read_tiff_structure(_MemoryWindow(payload[6:]))
            if payload_len > take:
                src.skip(payload_len - take)
            continue

        src.skip(payload_len)

    raise ExifError("no EXIF APP1 segment")


def _exif_from_tiff(src: _Source, head: bytes) -> dict:
    if len(head) < FILE_HEAD_CACHE:
        # Short file: everything we will ever need is already in memory.
        return _read_tiff_structure(_MemoryWindow(head))
    return _read_tiff_structure(_FileWindow(src, 0, head))


def _exif_from_png(src: _Source) -> dict:
    signature = src.read(8)
    if signature != b"\x89PNG\r\n\x1a\n":
        raise ExifError("not a PNG")
    for _ in range(MAX_CHUNKS):
        header = src.read(8)
        if len(header) < 8:
            break
        length, kind = struct.unpack(">I4s", header)
        if kind == b"IDAT" or kind == b"IEND":
            break
        if kind == b"eXIf" and 0 < length <= MAX_SEGMENT_BYTES:
            payload = src.read(length)
            if payload[:6] == b"Exif\x00\x00":
                payload = payload[6:]
            return _read_tiff_structure(_MemoryWindow(payload))
        src.skip(length + 4)  # payload plus CRC
    raise ExifError("no eXIf chunk")


def _exif_from_webp(src: _Source) -> dict:
    header = src.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
        raise ExifError("not a WebP")
    for _ in range(MAX_CHUNKS):
        chunk = src.read(8)
        if len(chunk) < 8:
            break
        fourcc, size = struct.unpack("<4sI", chunk)
        if fourcc == b"EXIF" and 0 < size <= MAX_SEGMENT_BYTES:
            payload = src.read(size)
            if payload[:6] == b"Exif\x00\x00":
                payload = payload[6:]
            return _read_tiff_structure(_MemoryWindow(payload))
        src.skip(size + (size & 1))
    raise ExifError("no EXIF chunk")


def _bmff_boxes(src: _Source, start: int, end: int,
                limit: int = MAX_CHUNKS) -> Iterator[tuple[bytes, int, int]]:
    """Yield (type, payload_start, payload_end) for boxes in a range."""
    position = start
    for _ in range(limit):
        if position + 8 > end:
            return
        header = src.pread(position, 8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        body = position + 8
        if size == 1:
            extended = src.pread(body, 8)
            if len(extended) < 8:
                return
            size = struct.unpack(">Q", extended)[0]
            body += 8
        elif size == 0:
            size = end - position
        if size < 8:
            return
        box_end = min(position + size, end)
        if body > box_end:
            return
        yield kind, body, box_end
        position += size


def _exif_from_heif(src: _Source, size: int) -> dict:
    """Find the Exif item in an ISO base media file and read it."""
    meta_range = None
    # Do not stop at mdat. Apple writes ftyp, free, mdat, meta, so on a real
    # iPhone library the metadata is usually behind the image data, and an
    # early exit here reported "no meta box" for 2,611 of 3,622 HEIC files.
    # Walking past mdat is cheap: _bmff_boxes seeks to each box and reads
    # only its eight byte header, so the byte budget barely moves.
    for kind, body, box_end in _bmff_boxes(src, 0, size):
        if kind == b"meta":
            meta_range = (body + 4, box_end)  # skip version and flags
            break
    if meta_range is None:
        raise ExifError("no meta box")

    exif_items: set[int] = set()
    locations: dict[int, tuple[int, int]] = {}
    for kind, body, box_end in _bmff_boxes(src, meta_range[0], meta_range[1]):
        if kind == b"iinf":
            exif_items |= _parse_iinf(src, body, box_end)
        elif kind == b"iloc":
            locations = _parse_iloc(src, body, box_end)
    if not exif_items or not locations:
        raise ExifError("no Exif item")

    for item_id in sorted(exif_items):
        where = locations.get(item_id)
        if not where:
            continue
        offset, length = where
        if length <= 0 or length > MAX_SEGMENT_BYTES:
            continue
        payload = src.pread(offset, min(length, MAX_SEGMENT_BYTES))
        if len(payload) < 8:
            continue
        # The item begins with a four byte offset to the TIFF header.
        skip = struct.unpack(">I", payload[:4])[0]
        block = payload[4 + skip:]
        if block[:6] == b"Exif\x00\x00":
            block = block[6:]
        if len(block) >= 8:
            return _read_tiff_structure(_MemoryWindow(block))
    raise ExifError("Exif item not readable")


def _parse_iinf(src: _Source, start: int, end: int) -> set[int]:
    header = src.pread(start, 4)
    if len(header) < 4:
        return set()
    version = header[0]
    cursor = start + 4
    if version == 0:
        count_bytes = src.pread(cursor, 2)
        if len(count_bytes) < 2:
            return set()
        cursor += 2
    else:
        count_bytes = src.pread(cursor, 4)
        if len(count_bytes) < 4:
            return set()
        cursor += 4

    found: set[int] = set()
    for kind, body, box_end in _bmff_boxes(src, cursor, end):
        if kind != b"infe":
            continue
        info = src.pread(body, 4)
        if len(info) < 4:
            continue
        infe_version = info[0]
        if infe_version < 2:
            continue
        id_size = 2 if infe_version == 2 else 4
        payload = src.pread(body + 4, id_size + 6)
        if len(payload) < id_size + 6:
            continue
        if id_size == 2:
            item_id = struct.unpack(">H", payload[:2])[0]
        else:
            item_id = struct.unpack(">I", payload[:4])[0]
        item_type = payload[id_size + 2:id_size + 6]
        if item_type == b"Exif":
            found.add(item_id)
    return found


def _parse_iloc(src: _Source, start: int, end: int) -> dict[int, tuple[int, int]]:
    header = src.pread(start, 6)
    if len(header) < 6:
        return {}
    version = header[0]
    sizes = header[4]
    lengths = header[5]
    offset_size = sizes >> 4
    length_size = sizes & 0x0F
    base_size = lengths >> 4
    index_size = lengths & 0x0F if version in (1, 2) else 0

    cursor = start + 6
    if version < 2:
        raw = src.pread(cursor, 2)
        if len(raw) < 2:
            return {}
        item_count = struct.unpack(">H", raw)[0]
        cursor += 2
    else:
        raw = src.pread(cursor, 4)
        if len(raw) < 4:
            return {}
        item_count = struct.unpack(">I", raw)[0]
        cursor += 4

    def number(data: bytes) -> int:
        return int.from_bytes(data, "big") if data else 0

    out: dict[int, tuple[int, int]] = {}
    for _ in range(min(item_count, MAX_CHUNKS)):
        if cursor >= end:
            break
        id_size = 2 if version < 2 else 4
        raw = src.pread(cursor, id_size)
        if len(raw) < id_size:
            break
        item_id = number(raw)
        cursor += id_size
        if version in (1, 2):
            cursor += 2  # construction method
        cursor += 2      # data reference index
        base_offset = number(src.pread(cursor, base_size))
        cursor += base_size
        raw = src.pread(cursor, 2)
        if len(raw) < 2:
            break
        extent_count = struct.unpack(">H", raw)[0]
        cursor += 2
        first: tuple[int, int] | None = None
        for _ in range(min(extent_count, 8)):
            cursor += index_size
            extent_offset = number(src.pread(cursor, offset_size))
            cursor += offset_size
            extent_length = number(src.pread(cursor, length_size))
            cursor += length_size
            if first is None:
                first = (base_offset + extent_offset, extent_length)
        if first is not None:
            out[item_id] = first
    return out


# ---------------------------------------------------------------------------
# Field extractors. Each one is small on purpose so it can be tested alone.
# ---------------------------------------------------------------------------


def _as_float(value) -> float | None:
    """Convert a TIFF value to a float, tolerating rationals and lists."""
    if value is None:
        return None
    if isinstance(value, tuple) and len(value) == 2:
        numerator, denominator = value
        if denominator == 0:
            return None
        return numerator / denominator
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, list) and value:
        return _as_float(value[0])
    return None


def _clean(text) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = " ".join(text.replace("\x00", " ").split())
    if not cleaned:
        return None
    if cleaned.lower() in {"unknown", "n/a", "none", "----", "-"}:
        return None
    return cleaned


def extract_camera(tags: dict) -> str | None:
    """A single human readable body name, make prefix removed if redundant."""
    make = _clean(tags.get("Make"))
    model = _clean(tags.get("Model")) or _clean(tags.get("UniqueCameraModel"))
    if model is None:
        return make
    if make:
        first = make.split()[0]
        if model.lower().startswith(make.lower()):
            return model
        if model.lower().startswith(first.lower()):
            return model
        return "%s %s" % (first, model)
    return model


def extract_lens(tags: dict) -> str | None:
    """The lens name, falling back to the focal range if only that is known."""
    model = _clean(tags.get("LensModel"))
    if model:
        make = _clean(tags.get("LensMake"))
        if make and not model.lower().startswith(make.split()[0].lower()):
            return "%s %s" % (make.split()[0], model)
        return model

    spec = tags.get("LensSpecification")
    if isinstance(spec, list) and len(spec) >= 2:
        low = _as_float(spec[0])
        high = _as_float(spec[1])
        if low and high:
            if abs(low - high) < 0.51:
                return "%gmm lens" % round(low, 1)
            return "%g-%gmm lens" % (round(low, 1), round(high, 1))
    return None


def extract_lens_range(tags: dict) -> tuple[float, float] | None:
    """The focal range the lens declares, which is not the range you use.

    LensSpecification is four rationals: shortest focal, longest focal,
    maximum aperture at the short end, maximum aperture at the long end.
    Having the declared range makes it possible to say that a zoom owns a
    long end its owner has never once reached.
    """
    spec = tags.get("LensSpecification")
    if not isinstance(spec, list) or len(spec) < 2:
        return None
    low = _as_float(spec[0])
    high = _as_float(spec[1])
    if low is None or high is None or low <= 0 or high <= 0:
        return None
    if high < low:
        low, high = high, low
    if high > 5000:
        return None
    return low, high


def extract_focal(tags: dict) -> float | None:
    value = _as_float(tags.get("FocalLength"))
    if value is None or value <= 0 or value > 5000:
        return None
    return value


def extract_focal35(tags: dict) -> float | None:
    value = _as_float(tags.get("FocalLengthIn35mmFilm"))
    if value is None or value <= 0 or value > 5000:
        return None
    return value


def extract_fnumber(tags: dict) -> float | None:
    value = _as_float(tags.get("FNumber"))
    if value is None or value <= 0:
        apex = _as_float(tags.get("ApertureValue"))
        if apex is None or apex < 0 or apex > 20:
            return None
        value = 2.0 ** (apex / 2.0)
    if value <= 0.4 or value > 128:
        return None
    return value


def extract_exposure(tags: dict) -> float | None:
    value = _as_float(tags.get("ExposureTime"))
    if value is None or value <= 0:
        apex = _as_float(tags.get("ShutterSpeedValue"))
        if apex is None or abs(apex) > 30:
            return None
        value = 2.0 ** (-apex)
    if value <= 0 or value > 3600:
        return None
    return value


def extract_iso(tags: dict) -> int | None:
    for key in ("ISOSpeedRatings", "ISOSpeed", "RecommendedExposureIndex"):
        raw = tags.get(key)
        if isinstance(raw, list) and raw:
            raw = raw[0]
        value = _as_float(raw)
        if value is None:
            continue
        # 65535 is the documented "see SensitivityType" escape hatch.
        if value <= 0 or value >= 65535:
            continue
        return int(round(value))
    return None


def parse_datetime(text) -> datetime | None:
    """Parse an EXIF timestamp, which is local wall clock time."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip().replace("/", ":").replace("-", ":")
    if not cleaned or cleaned.startswith("0000"):
        return None
    parts = cleaned.replace("T", " ").split()
    if not parts:
        return None
    date_part = parts[0]
    time_part = parts[1] if len(parts) > 1 else "00:00:00"
    date_bits = date_part.split(":")
    time_bits = time_part.split(".")[0].split(":")
    if len(date_bits) != 3:
        return None
    while len(time_bits) < 3:
        time_bits.append("0")
    try:
        year, month, day = (int(bit) for bit in date_bits)
        hour, minute, second = (int(bit) for bit in time_bits[:3])
    except ValueError:
        return None
    if second == 60:
        second = 59
    if not (1826 <= year <= 2200):
        return None
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def parse_tz_offset(text) -> int | None:
    """Parse an EXIF OffsetTime string such as '+05:30' into minutes."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if len(cleaned) < 3 or cleaned[0] not in "+-":
        return None
    sign = 1 if cleaned[0] == "+" else -1
    body = cleaned[1:].replace(":", "")
    if not body.isdigit() or len(body) not in (2, 4):
        return None
    hours = int(body[:2])
    minutes = int(body[2:]) if len(body) == 4 else 0
    if hours > 14 or minutes > 59:
        return None
    return sign * (hours * 60 + minutes)


def extract_datetime(tags: dict) -> datetime | None:
    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        value = parse_datetime(tags.get(key))
        if value is not None:
            return value
    return None


def extract_tz_offset(tags: dict) -> int | None:
    for key in ("OffsetTimeOriginal", "OffsetTime", "OffsetTimeDigitized"):
        value = parse_tz_offset(tags.get(key))
        if value is not None:
            return value
    return None


def _dms_to_degrees(value) -> float | None:
    """Convert a GPS coordinate triple to signed decimal degrees."""
    if isinstance(value, tuple) and len(value) == 2:
        value = [value]
    if not isinstance(value, list) or not value:
        return None
    parts = [_as_float(item) for item in value[:3]]
    while len(parts) < 3:
        parts.append(0.0)
    if parts[0] is None:
        return None
    degrees = parts[0]
    minutes = parts[1] or 0.0
    seconds = parts[2] or 0.0
    return abs(degrees) + abs(minutes) / 60.0 + abs(seconds) / 3600.0


def extract_gps(tags: dict) -> tuple[float, float] | None:
    """Latitude and longitude in signed decimal degrees, or None.

    The hemisphere references are the part that gets mishandled: south and
    west are negative, and the magnitude in the coordinate triple is always
    positive, so the sign has to come from the reference tag alone.
    """
    latitude = _dms_to_degrees(tags.get("GPSLatitude"))
    longitude = _dms_to_degrees(tags.get("GPSLongitude"))
    if latitude is None or longitude is None:
        return None

    lat_ref = tags.get("GPSLatitudeRef")
    lon_ref = tags.get("GPSLongitudeRef")
    lat_ref = lat_ref.strip().upper()[:1] if isinstance(lat_ref, str) else ""
    lon_ref = lon_ref.strip().upper()[:1] if isinstance(lon_ref, str) else ""

    if lat_ref == "S":
        latitude = -latitude
    elif lat_ref not in ("N", ""):
        return None
    if lon_ref == "W":
        longitude = -longitude
    elif lon_ref not in ("E", ""):
        return None

    if not (-90.0 <= latitude <= 90.0) or not (-180.0 <= longitude <= 180.0):
        return None
    # A great many cameras write 0/0 when they have no fix at all.
    if latitude == 0.0 and longitude == 0.0:
        return None
    return latitude, longitude


def extract_dimensions(tags: dict) -> tuple[int, int] | None:
    width = _as_float(tags.get("PixelXDimension"))
    height = _as_float(tags.get("PixelYDimension"))
    if width is None or height is None:
        width = _as_float(tags.get("ImageWidth"))
        height = _as_float(tags.get("ImageLength"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return int(width), int(height)


def extract_orientation(tags: dict) -> int | None:
    value = _as_float(tags.get("Orientation"))
    if value is None:
        return None
    number = int(value)
    return number if 1 <= number <= 8 else None


def extract_flash(tags: dict) -> bool | None:
    value = _as_float(tags.get("Flash"))
    if value is None:
        return None
    return bool(int(value) & 1)


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Photo:
    """The handful of numbers this tool cares about."""

    path: str
    container: str
    camera: str | None = None
    lens: str | None = None
    lens_min: float | None = None
    lens_max: float | None = None
    focal: float | None = None
    focal35: float | None = None
    fnumber: float | None = None
    exposure: float | None = None
    iso: int | None = None
    taken: datetime | None = None
    tz_minutes: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    width: int | None = None
    height: int | None = None
    flash: bool | None = None
    orientation: int | None = None

    @property
    def megapixels(self) -> float | None:
        if self.width and self.height:
            return self.width * self.height / 1_000_000.0
        return None

    def taken_utc(self) -> datetime | None:
        if self.taken is None:
            return None
        offset = self.tz_minutes or 0
        return (self.taken - timedelta(minutes=offset)).replace(
            tzinfo=timezone.utc)


@dataclass(slots=True)
class ScanResult:
    """Outcome of looking at one file."""

    path: str
    status: str          # ok | no-exif | unsupported | unreadable
    bytes_read: int
    container: str
    photo: Photo | None = None
    detail: str = ""


def _sniff(head: bytes, extension: str) -> str:
    if head[:2] == b"\xff\xd8":
        return "jpeg"
    if head[:4] in (b"II\x2a\x00", b"MM\x00\x2a"):
        return "tiff"
    if head[:2] in (b"II", b"MM") and extension in TIFF_EXTENSIONS:
        return "tiff"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"crx ":
            return "cr3"
        return "heif"
    if head[:4] == b"FUJI":
        return "raf"
    if head[:4] in (b"FOVb", b"\x00\xffX3F"):
        return "x3f"
    if head[:4] == b"\x00MRM":
        return "mrw"
    return "unknown"


def read_exif_tags(path: str) -> tuple[dict, str, int]:
    """Return (tags, container, bytes_read) for one file.

    Raises ExifError when the container is understood but carries no EXIF,
    or when the container itself is not one we parse.
    """
    extension = os.path.splitext(path)[1].lower()
    with open(path, "rb") as handle:
        src = _Source(handle)
        head = src.read(FILE_HEAD_CACHE)
        if not head:
            raise ExifError("empty file")
        container = _sniff(head, extension)

        # The sniff read above stays on the tally. Rewinding to parse a
        # container from its first byte re-reads bytes already counted, so
        # the reported figure is an upper bound on what left the disk, never
        # an understatement of it.
        if container == "jpeg":
            handle.seek(0)
            tags = _exif_from_jpeg(src)
        elif container == "tiff":
            tags = _exif_from_tiff(src, head)
        elif container == "png":
            handle.seek(0)
            tags = _exif_from_png(src)
        elif container == "webp":
            handle.seek(0)
            tags = _exif_from_webp(src)
        elif container == "heif":
            size = os.fstat(handle.fileno()).st_size
            tags = _exif_from_heif(src, size)
        else:
            raise ExifError("unsupported container: %s" % container)

        return tags, container, src.bytes_read


def photo_from_tags(path: str, tags: dict, container: str) -> Photo:
    """Assemble a Photo from an already parsed tag dictionary."""
    coordinates = extract_gps(tags)
    dimensions = extract_dimensions(tags)
    lens_range = extract_lens_range(tags)
    return Photo(
        path=path,
        container=container,
        camera=extract_camera(tags),
        lens=extract_lens(tags),
        lens_min=lens_range[0] if lens_range else None,
        lens_max=lens_range[1] if lens_range else None,
        focal=extract_focal(tags),
        focal35=extract_focal35(tags),
        fnumber=extract_fnumber(tags),
        exposure=extract_exposure(tags),
        iso=extract_iso(tags),
        taken=extract_datetime(tags),
        tz_minutes=extract_tz_offset(tags),
        latitude=coordinates[0] if coordinates else None,
        longitude=coordinates[1] if coordinates else None,
        width=dimensions[0] if dimensions else None,
        height=dimensions[1] if dimensions else None,
        flash=extract_flash(tags),
        orientation=extract_orientation(tags),
    )


def read_photo(path: str) -> ScanResult:
    """Read one file and never raise. The status field says what happened."""
    extension = os.path.splitext(path)[1].lower()
    if extension in UNSUPPORTED_EXTENSIONS:
        return ScanResult(path, "unsupported", 0, extension.lstrip("."),
                          detail="container layout not implemented")
    try:
        tags, container, used = read_exif_tags(path)
    except ExifError as error:
        detail = str(error)
        container = "unknown"
        if detail.startswith("unsupported container: "):
            container = detail.split(": ", 1)[1]
            status = "unsupported"
        else:
            status = "no-exif"
        return ScanResult(path, status, 0, container, detail=detail)
    except OSError as error:
        return ScanResult(path, "unreadable", 0, "unknown", detail=str(error))

    photo = photo_from_tags(path, tags, container)
    if photo.taken is None and photo.camera is None and photo.focal is None:
        return ScanResult(path, "no-exif", used, container,
                          detail="EXIF present but empty")
    return ScanResult(path, "ok", used, container, photo=photo)


def iter_image_files(root: str,
                     extensions: Sequence[str] | None = None) -> Iterator[str]:
    """Walk a tree lazily, yielding candidate image paths.

    Directories are visited with scandir and symlinked directories are not
    followed, so a library of any size streams through without the whole
    listing ever being held at once.
    """
    wanted = set(extensions) if extensions else IMAGE_EXTENSIONS
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                subdirectories = []
                for entry in entries:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if name not in SKIP_DIRECTORIES:
                                subdirectories.append(entry.path)
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    if os.path.splitext(name)[1].lower() in wanted:
                        yield entry.path
                stack.extend(sorted(subdirectories, reverse=True))
        except (OSError, PermissionError):
            continue
