"""Turn a stream of photo metadata into a report about shooting habits.

Everything here is written to run as a single pass. The aggregator holds
counters keyed by value, never a list of photographs, so memory grows with
the number of distinct focal lengths and shooting days rather than with the
size of the library. A hundred thousand frames costs the same as a thousand.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

from .exif import Photo, ScanResult

__all__ = [
    "Options",
    "Aggregator",
    "round_coord",
    "civil_twilight",
    "twilight_window",
    "snap_focal",
    "snap_fstop",
    "snap_iso",
    "format_shutter",
    "shutter_stop_bucket",
    "shutter_stop_label",
    "percentile_from_counter",
    "median_from_counter",
    "half_coverage_band",
    "find_absences",
    "cluster_locations",
    "estimate_tz_minutes",
]


# ---------------------------------------------------------------------------
# Reference series photographers actually think in
# ---------------------------------------------------------------------------

STANDARD_FOCALS = [
    8, 10, 12, 14, 16, 18, 20, 21, 24, 28, 30, 35, 40, 45, 50, 55, 60, 70,
    75, 85, 90, 100, 105, 120, 135, 150, 180, 200, 240, 300, 400, 500, 600,
    800,
]

NOTABLE_FOCALS = [14, 16, 20, 24, 28, 35, 50, 85, 105, 135, 200, 300, 400]

STANDARD_FSTOPS = [
    1.0, 1.1, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.5, 2.8, 3.2, 3.5, 4.0, 4.5,
    5.0, 5.6, 6.3, 7.1, 8.0, 9.0, 10.0, 11.0, 13.0, 14.0, 16.0, 18.0, 20.0,
    22.0, 25.0, 29.0, 32.0,
]

NOTABLE_FSTOPS = [1.4, 2.0, 2.8, 4.0, 5.6, 8.0, 11.0, 16.0, 22.0]

STANDARD_ISOS = [
    50, 64, 80, 100, 125, 160, 200, 250, 320, 400, 500, 640, 800, 1000,
    1250, 1600, 2000, 2500, 3200, 4000, 5000, 6400, 8000, 10000, 12800,
    16000, 20000, 25600, 32000, 40000, 51200, 64000, 80000, 102400, 128000,
    204800, 409600,
]

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# A zoom is treated as a zoom once the focal range exceeds this factor.
ZOOM_RATIO = 1.15
# Fraction of a zoom range treated as "the end of the barrel".
END_FRACTION = 0.15


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------


def round_coord(value: float, places: int = 2) -> float:
    """Round a coordinate symmetrically about zero.

    This is where GPS handling usually goes wrong. Truncating with int()
    walks a southern latitude north and a western longitude east; floor()
    walks every coordinate south and west. Both leak a consistent directional
    bias that survives averaging. Decimal with ROUND_HALF_UP rounds half away
    from zero, so a point in Sydney and its mirror image in the north are
    treated identically, and going through str() keeps the result free of the
    binary representation surprises that make -1.005 arrive as -1.00499999.
    """
    if value is None:
        return None
    if not math.isfinite(value):
        raise ValueError("coordinate is not finite")
    quantum = Decimal(1).scaleb(-places)
    result = float(Decimal(str(value)).quantize(quantum,
                                                rounding=ROUND_HALF_UP))
    # Do not hand back negative zero; it serialises badly and reads oddly.
    return result + 0.0 if result != 0 else 0.0


def snap(value: float, series: Sequence[float]) -> float:
    """Snap a measurement to the nearest entry of a reference series."""
    best = series[0]
    best_distance = abs(math.log(value / best)) if value > 0 else float("inf")
    for candidate in series[1:]:
        distance = abs(math.log(value / candidate)) if value > 0 else 0.0
        if distance < best_distance:
            best, best_distance = candidate, distance
    return best


def snap_focal(value: float) -> int:
    if value <= 0:
        return 0
    if value < STANDARD_FOCALS[0]:
        return STANDARD_FOCALS[0]
    if value > STANDARD_FOCALS[-1]:
        return int(round(value))
    return int(snap(value, STANDARD_FOCALS))


def snap_fstop(value: float) -> float:
    if value <= 0:
        return 0.0
    if value > STANDARD_FSTOPS[-1]:
        return STANDARD_FSTOPS[-1]
    return snap(value, STANDARD_FSTOPS)


def snap_iso(value: float) -> int:
    if value <= 0:
        return 0
    if value > STANDARD_ISOS[-1]:
        return int(round(value))
    return int(snap(value, STANDARD_ISOS))


def format_shutter(seconds: float) -> str:
    """Render an exposure time the way a camera back would."""
    if seconds is None or seconds <= 0:
        return "?"
    if seconds >= 1.0:
        if abs(seconds - round(seconds)) < 0.05:
            return "%ds" % int(round(seconds))
        return "%.1fs" % seconds
    denominator = 1.0 / seconds
    if denominator >= 1000:
        return "1/%d" % int(round(denominator / 100.0) * 100)
    if denominator >= 100:
        return "1/%d" % int(round(denominator / 5.0) * 5)
    return "1/%d" % int(round(denominator))


def shutter_stop_bucket(seconds: float) -> int:
    """Whole stop bucket for an exposure time, keyed on log2 seconds."""
    return int(round(math.log2(seconds)))


# The markings on a shutter dial are not powers of two: the stop below
# 1/500 is engraved 1/250, not 1/256. Deriving a label from 2 ** stop gives
# 1/510 and 1/255, which no photographer has ever seen on a camera.
SHUTTER_STOP_LABELS = {
    -15: "1/32000", -14: "1/16000", -13: "1/8000", -12: "1/4000",
    -11: "1/2000", -10: "1/1000", -9: "1/500", -8: "1/250", -7: "1/125",
    -6: "1/60", -5: "1/30", -4: "1/15", -3: "1/8", -2: "1/4", -1: "1/2",
}


def shutter_stop_label(stop: int) -> str:
    """The dial marking for a whole stop bucket."""
    if stop in SHUTTER_STOP_LABELS:
        return SHUTTER_STOP_LABELS[stop]
    if stop < 0:
        return format_shutter(2.0 ** stop)
    return "%ds" % (2 ** stop)


def percentile_from_counter(counter: Counter, fraction: float):
    """The smallest key whose cumulative share reaches fraction."""
    total = sum(counter.values())
    if total == 0:
        return None
    target = fraction * total
    seen = 0
    keys = sorted(counter)
    for key in keys:
        seen += counter[key]
        if seen >= target:
            return key
    return keys[-1]


def median_from_counter(counter: Counter):
    return percentile_from_counter(counter, 0.5)


def half_coverage_band(counter: Counter, fraction: float = 0.5):
    """Narrowest contiguous run of keys covering at least `fraction`.

    Reported alongside the median because a median of 35mm means something
    quite different when half the frames sit between 33 and 37 than when
    they are spread from 16 to 200.
    """
    total = sum(counter.values())
    if total == 0:
        return None
    keys = sorted(counter)
    need = fraction * total
    best = None
    left = 0
    running = 0
    for right, key in enumerate(keys):
        running += counter[key]
        while running - counter[keys[left]] >= need and left < right:
            running -= counter[keys[left]]
            left += 1
        if running >= need:
            width = keys[right] - keys[left]
            if best is None or width < best[0]:
                best = (width, keys[left], keys[right], running)
    if best is None:
        return None
    _, low, high, covered = best
    return {"low": low, "high": high, "count": covered,
            "share": covered / total}


def top_items(counter: Counter, limit: int = 10):
    total = sum(counter.values()) or 1
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"value": key, "count": count, "share": count / total}
            for key, count in ordered[:limit]]


def histogram(counter: Counter, key_order: Iterable | None = None):
    total = sum(counter.values()) or 1
    keys = list(key_order) if key_order is not None else sorted(counter)
    return [{"value": key, "count": counter.get(key, 0),
             "share": counter.get(key, 0) / total} for key in keys]


# ---------------------------------------------------------------------------
# Sun position. NOAA's low precision solar calculator, which is accurate to
# roughly a minute and needs nothing but the standard library.
# ---------------------------------------------------------------------------

CIVIL_ZENITH_DEGREES = 96.0  # sun six degrees below the horizon


def _solar_terms(day_of_year: int) -> tuple[float, float]:
    """Equation of time in minutes and solar declination in radians."""
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1)
    equation = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.001480 * math.sin(3 * gamma)
    )
    return equation, declination


def civil_twilight(latitude: float, longitude: float,
                   when: date, zenith: float = CIVIL_ZENITH_DEGREES):
    """Civil dawn and dusk in UTC hours, or None when there is neither.

    Returns a pair of floats which may fall outside 0..24: a location east
    of Greenwich can have its dawn on the previous UTC day. Callers add the
    local offset and take the result modulo 24. None means the sun never
    reaches the requested altitude that day, which is the polar case.
    """
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("latitude out of range")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("longitude out of range")

    day_of_year = when.timetuple().tm_yday
    equation, declination = _solar_terms(day_of_year)
    latitude_radians = math.radians(latitude)

    denominator = math.cos(latitude_radians) * math.cos(declination)
    if abs(denominator) < 1e-12:
        return None
    cosine = (
        math.cos(math.radians(zenith))
        - math.sin(latitude_radians) * math.sin(declination)
    ) / denominator
    if cosine > 1.0 or cosine < -1.0:
        return None

    hour_angle = math.degrees(math.acos(cosine))
    dawn = (720.0 - 4.0 * (longitude + hour_angle) - equation) / 60.0
    dusk = (720.0 - 4.0 * (longitude - hour_angle) - equation) / 60.0
    return dawn, dusk


def twilight_window(latitude: float, longitude: float,
                    tz_minutes: int, sample_days: int = 12):
    """Civil twilight in local clock hours, sampled across a whole year.

    The report marks a band rather than a line because dawn moves by hours
    over a year at temperate latitudes, and a single date would be a lie.
    """
    offset = tz_minutes / 60.0
    dawns: list[float] = []
    dusks: list[float] = []
    dark_days = 0
    reference_year = 2024  # a leap year, so all 366 positions exist
    for index in range(sample_days):
        month = int(index * 12 / sample_days) + 1
        sample = date(reference_year, month, 21)
        result = civil_twilight(latitude, longitude, sample)
        if result is None:
            dark_days += 1
            continue
        dawn, dusk = result
        dawns.append((dawn + offset) % 24.0)
        dusks.append((dusk + offset) % 24.0)

    if not dawns:
        return None
    return {
        "dawn_earliest": min(dawns),
        "dawn_latest": max(dawns),
        "dusk_earliest": min(dusks),
        "dusk_latest": max(dusks),
        "dawn_mean": sum(dawns) / len(dawns),
        "dusk_mean": sum(dusks) / len(dusks),
        "polar_samples": dark_days,
        "samples": len(dawns),
        "tz_minutes": tz_minutes,
    }


def estimate_tz_minutes(offsets: Counter, longitude: float | None):
    """Best guess at the library's clock offset from UTC.

    Cameras record local wall clock time. When the file carries an
    OffsetTime tag we believe it; otherwise the longitude of the photographs
    gives an offset good to within an hour, which is enough to place dawn.
    """
    if offsets:
        return offsets.most_common(1)[0][0], "OffsetTime tag"
    if longitude is None:
        return 0, "assumed UTC"
    return int(round(longitude / 15.0)) * 60, "estimated from longitude"


# ---------------------------------------------------------------------------
# Absence: the part of the report that is actually interesting
# ---------------------------------------------------------------------------


def find_absences(counter: Counter, candidates: Sequence,
                  total: int, threshold: float = 0.01,
                  bounded: bool = True):
    """Candidate values that barely appear.

    Only candidates inside the range the gear can reach are considered, so
    a 24-70 owner is not told they never shoot at 400mm.
    """
    if total <= 0:
        return []
    present = [key for key, count in counter.items() if count > 0]
    if bounded:
        if not present:
            return []
        low, high = min(present), max(present)
        pool = [c for c in candidates if low <= c <= high]
    else:
        pool = list(candidates)

    out = []
    for candidate in pool:
        count = _nearby_count(counter, candidate)
        share = count / total
        if share < threshold:
            out.append({"value": candidate, "count": count, "share": share})
    return out


def _nearby_count(counter: Counter, candidate: float) -> int:
    """Count values that would round to this candidate.

    A photographer who shoots 34mm has not avoided 35mm, so the tolerance is
    proportional rather than exact.
    """
    total = 0
    for key, count in counter.items():
        if key <= 0:
            continue
        if abs(math.log(key / candidate)) < 0.055:  # about a twelfth of a stop
            total += count
    return total


def contiguous_runs(values: Sequence[int], modulus: int | None = None):
    """Group sorted integers into runs, optionally wrapping at a modulus."""
    if not values:
        return []
    ordered = sorted(set(values))
    runs: list[list[int]] = []
    current = [ordered[0]]
    for value in ordered[1:]:
        if value == current[-1] + 1:
            current.append(value)
        else:
            runs.append(current)
            current = [value]
    runs.append(current)
    if (modulus is not None and len(runs) > 1
            and runs[0][0] == 0 and runs[-1][-1] == modulus - 1):
        runs[0] = runs[-1] + runs[0]
        runs.pop()
    return [(run[0], run[-1]) for run in runs]


# ---------------------------------------------------------------------------
# Location clustering
# ---------------------------------------------------------------------------


@dataclass
class Cell:
    count: int = 0
    first: date | None = None
    last: date | None = None
    days: set = field(default_factory=set)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def cluster_locations(cells: dict, radius_km: float = 25.0):
    """Group rounded coordinates into places.

    Single link agglomeration over a grid, so the cost is linear in the
    number of distinct cells rather than quadratic in the number of photos.
    """
    if not cells:
        return []

    keys = list(cells)
    parent = list(range(len(keys)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    step = max(radius_km / 111.0, 1e-6)
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    projected: list[tuple[float, float]] = []
    for index, (latitude, longitude) in enumerate(keys):
        x = longitude * math.cos(math.radians(latitude))
        y = latitude
        projected.append((y, x))
        buckets[(int(math.floor(y / step)), int(math.floor(x / step)))].append(
            index)

    for (row, column), members in buckets.items():
        neighbours: list[int] = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                neighbours.extend(buckets.get((row + dr, column + dc), ()))
        for a in members:
            for b in neighbours:
                if a >= b:
                    continue
                lat_a, lon_a = keys[a]
                lat_b, lon_b = keys[b]
                if _haversine_km(lat_a, lon_a, lat_b, lon_b) <= radius_km:
                    union(a, b)

    grouped: dict[int, list[int]] = defaultdict(list)
    for index in range(len(keys)):
        grouped[find(index)].append(index)

    clusters = []
    for members in grouped.values():
        count = sum(cells[keys[i]].count for i in members)
        if count == 0:
            continue
        lat_sum = sum(keys[i][0] * cells[keys[i]].count for i in members)
        lon_sum = sum(keys[i][1] * cells[keys[i]].count for i in members)
        firsts = [cells[keys[i]].first for i in members
                  if cells[keys[i]].first]
        lasts = [cells[keys[i]].last for i in members if cells[keys[i]].last]
        days: set = set()
        for i in members:
            days |= cells[keys[i]].days
        centre_lat = lat_sum / count
        centre_lon = lon_sum / count
        spread = max(
            (_haversine_km(centre_lat, centre_lon, keys[i][0], keys[i][1])
             for i in members), default=0.0)
        clusters.append({
            "latitude": centre_lat,
            "longitude": centre_lon,
            "count": count,
            "cells": len(members),
            "radius_km": spread,
            "first": min(firsts) if firsts else None,
            "last": max(lasts) if lasts else None,
            "days": len(days),
        })
    clusters.sort(key=lambda item: (-item["count"], item["latitude"]))
    return clusters


def format_coordinate(latitude: float, longitude: float,
                      places: int = 2) -> str:
    """A plain text coordinate with explicit hemispheres."""
    lat_hemisphere = "S" if latitude < 0 else "N"
    lon_hemisphere = "W" if longitude < 0 else "E"
    pattern = "%%.%df %%s, %%.%df %%s" % (places, places)
    return pattern % (abs(latitude), lat_hemisphere,
                      abs(longitude), lon_hemisphere)


# ---------------------------------------------------------------------------
# Options and the streaming aggregator
# ---------------------------------------------------------------------------


@dataclass
class Options:
    since: date | None = None
    until: date | None = None
    camera: str | None = None
    lens: str | None = None
    gps: str = "round"          # round | precise | off
    cluster_km: float = 25.0
    absence_threshold: float = 0.01
    min_photos_for_absence: int = 60

    @property
    def gps_places(self) -> int:
        return 5 if self.gps == "precise" else 2


@dataclass
class GearRecord:
    count: int = 0
    first: date | None = None
    last: date | None = None
    focals: Counter = field(default_factory=Counter)
    fnumbers: Counter = field(default_factory=Counter)
    isos: Counter = field(default_factory=Counter)
    nominal_min: float | None = None
    nominal_max: float | None = None
    days: set = field(default_factory=set)

    def observe(self, when: date | None) -> None:
        self.count += 1
        if when is None:
            return
        self.days.add(when)
        if self.first is None or when < self.first:
            self.first = when
        if self.last is None or when > self.last:
            self.last = when


class Aggregator:
    """Fold scan results into counters, one file at a time."""

    def __init__(self, options: Options | None = None) -> None:
        self.options = options or Options()

        self.files_seen = 0
        self.photos = 0
        self.filtered = 0
        self.no_exif = 0
        self.unsupported = 0
        self.unreadable = 0
        self.bytes_read = 0

        self.containers: Counter = Counter()
        self.unsupported_formats: Counter = Counter()
        self.no_exif_formats: Counter = Counter()

        self.cameras: dict[str, GearRecord] = {}
        self.lenses: dict[str, GearRecord] = {}
        self.pairs: Counter = Counter()

        self.focal: Counter = Counter()
        self.focal35: Counter = Counter()
        self.fnumber: Counter = Counter()
        self.exposure: Counter = Counter()
        self.iso: Counter = Counter()

        self.hour: Counter = Counter()
        self.weekday: Counter = Counter()
        self.month: Counter = Counter()
        self.day: Counter = Counter()

        self.tz_offsets: Counter = Counter()
        self.flash_fired = 0
        self.flash_known = 0

        self.cells: dict[tuple[float, float], Cell] = {}
        self.gps_photos = 0

        self.first_seen: datetime | None = None
        self.last_seen: datetime | None = None
        self.missing_timestamp = 0

        # Reciprocal rule bookkeeping: shots slower than one over the
        # focal length, which is where handheld frames start to soften.
        self.reciprocal_known = 0
        self.reciprocal_slow = 0
        self.wide_open_known = 0
        self.wide_open_shots = 0

    # -- ingestion ---------------------------------------------------------

    def add_result(self, result: ScanResult) -> None:
        self.files_seen += 1
        self.bytes_read += result.bytes_read
        if result.status == "ok" and result.photo is not None:
            self.containers[result.container] += 1
            self.add_photo(result.photo)
        elif result.status == "unsupported":
            self.unsupported += 1
            self.unsupported_formats[result.container] += 1
        elif result.status == "unreadable":
            self.unreadable += 1
        else:
            self.no_exif += 1
            self.no_exif_formats[result.container] += 1

    def accepts(self, photo: Photo) -> bool:
        options = self.options
        when = photo.taken.date() if photo.taken else None
        if options.since and (when is None or when < options.since):
            return False
        if options.until and (when is None or when > options.until):
            return False
        if options.camera:
            name = (photo.camera or "").lower()
            if options.camera.lower() not in name:
                return False
        if options.lens:
            name = (photo.lens or "").lower()
            if options.lens.lower() not in name:
                return False
        return True

    def add_photo(self, photo: Photo) -> None:
        if not self.accepts(photo):
            self.filtered += 1
            return
        self.photos += 1

        when = photo.taken.date() if photo.taken else None
        if photo.taken is not None:
            stamp = photo.taken
            if self.first_seen is None or stamp < self.first_seen:
                self.first_seen = stamp
            if self.last_seen is None or stamp > self.last_seen:
                self.last_seen = stamp
            self.hour[stamp.hour] += 1
            self.weekday[stamp.weekday()] += 1
            self.month[stamp.month] += 1
            self.day[when] += 1
        else:
            self.missing_timestamp += 1

        if photo.tz_minutes is not None:
            self.tz_offsets[photo.tz_minutes] += 1

        camera = photo.camera or "Unrecorded body"
        record = self.cameras.setdefault(camera, GearRecord())
        record.observe(when)

        lens = photo.lens or "Unrecorded lens"
        lens_record = self.lenses.setdefault(lens, GearRecord())
        lens_record.observe(when)
        if photo.lens_min and photo.lens_max:
            lens_record.nominal_min = photo.lens_min
            lens_record.nominal_max = photo.lens_max
        self.pairs[(camera, lens)] += 1

        if photo.focal:
            key = round(photo.focal, 1)
            self.focal[key] += 1
            lens_record.focals[key] += 1
        if photo.focal35:
            self.focal35[round(photo.focal35, 1)] += 1
        if photo.fnumber:
            key = round(photo.fnumber, 2)
            self.fnumber[key] += 1
            lens_record.fnumbers[key] += 1
        if photo.exposure:
            self.exposure[round(photo.exposure, 6)] += 1
        if photo.iso:
            self.iso[photo.iso] += 1
            lens_record.isos[photo.iso] += 1

        if photo.flash is not None:
            self.flash_known += 1
            if photo.flash:
                self.flash_fired += 1

        reference = photo.focal35 or photo.focal
        if reference and photo.exposure:
            self.reciprocal_known += 1
            if photo.exposure > 1.0 / reference:
                self.reciprocal_slow += 1

        if (self.options.gps != "off"
                and photo.latitude is not None
                and photo.longitude is not None):
            self.gps_photos += 1
            places = self.options.gps_places
            key = (round_coord(photo.latitude, places),
                   round_coord(photo.longitude, places))
            cell = self.cells.get(key)
            if cell is None:
                cell = self.cells[key] = Cell()
            cell.count += 1
            if when is not None:
                cell.days.add(when)
                if cell.first is None or when < cell.first:
                    cell.first = when
                if cell.last is None or when > cell.last:
                    cell.last = when

    # -- derived numbers ---------------------------------------------------

    def _wide_open_share(self):
        """Share of frames taken at, or within a third of a stop of, the
        widest aperture the lens was ever seen to offer."""
        eligible = 0
        wide = 0
        for record in self.lenses.values():
            if not record.fnumbers or record.count < 10:
                continue
            widest = min(record.fnumbers)
            limit = widest * (2.0 ** (1.0 / 6.0))
            for value, count in record.fnumbers.items():
                eligible += count
                if value <= limit:
                    wide += count
        if eligible == 0:
            return None
        return {"count": wide, "eligible": eligible, "share": wide / eligible}

    def _zoom_usage(self):
        out = []
        for name, record in sorted(self.lenses.items(),
                                   key=lambda kv: -kv[1].count):
            if record.count < 20 or len(record.focals) < 4:
                continue
            used_low = min(record.focals)
            used_high = max(record.focals)
            if used_low <= 0 or used_high / used_low < ZOOM_RATIO:
                continue

            if (record.nominal_min and record.nominal_max
                    and record.nominal_max / max(record.nominal_min, 1e-9)
                    >= ZOOM_RATIO):
                low, high = record.nominal_min, record.nominal_max
                basis = "declared range"
            else:
                low, high = used_low, used_high
                basis = "range you have used"

            span = math.log(high) - math.log(low)
            if span <= 0:
                continue
            total = sum(record.focals.values())
            wide_end = middle = long_end = 0
            for focal, count in record.focals.items():
                position = (math.log(min(max(focal, low), high))
                            - math.log(low)) / span
                if position <= END_FRACTION:
                    wide_end += count
                elif position >= 1.0 - END_FRACTION:
                    long_end += count
                else:
                    middle += count

            ends = (wide_end + long_end) / total
            if ends >= 0.65 and wide_end / total >= 0.2 \
                    and long_end / total >= 0.2:
                verdict = "shot at both ends"
                note = ("The barrel spends its life at one stop or the other. "
                        "Two primes would weigh less.")
            elif wide_end / total >= 0.6:
                verdict = "lives at the wide end"
                note = "The long end is mostly carried, not used."
            elif long_end / total >= 0.6:
                verdict = "lives at the long end"
                note = "The wide end is mostly carried, not used."
            else:
                verdict = "uses the range"
                note = "Frames are spread across the barrel."

            unused_long = None
            if (record.nominal_max
                    and used_high < record.nominal_max * 0.85):
                unused_long = {"declared": record.nominal_max,
                               "reached": used_high}
            unused_wide = None
            if (record.nominal_min
                    and used_low > record.nominal_min * 1.15):
                unused_wide = {"declared": record.nominal_min,
                               "reached": used_low}

            out.append({
                "lens": name,
                "count": record.count,
                "low": low,
                "high": high,
                "basis": basis,
                "wide_end": wide_end / total,
                "middle": middle / total,
                "long_end": long_end / total,
                "verdict": verdict,
                "note": note,
                "unused_long": unused_long,
                "unused_wide": unused_wide,
                "histogram": _focal_histogram(record.focals),
            })
        return out

    def _habits(self):
        """Short statements about combinations that look like reflexes."""
        habits = []
        total = self.photos or 1

        wide = self._wide_open_share()
        if wide and wide["share"] >= 0.5:
            habits.append({
                "title": "You shoot wide open",
                "detail": ("%.0f percent of frames on lenses with a known "
                           "maximum aperture were taken within a third of a "
                           "stop of it. That is a look, and it is also a "
                           "habit worth noticing." % (wide["share"] * 100)),
                "kind": "aperture",
            })
        elif wide and wide["share"] <= 0.08:
            habits.append({
                "title": "You almost never open up",
                "detail": ("Only %.0f percent of frames sit near the widest "
                           "aperture available. The fast glass is being "
                           "carried, not used." % (wide["share"] * 100)),
                "kind": "aperture",
            })

        if self.fnumber:
            snapped = Counter()
            for value, count in self.fnumber.items():
                snapped[snap_fstop(value)] += count
            value, count = snapped.most_common(1)[0]
            share = count / sum(snapped.values())
            if share >= 0.3:
                habits.append({
                    "title": "f/%s is your default" % _trim(value),
                    "detail": ("%.0f percent of every frame in the library "
                               "was taken at f/%s. A default is fine. Not "
                               "knowing it is the problem."
                               % (share * 100, _trim(value))),
                    "kind": "aperture",
                })
            landscape = sum(count for value, count in snapped.items()
                            if 7.0 <= value <= 11.5)
            if landscape / sum(snapped.values()) >= 0.5:
                habits.append({
                    "title": "Locked between f/8 and f/11",
                    "detail": ("Half the library sits in the two stops "
                               "everything is sharp at. Depth of field is "
                               "not being used as a decision."),
                    "kind": "aperture",
                })

        if self.reciprocal_known >= 40:
            share = self.reciprocal_slow / self.reciprocal_known
            if share >= 0.25:
                habits.append({
                    "title": "A quarter of your frames break the "
                             "reciprocal rule",
                    "detail": ("%.0f percent of frames used a shutter slower "
                               "than one over the focal length. Some of "
                               "those are on a tripod. The rest are a "
                               "sharpness problem you can measure."
                               % (share * 100)),
                    "kind": "shutter",
                })

        if self.exposure:
            fast = sum(count for value, count in self.exposure.items()
                       if value <= 1.0 / 1000)
            if fast / sum(self.exposure.values()) >= 0.35:
                habits.append({
                    "title": "You live above 1/1000",
                    "detail": ("A third of the library is faster than "
                               "1/1000. That is either fast glass in bright "
                               "light or a shutter speed set once and "
                               "forgotten."),
                    "kind": "shutter",
                })
            long_exposures = sum(count for value, count in
                                 self.exposure.items() if value >= 1.0)
            if long_exposures and long_exposures / sum(
                    self.exposure.values()) >= 0.05:
                habits.append({
                    "title": "You use a tripod more than you think",
                    "detail": ("%d frames are a second or longer, which is "
                               "%.0f percent of the library."
                               % (long_exposures,
                                  100.0 * long_exposures
                                  / sum(self.exposure.values()))),
                    "kind": "shutter",
                })

        if self.iso:
            base = min(self.iso)
            at_base = self.iso[base] / sum(self.iso.values())
            if at_base >= 0.6:
                habits.append({
                    "title": "You stay at base ISO",
                    "detail": ("%.0f percent of frames are at ISO %d. Clean "
                               "files, and a hard limit on when you are "
                               "willing to shoot."
                               % (at_base * 100, base)),
                    "kind": "iso",
                })
            auto_iso_spread = len([v for v in self.iso if self.iso[v] > 2])
            if auto_iso_spread >= 20 and at_base < 0.2:
                habits.append({
                    "title": "Auto ISO is making your decisions",
                    "detail": ("ISO values are spread across %d distinct "
                               "settings with no clear base. The camera is "
                               "choosing." % auto_iso_spread),
                    "kind": "iso",
                })

        if self.hour and self.photos >= 50:
            busiest = self.hour.most_common(1)[0]
            share = busiest[1] / sum(self.hour.values())
            if share >= 0.2:
                habits.append({
                    "title": "One hour holds %.0f percent of the library"
                             % (share * 100),
                    "detail": ("The hour beginning %02d:00 accounts for %d "
                               "frames. Everything else competes with it."
                               % (busiest[0], busiest[1])),
                    "kind": "time",
                })

        if self.weekday and self.photos >= 50:
            weekend = self.weekday[5] + self.weekday[6]
            share = weekend / sum(self.weekday.values())
            if share >= 0.6:
                habits.append({
                    "title": "This is a weekend practice",
                    "detail": ("%.0f percent of frames were taken on a "
                               "Saturday or Sunday." % (share * 100)),
                    "kind": "time",
                })

        if self.flash_known >= 40:
            share = self.flash_fired / self.flash_known
            if share <= 0.01:
                habits.append({
                    "title": "The flash has never come out",
                    "detail": ("Flash fired on {:,} of {:,} frames that "
                               "recorded the state.".format(
                                   self.flash_fired, self.flash_known)),
                    "kind": "light",
                })

        if len(self.cameras) >= 2:
            ordered = sorted(self.cameras.items(), key=lambda kv: -kv[1].count)
            top, second = ordered[0], ordered[1]
            if top[1].count / total >= 0.85:
                habits.append({
                    "title": "One body does the work",
                    "detail": ("%s took %.0f percent of the library. The "
                               "others are backups you own, not cameras you "
                               "use." % (top[0], 100.0 * top[1].count / total)),
                    "kind": "gear",
                })
            del second
        return habits

    def _absences(self):
        total = self.photos
        threshold = self.options.absence_threshold
        enough = total >= self.options.min_photos_for_absence

        # Below the floor the findings are withheld rather than merely
        # marked, so nothing downstream can present a gap in forty frames as
        # if it meant something.
        focals = stops = []
        if enough:
            focals = find_absences(self.focal, NOTABLE_FOCALS, total,
                                   threshold)
            stops = find_absences(self.fnumber, NOTABLE_FSTOPS, total,
                                  threshold)

        hours_total = sum(self.hour.values())
        quiet_hours = []
        if hours_total:
            for hour in range(24):
                count = self.hour.get(hour, 0)
                if count / hours_total < max(threshold, 0.005):
                    quiet_hours.append(hour)
        hour_runs = contiguous_runs(quiet_hours, modulus=24)

        never_days = []
        if self.weekday and sum(self.weekday.values()):
            weekday_total = sum(self.weekday.values())
            for index in range(7):
                count = self.weekday.get(index, 0)
                if count / weekday_total < 0.02:
                    never_days.append(WEEKDAY_NAMES[index])

        unreached = []
        for name, record in self.lenses.items():
            if not record.focals or not record.nominal_max:
                continue
            reached = max(record.focals)
            if reached < record.nominal_max * 0.85:
                unreached.append({
                    "lens": name,
                    "declared": record.nominal_max,
                    "reached": reached,
                })

        return {
            "enough_data": enough,
            "threshold": threshold,
            "focal_lengths": focals,
            "apertures": stops,
            "hours": [{"start": start, "end": end} for start, end in
                      hour_runs],
            "quiet_hours": quiet_hours,
            "weekdays": never_days,
            "unreached_focal_range": unreached,
        }

    def _locations(self):
        if self.options.gps == "off":
            return {"mode": "off", "clusters": [], "photos": 0,
                    "note": "Location analysis was disabled with --no-gps."}
        if not self.cells:
            return {"mode": self.options.gps, "clusters": [], "photos": 0,
                    "note": "No photograph in this library carries a GPS tag."}

        clusters = cluster_locations(self.cells, self.options.cluster_km)
        places = self.options.gps_places
        total = self.gps_photos or 1
        rendered = []
        for cluster in clusters[:40]:
            rendered.append({
                "label": format_coordinate(
                    round_coord(cluster["latitude"], places),
                    round_coord(cluster["longitude"], places), places),
                "latitude": round_coord(cluster["latitude"], places),
                "longitude": round_coord(cluster["longitude"], places),
                "count": cluster["count"],
                "share": cluster["count"] / total,
                "days": cluster["days"],
                "radius_km": round(cluster["radius_km"], 1),
                "first": _iso(cluster["first"]),
                "last": _iso(cluster["last"]),
            })
        if self.options.gps == "precise":
            note = ("Coordinates are shown to five decimal places, about a "
                    "metre. This file now records where these photographs "
                    "were taken precisely enough to find the front door.")
        else:
            note = ("Coordinates are rounded to two decimal places, about a "
                    "kilometre, before anything is written down. The precise "
                    "values are never stored in this report.")
        return {
            "mode": self.options.gps,
            "decimal_places": places,
            "photos": self.gps_photos,
            "share": self.gps_photos / (self.photos or 1),
            "cluster_km": self.options.cluster_km,
            "clusters": rendered,
            "total_clusters": len(clusters),
            "note": note,
        }

    def _time_of_day(self):
        hours = [{"hour": hour, "count": self.hour.get(hour, 0)}
                 for hour in range(24)]
        total = sum(self.hour.values()) or 1
        for entry in hours:
            entry["share"] = entry["count"] / total

        twilight = None
        # Why the band is missing matters. "No coordinate" and "the sun
        # never sets there" are different findings, and a reader told the
        # first when the second is true will go looking for GPS tags that
        # were in the files all along.
        if self.options.gps == "off":
            reason = "omitted"
        elif not self.cells:
            reason = "no-coordinate"
        else:
            reason = "undefined"
        polar_latitude = None
        if self.options.gps != "off" and self.cells:
            busiest = max(self.cells.items(), key=lambda kv: kv[1].count)[0]
            latitude, longitude = busiest
            tz_minutes, basis = estimate_tz_minutes(self.tz_offsets, longitude)
            window = twilight_window(latitude, longitude, tz_minutes)
            if window is None:
                polar_latitude = round_coord(latitude, 1)
            if window is not None:
                window["basis"] = basis
                window["latitude"] = round_coord(latitude, 1)
                window["longitude"] = round_coord(longitude, 1)
                golden = 0
                for entry in hours:
                    if _in_golden(entry["hour"], window):
                        golden += entry["count"]
                window["golden_share"] = golden / total
                window["golden_count"] = golden
                twilight = window

        return {
            "hours": hours,
            "weekdays": [{"day": WEEKDAY_NAMES[index],
                          "count": self.weekday.get(index, 0)}
                         for index in range(7)],
            "months": [{"month": MONTH_NAMES[index],
                        "count": self.month.get(index + 1, 0)}
                       for index in range(12)],
            "twilight": twilight,
            "twilight_missing": None if twilight else reason,
            "twilight_latitude": polar_latitude,
        }

    def _calendar(self):
        if not self.day:
            return {"years": [], "busiest_day": None, "days_shot": 0}
        years: dict[int, dict] = {}
        for when, count in self.day.items():
            bucket = years.setdefault(when.year, {})
            bucket[when.isoformat()] = count
        busiest = max(self.day.items(), key=lambda kv: kv[1])
        # Every year between the first frame and the last gets a row, even
        # the ones with nothing in them. A fallow year is a finding, and
        # skipping it would quietly close the gap it represents.
        ordered = []
        for year in range(min(years), max(years) + 1):
            days = years.get(year, {})
            ordered.append({
                "year": year,
                "days": days,
                "total": sum(days.values()),
                "active_days": len(days),
                "max": max(days.values()) if days else 0,
            })
        return {
            "years": ordered,
            "days_shot": len(self.day),
            "busiest_day": {"date": busiest[0].isoformat(),
                            "count": busiest[1]},
            "median_frames_per_active_day": median_from_counter(
                Counter(self.day.values())),
        }

    def _gear(self):
        total = self.photos or 1
        cameras = []
        for name, record in sorted(self.cameras.items(),
                                   key=lambda kv: (-kv[1].count, kv[0])):
            cameras.append({
                "name": name,
                "count": record.count,
                "share": record.count / total,
                "first": _iso(record.first),
                "last": _iso(record.last),
                "days": len(record.days),
            })
        lenses = []
        for name, record in sorted(self.lenses.items(),
                                   key=lambda kv: (-kv[1].count, kv[0])):
            focals = sorted(record.focals) if record.focals else []
            lenses.append({
                "name": name,
                "count": record.count,
                "share": record.count / total,
                "first": _iso(record.first),
                "last": _iso(record.last),
                "days": len(record.days),
                "focal_low": focals[0] if focals else None,
                "focal_high": focals[-1] if focals else None,
                "declared_low": record.nominal_min,
                "declared_high": record.nominal_max,
                "median_focal": median_from_counter(record.focals),
                "widest_aperture": min(record.fnumbers)
                if record.fnumbers else None,
            })
        pairs = [{"camera": camera, "lens": lens, "count": count,
                  "share": count / total}
                 for (camera, lens), count in self.pairs.most_common(12)]
        return {"cameras": cameras, "lenses": lenses, "pairs": pairs}

    def _focal_section(self):
        if not self.focal:
            return {"available": False}
        total = sum(self.focal.values())
        median = median_from_counter(self.focal)
        band = half_coverage_band(self.focal)
        return {
            "available": True,
            "count": total,
            "median": median,
            "band": band,
            "p10": percentile_from_counter(self.focal, 0.10),
            "p90": percentile_from_counter(self.focal, 0.90),
            "min": min(self.focal),
            "max": max(self.focal),
            "distinct": len(self.focal),
            "histogram": _focal_histogram(self.focal),
            "top": _focal_top(self.focal, 8),
            "zooms": self._zoom_usage(),
            "thirty_five_available": bool(self.focal35),
        }

    def _aperture_section(self):
        if not self.fnumber:
            return {"available": False}
        snapped = _grouped(self.fnumber, snap_fstop)
        total = sum(snapped.values())
        return {
            "available": True,
            "count": total,
            "histogram": [{"value": value, "label": "f/" + _trim(value),
                           "count": count, "share": count / total}
                          for value, count in sorted(snapped.items())],
            "median": median_from_counter(self.fnumber),
            "widest": min(self.fnumber),
            "narrowest": max(self.fnumber),
            "top": [{"label": "f/" + _trim(value), "count": count,
                     "share": count / total}
                    for value, count in
                    sorted(snapped.items(), key=lambda kv: -kv[1])[:6]],
            "wide_open": self._wide_open_share(),
        }

    def _shutter_section(self):
        if not self.exposure:
            return {"available": False}
        buckets: Counter = Counter()
        for value, count in self.exposure.items():
            buckets[shutter_stop_bucket(value)] += count
        total = sum(buckets.values())
        low, high = min(buckets), max(buckets)
        histogram_rows = []
        for stop in range(low, high + 1):
            seconds = 2.0 ** stop
            histogram_rows.append({
                "stop": stop,
                "seconds": seconds,
                "label": shutter_stop_label(stop),
                "count": buckets.get(stop, 0),
                "share": buckets.get(stop, 0) / total,
            })
        snapped = Counter()
        for value, count in self.exposure.items():
            snapped[format_shutter(value)] += count
        return {
            "available": True,
            "count": total,
            "histogram": histogram_rows,
            "fastest": format_shutter(min(self.exposure)),
            "slowest": format_shutter(max(self.exposure)),
            "median": format_shutter(median_from_counter(self.exposure)),
            "top": [{"label": label, "count": count, "share": count / total}
                    for label, count in snapped.most_common(6)],
            "reciprocal": ({"known": self.reciprocal_known,
                            "slow": self.reciprocal_slow,
                            "share": self.reciprocal_slow
                            / self.reciprocal_known}
                           if self.reciprocal_known else None),
        }

    def _iso_section(self):
        if not self.iso:
            return {"available": False}
        snapped = _grouped(self.iso, snap_iso)
        total = sum(snapped.values())
        ceiling = percentile_from_counter(self.iso, 0.99)
        above = sum(count for value, count in self.iso.items()
                    if value > ceiling)
        return {
            "available": True,
            "count": total,
            "histogram": [{"value": value, "label": str(value),
                           "count": count, "share": count / total}
                          for value, count in sorted(snapped.items())],
            "base": min(self.iso),
            "max": max(self.iso),
            "median": median_from_counter(self.iso),
            "p90": percentile_from_counter(self.iso, 0.90),
            "p95": percentile_from_counter(self.iso, 0.95),
            "ceiling": ceiling,
            "above_ceiling": above,
            "top": [{"label": str(value), "count": count,
                     "share": count / total}
                    for value, count in
                    sorted(snapped.items(), key=lambda kv: -kv[1])[:6]],
        }

    # -- assembly ----------------------------------------------------------

    def report(self, meta: dict | None = None) -> dict:
        meta = dict(meta or {})
        span_days = None
        if self.first_seen and self.last_seen:
            span_days = (self.last_seen.date() - self.first_seen.date()).days + 1

        return {
            "meta": meta,
            "scan": {
                "files_seen": self.files_seen,
                "photos": self.photos,
                "filtered_out": self.filtered,
                "no_exif": self.no_exif,
                "unsupported": self.unsupported,
                "unreadable": self.unreadable,
                "bytes_read": self.bytes_read,
                "containers": dict(self.containers.most_common()),
                "unsupported_formats": dict(
                    self.unsupported_formats.most_common()),
                "no_exif_formats": dict(self.no_exif_formats.most_common()),
                "missing_timestamp": self.missing_timestamp,
            },
            "span": {
                "first": _iso(self.first_seen),
                "last": _iso(self.last_seen),
                "days": span_days,
                "active_days": len(self.day),
                "years": sorted({d.year for d in self.day}),
            },
            "gear": self._gear(),
            "focal": self._focal_section(),
            "aperture": self._aperture_section(),
            "shutter": self._shutter_section(),
            "iso": self._iso_section(),
            "time_of_day": self._time_of_day(),
            "calendar": self._calendar(),
            "locations": self._locations(),
            "absences": self._absences(),
            "habits": self._habits(),
            "filters": {
                "since": _iso(self.options.since),
                "until": _iso(self.options.until),
                "camera": self.options.camera,
                "lens": self.options.lens,
                "gps": self.options.gps,
            },
        }


# ---------------------------------------------------------------------------
# Module level helpers used by the aggregator
# ---------------------------------------------------------------------------


def _grouped(counter: Counter, key) -> Counter:
    out: Counter = Counter()
    for value, count in counter.items():
        out[key(value)] += count
    return out


# A bucket is named after the value recorded inside it only when that one
# value accounts for this much of it. Below the line the bucket is a smear
# of zoom positions and the canonical marking is the honest label.
DOMINANT_SHARE = 0.6


def _snapped_groups(counter: Counter, key):
    """Group values onto a canonical series, keeping what was recorded.

    Snapping is what stops a zoom reporting 27.4mm and 27.6mm from filling
    the chart with near duplicates. Naming the resulting bucket after the
    series entry, though, rewrites a 23mm prime as 24mm, and its owner will
    notice. So a bucket carrying one dominant value is named after it, and
    a bucket of scattered zoom positions keeps the canonical marking.
    """
    groups: dict = defaultdict(Counter)
    for value, count in counter.items():
        groups[key(value)][value] += count
    out = []
    for bucket in sorted(groups):
        members = groups[bucket]
        total = sum(members.values())
        modal, modal_count = members.most_common(1)[0]
        if modal_count / total < DOMINANT_SHARE:
            modal = bucket
        out.append((bucket, modal, total))
    return out


def _focal_histogram(counter: Counter):
    """Bars for the focal length chart.

    A bucket is named after the focal length that dominates it, so a 23mm
    prime is not relabelled 24mm by the snapping. "value" therefore carries
    that same represented length rather than the grouping key: a JSON
    consumer reading value 24 next to the label "23mm" would reasonably
    conclude one of them was wrong. The key itself stays on "bucket" for
    anyone who needs to line these rows up against the standard series.
    """
    groups = _snapped_groups(counter, snap_focal)
    total = sum(count for _, _, count in groups) or 1
    return [{"value": round(modal, 1), "bucket": bucket,
             "label": "%gmm" % round(modal, 1),
             "count": count, "share": count / total}
            for bucket, modal, count in groups]


def _focal_top(counter: Counter, limit: int = 8):
    groups = _snapped_groups(counter, snap_focal)
    total = sum(count for _, _, count in groups) or 1
    rows = [{"value": round(modal, 1), "count": count, "share": count / total}
            for _, modal, count in groups]
    rows.sort(key=lambda row: (-row["count"], row["value"]))
    return rows[:limit]


def _trim(value: float) -> str:
    text = ("%.1f" % value).rstrip("0").rstrip(".")
    return text or "0"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value.isoformat()


def _in_golden(hour: int, window: dict) -> bool:
    """Is this hour inside the light photographers turn up for?

    Golden hour is taken as the hour on the daylight side of civil twilight
    at each end, which is close enough for a histogram and does not pretend
    to a precision the estimated timezone cannot support.
    """
    dawn = window["dawn_mean"]
    dusk = window["dusk_mean"]
    for start in (dawn, dusk - 1.0):
        if start <= hour < start + 1.5:
            return True
    return False
