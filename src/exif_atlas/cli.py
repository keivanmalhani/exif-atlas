"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime
from typing import Iterable, Iterator

from . import __version__
from .analyze import Aggregator, Options
from .exif import ScanResult, iter_image_files, read_photo
from .render import render_html, render_text

PROGRAM = "exif-atlas"

EPILOG = """\
privacy
  A photo library's GPS tags are a map of where somebody lives. Coordinates
  are therefore rounded to two decimal places, roughly a kilometre, before
  anything is written to the report, and the precise values are never stored.
  Pass --precise-gps only when you understand that the resulting file locates
  your front door, and --no-gps to leave location out of the report entirely.

examples
  exif-atlas scan ~/Pictures -o atlas.html
  exif-atlas scan ~/Pictures --since 2024-01-01 --camera "X-T4"
  exif-atlas scan ~/Pictures --no-gps -o share-with-anyone.html
  exif-atlas scan ~/Pictures --json report.json
"""


def parse_day(text: str) -> date:
    try:
        return datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(
            "expected a date as YYYY-MM-DD, got %r" % text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description="Build a report about how you actually shoot from the "
                    "EXIF metadata already in your photographs.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version="%s %s" % (PROGRAM, __version__))

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    scan = subparsers.add_parser(
        "scan",
        help="read a folder of photographs and write an atlas",
        description="Walk FOLDER, read only the metadata headers, and write "
                    "one self-contained HTML report.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan.add_argument("folder", metavar="FOLDER",
                      help="folder to walk, recursively")
    scan.add_argument("-o", "--output", metavar="PATH", default=None,
                      help="where to write the HTML report "
                           "(default: atlas.html)")
    scan.add_argument("--json", metavar="PATH", nargs="?", const="-",
                      default=None, dest="json_path",
                      help="also write the report as JSON; with no path it "
                           "goes to stdout. If --json is given without -o, "
                           "the HTML file is not written at all.")
    scan.add_argument("--since", metavar="YYYY-MM-DD", type=parse_day,
                      default=None,
                      help="ignore frames taken before this date")
    scan.add_argument("--until", metavar="YYYY-MM-DD", type=parse_day,
                      default=None,
                      help="ignore frames taken after this date")
    scan.add_argument("--camera", metavar="TEXT", default=None,
                      help="only include bodies whose name contains TEXT, "
                           "case insensitive")
    scan.add_argument("--lens", metavar="TEXT", default=None,
                      help="only include lenses whose name contains TEXT, "
                           "case insensitive")
    scan.add_argument("--precise-gps", action="store_true",
                      help="write exact coordinates instead of rounding to "
                           "two decimal places. A photo library's GPS tags "
                           "are a map of where somebody lives, so the "
                           "default is deliberately coarse; this flag "
                           "produces a file that locates your front door.")
    scan.add_argument("--no-gps", action="store_true",
                      help="leave location out of the report entirely")
    scan.add_argument("--cluster-km", metavar="KM", type=float, default=25.0,
                      help="how close two coordinates must be to count as "
                           "the same place (default: 25)")
    scan.add_argument("--workers", metavar="N", type=int, default=None,
                      help="how many files to read at once "
                           "(default: scaled to the machine)")
    scan.add_argument("--quiet", action="store_true",
                      help="print nothing but errors")
    return parser


def default_workers() -> int:
    cores = os.cpu_count() or 2
    return max(2, min(16, cores * 2))


def bounded_map(paths: Iterable[str], workers: int) -> Iterator[ScanResult]:
    """Read files in parallel without materialising the file list.

    At most a few hundred futures exist at any moment, so a library of any
    size streams through at constant memory.
    """
    if workers <= 1:
        for path in paths:
            yield read_photo(path)
        return

    limit = workers * 24
    iterator = iter(paths)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for path in iterator:
            pending.add(pool.submit(read_photo, path))
            if len(pending) >= limit:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    yield future.result()
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()


def run_scan(args: argparse.Namespace, stream=None) -> int:
    stream = stream or sys.stderr
    folder = os.path.abspath(os.path.expanduser(args.folder))
    if not os.path.isdir(folder):
        print("%s: not a folder: %s" % (PROGRAM, args.folder), file=stream)
        return 2

    if args.precise_gps and args.no_gps:
        print("%s: --precise-gps and --no-gps contradict each other"
              % PROGRAM, file=stream)
        return 2
    if args.since and args.until and args.since > args.until:
        print("%s: --since is after --until" % PROGRAM, file=stream)
        return 2

    gps_mode = "off" if args.no_gps else (
        "precise" if args.precise_gps else "round")
    options = Options(
        since=args.since,
        until=args.until,
        camera=args.camera,
        lens=args.lens,
        gps=gps_mode,
        cluster_km=max(0.1, args.cluster_km),
    )

    aggregator = Aggregator(options)
    workers = args.workers if args.workers else default_workers()
    quiet = args.quiet

    if not quiet:
        print("%s %s: reading headers under %s"
              % (PROGRAM, __version__, folder), file=stream)

    started = time.perf_counter()
    last_tick = started
    for result in bounded_map(iter_image_files(folder), workers):
        aggregator.add_result(result)
        if not quiet and aggregator.files_seen % 500 == 0:
            now = time.perf_counter()
            if now - last_tick > 0.5:
                last_tick = now
                print("  %s files, %s with metadata"
                      % ("{:,}".format(aggregator.files_seen),
                         "{:,}".format(aggregator.photos)),
                      file=stream)
    elapsed = max(time.perf_counter() - started, 1e-9)

    rate = aggregator.files_seen / elapsed
    meta = {
        "version": __version__,
        "root": folder,
        "generated": datetime.now().strftime("%d %B %Y at %H:%M"),
        "elapsed_seconds": elapsed,
        "files_per_second": rate,
    }
    report = aggregator.report(meta)

    if aggregator.files_seen == 0:
        print("%s: no image files found under %s" % (PROGRAM, folder),
              file=stream)
        return 1

    wrote_json = False
    if args.json_path is not None:
        payload = json.dumps(report, indent=2, sort_keys=False,
                             default=str)
        if args.json_path == "-":
            sys.stdout.write(payload + "\n")
        else:
            _write(args.json_path, payload + "\n")
            if not quiet:
                print("  wrote %s" % args.json_path, file=stream)
        wrote_json = True

    write_html = args.output is not None or not wrote_json
    output_path = args.output or "atlas.html"
    if write_html:
        _write(output_path, render_html(report))

    if not quiet:
        print("", file=stream)
        print(render_text(report), file=stream)
        print("", file=stream)
        print("  read %s %s in %.2fs, %s files per second"
              % ("{:,}".format(aggregator.files_seen),
                 "file" if aggregator.files_seen == 1 else "files",
                 elapsed, "{:,}".format(int(rate))), file=stream)
        if write_html:
            size = os.path.getsize(output_path)
            print("  wrote %s (%s KB), open it in any browser"
                  % (output_path, "{:,}".format(round(size / 1024))),
                  file=stream)
        if gps_mode == "precise":
            print("", file=stream)
            print("  warning: this file contains precise coordinates for "
                  "%s frames." % "{:,}".format(aggregator.gps_photos),
                  file=stream)
    return 0


def _write(path: str, text: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    if args.command == "scan":
        try:
            return run_scan(args)
        except KeyboardInterrupt:  # pragma: no cover - interactive only
            print("\n%s: interrupted" % PROGRAM, file=sys.stderr)
            return 130
    parser.print_help()  # pragma: no cover - argparse rejects other commands
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
