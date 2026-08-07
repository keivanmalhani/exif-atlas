"""The HTML report: self-contained, escaped, and free of anything remote."""

from __future__ import annotations

import re
from datetime import datetime

from exif_atlas.analyze import Aggregator, Options
from exif_atlas.exif import Photo, ScanResult
from exif_atlas.render import (bar_chart, esc, plural, render_html,
                               render_text)


def frame(**kwargs):
    settings = dict(
        path="/photos/x.jpg", container="jpeg", camera="FUJIFILM X-T4",
        lens="XF23mmF1.4 R", focal=23.0, fnumber=1.4, exposure=1 / 250.0,
        iso=400, taken=datetime(2024, 6, 21, 7, 30), tz_minutes=60,
    )
    settings.update(kwargs)
    return ScanResult(settings["path"], "ok", 512, settings["container"],
                      photo=Photo(**settings))


def build(results=None, options=None, meta=None):
    aggregator = Aggregator(options or Options())
    for result in results or [frame()] * 80:
        aggregator.add_result(result)
    return aggregator.report(meta or {"version": "0.1.0", "root": "/photos",
                                      "generated": "1 January 2026",
                                      "elapsed_seconds": 1.0,
                                      "files_per_second": 80.0})


def test_output_is_one_html_document():
    html = render_html(build())
    assert html.lstrip().startswith("<!doctype html")
    assert html.rstrip().endswith("</html>")


def test_nothing_is_fetched_over_the_network():
    """One file means one file. Any remote reference breaks that."""
    html = render_html(build())
    for pattern in ("http://", "https://", "//cdn", "<script src",
                    "<link rel=\"stylesheet\"", "@import", "url(http"):
        assert pattern not in html


def test_styles_are_inline():
    html = render_html(build())
    assert "<style" in html


def test_charts_are_inline_svg():
    html = render_html(build())
    assert "<svg" in html
    assert "<img" not in html


def test_both_colour_schemes_are_defined():
    html = render_html(build())
    normalised = html.replace("prefers-color-scheme: ", "prefers-color-scheme:")
    assert "prefers-color-scheme:dark" in normalised


def test_output_is_pure_ascii():
    render_html(build()).encode("ascii")


def test_no_emoji_or_decorative_symbols():
    """The report is typography, not iconography."""
    html = render_html(build())
    for character in html:
        assert ord(character) < 0x80, repr(character)


def test_the_camera_name_reaches_the_page():
    assert "X-T4" in render_html(build())


def test_html_special_characters_in_gear_names_are_escaped():
    """Lens names contain characters that would otherwise close a tag."""
    nasty = 'Sigma 35mm <script>alert("x")</script> & "Art"'
    html = render_html(build([frame(lens=nasty)] * 80))
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html


def test_esc_handles_every_dangerous_character():
    assert esc('<&>"') == "&lt;&amp;&gt;&quot;"
    assert esc(None) == ""
    assert esc(42) == "42"


def test_a_report_with_no_photographs_still_renders():
    html = render_html(build([]))
    assert "<html" in html
    assert len(html) > 1000


def test_a_report_with_one_photograph_still_renders():
    html = render_html(build([frame()]))
    assert "<html" in html


def test_precise_coordinates_are_absent_by_default():
    results = [frame(latitude=51.507351, longitude=-0.127758)] * 80
    html = render_html(build(results))
    assert "51.51" in html
    assert "51.507351" not in html


def test_the_privacy_note_appears_when_locations_do():
    results = [frame(latitude=51.5, longitude=-0.12)] * 80
    html = render_html(build(results))
    assert "rounded" in html.lower()


def test_precise_mode_says_so_on_the_page():
    results = [frame(latitude=51.507351, longitude=-0.127758)] * 80
    html = render_html(build(results, Options(gps="precise")))
    assert "51.50735" in html
    assert "front door" in html


