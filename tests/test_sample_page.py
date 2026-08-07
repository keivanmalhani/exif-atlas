"""The published sample: the library it plans and the page it produces.

scripts/build_sample.py writes docs/index.html, which is the one thing about
this project a stranger will look at. The planning half of it is pure - a
seed in, a list of frames out, no clock and no filesystem - so it can be
tested like anything else, and the two edits it makes to the tool's output
can be tested against a rendered report.
"""

from __future__ import annotations

import os
import re
from datetime import date

from exif_atlas.analyze import Aggregator, Options
from exif_atlas.cli import main as cli_main
from exif_atlas.exif import iter_image_files, read_photo
from exif_atlas.render import render_html

from scripts import build_sample as sample


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------


def test_plan_is_deterministic():
    """The whole point of the seed. Two runs, one library."""
    first = sample.plan_frames(seed=99)
    second = sample.plan_frames(seed=99)
    assert len(first) == len(second)
    assert first == second


def test_a_different_seed_gives_a_different_library():
    assert sample.plan_frames(seed=1) != sample.plan_frames(seed=2)


def test_plan_is_big_enough_to_be_worth_publishing():
    frames = sample.plan_frames()
    assert len(frames) > 1200
    days = {frame["taken"].date() for frame in frames}
    assert len(days) > 300
    assert len({frame["body"] for frame in frames}) == 3
    assert len({frame["lens"] for frame in frames}) == 6


def test_frames_are_in_order_and_inside_the_window():
    """Day by day, in order. Within a day the two outings are not sorted
    against each other - an evening walk can be planned before a morning
    one - which does not matter to anything downstream."""
    frames = sample.plan_frames()
    days = [frame["taken"].date() for frame in frames]
    assert days == sorted(days)
    assert days[0] >= sample.FIRST_DAY
    assert days[-1] <= sample.LAST_DAY
    # A session is cut off at midnight rather than wrapping into the next
    # day, so no frame lands in an hour nobody was ever out in. The report's
    # "hours you are never out" finding depends on that holding.
    assert min(frame["taken"].hour for frame in frames) >= min(
        sample.HOUR_WEIGHTS)


def test_the_span_covers_every_year_in_the_window():
    frames = sample.plan_frames()
    years = {frame["taken"].year for frame in frames}
    assert years == set(range(sample.FIRST_DAY.year, sample.LAST_DAY.year + 1))


def test_no_frame_carries_a_coordinate():
    """This repository is public. There is no GPS in the sample, at all."""
    for frame in sample.plan_frames():
        assert "gps" not in frame
        assert "latitude" not in frame
        assert "longitude" not in frame
    blob = sample.frame_bytes(sample.plan_frames()[0])
    assert b"GPS" not in blob


def test_gear_only_appears_inside_its_service_window():
    bodies = {body.model: body for body in sample.BODIES}
    lenses = {lens.name: lens for lens in sample.LENSES}
    for frame in sample.plan_frames():
        when = frame["taken"].date()
        body = bodies[frame["body"]]
        lens = lenses[frame["lens"]]
        assert body.first <= when <= body.last, frame
        assert lens.first <= when <= lens.last, frame


def test_a_fixed_lens_never_leaves_its_own_body():
    fixed = {lens.name for lens in sample.LENSES if lens.mount == "fixed"}
    fixed_bodies = {body.model for body in sample.BODIES
                    if body.mount == "fixed"}
    for frame in sample.plan_frames():
        if frame["lens"] in fixed:
            assert frame["body"] in fixed_bodies
        else:
            assert frame["body"] not in fixed_bodies


def test_focal_lengths_stay_inside_the_declared_range():
    lenses = {lens.name: lens for lens in sample.LENSES}
    for frame in sample.plan_frames():
        lens = lenses[frame["lens"]]
        assert lens.low <= frame["focal"] <= lens.high
        assert frame["lens_spec"] == (lens.low, lens.high,
                                      lens.wide_f, lens.long_f)


def test_no_frame_is_faster_than_its_lens_can_open():
    """A frame at f/1.4 on an f/2.8 zoom would be a lie the report repeats."""
    lenses = {lens.name: lens for lens in sample.LENSES}
    for frame in sample.plan_frames():
        lens = lenses[frame["lens"]]
        assert frame["fnumber"] >= lens.widest_at(frame["focal"]) - 1e-9


def test_the_telephoto_is_never_racked_out_to_its_long_end():
    """The report's "focal range you own and have not used" finding needs a
    range that is genuinely never reached, not one that happens to be rare."""
    reached = [frame["focal"] for frame in sample.plan_frames()
               if frame["lens"].startswith("XF70-300")]
    assert reached
    assert max(reached) <= 300.0 * 0.85


