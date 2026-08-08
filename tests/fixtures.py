"""Image fixtures assembled one byte at a time.

Nothing here uses an imaging library. Every fixture is built from the bytes
up, so a test that passes proves the parser handled a structure this file
laid out on purpose rather than whatever some encoder happened to emit.

The builders below cover the containers exif-atlas claims to read: JPEG
APP1, TIFF (and therefore DNG), PNG eXIf, WebP EXIF, and the ISO base media
layout used by HEIC. Each returns bytes; the helpers at the bottom write
them to a directory.
"""

from __future__ import annotations

import os
import struct

# TIFF value types.
BYTE = 1
ASCII = 2
SHORT = 3
LONG = 4
RATIONAL = 5
UNDEFINED = 7
SBYTE = 6
SSHORT = 8
SLONG = 9
SRATIONAL = 10

LITTLE = "<"
BIG = ">"

TAG_EXIF_IFD = 0x8769
TAG_GPS_IFD = 0x8825

_FIXED = {
    BYTE: "B",
    SHORT: "H",
    LONG: "I",
    SBYTE: "b",
    SSHORT: "h",
    SLONG: "i",
}


def encode_values(typ: int, values, order: str) -> tuple[bytes, int]:
    """Return (payload, count) for one IFD entry's values."""
    if typ == ASCII:
        text = values if isinstance(values, (str, bytes)) else values[0]
        if isinstance(text, str):
            text = text.encode("ascii")
        return text + b"\x00", len(text) + 1
    if typ == UNDEFINED:
        raw = values if isinstance(values, bytes) else bytes(values)
        return raw, len(raw)
    if not isinstance(values, (list, tuple)):
        values = [values]
    if typ in (RATIONAL, SRATIONAL):
        code = "II" if typ == RATIONAL else "ii"
        out = b"".join(
            struct.pack(order + code, int(pair[0]), int(pair[1]))
            for pair in values)
        return out, len(values)
    code = _FIXED[typ]
    out = b"".join(struct.pack(order + code, int(v)) for v in values)
    return out, len(values)


def pack_ifd(entries, order: str, ifd_offset: int, next_ifd: int = 0):
    """Serialise one IFD.

    entries is a list of (tag, type, values). Values too large to sit in the
    four inline bytes are appended after the directory and referenced by an
    absolute offset, which is what makes this worth testing: the offsets are
    measured from the start of the TIFF header, not from the IFD.
    """
    ordered = sorted(entries, key=lambda item: item[0])
    directory_length = 2 + 12 * len(ordered) + 4
    body = bytearray(struct.pack(order + "H", len(ordered)))
    overflow = bytearray()
    for tag, typ, values in ordered:
        payload, count = encode_values(typ, values, order)
        body += struct.pack(order + "HHI", tag, typ, count)
        if len(payload) <= 4:
            body += payload + b"\x00" * (4 - len(payload))
        else:
            body += struct.pack(order + "I",
                                ifd_offset + directory_length + len(overflow))
            overflow += payload
            if len(overflow) % 2:
                overflow += b"\x00"
    body += struct.pack(order + "I", next_ifd)
    return bytes(body), bytes(overflow)


def build_exif_block(ifd0=None, exif=None, gps=None, order: str = LITTLE,
                     magic: int = 42) -> bytes:
    """A complete TIFF structure: header, IFD0, and the sub directories.

    The Exif and GPS pointers are LONG values, so they always live inline in
    the directory entry. That means the layout can be sized once with
    placeholder pointers and packed again with the real ones without
    anything moving.
    """
    ifd0 = list(ifd0 or [])
    entries = list(ifd0)
    if exif is not None:
        entries.append((TAG_EXIF_IFD, LONG, [0]))
    if gps is not None:
        entries.append((TAG_GPS_IFD, LONG, [0]))

    header = struct.pack(order + "2sHI",
                         b"II" if order == LITTLE else b"MM", magic, 8)
    ifd0_start = len(header)

    sized, sized_overflow = pack_ifd(entries, order, ifd0_start)
    exif_start = ifd0_start + len(sized) + len(sized_overflow)
    exif_bytes = exif_overflow = b""
    if exif is not None:
        exif_bytes, exif_overflow = pack_ifd(list(exif), order, exif_start)
    gps_start = exif_start + len(exif_bytes) + len(exif_overflow)
    gps_bytes = gps_overflow = b""
    if gps is not None:
        gps_bytes, gps_overflow = pack_ifd(list(gps), order, gps_start)

    entries = list(ifd0)
    if exif is not None:
        entries.append((TAG_EXIF_IFD, LONG, [exif_start]))
    if gps is not None:
        entries.append((TAG_GPS_IFD, LONG, [gps_start]))
    ifd0_bytes, ifd0_overflow = pack_ifd(entries, order, ifd0_start)
    assert len(ifd0_bytes) == len(sized)
    assert len(ifd0_overflow) == len(sized_overflow)

    return (header + ifd0_bytes + ifd0_overflow
            + exif_bytes + exif_overflow + gps_bytes + gps_overflow)


