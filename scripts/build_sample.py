#!/usr/bin/env python3
"""Regenerate docs/index.html, the sample atlas published on GitHub Pages.

Why this exists
---------------
The output of exif-atlas is the product, and until this script existed the
only way to see one was to install the tool and point it at your own
photographs. Publishing a hand written mock up of a report would have been
faster and would also have been a lie: the first time the renderer changed,
the page on the internet would have been describing a tool that no longer
existed.

So the sample is not written by hand. This script synthesises a library of
image files using the project's own test fixtures - the same byte level
builders in tests/fixtures.py that the test suite uses, assembling real EXIF
structures one byte at a time - drops them in a temporary directory, runs the
actual scan through exif_atlas.cli.main, and writes what comes out. If the
renderer changes, rerunning this changes the published page. Nothing about
the report is faked.

Three edits are made to the tool's output afterwards, all cosmetic and all
declared here so nobody has to diff the file to find them:

  1. An honest header is inserted at the top of the page saying, in plain
     words, that the library behind it was generated rather than shot. A
     stranger arriving from a link must not think these are somebody's
     photographs. It is built from the report's own CSS classes so it reads
     as part of the document rather than a banner stapled on.
  2. The <title> is rewritten. The tool titles a report after the folder it
     scanned, which here is a temporary directory, and a browser tab reading
     "/tmp/exif-atlas-sample-8fc21a/sample-library" looks like a mistake.
  3. The same temporary path is printed under the headline, and is replaced
     there too, for the same reason. It says what the library actually is
     instead.

Nothing else is touched. The numbers, the charts, the prose and the layout
are exactly what the tool produced.

Privacy
-------
The generated library carries no GPS tags at all, and the scan is run with
--no-gps on top of that, so there is no coordinate anywhere in the published
file. Nothing here comes from a real camera, a real photograph or a real
person. No network call is made at any point, by this script or by the tool.

Why it is not wired into CI
---------------------------
It could be: it takes a couple of seconds and needs nothing that CI does not
already have. It is deliberately left out anyway, for two reasons.

The first is that the run is not reproducible byte for byte. The library
itself is deterministic - one seed, no clock, no environment - but the report
carries the date it was generated and the measured scan rate, so a CI job
that rebuilt the page on every push would produce a diff on every push. That
is noise in the history of a file whose real content changes perhaps twice a
year.

The second is that a deploy step which regenerates a published page from a
seed is a deploy step that can silently publish a broken page. The page is a
thing to look at, and looking at it is a human job. Regenerating it is one
command, run deliberately, with the result reviewed before it ships:

    python3 scripts/build_sample.py

CI does check what CI is good at. The ASCII sweep already covers docs/, and
the existing self contained check covers the renderer that produced the page.
tests/test_sample_page.py covers the logic in this file.
"""

from __future__ import annotations