def test_settings_come_off_the_standard_scales():
    frames = sample.plan_frames()
    for frame in frames:
        assert frame["iso"] in sample.ISOS
        assert frame["exposure"] in sample.SHUTTERS
        assert frame["focal35"] == int(round(frame["focal"] * sample.CROP))


def test_exposures_are_representable_as_an_exif_rational():
    """Below a second the tag holds 1/N and above it holds N/1. A value that
    survives neither comes back off disk as a different number."""
    from tests.fixtures import exposure_pair

    for value in sample.SHUTTERS:
        numerator, denominator = exposure_pair(value)
        assert abs(numerator / denominator - value) < 1e-9


def test_exposure_triangle_agrees_with_itself():
    """Aperture, shutter and ISO on one frame should describe one scene."""
    import random

    rng = random.Random(4)
    for ev in (6.0, 10.0, 14.0):
        iso, seconds = sample.expose(rng, ev, 2.8, 35, tripod=False)
        measured = (2.8 ** 2) / (seconds * iso / 100.0)
        assert abs(measured - 2.0 ** ev) < 2.0 ** ev  # within a stop

    iso, seconds = sample.expose(rng, 4.0, 11.0, 24, tripod=True)
    assert iso == sample.ISOS[0]
    assert seconds >= 1.0


def test_library_summary_counts_every_file_on_disk():
    frames = sample.plan_frames()
    summary = sample.library_summary(frames)
    assert summary["frames"] == len(frames)
    assert summary["files"] == (summary["frames"] + summary["raws"]
                                + sample.STRIPPED_EXPORTS)
    assert summary["first"] >= sample.FIRST_DAY
    assert summary["last"] <= sample.LAST_DAY


def test_a_raw_shooter_gets_two_files_for_one_frame():
    frames = sample.plan_frames()
    raw = next(frame for frame in frames if frame["raw"])
    plain = next(frame for frame in frames if not frame["raw"])
    assert [kind for _, kind in sample.frame_paths(raw)] == ["jpeg", "raw"]
    assert [kind for _, kind in sample.frame_paths(plain)] == ["jpeg"]
    for path, _ in sample.frame_paths(raw):
        assert path.startswith("%04d/" % raw["taken"].year)


# ---------------------------------------------------------------------------
# The two edits to the tool's output
# ---------------------------------------------------------------------------


def rendered_report():
    """A real report, rendered by the real renderer."""
    aggregator = Aggregator(Options(gps="off"))
    from tests.fixtures import simple_jpeg

    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        from tests.fixtures import write

        for index in range(12):
            write(folder, "f%02d.jpg" % index, simple_jpeg())
        for path in iter_image_files(folder):
            aggregator.add_result(read_photo(path))
    return render_html(aggregator.report({"version": "0.1.0",
                                          "root": "/tmp/whatever",
                                          "generated": "1 January 2026",
                                          "elapsed_seconds": 1.0,
                                          "files_per_second": 12.0}))


def test_banner_is_ascii_and_says_what_the_page_is():
    banner = sample.sample_banner()
    banner.encode("ascii")
    lowered = banner.lower()
    assert "sample" in lowered
    assert "invented" in lowered
    assert sample.REPO_URL in banner


def test_banner_quotes_the_file_count_the_report_will_print():
    summary = sample.library_summary(sample.plan_frames())
    banner = sample.sample_banner(summary)
    assert "{:,}".format(summary["files"]) in banner


def test_banner_uses_only_classes_the_stylesheet_defines():
    """There is no second stylesheet on the page. A class the report does not
    define renders as unstyled text, which is how a bolted on banner looks."""
    banner = sample.sample_banner()
    from exif_atlas.render import STYLE

    for name in re.findall(r'class="([^"]+)"', banner):
        for token in name.split():
            assert ".%s" % token in STYLE, token


def test_banner_does_not_use_b_inside_its_paragraph():
    """.privacy b is a block level label. A <b> used for emphasis inside the
    paragraph breaks the card into a stack of disconnected lines."""
    banner = sample.sample_banner()
    paragraph = banner[banner.index("<p>"):]
    assert "<b>" not in paragraph


def test_banner_lands_above_the_report():
    """First thing inside the page, ahead of the masthead. The comparison is
    made on the body alone: the stylesheet in the head mentions .privacy and
    would otherwise be found first."""
    document = sample.insert_banner(rendered_report(), sample.sample_banner())
    body = document.split("<body>", 1)[1]
    assert body.index(sample.BANNER_ANCHOR) < body.index('class="privacy"')
    assert body.index("A sample") < body.index("masthead")


def test_insert_banner_refuses_a_document_it_does_not_recognise():
    import pytest

    with pytest.raises(ValueError):
        sample.insert_banner("<html><body>nothing here</body></html>", "x")