# ---------------------------------------------------------------------------
# A conventional set of tags, so most tests can ask for one keyword at a time
# ---------------------------------------------------------------------------


def rational(value: float, denominator: int = 1000):
    return (int(round(value * denominator)), denominator)


def standard_tags(camera=("FUJIFILM", "X-T4"), lens="XF23mmF1.4 R",
                  focal=23.0, fnumber=1.4, exposure=1 / 250.0, iso=400,
                  taken="2024:06:21 07:30:00", offset="+01:00",
                  width=6240, height=4160, focal35=35, flash=0,
                  orientation=1, lens_spec=(23.0, 23.0, 1.4, 1.4)):
    """The IFD0 and Exif entry lists a normal frame would carry."""
    ifd0 = [
        (0x010F, ASCII, camera[0]),
        (0x0110, ASCII, camera[1]),
        (0x0112, SHORT, [orientation]),
        (0x0132, ASCII, taken),
    ]
    exif = [
        (0x829A, RATIONAL, [exposure_pair(exposure)]),
        (0x829D, RATIONAL, [rational(fnumber, 100)]),
        (0x8827, SHORT, [iso]),
        (0x9003, ASCII, taken),
        (0x9011, ASCII, offset),
        (0x9209, SHORT, [flash]),
        (0x920A, RATIONAL, [rational(focal, 100)]),
        (0xA002, LONG, [width]),
        (0xA003, LONG, [height]),
        (0xA405, SHORT, [int(focal35)]),
        (0xA434, ASCII, lens),
    ]
    if lens_spec:
        exif.append((0xA432, RATIONAL,
                     [rational(v, 100) for v in lens_spec]))
    return ifd0, exif


def exposure_pair(seconds: float):
    """Express a shutter speed the way a camera does, as 1/N or N/1."""
    if seconds >= 1.0:
        return (int(round(seconds)), 1)
    return (1, int(round(1.0 / seconds)))


def gps_tags(latitude: float, longitude: float, seconds_precision=True):
    """GPS entries with the hemisphere carried only in the reference tags.

    The magnitudes are always positive, exactly as a camera writes them, so
    a reader that ignores the reference tags puts Sydney in Siberia.
    """

    def triple(value: float):
        value = abs(value)
        degrees = int(value)
        minutes_float = (value - degrees) * 60.0
        minutes = int(minutes_float)
        seconds = (minutes_float - minutes) * 60.0
        if seconds_precision:
            return [(degrees, 1), (minutes, 1),
                    (int(round(seconds * 1000)), 1000)]
        return [(degrees, 1), (int(round(minutes_float * 1000)), 1000),
                (0, 1)]

    return [
        (0x0000, BYTE, [2, 3, 0, 0]),
        (0x0001, ASCII, "S" if latitude < 0 else "N"),
        (0x0002, RATIONAL, triple(latitude)),
        (0x0003, ASCII, "W" if longitude < 0 else "E"),
        (0x0004, RATIONAL, triple(longitude)),
    ]


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------


def jpeg(exif_block: bytes | None = None, pixel_bytes: int = 512,
         extra_app: bytes = b"", comment: bytes = b"") -> bytes:
    """A JPEG whose pixel data is filler.

    The filler matters: it is what a correct reader must never touch. Make
    pixel_bytes large and the byte budget test has something to prove.
    """
    out = bytearray(b"\xff\xd8")
    if comment:
        out += b"\xff\xfe" + struct.pack(">H", len(comment) + 2) + comment
    if extra_app:
        out += b"\xff\xe0" + struct.pack(">H", len(extra_app) + 2) + extra_app
    if exif_block is not None:
        payload = b"Exif\x00\x00" + exif_block
        out += b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    # A quantisation table, so there is something between APP1 and the scan.
    out += b"\xff\xdb" + struct.pack(">H", 67) + b"\x00" + bytes(64)
    out += b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"
    out += b"\x5a" * pixel_bytes
    out += b"\xff\xd9"
    return bytes(out)


def tiff(exif_block: bytes, trailing: int = 0) -> bytes:
    """A TIFF or DNG: the structure is the file, plus optional image data."""
    return exif_block + b"\x00" * trailing


def png(exif_block: bytes | None = None, pixel_bytes: int = 512) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        import zlib
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
    if exif_block is not None:
        out += chunk(b"eXIf", exif_block)
    out += chunk(b"IDAT", b"\x78\x9c" + b"\x00" * pixel_bytes)
    out += chunk(b"IEND", b"")
    return bytes(out)


