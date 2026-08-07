"""The command line: arguments, exit codes, and what lands on disk."""

from __future__ import annotations

import json
import os

import pytest

from exif_atlas import __version__, cli
from tests import fixtures as fx


def library(root, count=8, gps=(51.507351, -0.127758)):
    """A small folder of photographs with a bit of variety in it."""
    focals = [23.0, 35.0, 50.0, 23.0]
    for index in range(count):
        fx.write(root, "sub%d/frame%04d.jpg" % (index % 2, index),
                 fx.simple_jpeg(
                     focal=focals[index % len(focals)],
                     iso=[200, 400, 1600, 6400][index % 4],
                     taken="2024:0%d:1%d 0%d:15:00" % (
                         index % 3 + 4, index % 5, index % 6 + 4),
                     gps=gps if index % 2 == 0 else None))
    return root


def test_version_flag_prints_the_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 1
    assert "scan" in capsys.readouterr().out


def test_scan_writes_an_html_file(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    out = str(tmp_path / "atlas.html")
    assert cli.main(["scan", root, "-o", out]) == 0
    assert os.path.isfile(out)
    html = open(out, encoding="utf-8").read()
    assert html.lstrip().startswith("<!doctype html")
    assert "X-T4" in html


def test_scan_reports_a_rate(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    cli.main(["scan", root, "-o", str(tmp_path / "a.html")])
    assert "files per second" in capsys.readouterr().err


def test_missing_folder_is_an_error(tmp_path, capsys):
    assert cli.main(["scan", str(tmp_path / "nope")]) == 2
    assert "not a folder" in capsys.readouterr().err


def test_an_empty_folder_is_reported(tmp_path, capsys):
    empty = str(tmp_path / "empty")
    os.makedirs(empty)
    assert cli.main(["scan", empty]) == 1
    assert "no image files" in capsys.readouterr().err


def test_json_to_stdout(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    assert cli.main(["scan", root, "--json", "--quiet"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["scan"]["photos"] == 8
    assert payload["meta"]["version"] == __version__


def test_json_to_a_file(tmp_path):
    root = library(str(tmp_path / "photos"))
    target = str(tmp_path / "report.json")
    assert cli.main(["scan", root, "--json", target, "--quiet"]) == 0
    payload = json.load(open(target, encoding="utf-8"))
    assert payload["gear"]["cameras"][0]["name"] == "FUJIFILM X-T4"


def test_json_alone_does_not_write_html(tmp_path, monkeypatch):
    """--json without -o means the caller wanted data, not a page."""
    root = library(str(tmp_path / "photos"))
    monkeypatch.chdir(str(tmp_path))
    assert cli.main(["scan", root, "--json", "r.json", "--quiet"]) == 0
    assert os.path.exists("r.json")
    assert not os.path.exists("atlas.html")


def test_no_output_flag_at_all_writes_the_default_name(tmp_path, monkeypatch):
    root = library(str(tmp_path / "photos"))
    monkeypatch.chdir(str(tmp_path))
    assert cli.main(["scan", root, "--quiet"]) == 0
    assert os.path.isfile("atlas.html")


def test_since_filter_reaches_the_report(tmp_path, capsys):
    root = str(tmp_path / "photos")
    fx.write(root, "old.jpg", fx.simple_jpeg(taken="2022:01:01 10:00:00"))
    fx.write(root, "new.jpg", fx.simple_jpeg(taken="2024:01:01 10:00:00"))
    cli.main(["scan", root, "--since", "2023-01-01", "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["scan"]["photos"] == 1
    assert payload["filters"]["since"] == "2023-01-01"


def test_a_bad_date_is_refused(capsys):
    with pytest.raises(SystemExit):
        cli.main(["scan", ".", "--since", "last tuesday"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_since_after_until_is_refused(tmp_path, capsys):
    root = library(str(tmp_path / "photos"), count=2)
    assert cli.main(["scan", root, "--since", "2025-01-01",
                     "--until", "2024-01-01"]) == 2
    assert "after" in capsys.readouterr().err


def test_camera_filter(tmp_path, capsys):
    root = str(tmp_path / "photos")
    fx.write(root, "fuji.jpg", fx.simple_jpeg(camera=("FUJIFILM", "X-T4")))
    fx.write(root, "nikon.jpg", fx.simple_jpeg(camera=("NIKON", "Z 6")))
    cli.main(["scan", root, "--camera", "X-T4", "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["scan"]["photos"] == 1
    assert payload["gear"]["cameras"][0]["name"] == "FUJIFILM X-T4"


def test_precise_gps_is_opt_in(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    cli.main(["scan", root, "--json", "--quiet"])
    default = capsys.readouterr().out
    assert "51.507" not in default

    cli.main(["scan", root, "--precise-gps", "--json", "--quiet"])
    precise = capsys.readouterr().out
    assert "51.507" in precise


def test_precise_gps_warns_on_the_console(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    cli.main(["scan", root, "--precise-gps", "-o", str(tmp_path / "a.html")])
    assert "precise coordinates" in capsys.readouterr().err


def test_no_gps_removes_locations(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    cli.main(["scan", root, "--no-gps", "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["locations"]["clusters"] == []
    assert "51.5" not in json.dumps(payload)


def test_precise_and_no_gps_together_are_refused(tmp_path, capsys):
    root = library(str(tmp_path / "photos"), count=2)
    assert cli.main(["scan", root, "--precise-gps", "--no-gps"]) == 2
    assert "contradict" in capsys.readouterr().err


def test_quiet_prints_nothing_to_stderr(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    cli.main(["scan", root, "-o", str(tmp_path / "a.html"), "--quiet"])
    assert capsys.readouterr().err == ""


def test_the_help_text_explains_the_gps_default(capsys):
    with pytest.raises(SystemExit):
        cli.main(["scan", "--help"])
    help_text = capsys.readouterr().out
    assert "--precise-gps" in help_text
    assert "where somebody lives" in help_text


def test_unsupported_formats_are_counted_in_the_output(tmp_path, capsys):
    root = str(tmp_path / "photos")
    fx.write(root, "a.jpg", fx.simple_jpeg())
    fx.write(root, "b.cr3", b"\x00\x00\x00\x18ftypcrx ")
    cli.main(["scan", root, "--json", "--quiet"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["scan"]["unsupported"] == 1
    assert payload["scan"]["unsupported_formats"] == {"cr3": 1}


def test_single_worker_path_gives_the_same_answer(tmp_path, capsys):
    root = library(str(tmp_path / "photos"))
    cli.main(["scan", root, "--workers", "1", "--json", "--quiet"])
    serial = json.loads(capsys.readouterr().out)
    cli.main(["scan", root, "--workers", "4", "--json", "--quiet"])
    parallel = json.loads(capsys.readouterr().out)
    assert serial["scan"]["photos"] == parallel["scan"]["photos"]
    assert serial["focal"]["histogram"] == parallel["focal"]["histogram"]


def test_the_html_survives_a_round_trip_to_disk(tmp_path):
    root = library(str(tmp_path / "photos"))
    out = str(tmp_path / "nested" / "deeper" / "atlas.html")
    assert cli.main(["scan", root, "-o", out, "--quiet"]) == 0
    text = open(out, encoding="utf-8").read()
    text.encode("ascii")
    assert text.count("</html>") == 1


def test_bounded_map_yields_every_file(tmp_path):
    root = library(str(tmp_path / "photos"), count=40)
    from exif_atlas.exif import iter_image_files
    results = list(cli.bounded_map(iter_image_files(root), workers=4))
    assert len(results) == 40
    assert all(result.status == "ok" for result in results)


def test_bounded_map_serial_mode(tmp_path):
    root = library(str(tmp_path / "photos"), count=5)
    from exif_atlas.exif import iter_image_files
    results = list(cli.bounded_map(iter_image_files(root), workers=1))
    assert len(results) == 5


def test_default_workers_is_sane():
    assert 2 <= cli.default_workers() <= 16
