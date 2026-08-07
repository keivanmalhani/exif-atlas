# exif-atlas

[![CI](https://github.com/keivanmalhani/exif-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/keivanmalhani/exif-atlas/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**[keivanmalhani.github.io/exif-atlas](https://keivanmalhani.github.io/exif-atlas/)**
is a sample report, so you can see what this produces without installing it.
The library behind it was generated rather than photographed, and the page
says so at the top.

Point it at a folder of photographs and it writes one self-contained HTML
file describing how you actually shoot.

It is not a gallery. It never opens a picture. It reads the metadata that
is already sitting in the file headers and turns it into a report about
your own habits: which bodies and lenses, which focal lengths, which
apertures, what hours, and the settings you never touch.

```
exif-atlas scan ~/Pictures -o atlas.html
```

## Why this exists

Lightroom can filter on every one of these fields. What it will not do is
show you the shape of your practice: that half your frames sit at one
focal length, that a zoom you carry everywhere lives at its two ends, that
you own the hours before nine and never shoot after ten.

The online EXIF tools that would answer some of this want you to upload
your photographs first. A photo library is not a thing to hand to a web
form. This runs on your machine, reads only header bytes, makes no network
calls at all, and produces one file you can look at or send to somebody.

## Install

Requires Python 3.11 or newer. There are no runtime dependencies.

```
pip install git+https://github.com/keivanmalhani/exif-atlas
```

From a checkout:

```
git clone https://github.com/keivanmalhani/exif-atlas
cd exif-atlas
pip install -e ".[dev]"
pytest -q
```

## Using it

```
exif-atlas scan FOLDER -o atlas.html      write the report
exif-atlas scan FOLDER --json report.json  the same numbers as JSON
exif-atlas scan FOLDER --json              JSON to stdout
exif-atlas scan FOLDER --since 2024-01-01  only frames after a date
exif-atlas scan FOLDER --camera "X-T4"     only one body
exif-atlas scan FOLDER --no-gps            leave location out entirely
exif-atlas scan FOLDER --precise-gps       exact coordinates, see PRIVACY
exif-atlas --version
```

`--until`, `--lens`, `--cluster-km`, `--workers` and `--quiet` are also
there; `exif-atlas scan --help` lists everything.

### What a run looks like

```
$ exif-atlas scan ~/Pictures -o atlas.html
exif-atlas 0.1.0: reading headers under /root/Pictures

  frames read      1,020
  date span        1 Jan 2022 to 25 Dec 2024
  days shot        620
  bodies           3
  most used        FUJIFILM X-H2 (560 frames)
  median focal     23mm
  wide open        49% of frames
  iso ceiling      6,400 (99th percentile)
  places           4 clusters, 1,020 frames tagged
  not parsed       34 files (cr3, raf)
  no exif          10 files

  read 1,064 files in 0.29s, 3,682 files per second
  wrote atlas.html (158 KB), open it in any browser
```

That run is against a sample library built by the test fixtures rather
than somebody's personal photographs, so the numbers are reproducible and
nobody's coordinates are in this README. The progress ticker goes to
stderr, which is why `--json` on stdout pipes cleanly.

The [published sample](https://keivanmalhani.github.io/exif-atlas/) is the
same idea at a larger scale, and it is the page rather than the console
output. `scripts/build_sample.py` synthesises a library of 2,961 files with
the same fixtures, runs this exact scan over it, and writes
`docs/index.html`. Rerun it whenever the renderer changes:

```
python3 scripts/build_sample.py
```

The `not parsed` line is deliberate. Formats whose containers are not
implemented are counted and named rather than dropped quietly, so the
totals in the report always add up to the number of files on disk.

## What the report contains

**Gear.** Every body and lens, with frame counts, the number of days each
was used, and the date range it was in service, drawn as a timeline. The
month you switched bodies is visible without you having to remember it.

**Focal length.** The distribution, and the one number worth having: the
narrowest band of focal lengths that covers half your frames. A median of
35mm means something quite different when half your frames sit between 33
and 37 than when they run from 16 to 200. For zooms it also reports
whether you use the range or only the two ends, and whether there is a
long end you have never once reached.

**Aperture and shutter.** Distributions on the standard third-stop and
whole-stop scales, plus the habits that fall out of them: shooting wide
open, a default aperture, frames slower than one over the focal length.

**ISO.** The distribution, the base ISO, and the ceiling that actually
survives into the pictures you kept, which is not the number on the
camera's spec sheet.

**Time of day.** Frames by hour. Where a GPS tag exists, the chart marks
where civil dawn and dusk fall across a year at that latitude, so golden
hour use is visible rather than guessed at.

**Calendar.** A heatmap of shooting days across every year in the span,
including the years with nothing in them.

**Locations.** Coordinates clustered into places with counts and date
ranges. Read the PRIVACY section below before using `--precise-gps`.

**You never shoot this.** The focal lengths, apertures and hours inside
the range your gear can reach that hold almost nothing. Absence is usually
the more interesting finding, and it is the part no catalogue will show
you, because you cannot filter for the pictures you did not take.

## PRIVACY

**Coordinates are rounded to two decimal places by default.** That is
about a kilometre. The rounding happens before anything is written down:
the precise values are never stored in the report, not in the HTML and not
in the JSON.

This is the default because a photo library's GPS tags are a map of where
somebody lives. The densest cluster in almost any personal library is the
photographer's home, and the second densest is often a child's school or a
relative's house. A report that plots those precisely is a document you
have to be careful with forever. One rounded to a kilometre is a document
you can send to somebody.

- `--precise-gps` writes coordinates to five decimal places, about a
  metre. It exists because sometimes you genuinely want it. The output
  file then locates your front door, and the tool says so on the console
  and on the page itself.
- `--no-gps` leaves location out of the report completely. No cluster
  table, no coordinates anywhere in the file.

Rounding is done half away from zero rather than by truncation, so a point
in Sydney and its mirror image north of the equator move by the same
distance in opposite directions. Truncating towards zero would walk every
southern and western coordinate back towards the equator and the meridian,
a bias that survives averaging and quietly shifts every cluster.

Beyond location: nothing is uploaded, no network call is ever made, and
the report is one file with no scripts and no external references. Open it
with the network turned off and it looks the same.

## What it will not do

- **It will not read your pictures.** Only header bytes are read, and the
  report says how many. The cost is flat rather than proportional: a 1 MB
  JPEG and a 64 MB one each cost 4,592 bytes, which is 0.007% of the
  larger file. There is a test that counts the bytes crossing the file
  boundary and fails if that stops being true.
- **It will not edit or move anything.** The scan opens files read-only.
- **It is not a culling tool and not a gallery.** No thumbnails, no
  ratings, no picks. It never decodes an image, so it cannot show you one.
- **It cannot read every raw format.** JPEG, TIFF and the TIFF-derived
  raws (DNG, NEF, ARW, CR2, ORF, RW2 and friends), HEIC, PNG and WebP are
  parsed. Canon CR3, Fujifilm RAF, Sigma X3F and a few others use private
  container layouts that are not implemented. Those files are counted and
  named in the report rather than skipped silently.
- **It will not read sidecars.** Ratings, keywords and edits live in XMP
  and in your catalogue, not in the file header. This reports what the
  camera wrote.
- **It cannot know which frames you kept.** Point it at your selects
  folder if you want the report to describe your selects; point it at the
  card dump and it describes the card dump.
- **It does not phone home, check for updates, or write anything outside
  the output path you give it.**

## Scale

Designed for a real library rather than a sample folder. Files stream
through: the list is never materialised and neither is the set of results.

```
45,000 files, 529 MB on disk
13.38 seconds, 3,362 files per second
16.9 MB peak resident memory
197 MB of header bytes read, 4,598 bytes per file
```

Peak memory is set by the counters, not by the library: results are folded
in one at a time and neither the file list nor the result set is ever held
whole. The per-file read is flat, so the same scan over 45,000 raw files
of 40 MB each would read the same 197 MB and use the same memory.

## JSON

`--json` emits the same numbers the HTML is built from, so the report is
not the only way out.

An excerpt from the run above, abridged to two sections:

```json
{
  "focal": {
    "median": 23.0,
    "band": { "low": 17.0, "high": 23.0, "count": 517, "share": 0.5069 },
    "p10": 16.0,
    "p90": 55.0,
    "distinct": 9
  },
  "absences": {
    "focal_lengths": [ { "value": 20, "count": 0, "share": 0.0 } ],
    "hours": [ { "start": 22, "end": 4 }, { "start": 10, "end": 10 } ]
  }
}
```

Coordinates in the JSON obey the same rounding rule as the HTML.

## How it works

`exif.py` parses EXIF out of file headers using nothing but the standard
library: JPEG APP1, TIFF (and therefore DNG and the other TIFF-derived
raws) in both byte orders, the HEIC meta box, the PNG `eXIf` chunk and the
WebP `EXIF` chunk. It seeks to the metadata, reads it, and stops. It never
walks past the start of scan marker into pixel data.

`analyze.py` folds results into counters one file at a time, so memory
does not grow with the library. `render.py` writes the HTML: inline CSS,
inline SVG charts drawn by hand, no scripts, no fonts to fetch, light and
dark through `prefers-color-scheme`.

## Development

```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q
```

The tests build JPEG, TIFF, PNG, WebP and HEIC fixtures byte by byte
rather than depending on an imaging library, so the parser is tested
against structures the suite lays out on purpose. They cover malformed and
truncated headers, GPS sign handling for all four hemispheres, the
rounding, the twilight calculation against published almanac times, the
byte budget, and the installed console script.

The source is ASCII only, which CI checks.

`docs/index.html` is the published sample, and it is regenerated by hand
rather than by CI. The run is deterministic apart from the timestamp and the
measured scan rate the report prints, so wiring it into a workflow would put
a diff on the page for every push; and a page whose whole job is to be
looked at is worth looking at before it ships. Change the renderer, run
`python3 scripts/build_sample.py`, open the file.

## Licence

MIT. See [LICENSE](LICENSE).