import argparse
import math
import os
import random
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _entry in (ROOT, os.path.join(ROOT, "src")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

# The CI workflow imports the fixtures the same way. They are test support
# code, but they are the only honest source of EXIF in this repository.
from tests import fixtures as fx                       # noqa: E402
from exif_atlas import cli                             # noqa: E402

REPO_URL = "https://github.com/keivanmalhani/exif-atlas"
DOCS_PAGE = os.path.join(ROOT, "docs", "index.html")

# One seed, no clock, no environment: the same library every time.
SEED = 6127

# The window the fictional photographer was active in. Fixed rather than
# measured from today's date: a library that moved with the clock would give
# a different page on every run and the point of the seed is that it does not.
FIRST_DAY = date(2021, 2, 6)
LAST_DAY = date(2026, 5, 30)

CROP = 1.5                      # APS-C, so focal35 is a real conversion

# Web sized copies with the metadata stripped, the way they come back from an
# editor. They are what puts a "no exif" line in the report.
STRIPPED_EXPORTS = 6

# Shutter speeds a camera can actually record. Everything below a second is
# 1/N for a whole N and everything above is a whole number of seconds, which
# is what an EXIF RATIONAL can hold exactly. Third stops throughout, so the
# report's third stop histogram has something real to draw.
FAST_SHUTTERS = [
    8000, 6400, 5000, 4000, 3200, 2500, 2000, 1600, 1250, 1000, 800, 640,
    500, 400, 320, 250, 200, 160, 125, 100, 80, 60, 50, 40, 30, 25, 20, 15,
    13, 10, 8, 6, 5, 4, 3, 2,
]
SLOW_SHUTTERS = [1, 2, 3, 4, 5, 6, 8, 10, 13, 15, 20, 25, 30]
SHUTTERS = sorted([1.0 / n for n in FAST_SHUTTERS]
                  + [float(n) for n in SLOW_SHUTTERS])

# Fujifilm base ISO is 160, and this photographer will not go past 6400.
ISOS = [160, 200, 250, 320, 400, 500, 640, 800, 1000, 1250, 1600, 2000,
        2500, 3200, 4000, 5000, 6400]

# Scene brightness in EV at ISO 100, by hour. Rough, but coherent: it is what
# makes the aperture, the shutter and the ISO on a frame agree with each
# other instead of being three unrelated random numbers.
EV_BY_HOUR = {
    0: 3.0, 1: 3.0, 2: 3.0, 3: 3.5, 4: 5.0, 5: 7.0, 6: 9.0, 7: 10.5,
    8: 12.0, 9: 13.0, 10: 14.0, 11: 14.5, 12: 15.0, 13: 15.0, 14: 14.5,
    15: 14.0, 16: 13.0, 17: 12.0, 18: 11.0, 19: 9.5, 20: 8.0, 21: 6.0,
    22: 4.5, 23: 3.5,
}
EV_BY_MONTH = {
    1: -1.6, 2: -1.2, 3: -0.6, 4: -0.2, 5: 0.0, 6: 0.2,
    7: 0.2, 8: 0.0, 9: -0.3, 10: -0.8, 11: -1.4, 12: -1.7,
}

# The hours this photographer actually goes out in, before the season and the
# weekday push it around. Weighted towards the ends of the day on purpose:
# that is the habit the report is meant to be able to see.
HOUR_WEIGHTS = {
    5: 2, 6: 7, 7: 12, 8: 11, 9: 7, 10: 5, 11: 4, 12: 4, 13: 4, 14: 5,
    15: 6, 16: 8, 17: 12, 18: 14, 19: 11, 20: 7, 21: 4, 22: 2, 23: 1,
}

MONTH_WEIGHTS = {
    1: 0.55, 2: 0.70, 3: 0.95, 4: 1.25, 5: 1.35, 6: 1.20,
    7: 0.95, 8: 0.90, 9: 1.30, 10: 1.35, 11: 0.85, 12: 0.65,
}


class Body:
    """A camera, and the window it was in service."""

    def __init__(self, make, model, first, last, weight, prefix,
                 mount="XF", raw_share=0.0):
        self.make = make
        self.model = model
        self.first = first
        self.last = last
        self.weight = weight
        self.prefix = prefix
        self.mount = mount
        self.raw_share = raw_share

    @property
    def name(self):
        return "%s %s" % (self.make, self.model)

    def in_service(self, day):
        return self.first <= day <= self.last


class Lens:
    """A lens, its declared specification, and the window it was owned."""

    def __init__(self, name, low, high, wide_f, long_f, first, last, weight,
                 focals, stops, mount="XF"):
        self.name = name
        self.low = low
        self.high = high
        self.wide_f = wide_f
        self.long_f = long_f
        self.first = first
        self.last = last
        self.weight = weight
        self.focals = focals            # (focal, weight) pairs
        self.stops = stops              # (fnumber, weight) pairs
        self.mount = mount

    @property
    def spec(self):
        """The LensSpecification tuple EXIF tag 0xA432 carries."""
        return (self.low, self.high, self.wide_f, self.long_f)

    def in_service(self, day):
        return self.first <= day <= self.last

    def widest_at(self, focal):
        """Maximum aperture at a focal length, for a variable aperture zoom."""
        if self.high <= self.low or self.long_f <= self.wide_f:
            return self.wide_f
        position = ((math.log(focal) - math.log(self.low))
                    / (math.log(self.high) - math.log(self.low)))
        stops = (math.log(self.long_f) - math.log(self.wide_f)) * position
        return self.wide_f * math.exp(stops)


# A photographer who bought an X-T3, added an X100V to carry every day, moved
# to an X-T5 in the spring of 2023 and kept the old body for two months. The
# 23mm prime goes when the standard zoom arrives; the telephoto turns up late
# and never gets racked out to 300.
BODIES = [
    Body("FUJIFILM", "X-T3", date(2021, 2, 6), date(2023, 6, 18),
         weight=10, prefix="DSCF", raw_share=0.12),
    Body("FUJIFILM", "X-T5", date(2023, 4, 22), LAST_DAY,
         weight=13, prefix="DSCF", raw_share=0.24),
    Body("FUJIFILM", "X100V", FIRST_DAY, LAST_DAY,
         weight=5, prefix="DSCF", mount="fixed"),
]

LENSES = [
    Lens("XF23mmF1.4 R", 23.0, 23.0, 1.4, 1.4,
         date(2021, 2, 6), date(2023, 10, 14), weight=11,
         focals=[(23.0, 1.0)],
         stops=[(1.4, 26), (1.6, 8), (2.0, 22), (2.8, 18), (4.0, 12),
                (5.6, 8), (8.0, 5), (11.0, 1)]),
    Lens("XF35mmF1.4 R", 35.0, 35.0, 1.4, 1.4,
         date(2021, 2, 6), LAST_DAY, weight=13,
         focals=[(35.0, 1.0)],
         stops=[(1.4, 22), (1.6, 7), (2.0, 24), (2.8, 20), (4.0, 12),
                (5.6, 8), (8.0, 6), (11.0, 1)]),
    Lens("XF56mmF1.2 R", 56.0, 56.0, 1.2, 1.2,
         date(2021, 6, 12), LAST_DAY, weight=5,
         focals=[(56.0, 1.0)],
         stops=[(1.2, 30), (1.4, 12), (2.0, 24), (2.8, 16), (4.0, 10),
                (5.6, 6), (8.0, 2)]),
    # A standard zoom that gets used at the two ends of the barrel and almost
    # nowhere in between, which is a thing the report is built to notice.
    # The focal lengths are the positions actually marked on the barrel:
    # a zoom ring has detents and printed numbers, and a hand looking for a
    # framing lands on them far more often than anywhere between them.
    Lens("XF16-55mmF2.8 R LM WR", 16.0, 55.0, 2.8, 2.8,
         date(2022, 9, 3), LAST_DAY, weight=14,
         focals=[(16.0, 34), (18.0, 9), (23.0, 7), (27.0, 5), (35.0, 8),
                 (45.0, 6), (55.0, 31)],
         stops=[(2.8, 24), (4.0, 20), (5.6, 18), (8.0, 22), (11.0, 12),
                (16.0, 4)]),
    # Bought late, used rarely, and never once taken past 200mm.
    Lens("XF70-300mmF4-5.6 R LM OIS WR", 70.0, 300.0, 4.0, 5.6,
         date(2024, 3, 9), LAST_DAY, weight=4,
         focals=[(70.0, 22), (90.0, 16), (135.0, 24), (200.0, 26)],
         stops=[(4.0, 14), (5.6, 26), (8.0, 26), (11.0, 12)]),
    Lens("X100V 23mm F2", 23.0, 23.0, 2.0, 2.0,
         FIRST_DAY, LAST_DAY, weight=1,
         focals=[(23.0, 1.0)],
         stops=[(2.0, 30), (2.8, 20), (4.0, 18), (5.6, 14), (8.0, 12),
                (11.0, 5), (16.0, 1)],
         mount="fixed"),
]


# ---------------------------------------------------------------------------
# Pure planning logic. Everything below to write_library is deterministic
# given a seed and touches no filesystem, which is why it can be tested.
# ---------------------------------------------------------------------------


def weighted(rng, pairs):
    """Pick one item from (item, weight) pairs."""
    items = [item for item, _ in pairs]
    weights = [weight for _, weight in pairs]
    return rng.choices(items, weights=weights, k=1)[0]


def snap_to(value, series):
    """Nearest member of a series, compared in stops rather than linearly."""
    return min(series, key=lambda candidate: abs(math.log(candidate)
                                                 - math.log(value)))


def scene_ev(rng, when):
    """Brightness of the light in EV at ISO 100, give or take a cloud."""
    base = EV_BY_HOUR[when.hour] + EV_BY_MONTH[when.month]
    return base + rng.gauss(0.0, 1.05)


def expose(rng, ev, fnumber, focal35, tripod):
    """Choose an ISO and a shutter speed that agree with the aperture.

    N squared over t equals 2 to the EV times ISO over 100. The photographer
    picks the aperture and the camera finds the rest, raising ISO only as far
    as it needs to keep the shutter above the minimum. That minimum is auto
    ISO's usual rule, twice the equivalent focal length and never slower than
    1/125. On a tripod it does not apply and the ISO stays at base, which is
    where the long exposures in the report come from.
    """
    limit = 1.0 / max(125.0, focal35 * 2.0) if not tripod else 30.0
    light = (2.0 ** ev) / 100.0
    if tripod:
        iso = ISOS[0]
    else:
        needed = (fnumber ** 2) / (light * limit)
        iso = ISOS[-1]
        for candidate in ISOS:
            if candidate >= needed:
                iso = candidate
                break
        # A photographer is not a light meter. Sometimes the ISO is left
        # where the last frame put it.
        if rng.random() < 0.16:
            index = ISOS.index(iso)
            index = min(len(ISOS) - 1, max(0, index + rng.choice([-1, 1, 1])))
            iso = ISOS[index]
    seconds = (fnumber ** 2) / (light * iso)
    seconds = min(max(seconds, SHUTTERS[0]), SHUTTERS[-1])
    return iso, snap_to(seconds, SHUTTERS)


def available(items, day):
    return [item for item in items if item.in_service(day)]


def pick_day_sessions(rng, day, trip_days):
    """How many outings happen on a given day, if any."""
    if day in trip_days:
        return 2 if rng.random() < 0.30 else 1
    weekend = day.weekday() >= 5
    chance = (0.40 if weekend else 0.17) * MONTH_WEIGHTS[day.month]
    if rng.random() >= chance:
        return 0
    return 2 if (weekend and rng.random() < 0.14) else 1


def build_trip_days(rng, first, last):
    """A handful of multi day trips a year, when the shooting gets dense."""
    days = set()
    for year in range(first.year, last.year + 1):
        for _ in range(rng.randint(3, 4)):
            month = rng.choice([3, 4, 5, 6, 7, 8, 9, 10])
            start = date(year, month, rng.randint(1, 22))
            if not (first <= start <= last):
                continue
            for offset in range(rng.randint(3, 6)):
                moment = start + timedelta(days=offset)
                if first <= moment <= last:
                    days.add(moment)
    return days


def plan_frames(seed=SEED, first=FIRST_DAY, last=LAST_DAY):
    """Work out every frame in the sample library.

    Returns a list of dicts, in the order they were taken. No file is
    written and no clock is read: the same seed gives the same library on
    any machine, which is what makes the published page reproducible.
    """
    rng = random.Random(seed)
    trip_days = build_trip_days(rng, first, last)
    frames = []
    counters = {}

    day = first
    while day <= last:
        for _ in range(pick_day_sessions(rng, day, trip_days)):
            frames.extend(_plan_session(rng, day, day in trip_days, counters))
        day += timedelta(days=1)
    return frames


def _plan_session(rng, day, on_trip, counters):
    """One outing: a body, a lens, an hour, and a burst of frames."""
    bodies = available(BODIES, day)
    if not bodies:
        return []
    body = weighted(rng, [(item, item.weight) for item in bodies])

    if body.mount == "fixed":
        pool = [lens for lens in available(LENSES, day)
                if lens.mount == "fixed"]
    else:
        pool = [lens for lens in available(LENSES, day)
                if lens.mount == body.mount]
    if not pool:
        return []
    lens = weighted(rng, [(item, item.weight) for item in pool])

    hour = weighted(rng, sorted(HOUR_WEIGHTS.items()))
    minute = rng.randrange(60)
    start = datetime(day.year, day.month, day.day, hour, minute,
                     rng.randrange(60))

    # Tripod work happens in the dark, at a small aperture, and it is where
    # the frames slower than a second come from.
    tripod = hour in (5, 6, 20, 21, 22, 23) and rng.random() < 0.14

    if on_trip:
        count = rng.randint(4, 15)
    else:
        count = rng.randint(1, 6)

    out = []
    for index in range(count):
        when = start + timedelta(seconds=index * rng.randint(12, 240))
        if when.date() != day:
            break
        focal = weighted(rng, lens.focals)
        widest = lens.widest_at(focal)
        stops = [(value, weight) for value, weight in lens.stops
                 if value >= widest - 1e-9]
        if not stops:
            stops = [(snap_to(widest, [value for value, _ in lens.stops]), 1)]
        fnumber = weighted(rng, stops)
        if tripod:
            fnumber = weighted(rng, [(value, weight)
                                     for value, weight in lens.stops
                                     if value >= 5.6] or stops)
        focal35 = int(round(focal * CROP))
        iso, exposure = expose(rng, scene_ev(rng, when), fnumber, focal35,
                               tripod)

        counters[body.prefix] = counters.get(body.prefix, 1000) + 1
        out.append({
            "taken": when,
            "camera": (body.make, body.model),
            "body": body.model,
            "lens": lens.name,
            "focal": focal,
            "focal35": focal35,
            "fnumber": fnumber,
            "exposure": exposure,
            "iso": iso,
            "lens_spec": lens.spec,
            "stem": "%s%04d" % (body.prefix, counters[body.prefix] % 10000),
            "raw": body.raw_share > 0 and rng.random() < body.raw_share,
        })
    return out


def frame_paths(frame):
    """Where a frame's files live under the library root.

    Returns a list of (relative path, kind) pairs. A raw shooter ends up with
    two files per frame, and the raw is a container this tool does not parse,
    which is the case the report's "not parsed" line exists to describe.
    """
    when = frame["taken"]
    folder = "%04d/%04d-%02d" % (when.year, when.year, when.month)
    out = [("%s/%s.JPG" % (folder, frame["stem"]), "jpeg")]
    if frame["raw"]:
        out.append(("%s/%s.RAF" % (folder, frame["stem"]), "raw"))
    return out


def library_summary(frames):
    """Counts worth printing before the scan confirms them."""
    days = {frame["taken"].date() for frame in frames}
    raws = sum(1 for frame in frames if frame["raw"])
    return {
        "frames": len(frames),
        "raws": raws,
        # Everything on disk, which is the number the report's own footer
        # counts. The banner quotes it, so the two must not disagree.
        "files": len(frames) + raws + STRIPPED_EXPORTS,
        "days": len(days),
        "bodies": len({frame["body"] for frame in frames}),
        "lenses": len({frame["lens"] for frame in frames}),
        "first": min(days) if days else None,
        "last": max(days) if days else None,
    }


# ---------------------------------------------------------------------------
# Writing the library
# ---------------------------------------------------------------------------


def frame_bytes(frame):
    """The JPEG for one frame, EXIF and all, built a byte at a time.

    No GPS is passed, ever. There is no coordinate anywhere in this library.
    """
    return fx.simple_jpeg(
        camera=frame["camera"],
        lens=frame["lens"],
        focal=frame["focal"],
        fnumber=frame["fnumber"],
        exposure=frame["exposure"],
        iso=frame["iso"],
        taken=frame["taken"].strftime("%Y:%m:%d %H:%M:%S"),
        offset="+00:00",
        focal35=frame["focal35"],
        flash=0,
        lens_spec=frame["lens_spec"],
        pixel_bytes=768,
    )


def write_library(frames, directory):
    """Write every file of the sample library under directory."""
    written = 0
    for frame in frames:
        payload = frame_bytes(frame)
        for relative, kind in frame_paths(frame):
            if kind == "raw":
                # A private container this tool does not implement. The bytes
                # only have to be unreadable, which is the point of the file.
                fx.write(directory, relative,
                         b"FUJIFILMCCD-RAW 0201FF129502" + bytes(2048))
            else:
                fx.write(directory, relative, payload)
            written += 1

    # A few exports with the metadata stripped, the way a web sized copy
    # comes back from an editor. The report counts these rather than hiding
    # them, so the totals add up to the files actually on disk.
    for index in range(STRIPPED_EXPORTS):
        fx.write(directory, "exports/web-%02d.jpg" % index,
                 fx.jpeg(None, pixel_bytes=2048))
        written += 1
    return written


# ---------------------------------------------------------------------------
# The two edits made to the tool's output
# ---------------------------------------------------------------------------

BANNER_ANCHOR = '<main class="atlas">'


def sample_banner(summary=None):
    """The honest header that goes above the report.

    One label and one paragraph inside the report's own callout class: the
    same shape the tool itself emits for a privacy note, so it reads as part
    of the document rather than something stapled to the top of it. Note that
    the stylesheet sets .privacy b to a block level label, so <b> cannot be
    used for emphasis inside the paragraph and <p> cannot be repeated - both
    turn the card into a stack of disconnected lines.
    """
    scale = "The files behind it"
    if summary:
        scale = "The %s files behind it" % "{:,}".format(summary["files"])
    return (
        '<div class="privacy">'
        "<b>A sample. Nobody took these pictures.</b>"
        "<p>Everything below is the real output of exif-atlas, but the "
        "library it describes was invented. %s were generated by this "
        "project's own test fixtures, which assemble EXIF a byte at a time "
        "and leave the rest of the frame as padding: there is not one "
        "photograph among them, no real person's habits on this page, and no "
        "coordinates anywhere in it. exif-atlas is a command line tool that "
        "reads the metadata already sitting in your own files and writes one "
        "self contained page like this one, on your own machine, without "
        "making a network call or opening a picture. Point it at a real "
        "folder and this is the shape of what comes back: "
        '<a href="%s">%s</a>.</p>'
        "</div>" % (scale, REPO_URL, REPO_URL.replace("https://", ""))
    )


def insert_banner(document, banner):
    """Put the banner at the top of the page, above the masthead."""
    if BANNER_ANCHOR not in document:
        raise ValueError("no %r in the rendered report; the renderer's "
                         "document skeleton changed and this script needs "
                         "updating" % BANNER_ANCHOR)
    return document.replace(BANNER_ANCHOR, BANNER_ANCHOR + "\n" + banner, 1)


def retitle(document, title):
    """Replace the report's title, which is otherwise a temporary path.

    Confined to the head on purpose. Every bar in every chart carries an SVG
    <title> as its tooltip - there are several hundred of them further down
    the document - so a search over the whole file is a search through a
    haystack of near misses.
    """
    head_end = document.find("</head>")
    if head_end < 0:
        raise ValueError("no <head> in the rendered report")
    start = document.find("<title>", 0, head_end)
    end = document.find("</title>", 0, head_end)
    if start < 0 or end < start:
        raise ValueError("no <title> in the head of the rendered report")
    return document[:start] + "<title>" + title + document[end:]


def reroot(document, label):
    """Replace the scanned path the report prints under the headline.

    The tool names the folder it scanned, which here is a temporary directory,
    and a published page that says /tmp/exif-atlas-sample-aabx8t2g/sample-library
    reads as a mistake to anyone who lands on it. The class is the renderer's
    own, emitted once, so an exact match on the opening tag is unambiguous.
    """
    pattern = re.compile(r'<p class="path">.*?</p>', re.DOTALL)
    if not pattern.search(document):
        raise ValueError('no <p class="path"> in the rendered report; the '
                         "renderer stopped printing the scanned root and this "
                         "script needs updating")
    return pattern.sub('<p class="path">%s</p>' % label, document, count=1)


def finish_page(document, summary=None):
    """Apply the edits. Kept together so there is one list of them."""
    document = insert_banner(document, sample_banner(summary))
    document = retitle(document, "exif-atlas: a sample atlas")
    label = "a generated sample library"
    if summary:
        label = "a generated sample library, %s files" % "{:,}".format(
            summary["files"])
    return reroot(document, label)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def build(output=DOCS_PAGE, seed=SEED, keep=False):
    workspace = tempfile.mkdtemp(prefix="exif-atlas-sample-")
    library = os.path.join(workspace, "sample-library")
    os.makedirs(library, exist_ok=True)
    scratch = os.path.join(workspace, "atlas.html")
    try:
        frames = plan_frames(seed)
        summary = library_summary(frames)
        files = write_library(frames, library)
        print("built %s files under %s" % ("{:,}".format(files), library))
        print("")

        status = cli.main(["scan", library, "-o", scratch, "--no-gps"])
        if status != 0:
            raise SystemExit("scan failed with status %d" % status)

        with open(scratch, encoding="utf-8") as handle:
            document = handle.read()
        document = finish_page(document, summary)
        document.encode("ascii")        # the repository is ASCII only

        directory = os.path.dirname(os.path.abspath(output))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory, exist_ok=True)
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(document)
        shown = os.path.relpath(output, ROOT)
        if shown.startswith(".."):          # written outside the repository
            shown = os.path.abspath(output)
        print("")
        print("  wrote %s (%s KB)"
              % (shown,
                 "{:,}".format(round(len(document.encode("utf-8")) / 1024))))
        return output
    finally:
        if keep:
            print("  library kept at %s" % library)
        else:
            shutil.rmtree(workspace, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Regenerate the sample atlas published at docs/index.html")
    parser.add_argument("-o", "--output", default=DOCS_PAGE,
                        help="where to write the page "
                             "(default: docs/index.html)")
    parser.add_argument("--seed", type=int, default=SEED,
                        help="library seed (default: %d)" % SEED)
    parser.add_argument("--keep", action="store_true",
                        help="leave the generated library on disk")
    args = parser.parse_args(argv)
    build(output=args.output, seed=args.seed, keep=args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