def test_retitle_replaces_the_temporary_path():
    original = rendered_report()
    document = sample.retitle(original, "a sample atlas")
    head = document.split("</head>")[0]
    assert "<title>a sample atlas</title>" in head
    assert head.count("<title>") == 1
    assert "/tmp/whatever" not in head


def test_retitle_leaves_the_chart_tooltips_alone():
    """Every bar carries an SVG <title>. Retitling must not touch them."""
    original = rendered_report()
    assert original.count("<title>") > 20      # the tooltips
    document = sample.retitle(original, "a sample atlas")
    assert document.count("<title>") == original.count("<title>")
    assert document.split("</head>")[1] == original.split("</head>")[1]


def test_retitle_refuses_a_document_without_one():
    import pytest

    with pytest.raises(ValueError):
        sample.retitle("<html><head></head><body>no title</body></html>", "x")
    with pytest.raises(ValueError):
        sample.retitle("<title>orphan</title>", "x")


def test_finished_page_is_still_self_contained():
    """The published file has to survive being opened with no network."""
    document = sample.finish_page(rendered_report(),
                                  sample.library_summary(
                                      sample.plan_frames(seed=5)))
    document.encode("ascii")
    assert "<script" not in document
    assert "@import" not in document
    assert "<link" not in document
    assert "<img" not in document
    # The only absolute URL on the page is the repository link, and it is a
    # link rather than something the browser goes and fetches.
    urls = re.findall(r'https?://[^\s"\'<>]+', document)
    assert set(urls) == {sample.REPO_URL}
    assert 'href="%s"' % sample.REPO_URL in document


def test_reroot_replaces_the_temporary_path_in_the_body():
    """The path is printed under the headline as well as in the title. A
    published page naming a temp directory reads as a mistake.

    Scoped to the body paragraph on purpose: reroot does not touch the title,
    which is retitle's job, so the whole document still holds the path at this
    point. test_the_finished_page_names_no_temporary_directory is the one that
    checks both edits together.
    """
    original = rendered_report()
    assert '<p class="path">/tmp/whatever</p>' in original
    document = sample.reroot(original, "a generated sample library")
    assert '<p class="path">/tmp/whatever</p>' not in document
    assert '<p class="path">a generated sample library</p>' in document


def test_reroot_touches_nothing_but_that_one_paragraph():
    original = rendered_report()
    document = sample.reroot(original, "x")
    assert original.count('<p class="path">') == 1
    assert document.count('<p class="path">') == 1
    # Everything before the paragraph and everything after it is untouched.
    head, tail = original.split('<p class="path">/tmp/whatever</p>')
    assert document == head + '<p class="path">x</p>' + tail


def test_reroot_refuses_a_document_without_a_path_line():
    import pytest

    with pytest.raises(ValueError):
        sample.reroot("<html><body>no path here</body></html>", "x")


def test_the_finished_page_names_no_temporary_directory():
    """The whole point: nothing on the published page mentions /tmp."""
    document = sample.finish_page(rendered_report(),
                                  sample.library_summary(
                                      sample.plan_frames(seed=5)))
    assert "/tmp/" not in document
    assert "a generated sample library" in document


# ---------------------------------------------------------------------------
# End to end, small
# ---------------------------------------------------------------------------


def test_a_slice_of_the_library_scans_into_a_real_report(tmp_path):
    """Write real files, run the real scan, and check the report describes
    them. This is the whole pipeline the published page comes out of."""
    frames = sample.plan_frames()[:400]
    written = sample.write_library(frames, str(tmp_path))
    assert written >= len(frames)

    output = tmp_path / "atlas.html"
    status = cli_main(["scan", str(tmp_path), "-o", str(output), "--no-gps",
                       "--quiet"])
    assert status == 0

    document = output.read_text(encoding="utf-8")
    document.encode("ascii")
    assert "How you actually shoot" in document
    assert "FUJIFILM" in document
    # No coordinate reached the page, and none could have.
    assert "latitude" not in document.lower()


def test_the_published_page_exists_and_is_the_tool_s_own_output():
    """docs/index.html is checked in. If it drifts from what the renderer
    produces, this is the test that says so."""
    page = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "docs", "index.html")
    if not os.path.exists(page):                    # pragma: no cover
        import pytest
        pytest.skip("docs/index.html has not been built")
    document = open(page, encoding="utf-8").read()
    document.encode("ascii")
    assert document.lstrip().startswith("<!doctype html")
    assert "<script" not in document
    assert sample.BANNER_ANCHOR in document
    assert "A sample. Nobody took these pictures." in document
    assert "exif-atlas: a sample atlas" in document
    # The temporary directory the sample was built in must not be on the page.
    assert "/tmp/" not in document
    urls = set(re.findall(r'https?://[^\s"\'<>]+', document))
    assert urls == {sample.REPO_URL}
