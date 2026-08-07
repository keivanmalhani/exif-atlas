"""Render a report dictionary as one self-contained HTML file.

There is no network in this file and nothing to fetch. Charts are SVG
elements written out here as text, styled by the same stylesheet as the rest
of the page, which is what makes them follow prefers-color-scheme without a
line of script. The output opens from a file:// URL, survives being emailed,
and prints.
"""

from __future__ import annotations

import html
import math
from datetime import date, datetime
from typing import Iterable, Sequence

__all__ = ["render_html", "render_text"]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def num(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float) and not value.is_integer():
        return "%.1f" % value
    return "{:,}".format(int(value))


def plural(count, singular: str, many: str | None = None) -> str:
    """Format a count with a noun that agrees with it.

    A report that says "1 frames" reads like a machine wrote it, which is
    exactly the impression this file is trying not to give.
    """
    try:
        one = abs(int(count)) == 1
    except (TypeError, ValueError):
        one = False
    word = singular if one else (many or singular + "s")
    return "%s %s" % (num(count), word)


def pct(value, digits: int = 0) -> str:
    if value is None:
        return "-"
    scaled = value * 100.0
    if 0 < scaled < 1:
        return "<1%"
    return "%.*f%%" % (digits, scaled)


def trim(value) -> str:
    if value is None:
        return "-"
    text = ("%.1f" % float(value)).rstrip("0").rstrip(".")
    return text or "0"