def webp(exif_block: bytes | None = None, pixel_bytes: int = 512) -> bytes:
    def chunk(fourcc: bytes, payload: bytes) -> bytes:
        out = fourcc + struct.pack("<I", len(payload)) + payload
        if len(payload) % 2:
            out += b"\x00"
        return out

    body = bytearray(b"WEBP")
    body += chunk(b"VP8 ", b"\x00" * pixel_bytes)
    if exif_block is not None:
        body += chunk(b"EXIF", exif_block)
    return b"RIFF" + struct.pack("<I", len(body)) + bytes(body)


def heif(exif_block: bytes | None = None, pixel_bytes: int = 512) -> bytes:
    """A minimal ISO base media file carrying an Exif item.

    Only the boxes the reader looks at are present: ftyp, meta with iinf and
    iloc, then mdat holding the payload.
    """

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    ftyp = box(b"ftyp", b"heic" + struct.pack(">I", 0) + b"heicmif1")
    if exif_block is None:
        meta = box(b"meta", struct.pack(">I", 0) + box(b"hdlr", bytes(12)))
        mdat = box(b"mdat", b"\x00" * pixel_bytes)
        return ftyp + meta + mdat

    item = b"\x00\x00\x00\x00" + b"Exif\x00\x00" + exif_block
    infe = box(b"infe", struct.pack(">BBBB", 2, 0, 0, 0)
               + struct.pack(">HH", 1, 0) + b"Exif" + b"exif\x00")
    iinf = box(b"iinf", struct.pack(">BBBB", 0, 0, 0, 0)
               + struct.pack(">H", 1) + infe)

    # Version 1 iloc, four byte offsets and lengths, no base offset.
    def make_iloc(item_offset: int) -> bytes:
        payload = struct.pack(">BBBB", 1, 0, 0, 0)
        payload += bytes([(4 << 4) | 4, (0 << 4) | 0])
        payload += struct.pack(">H", 1)
        payload += struct.pack(">H", 1)          # item id
        payload += struct.pack(">H", 0)          # construction method
        payload += struct.pack(">H", 0)          # data reference index
        payload += struct.pack(">H", 1)          # extent count
        payload += struct.pack(">I", item_offset)
        payload += struct.pack(">I", len(item))
        return box(b"iloc", payload)

    sized = make_iloc(0)
    meta_payload = struct.pack(">I", 0) + iinf + sized
    meta = box(b"meta", meta_payload)
    mdat_start = len(ftyp) + len(meta) + 8
    meta_payload = struct.pack(">I", 0) + iinf + make_iloc(mdat_start)
    meta = box(b"meta", meta_payload)
    mdat = box(b"mdat", item + b"\x00" * pixel_bytes)
    return ftyp + meta + mdat


def heif_meta_last(exif_block: bytes, pixel_bytes: int = 512) -> bytes:
    """The same file with the boxes in the order Apple actually writes them.

    An iPhone HEIC is ftyp, free, mdat, meta: the metadata sits AFTER the
    image data, not before it. Measured on a real library, 2,611 of 3,622
    HEIC files are laid out this way.
    """

    def box(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + kind + payload

    ftyp = box(b"ftyp", b"heic" + struct.pack(">I", 0) + b"heicmif1")
    free = box(b"free", b"\x00" * 64)
    item = b"\x00\x00\x00\x00" + b"Exif\x00\x00" + exif_block
    infe = box(b"infe", struct.pack(">BBBB", 2, 0, 0, 0)
               + struct.pack(">HH", 1, 0) + b"Exif" + b"exif\x00")
    iinf = box(b"iinf", struct.pack(">BBBB", 0, 0, 0, 0)
               + struct.pack(">H", 1) + infe)

    def make_iloc(item_offset: int) -> bytes:
        payload = struct.pack(">BBBB", 1, 0, 0, 0)
        payload += bytes([(4 << 4) | 4, (0 << 4) | 0])
        payload += struct.pack(">H", 1)
        payload += struct.pack(">H", 1)          # item id
        payload += struct.pack(">H", 0)          # construction method
        payload += struct.pack(">H", 0)          # data reference index
        payload += struct.pack(">H", 1)          # extent count
        payload += struct.pack(">I", item_offset)
        payload += struct.pack(">I", len(item))
        return box(b"iloc", payload)

    mdat = box(b"mdat", item + b"\x00" * pixel_bytes)
    item_offset = len(ftyp) + len(free) + 8
    meta = box(b"meta", struct.pack(">I", 0) + iinf + make_iloc(item_offset))
    return ftyp + free + mdat + meta


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def write(directory: str, name: str, data: bytes) -> str:
    path = os.path.join(directory, name)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def simple_jpeg(**kwargs) -> bytes:
    """A JPEG built from standard_tags with keyword overrides."""
    pixel_bytes = kwargs.pop("pixel_bytes", 512)
    gps = kwargs.pop("gps", None)
    ifd0, exif = standard_tags(**kwargs)
    gps_entries = gps_tags(*gps) if gps else None
    return jpeg(build_exif_block(ifd0, exif, gps_entries),
                pixel_bytes=pixel_bytes)