def test_no_gps_leaves_no_coordinate_anywhere():
    results = [frame(latitude=51.5, longitude=-0.12)] * 80
    html = render_html(build(results, Options(gps="off")))
    assert "51.5" not in html
    assert "--no-gps" in html


def test_the_absence_section_is_rendered():
    results = [frame(focal=24.0)] * 60 + [frame(focal=70.0)] * 60
    html = render_html(build(results))
    assert "never" in html.lower()


def test_the_files_per_second_figure_reaches_the_page():
    html = render_html(build(meta={"version": "0.1.0", "root": "/photos",
                                   "generated": "1 January 2026",
                                   "elapsed_seconds": 2.0,
                                   "files_per_second": 1234.0}))
    assert "1,234" in html


def test_svg_bars_stay_inside_the_viewbox():
    rows = [{"label": "%dmm" % f, "count": c, "share": c / 100.0}
            for f, c in ((24, 50), (35, 30), (50, 20))]
    svg = bar_chart(rows)
    width = float(re.search(r'viewBox="0 0 ([\d.]+)', svg).group(1))
    rects = re.findall(r'<rect[^>]*x="([\d.]+)"[^>]*width="([\d.]+)"', svg)
    assert rects
    for x, w in rects:
        assert float(x) + float(w) <= width + 0.5


def test_a_chart_of_one_bar_does_not_divide_by_zero():
    svg = bar_chart([{"label": "23mm", "count": 5, "share": 1.0}])
    assert "<svg" in svg


def test_a_chart_of_nothing_produces_nothing_broken():
    empty = bar_chart([])
    assert "<svg" not in empty
    assert "Nothing recorded" in empty


def test_svg_is_well_formed_xml():
    """Every chart on the page has to parse, or a browser will guess."""
    from xml.etree import ElementTree
    html = render_html(build())
    for fragment in re.findall(r"<svg.*?</svg>", html, re.S):
        ElementTree.fromstring(fragment)


def test_the_document_has_one_h1():
    html = render_html(build())
    assert html.count("<h1") == 1


def test_tabular_numerals_are_requested_for_compared_figures():
    html = render_html(build())
    assert "tabular-nums" in html


def test_the_page_declares_a_viewport():
    assert 'name="viewport"' in render_html(build())


def test_the_text_summary_mentions_the_headline_numbers():
    text = render_text(build())
    assert "X-T4" in text
    assert "23" in text


def test_the_text_summary_is_ascii_and_unstyled():
    text = render_text(build())
    text.encode("ascii")
    assert "<" not in text


# ---------------------------------------------------------------------------
# Counts and the nouns attached to them
# ---------------------------------------------------------------------------


def test_plural_uses_the_singular_for_one():
    assert plural(1, "frame") == "1 frame"


def test_plural_uses_the_plural_for_zero():
    assert plural(0, "frame") == "0 frames"


def test_plural_uses_the_plural_for_many():
    assert plural(2, "frame") == "2 frames"


def test_plural_takes_an_irregular_plural():
    assert plural(3, "body", "bodies") == "3 bodies"
    assert plural(1, "body", "bodies") == "1 body"


def test_plural_groups_thousands_like_every_other_number():
    assert plural(1200, "file") == "1,200 files"


def test_plural_survives_a_missing_count():
    assert plural(None, "frame") == "- frames"


def test_a_library_of_one_frame_never_says_one_frames():
    report = build(results=[frame()])
    html = render_html(report)
    assert "1 frames" not in html
    assert "1 places" not in html
    assert "1 days" not in html
    assert "1 files" not in html


def test_the_text_summary_of_one_frame_reads_as_english():
    text = render_text(build(results=[frame()]))
    assert "1 frame)" in text
    assert "1 frames" not in text


def test_a_single_day_is_reported_as_a_day():
    html = render_html(build(results=[frame()]))
    assert "1 day with at least one frame" in html


def test_many_frames_still_read_as_plural():
    html = render_html(build())
    assert "80 frames" in html