def pretty_date(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return parsed.strftime("%d %b %Y").lstrip("0")


def _to_date(iso: str | None) -> date | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# SVG primitives
# ---------------------------------------------------------------------------

CHART_WIDTH = 720


def _axis_steps(maximum: int) -> list[int]:
    if maximum <= 0:
        return [0]
    magnitude = 10 ** int(math.floor(math.log10(maximum)))
    for factor in (1, 2, 2.5, 5, 10):
        step = magnitude * factor
        if maximum / step <= 4:
            break
    steps = []
    value = 0.0
    while value <= maximum + step * 0.001:
        steps.append(int(round(value)))
        value += step
    return steps


def _chart_block(svg: str) -> str:
    """Wrap a chart so a narrow screen can hold it at a legible size.

    An SVG with a fixed viewBox scales its type down with everything else.
    In a 400px column a 10px axis label lands at 5px, which is decoration
    rather than information, so below the breakpoint the chart keeps a
    floor width and the block scrolls sideways instead.
    """
    return '<div class="chartwrap">%s</div>' % svg


def bar_chart(rows: Sequence[dict], *, height: int = 210,
              label_key: str = "label", value_key: str = "count",
              label_every: int = 1, highlight: Iterable = (),
              bands: Sequence[dict] = (), markers: Sequence[dict] = (),
              value_labels: bool = False, caption: str = "") -> str:
    """A vertical bar chart as inline SVG."""
    if not rows:
        return '<p class="empty">Nothing recorded.</p>'

    # Marker captions get a reserved strip above the plot. Drawn inside it
    # they collide with whichever bar happens to be tallest, which is
    # exactly the bar the reader came to look at.
    headroom = 15 if markers else 0
    # No vertical scale means no gutter to hold it in.
    gutter = 10 if value_labels else 42
    left, right, top, bottom = gutter, 10, 16 + headroom, 36
    plot_width = CHART_WIDTH - left - right
    plot_height = height - top - bottom
    count = len(rows)
    slot = plot_width / count
    bar_width = max(1.4, min(slot * 0.74, 46.0))
    maximum = max(int(row.get(value_key) or 0) for row in rows) or 1
    highlight_set = set(highlight)

    def x_at(position: float) -> float:
        return left + position * slot

    def y_at(value: float) -> float:
        return top + plot_height - (value / maximum) * plot_height

    parts = [
        '<svg class="chart" viewBox="0 0 %d %d" role="img" '
        'preserveAspectRatio="xMidYMid meet" aria-label="%s">'
        % (CHART_WIDTH, height, esc(caption or "bar chart"))
    ]

    for band in bands:
        start = x_at(band["start"])
        end = x_at(band["end"])
        if end <= start:
            continue
        parts.append(
            '<rect class="band %s" x="%.1f" y="%.1f" width="%.1f" '
            'height="%.1f"/>' % (band.get("kind", ""), start, top,
                                 end - start, plot_height))

    # With a value over every bar the vertical scale is saying the same
    # thing a second time, so it is left out.
    for step in ([] if value_labels else _axis_steps(maximum)):
        y = y_at(step)
        parts.append('<line class="grid" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                     % (left, y, CHART_WIDTH - right, y))
        parts.append('<text class="axis num" x="%d" y="%.1f" '
                     'text-anchor="end">%s</text>'
                     % (left - 7, y + 3.6, esc(num(step))))

    for index, row in enumerate(rows):
        value = int(row.get(value_key) or 0)
        label = str(row.get(label_key, ""))
        x = x_at(index + 0.5) - bar_width / 2
        bar_height = max(0.0, (value / maximum) * plot_height)
        classes = "bar"
        if label in highlight_set or row.get("highlight"):
            classes += " strong"
        if value == 0:
            classes += " zero"
        parts.append(
            '<rect class="%s" x="%.1f" y="%.1f" width="%.1f" height="%.1f">'
            '<title>%s: %s</title></rect>'
            % (classes, x, y_at(value), bar_width,
               max(bar_height, 1.0 if value else 0.0),
               esc(label), esc(num(value))))
        if value_labels and value and count <= 16:
            parts.append('<text class="value num" x="%.1f" y="%.1f" '
                         'text-anchor="middle">%s</text>'
                         % (x + bar_width / 2, y_at(value) - 5,
                            esc(num(value))))
        if index % label_every == 0:
            parts.append('<text class="axis" x="%.1f" y="%d" '
                         'text-anchor="middle">%s</text>'
                         % (x_at(index + 0.5), height - 18, esc(label)))

    for marker in markers:
        x = x_at(marker["at"])
        parts.append('<line class="marker" x1="%.1f" y1="%d" x2="%.1f" '
                     'y2="%.1f"/>' % (x, top, x, top + plot_height))
        anchor = marker.get("anchor", "start")
        offset = 4 if anchor == "start" else -4
        parts.append('<text class="marker-label" x="%.1f" y="%d" '
                     'text-anchor="%s">%s</text>'
                     % (x + offset, top - 5, anchor, esc(marker["label"])))

    parts.append('<line class="axis-line" x1="%d" y1="%.1f" x2="%d" '
                 'y2="%.1f"/>' % (left, top + plot_height,
                                  CHART_WIDTH - right, top + plot_height))
    parts.append("</svg>")
    return _chart_block("".join(parts))


def range_chart(rows: Sequence[dict], *, caption: str = "") -> str:
    """A horizontal span per item, used for when gear was in service."""
    usable = [row for row in rows if row.get("first") and row.get("last")]
    if not usable:
        return '<p class="empty">No dated frames, so no timeline.</p>'

    starts = [_to_date(row["first"]) for row in usable]
    ends = [_to_date(row["last"]) for row in usable]
    low = min(d for d in starts if d)
    high = max(d for d in ends if d)
    total_days = max((high - low).days, 1)

    left, right, top = 176, 14, 12
    row_height = 26
    height = top + row_height * len(usable) + 34
    plot_width = CHART_WIDTH - left - right

    def x_at(when: date) -> float:
        return left + ((when - low).days / total_days) * plot_width

    parts = ['<svg class="chart timeline" viewBox="0 0 %d %d" role="img" '
             'preserveAspectRatio="xMidYMid meet" aria-label="%s">'
             % (CHART_WIDTH, height, esc(caption or "gear timeline"))]

    years = sorted({low.year + offset
                    for offset in range(high.year - low.year + 1)})
    for year in years:
        mark = date(year, 1, 1)
        if mark < low:
            mark = low
        x = x_at(mark)
        parts.append('<line class="grid" x1="%.1f" y1="%d" x2="%.1f" '
                     'y2="%d"/>' % (x, top - 4, x,
                                    top + row_height * len(usable) + 4))
        parts.append('<text class="axis num" x="%.1f" y="%d" '
                     'text-anchor="middle">%d</text>'
                     % (x, top + row_height * len(usable) + 20, year))

    for index, row in enumerate(usable):
        y = top + index * row_height + row_height / 2
        start = _to_date(row["first"])
        end = _to_date(row["last"])
        x1 = x_at(start)
        x2 = max(x_at(end), x1 + 3)
        label = row["name"]
        if len(label) > 26:
            label = label[:25] + "..."
        parts.append('<text class="axis name" x="%d" y="%.1f" '
                     'text-anchor="end">%s</text>'
                     % (left - 12, y + 4, esc(label)))
        parts.append('<rect class="span" x="%.1f" y="%.1f" width="%.1f" '
                     'height="10" rx="5"><title>%s: %s to %s, %s'
                     '</title></rect>'
                     % (x1, y - 5, x2 - x1, esc(row["name"]),
                        esc(pretty_date(row["first"])),
                        esc(pretty_date(row["last"])),
                        esc(plural(row["count"], "frame"))))
    parts.append("</svg>")
    return _chart_block("".join(parts))


def calendar_heatmap(years: Sequence[dict]) -> str:
    """A year per block, one square per day, shaded by frame count."""
    if not years:
        return '<p class="empty">No dated frames, so no calendar.</p>'

    cell = 10
    gap = 2
    pitch = cell + gap
    label_gutter = 34
    block_height = 7 * pitch + 26
    width = label_gutter + 54 * pitch + 8
    height = len(years) * block_height + 12

    parts = ['<svg class="chart calendar" viewBox="0 0 %d %d" role="img" '
             'width="%d" aria-label="shooting days by year">'
             % (width, height, width)]

    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    for block, entry in enumerate(years):
        base_y = block * block_height + 6
        year = entry["year"]
        counts = entry["days"]
        peak = max(entry["max"], 1)
        parts.append('<text class="axis year" x="0" y="%d">%d</text>'
                     % (base_y + 12, year))

        january = date(year, 1, 1)
        offset = january.weekday()
        for month in range(1, 13):
            first = date(year, month, 1)
            column = (first.timetuple().tm_yday - 1 + offset) // 7
            parts.append('<text class="axis month" x="%.1f" y="%d">%s</text>'
                         % (label_gutter + column * pitch, base_y + 8,
                            month_names[month - 1]))

        total_days = 366 if _is_leap(year) else 365
        january_ordinal = january.toordinal()
        for day_of_year in range(1, total_days + 1):
            current = date.fromordinal(january_ordinal + day_of_year - 1)
            column = (day_of_year - 1 + offset) // 7
            row = current.weekday()
            key = current.isoformat()
            count = counts.get(key, 0)
            level = 0
            if count:
                ratio = count / peak
                level = 1 + min(3, int(ratio * 4)) if ratio < 1 else 4
            x = label_gutter + column * pitch
            y = base_y + 14 + row * pitch
            title = ("%s: %s" % (current.strftime("%d %b %Y"),
                                 plural(count, "frame"))) if count else \
                    "%s: no frames" % current.strftime("%d %b %Y")
            parts.append('<rect class="day l%d" x="%.1f" y="%.1f" width="%d" '
                         'height="%d" rx="2"><title>%s</title></rect>'
                         % (level, x, y, cell, cell, esc(title)))
    parts.append("</svg>")
    return "".join(parts)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def meter(share: float) -> str:
    width = max(0.0, min(1.0, share or 0.0)) * 100.0
    return ('<span class="meter"><span style="width:%.1f%%"></span></span>'
            % width)


# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

STYLE = """
*,*::before,*::after{box-sizing:border-box}
:root{
  color-scheme:light dark;
  --sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,
    "Helvetica Neue",Arial,sans-serif;
  --serif:ui-serif,Georgia,Cambria,"Times New Roman",Times,serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --bg:#fbfaf8;
  --panel:#ffffff;
  --ink:#16161a;
  --ink-soft:#54545e;
  --ink-faint:#8b8b93;
  --rule:#e4e1db;
  --rule-soft:#efece7;
  --accent:#a94b26;
  --accent-ink:#a94b26;
  --flag:#7a5c15;
  --flag-bg:rgba(168,132,32,.10);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#111112;
    --panel:#17171a;
    --ink:#eceae5;
    --ink-soft:#a5a49e;
    --ink-faint:#73726d;
    --rule:#2b2b2f;
    --rule-soft:#212125;
    --accent:#dd8253;
    --accent-ink:#e79465;
    --flag:#cfa855;
    --flag-bg:rgba(207,168,85,.10);
  }
}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--ink);
  font-family:var(--sans);font-size:15.5px;line-height:1.62;
  -webkit-font-smoothing:antialiased;
}
.atlas{max-width:960px;margin:0 auto;padding:44px 22px 90px}
a{color:var(--accent-ink)}
.num{font-family:var(--mono);font-variant-numeric:tabular-nums}

header.masthead{border-bottom:1px solid var(--rule);padding-bottom:30px}
.eyebrow{
  font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;
  font-weight:600;color:var(--ink-faint);margin:0 0 14px
}
h1{
  font-family:var(--serif);font-weight:400;
  font-size:clamp(30px,6.4vw,52px);line-height:1.06;
  letter-spacing:-.022em;margin:0 0 14px
}
.subtitle{color:var(--ink-soft);margin:0;max-width:56ch;font-size:16px}
.path{font-family:var(--mono);font-size:12.5px;color:var(--ink-faint);
  word-break:break-all;margin-top:14px}

/* Each cell carries its own hairline instead of the grid showing a
   coloured background through a 1px gap. With auto-fit the last row is
   often short, and a background bleed turns that empty track into a solid
   slab of rule colour. */
.stats{
  display:grid;gap:1px;
  grid-template-columns:repeat(auto-fit,minmax(148px,1fr));
  margin:30px 0 4px
}
.stat{
  background:var(--panel);padding:15px 16px 14px;
  box-shadow:0 0 0 1px var(--rule)
}
.stat b{
  display:block;font-family:var(--serif);font-weight:400;
  font-size:29px;line-height:1.08;letter-spacing:-.02em;
  font-variant-numeric:tabular-nums
}
.stat span{
  display:block;font-size:11px;letter-spacing:.09em;text-transform:uppercase;
  color:var(--ink-faint);margin-top:7px;font-weight:600
}

section{margin-top:58px}
h2{
  font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;
  font-weight:700;color:var(--ink-faint);margin:0 0 6px;
  padding-bottom:9px;border-bottom:1px solid var(--rule)
}
h3{
  font-family:var(--serif);font-weight:400;font-size:25px;
  letter-spacing:-.015em;line-height:1.2;margin:20px 0 8px
}
h4{font-size:13px;font-weight:700;margin:26px 0 8px;letter-spacing:.01em}
p{margin:0 0 14px;max-width:68ch}
.lede{font-size:17px;color:var(--ink-soft);max-width:60ch}
.note{font-size:13.5px;color:var(--ink-faint);max-width:66ch}
.empty{color:var(--ink-faint);font-style:italic}

.headline{
  font-family:var(--serif);font-size:clamp(22px,4.2vw,32px);
  line-height:1.28;font-weight:400;letter-spacing:-.015em;
  margin:6px 0 14px;max-width:24ch
}
.headline em{font-style:normal;color:var(--accent-ink)}

.card{
  background:var(--panel);border:1px solid var(--rule);border-radius:3px;
  padding:18px 20px;margin:18px 0
}
.grid2{display:grid;gap:16px;grid-template-columns:repeat(auto-fit,
  minmax(268px,1fr))}

table{width:100%;border-collapse:collapse;font-size:14px;margin:10px 0 4px}
th{
  text-align:left;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--ink-faint);font-weight:700;padding:0 10px 8px 0;
  border-bottom:1px solid var(--rule);white-space:nowrap
}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--rule-soft);
  vertical-align:middle}
td.r,th.r{text-align:right}
tr:last-child td{border-bottom:none}
.name{font-weight:600}
.sub{color:var(--ink-faint);font-size:12.5px}
/* Only as a span. Setting display:block on a td takes the cell out of the
   row and every rule and baseline in that row stops lining up. */
span.sub{display:block}

.meter{
  display:block;height:5px;background:var(--rule-soft);border-radius:3px;
  overflow:hidden;min-width:56px
}
.meter>span{display:block;height:100%;background:var(--accent);opacity:.8}

.chart{width:100%;height:auto;display:block;margin:14px 0 4px}
.chart .bar{fill:var(--accent);opacity:.82}
.chart .bar.strong{opacity:1}
.chart .bar.zero{fill:var(--ink-faint);opacity:.22}
.chart .grid{stroke:var(--rule);stroke-width:1}
.chart .axis-line{stroke:var(--rule);stroke-width:1}
.chart .axis{fill:var(--ink-faint);font-family:var(--sans);font-size:10.5px;
  font-weight:500}
.chart .axis.name{fill:var(--ink-soft);font-size:11.5px;font-weight:600}
.chart .axis.year{fill:var(--ink-soft);font-family:var(--mono);
  font-size:11px;font-weight:600}
.chart .axis.month{fill:var(--ink-faint);font-size:9.5px}
.chart .value{fill:var(--ink-soft);font-family:var(--mono);font-size:10px;
  font-weight:600}
.chart .band{fill:var(--ink);opacity:.055}
.chart .band.twilight{fill:var(--accent);opacity:.09}
.chart .marker{stroke:var(--accent);stroke-width:1;stroke-dasharray:2 3;
  opacity:.75}
.chart .marker-label{fill:var(--accent-ink);font-family:var(--sans);
  font-size:9.5px;font-weight:700;letter-spacing:.05em}
.chart .span{fill:var(--accent);opacity:.85}
.chart .day{fill:var(--rule-soft)}
.chart .day.l1{fill:var(--accent);opacity:.24}
.chart .day.l2{fill:var(--accent);opacity:.45}
.chart .day.l3{fill:var(--accent);opacity:.68}
.chart .day.l4{fill:var(--accent);opacity:.92}
.scroll{overflow-x:auto;padding-bottom:6px;-webkit-overflow-scrolling:touch}
.scroll .chart{width:auto;max-width:none}

.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:11.5px;
  color:var(--ink-faint);margin-top:4px;align-items:center}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;
  margin-right:5px;vertical-align:-1px;background:var(--accent)}

.habit{border-left:2px solid var(--accent);padding:2px 0 2px 16px;
  margin:20px 0}
.habit b{display:block;font-family:var(--serif);font-weight:400;
  font-size:20px;letter-spacing:-.01em;margin-bottom:3px}
.habit p{margin:0;color:var(--ink-soft);font-size:14.5px}

.absent{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 18px}
.absent span{
  border:1px solid var(--rule);border-radius:2px;padding:4px 9px;
  font-family:var(--mono);font-size:12.5px;color:var(--ink-soft);
  background:var(--panel)
}
.absent span.hard{border-color:var(--accent);color:var(--accent-ink)}

.privacy{
  border:1px solid var(--rule);border-left:3px solid var(--accent);
  background:var(--panel);padding:16px 18px;border-radius:3px;margin:18px 0
}
.privacy b{display:block;font-size:11.5px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--accent-ink);margin-bottom:7px}
.privacy p{margin:0;font-size:14px;color:var(--ink-soft)}
.warn{border-left-color:var(--flag);background:var(--flag-bg)}
.warn b{color:var(--flag)}

footer{margin-top:70px;padding-top:22px;border-top:1px solid var(--rule);
  font-size:12.5px;color:var(--ink-faint)}
footer p{max-width:70ch;margin:0 0 8px}
footer code{font-family:var(--mono);font-size:12px}

@media (max-width:560px){
  .atlas{padding:30px 16px 64px}
  body{font-size:15px}
  td,th{padding-right:6px}
  .hide-narrow{display:none}
  .chartwrap{overflow-x:auto;padding-bottom:6px;
    -webkit-overflow-scrolling:touch}
  .chartwrap .chart{width:620px;max-width:none}
}
@media print{
  :root{--bg:#fff;--panel:#fff;--ink:#000;--ink-soft:#333;--rule:#ccc}
  .atlas{max-width:none}
  section{break-inside:avoid}
}
"""


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _masthead(report: dict) -> str:
    meta = report.get("meta", {})
    scan = report["scan"]
    span = report["span"]
    filters = report.get("filters", {})

    active = []
    if filters.get("since"):
        active.append("from %s" % filters["since"])
    if filters.get("until"):
        active.append("to %s" % filters["until"])
    if filters.get("camera"):
        active.append("camera matching %s" % filters["camera"])
    if filters.get("lens"):
        active.append("lens matching %s" % filters["lens"])
    if filters.get("gps") == "off":
        active.append("location omitted")

    rate = meta.get("files_per_second")
    span_years = span.get("years") or []
    if len(span_years) > 1:
        span_label = "%d-%d" % (span_years[0], span_years[-1])
    elif span_years:
        span_label = str(span_years[0])
    else:
        span_label = "-"

    def agree(count, singular: str, many: str | None = None) -> str:
        """The label under a stat, matching the number above it."""
        try:
            one = abs(int(count)) == 1
        except (TypeError, ValueError):
            return many or singular + "s"
        return singular if one else (many or singular + "s")

    cameras_seen = len(report["gear"]["cameras"])
    lenses_seen = len(report["gear"]["lenses"])
    days_seen = span.get("active_days")
    stats = [
        (num(scan["photos"]), agree(scan["photos"], "frame") + " read"),
        (span_label, agree(len(span_years), "year") + " covered"),
        (num(days_seen), agree(days_seen, "day") + " shot"),
        (num(cameras_seen), agree(cameras_seen, "body", "bodies")),
        (num(lenses_seen), agree(lenses_seen, "lens", "lenses")),
    ]

    parts = ['<header class="masthead">',
             '<p class="eyebrow">exif-atlas report</p>',
             '<h1>How you actually shoot</h1>',
             '<p class="subtitle">Every number below came out of the '
             'metadata already in your files. No photograph was opened, '
             'nothing was uploaded, and nothing here left this machine.'
             '</p>']
    if meta.get("root"):
        parts.append('<p class="path">%s</p>' % esc(meta["root"]))
    if active:
        parts.append('<p class="note">Filtered: %s.</p>'
                     % esc("; ".join(active)))
    parts.append('<div class="stats">')
    for value, label in stats:
        parts.append('<div class="stat"><b>%s</b><span>%s</span></div>'
                     % (esc(value), esc(label)))
    parts.append("</div>")
    if scan["photos"]:
        detail = "%s to %s" % (pretty_date(span.get("first")),
                               pretty_date(span.get("last")))
        if rate:
            detail += ", read at %s files per second" % num(int(rate))
        parts.append('<p class="note">%s.</p>' % esc(detail))
    parts.append("</header>")
    return "".join(parts)


def _gear_section(report: dict) -> str:
    gear = report["gear"]
    parts = ['<section id="gear"><h2>Gear</h2>',
             '<h3>What took the pictures, and when</h3>']

    cameras = gear["cameras"]
    if not cameras:
        parts.append('<p class="empty">No camera was named in any file.</p>')
    else:
        parts.append('<table><thead><tr><th>Body</th><th class="r">Frames'
                     '</th><th class="r hide-narrow">Share</th>'
                     '<th class="hide-narrow">In service</th>'
                     '<th class="r hide-narrow">Days</th></tr></thead><tbody>')
        for row in cameras[:20]:
            parts.append(
                '<tr><td class="name">%s</td>'
                '<td class="r num">%s</td>'
                '<td class="r hide-narrow"><span class="num">%s</span>%s</td>'
                '<td class="hide-narrow num sub">%s to %s</td>'
                '<td class="r num hide-narrow">%s</td></tr>'
                % (esc(row["name"]), num(row["count"]), pct(row["share"]),
                   meter(row["share"]), esc(pretty_date(row["first"])),
                   esc(pretty_date(row["last"])), num(row["days"])))
        parts.append("</tbody></table>")

        if len(cameras) > 1:
            parts.append('<h4>When each body was in your hands</h4>')
            parts.append(range_chart(cameras[:12],
                                     caption="camera bodies over time"))

    lenses = [row for row in gear["lenses"]
              if row["name"] != "Unrecorded lens"]
    parts.append('<h4>Lenses</h4>')
    if not lenses:
        parts.append('<p class="empty">No lens was named in any file. '
                     'Adapted and fully manual glass records nothing, which '
                     'is itself a finding.</p>')
    else:
        parts.append('<table><thead><tr><th>Lens</th><th class="r">Frames'
                     '</th><th class="r hide-narrow">Share</th>'
                     '<th class="hide-narrow">Focal used</th>'
                     '<th class="hide-narrow">In service</th></tr></thead>'
                     '<tbody>')
        for row in lenses[:24]:
            if row["focal_low"] and row["focal_high"]:
                if abs(row["focal_high"] - row["focal_low"]) < 0.6:
                    focal = "%smm" % trim(row["focal_low"])
                else:
                    focal = "%s-%smm" % (trim(row["focal_low"]),
                                         trim(row["focal_high"]))
            else:
                focal = "-"
            parts.append(
                '<tr><td class="name">%s</td>'
                '<td class="r num">%s</td>'
                '<td class="r hide-narrow"><span class="num">%s</span>%s</td>'
                '<td class="num hide-narrow">%s</td>'
                '<td class="num sub hide-narrow">%s to %s</td></tr>'
                % (esc(row["name"]), num(row["count"]), pct(row["share"]),
                   meter(row["share"]), esc(focal),
                   esc(pretty_date(row["first"])),
                   esc(pretty_date(row["last"]))))
        parts.append("</tbody></table>")

    unrecorded = [row for row in gear["lenses"]
                  if row["name"] == "Unrecorded lens"]
    if unrecorded:
        parts.append('<p class="note">%s name no lens at all. That is '
                     'usually adapted or manual glass, which reports nothing '
                     'to the body.</p>'
                     % esc(plural(unrecorded[0]["count"], "frame")))

    parts.append("</section>")
    return "".join(parts)


def _focal_section(report: dict) -> str:
    focal = report["focal"]
    parts = ['<section id="focal"><h2>Focal length</h2>']
    if not focal.get("available"):
        parts.append('<p class="empty">No focal length was recorded.</p>'
                     "</section>")
        return "".join(parts)

    median = focal["median"]
    band = focal.get("band")
    parts.append('<h3>The one number</h3>')
    parts.append('<p class="headline">Half your frames are at <em>%smm or '
                 'wider</em>.</p>' % esc(trim(median)))
    if band:
        if abs(band["high"] - band["low"]) < 0.6:
            band_text = ("Half of everything sits at a single focal length, "
                         "%smm." % trim(band["low"]))
        else:
            band_text = ("The tightest band holding half the library runs "
                         "%s to %smm, which is %s of your frames."
                         % (trim(band["low"]), trim(band["high"]),
                            pct(band["share"])))
        parts.append('<p class="lede">%s</p>' % esc(band_text))
    parts.append('<p class="note">Range used: %smm to %smm, across %s '
                 'distinct focal lengths. Bars are grouped to the nearest '
                 'standard focal length.</p>'
                 % (esc(trim(focal["min"])), esc(trim(focal["max"])),
                    esc(num(focal["distinct"]))))

    rows = focal["histogram"]
    step = max(1, len(rows) // 14)
    parts.append(bar_chart(rows, label_every=step,
                           caption="frames by focal length"))

    zooms = focal.get("zooms") or []
    if zooms:
        parts.append('<h4>Zooms: the range, and what you do with it</h4>')
        for zoom in zooms[:6]:
            parts.append('<div class="card">')
            parts.append('<h3 style="margin-top:0">%s</h3>' % esc(zoom["lens"]))
            parts.append('<p class="lede">%s. %s</p>'
                         % (esc(zoom["verdict"][0].upper()
                                + zoom["verdict"][1:]), esc(zoom["note"])))
            parts.append('<table><thead><tr><th>Part of the barrel</th>'
                         '<th class="r">Share</th><th></th></tr></thead>'
                         '<tbody>')
            for label, share in (
                    ("Wide end (%smm side)" % trim(zoom["low"]),
                     zoom["wide_end"]),
                    ("Middle", zoom["middle"]),
                    ("Long end (%smm side)" % trim(zoom["high"]),
                     zoom["long_end"])):
                parts.append('<tr><td>%s</td><td class="r num">%s</td>'
                             '<td style="width:44%%">%s</td></tr>'
                             % (esc(label), pct(share), meter(share)))
            parts.append("</tbody></table>")
            if zoom.get("unused_long"):
                gap = zoom["unused_long"]
                parts.append('<p class="note">Declared to %smm. The longest '
                             'frame you have ever taken with it is %smm.</p>'
                             % (esc(trim(gap["declared"])),
                                esc(trim(gap["reached"]))))
            if zoom.get("unused_wide"):
                gap = zoom["unused_wide"]
                parts.append('<p class="note">Declared from %smm. The widest '
                             'frame you have ever taken with it is %smm.</p>'
                             % (esc(trim(gap["declared"])),
                                esc(trim(gap["reached"]))))
            parts.append("</div>")
    parts.append("</section>")
    return "".join(parts)


def _exposure_section(report: dict) -> str:
    aperture = report["aperture"]
    shutter = report["shutter"]
    parts = ['<section id="exposure"><h2>Aperture and shutter</h2>']

    if aperture.get("available"):
        parts.append('<h3>Aperture</h3>')
        wide = aperture.get("wide_open")
        if wide:
            parts.append('<p class="headline"><em>%s</em> of your frames are '
                         'within a third of a stop of wide open.</p>'
                         % esc(pct(wide["share"])))
        rows = aperture["histogram"]
        step = max(1, len(rows) // 16)
        parts.append(bar_chart(rows, label_every=step,
                               caption="frames by aperture"))
        parts.append('<p class="note">Widest recorded f/%s, narrowest f/%s, '
                     'median f/%s.</p>'
                     % (esc(trim(aperture["widest"])),
                        esc(trim(aperture["narrowest"])),
                        esc(trim(aperture["median"]))))
    else:
        parts.append('<p class="empty">No aperture was recorded.</p>')

    if shutter.get("available"):
        parts.append('<h3>Shutter</h3>')
        rows = shutter["histogram"]
        parts.append(bar_chart(rows, label_every=max(1, len(rows) // 12),
                               caption="frames by shutter speed"))
        parts.append('<p class="note">Grouped into whole stops. Fastest %s, '
                     'slowest %s, median %s.</p>'
                     % (esc(shutter["fastest"]), esc(shutter["slowest"]),
                        esc(shutter["median"])))
        reciprocal = shutter.get("reciprocal")
        if reciprocal and reciprocal["known"] >= 20:
            parts.append('<p class="note">%s of frames used a shutter slower '
                         'than one over the focal length. Some of those were '
                         'on a tripod; the rest are the frames worth checking '
                         'at full size.</p>' % esc(pct(reciprocal["share"])))
    else:
        parts.append('<p class="empty">No shutter speed was recorded.</p>')

    habits = [h for h in report.get("habits", [])
              if h["kind"] in ("aperture", "shutter")]
    if habits:
        parts.append('<h4>What that adds up to</h4>')
        for habit in habits:
            parts.append('<div class="habit"><b>%s</b><p>%s</p></div>'
                         % (esc(habit["title"]), esc(habit["detail"])))
    parts.append("</section>")
    return "".join(parts)


def _iso_section(report: dict) -> str:
    iso = report["iso"]
    parts = ['<section id="iso"><h2>ISO</h2>']
    if not iso.get("available"):
        parts.append('<p class="empty">No ISO value was recorded.</p>'
                     "</section>")
        return "".join(parts)

    parts.append('<h3>Your real ceiling</h3>')
    parts.append('<p class="headline">Ninety nine percent of what you keep '
                 'is at <em>ISO %s or below</em>.</p>' % esc(num(iso["ceiling"])))
    if iso["above_ceiling"]:
        tail = ('Only %s out of %s sit above it.'
                % (esc(plural(iso["above_ceiling"], "frame")),
                   esc(num(iso["count"]))))
    elif iso["ceiling"] == iso["max"]:
        tail = ('Nothing in the library goes above it: the ceiling and the '
                'highest frame you kept are the same number.')
    else:
        tail = 'Nothing in the library goes above it.'
    parts.append('<p class="lede">The camera goes higher. The pictures you '
                 'kept say this is where you actually stop. %s</p>' % tail)
    rows = iso["histogram"]
    parts.append(bar_chart(rows, label_every=max(1, len(rows) // 12),
                           caption="frames by ISO"))
    parts.append('<p class="note">Base ISO %s, median %s, highest single '
                 'frame ISO %s.</p>'
                 % (esc(num(iso["base"])), esc(num(iso["median"])),
                    esc(num(iso["max"]))))
    for habit in report.get("habits", []):
        if habit["kind"] == "iso":
            parts.append('<div class="habit"><b>%s</b><p>%s</p></div>'
                         % (esc(habit["title"]), esc(habit["detail"])))
    parts.append("</section>")
    return "".join(parts)


def _time_section(report: dict) -> str:
    time_of_day = report["time_of_day"]
    parts = ['<section id="time"><h2>Time of day</h2>',
             '<h3>When you press the shutter</h3>']

    hours = time_of_day["hours"]
    rows = [{"label": "%02d" % row["hour"], "count": row["count"]}
            for row in hours]
    twilight = time_of_day.get("twilight")
    bands = []
    markers = []
    if twilight:
        bands.append({"start": 0, "end": twilight["dawn_earliest"],
                      "kind": ""})
        bands.append({"start": twilight["dawn_earliest"],
                      "end": twilight["dawn_latest"], "kind": "twilight"})
        bands.append({"start": twilight["dusk_earliest"],
                      "end": twilight["dusk_latest"], "kind": "twilight"})
        bands.append({"start": twilight["dusk_latest"], "end": 24,
                      "kind": ""})
        markers.append({"at": twilight["dawn_mean"], "label": "CIVIL DAWN",
                        "anchor": "start"})
        markers.append({"at": twilight["dusk_mean"], "label": "CIVIL DUSK",
                        "anchor": "end"})
    parts.append(bar_chart(rows, label_every=2, bands=bands, markers=markers,
                           caption="frames by hour of day"))

    if twilight:
        parts.append('<p class="note">Shaded blocks are night. The lighter '
                     'bands are how far civil dawn and dusk move across a '
                     'year at %s, the coordinate most of your GPS tagged '
                     'frames cluster around. Local time taken as UTC%s%02d:%02d, '
                     '%s.</p>'
                     % (esc("%.1f, %.1f" % (twilight["latitude"],
                                            twilight["longitude"])),
                        "+" if twilight["tz_minutes"] >= 0 else "-",
                        abs(twilight["tz_minutes"]) // 60,
                        abs(twilight["tz_minutes"]) % 60,
                        esc(twilight["basis"])))
        parts.append('<p class="lede">%s of your frames fall in the hour and '
                     'a half either side of civil twilight, the light people '
                     'set alarms for.</p>' % esc(pct(twilight["golden_share"])))
    else:
        missing = time_of_day.get("twilight_missing")
        if missing == "omitted":
            why = ('Location was left out of this report, so twilight '
                   'boundaries cannot be drawn.')
        elif missing == "undefined":
            latitude = time_of_day.get("twilight_latitude")
            where = ((" at latitude %s" % trim(latitude))
                     if latitude is not None else "")
            why = ('Civil twilight does not resolve%s: for part of the year '
                   'the sun there never sits six degrees below the horizon, '
                   'so there is no dawn or dusk line to draw.' % where)
        else:
            why = ('No GPS coordinate was available, so twilight boundaries '
                   'cannot be drawn.')
        parts.append('<p class="note">%s Times are the camera clock as '
                     'written.</p>' % why)

    parts.append('<div class="grid2">')
    parts.append('<div><h4>By weekday</h4>%s</div>'
                 % bar_chart([{"label": row["day"], "count": row["count"]}
                              for row in time_of_day["weekdays"]],
                             height=170, value_labels=True,
                             caption="frames by weekday"))
    parts.append('<div><h4>By month</h4>%s</div>'
                 % bar_chart([{"label": row["month"], "count": row["count"]}
                              for row in time_of_day["months"]],
                             height=170, value_labels=True,
                             caption="frames by month"))
    parts.append("</div>")

    for habit in report.get("habits", []):
        if habit["kind"] in ("time", "light"):
            parts.append('<div class="habit"><b>%s</b><p>%s</p></div>'
                         % (esc(habit["title"]), esc(habit["detail"])))
    parts.append("</section>")
    return "".join(parts)


def _calendar_section(report: dict) -> str:
    calendar = report["calendar"]
    parts = ['<section id="calendar"><h2>Calendar</h2>',
             '<h3>Days you went out</h3>']
    if not calendar["years"]:
        parts.append('<p class="empty">No dated frames.</p></section>')
        return "".join(parts)
    busiest = calendar.get("busiest_day")
    parts.append('<p class="lede">%s with at least one frame. The '
                 'busiest was %s with %s.</p>'
                 % (esc(plural(calendar["days_shot"], "day")),
                    esc(pretty_date(busiest["date"])) if busiest else "-",
                    esc(num(busiest["count"])) if busiest else "-"))
    parts.append('<div class="scroll">%s</div>'
                 % calendar_heatmap(calendar["years"]))
    parts.append('<div class="legend"><span><i style="opacity:.24"></i>'
                 'quiet</span><span><i style="opacity:.45"></i></span>'
                 '<span><i style="opacity:.68"></i></span>'
                 '<span><i style="opacity:.92"></i>your busiest days</span>'
                 '</div>')
    parts.append('<p class="note">Shading is relative to the busiest day of '
                 'that year, so a heavy year and a light year are each read '
                 'on their own terms.</p>')
    parts.append("</section>")
    return "".join(parts)


def _location_section(report: dict) -> str:
    locations = report["locations"]
    parts = ['<section id="locations"><h2>Locations</h2>']

    if locations["mode"] == "off":
        parts.append('<div class="privacy"><b>Location omitted</b><p>%s</p>'
                     "</div></section>" % esc(locations["note"]))
        return "".join(parts)

    if not locations["clusters"]:
        parts.append('<p class="empty">%s</p></section>'
                     % esc(locations["note"]))
        return "".join(parts)

    parts.append('<h3>Where the frames were taken</h3>')
    parts.append('<p class="lede">%s of %s frames carry a coordinate, '
                 'falling into %s once anything within %s kilometres '
                 'is treated as the same place.</p>'
                 % (esc(num(locations["photos"])),
                    esc(num(report["scan"]["photos"])),
                    esc(plural(locations["total_clusters"], "place")),
                    esc(num(locations["cluster_km"]))))

    warn = locations["mode"] == "precise"
    parts.append('<div class="privacy%s"><b>%s</b><p>%s</p></div>'
                 % (" warn" if warn else "",
                    "Precise coordinates are in this file"
                    if warn else "Coordinates are rounded",
                    esc(locations["note"])))

    parts.append('<table><thead><tr><th>Place</th><th class="r">Frames</th>'
                 '<th class="r hide-narrow">Share</th>'
                 '<th class="r hide-narrow">Days</th>'
                 '<th class="hide-narrow">First to last</th></tr></thead>'
                 '<tbody>')
    for cluster in locations["clusters"][:25]:
        parts.append('<tr><td class="num name">%s</td>'
                     '<td class="r num">%s</td>'
                     '<td class="r hide-narrow"><span class="num">%s</span>%s'
                     '</td><td class="r num hide-narrow">%s</td>'
                     '<td class="num sub hide-narrow">%s to %s</td></tr>'
                     % (esc(cluster["label"]), num(cluster["count"]),
                        pct(cluster["share"]), meter(cluster["share"]),
                        num(cluster["days"]),
                        esc(pretty_date(cluster["first"])),
                        esc(pretty_date(cluster["last"]))))
    parts.append("</tbody></table>")
    parts.append("</section>")
    return "".join(parts)


def _absence_section(report: dict) -> str:
    absences = report["absences"]
    parts = ['<section id="absences"><h2>You never shoot this</h2>',
             '<h3>The gaps</h3>',
             '<p class="lede">Presence is easy to see in a catalogue. '
             'Absence is not, and absence is usually the more interesting '
             'finding. These are the settings sitting inside the range your '
             'gear can reach that almost nothing in the library uses.</p>']

    if not absences["enough_data"]:
        parts.append('<p class="note">Fewer frames than this section needs '
                     'to say anything honest. Treat what follows as noise.'
                     '</p>')

    focals = absences["focal_lengths"]
    parts.append('<h4>Focal lengths inside your range</h4>')
    if focals:
        parts.append('<div class="absent">')
        for entry in focals:
            hard = " hard" if entry["count"] == 0 else ""
            label = "%dmm" % entry["value"]
            if entry["count"]:
                label += " (%d)" % entry["count"]
            parts.append('<span class="%s">%s</span>' % (hard.strip(),
                                                         esc(label)))
        parts.append("</div>")
        if any(entry["count"] == 0 for entry in focals):
            parts.append('<p class="note">Marked in colour where the count '
                         'is exactly zero.</p>')
        else:
            parts.append('<p class="note">The count for each is in '
                         'brackets. None is quite empty, but none is '
                         'reached for either.</p>')
    else:
        parts.append('<p class="note">Nothing. Every standard focal length '
                     'inside your range gets used.</p>')

    stops = absences["apertures"]
    parts.append('<h4>Apertures inside your range</h4>')
    if stops:
        parts.append('<div class="absent">')
        for entry in stops:
            hard = "hard" if entry["count"] == 0 else ""
            label = "f/%s" % trim(entry["value"])
            if entry["count"]:
                label += " (%d)" % entry["count"]
            parts.append('<span class="%s">%s</span>' % (hard, esc(label)))
        parts.append("</div>")
    else:
        parts.append('<p class="note">Nothing. You use the whole scale.</p>')

    hours = absences["hours"]
    parts.append('<h4>Hours you are never out</h4>')
    if hours:
        parts.append('<div class="absent">')
        for entry in hours:
            if entry["start"] == entry["end"]:
                label = "%02d:00" % entry["start"]
            else:
                label = "%02d:00 to %02d:59" % (entry["start"], entry["end"])
            parts.append('<span class="hard">%s</span>' % esc(label))
        parts.append("</div>")
        parts.append('<p class="note">%s of the 24 hours in a day hold '
                     'almost nothing.</p>'
                     % esc(num(len(absences["quiet_hours"]))))
    else:
        parts.append('<p class="note">You have shot in every hour of the '
                     'day.</p>')

    if absences["weekdays"]:
        parts.append('<h4>Days you never shoot</h4><div class="absent">')
        for day in absences["weekdays"]:
            parts.append('<span class="hard">%s</span>' % esc(day))
        parts.append("</div>")

    unreached = absences["unreached_focal_range"]
    if unreached:
        parts.append('<h4>Focal range you own and have not used</h4>')
        parts.append("<ul>")
        for entry in unreached[:8]:
            parts.append('<li>%s goes to %smm. You have never gone past '
                         '%smm.</li>' % (esc(entry["lens"]),
                                         esc(trim(entry["declared"])),
                                         esc(trim(entry["reached"]))))
        parts.append("</ul>")

    parts.append("</section>")
    return "".join(parts)


def _habits_section(report: dict) -> str:
    habits = [h for h in report.get("habits", [])
              if h["kind"] in ("gear",)]
    if not habits:
        return ""
    parts = ['<section id="habits"><h2>Habits</h2>']
    for habit in habits:
        parts.append('<div class="habit"><b>%s</b><p>%s</p></div>'
                     % (esc(habit["title"]), esc(habit["detail"])))
    parts.append("</section>")
    return "".join(parts)


def _footer(report: dict) -> str:
    scan = report["scan"]
    meta = report.get("meta", {})
    parts = ['<footer>']

    coverage = []
    coverage.append("%s were looked at"
                    % plural(scan["files_seen"], "file"))
    coverage.append("%s carried usable EXIF" % num(scan["photos"]))
    if scan["filtered_out"]:
        coverage.append("%s were excluded by your filters"
                        % num(scan["filtered_out"]))
    if scan["no_exif"]:
        coverage.append("%s had no EXIF at all" % num(scan["no_exif"]))
    if scan["unreadable"]:
        coverage.append("%s could not be opened" % num(scan["unreadable"]))
    parts.append("<p>%s.</p>" % esc(", ".join(coverage)))

    if scan["unsupported"]:
        formats = ", ".join(
            "%s (%s)" % (name.upper(), num(count))
            for name, count in scan["unsupported_formats"].items())
        parts.append('<p><b>%s skipped because their container is '
                     'not implemented:</b> %s. These formats keep their '
                     'metadata in private layouts. They are counted here '
                     'rather than dropped, so the totals above are honest '
                     'about what they leave out.</p>'
                     % (esc(plural(scan["unsupported"], "file was",
                                   "files were")), esc(formats)))

    if scan["missing_timestamp"]:
        parts.append('<p>%s carried EXIF but no timestamp, so they '
                     'appear in the gear and settings numbers and not in the '
                     'calendar.</p>'
                     % esc(plural(scan["missing_timestamp"], "frame")))

    read = scan.get("bytes_read", 0)
    if read:
        parts.append('<p>Only header bytes were read: %s in total across the '
                     'whole library, an average of %s bytes per file. Image '
                     'data was never decoded.</p>'
                     % (esc(_bytes(read)),
                        esc(num(int(read / max(scan["photos"], 1))))))

    generated = meta.get("generated", "")
    version = meta.get("version", "")
    elapsed = meta.get("elapsed_seconds")
    line = "Generated by exif-atlas"
    if version:
        line += " %s" % version
    if generated:
        line += " on %s" % generated
    if elapsed:
        line += " in %.1f seconds" % elapsed
    parts.append("<p>%s. This file is self contained: no scripts, no fonts "
                 "to fetch, no requests of any kind.</p>" % esc(line))
    parts.append("</footer>")
    return "".join(parts)


def _bytes(count: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            if unit == "bytes":
                return "%d bytes" % count
            return "%.1f %s" % (count, unit)
        count /= 1024.0
    return "%d bytes" % count  # pragma: no cover


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def render_html(report: dict) -> str:
    """Build the whole document."""
    title = "exif-atlas"
    root = report.get("meta", {}).get("root")
    if root:
        title = "exif-atlas: %s" % root

    body = [
        _masthead(report),
        _habits_section(report),
        _gear_section(report),
        _focal_section(report),
        _exposure_section(report),
        _iso_section(report),
        _time_section(report),
        _calendar_section(report),
        _location_section(report),
        _absence_section(report),
        _footer(report),
    ]

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="generator" content="exif-atlas %s">\n'
        '<meta name="referrer" content="no-referrer">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n"
        '<body>\n<main class="atlas">\n%s\n</main>\n</body>\n</html>\n'
        % (esc(report.get("meta", {}).get("version", "")), esc(title),
           STYLE, "\n".join(part for part in body if part))
    )


def render_text(report: dict) -> str:
    """A short terminal summary, printed after a scan."""
    scan = report["scan"]
    span = report["span"]
    lines = []
    lines.append("  frames read      %s" % num(scan["photos"]))
    if span.get("first"):
        lines.append("  date span        %s to %s"
                     % (pretty_date(span["first"]), pretty_date(span["last"])))
        lines.append("  days shot        %s" % num(span["active_days"]))
    cameras = report["gear"]["cameras"]
    if cameras:
        lines.append("  bodies           %s" % num(len(cameras)))
        lines.append("  most used        %s (%s)"
                     % (cameras[0]["name"],
                        plural(cameras[0]["count"], "frame")))
    focal = report["focal"]
    if focal.get("available"):
        lines.append("  median focal     %smm" % trim(focal["median"]))
    aperture = report["aperture"]
    if aperture.get("available") and aperture.get("wide_open"):
        lines.append("  wide open        %s of frames"
                     % pct(aperture["wide_open"]["share"]))
    iso = report["iso"]
    if iso.get("available"):
        lines.append("  iso ceiling      %s (99th percentile)"
                     % num(iso["ceiling"]))
    locations = report["locations"]
    if locations["mode"] != "off" and locations["clusters"]:
        lines.append("  places           %s, %s tagged"
                     % (plural(locations["total_clusters"], "cluster"),
                        plural(locations["photos"], "frame")))
    if scan["unsupported"]:
        lines.append("  not parsed       %s (%s)"
                     % (plural(scan["unsupported"], "file"),
                        ", ".join(scan["unsupported_formats"])))
    if scan["no_exif"]:
        lines.append("  no exif          %s"
                     % plural(scan["no_exif"], "file"))
    return "\n".join(lines)
