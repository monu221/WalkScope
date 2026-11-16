"""Area report (build step 10): one Report, rendered as Markdown or PDF.

build_report collects everything from a scored network for one persona (and
any what-if edits): area figures by the "Area summaries" rule (length-weighted,
roads only, footways and paths separately), the indicator breakdown, the
weakest streets, data completeness with the persona caution, the scenario
before and after, and map images. to_markdown and to_pdf render the same
Report, so the two downloads always agree.

The map image is drawn with Pillow from the segment geometries (streets only,
no basemap), so making a report needs no browser and no tile downloads.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Sequence

import pandas as pd
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

from walkscope.completeness import Completeness, caution_message, completeness
from walkscope.config import INDICATORS, Persona, load_indicator_params
from walkscope.mapview import colour_range, score_colormap
from walkscope.network import Network
from walkscope.scenario import Edit, apply_edits, changed_streets, to_network_crs
from walkscope.scoring import (INDICATOR_LABELS, STREET_DESIGN, apply_persona, area_summary, length_weighted_mean,
                               pedestrian_mask, weakest, weakest_indicator)

OSM_CREDIT = ("Map data © OpenStreetMap contributors, available under the Open Database License (ODbL): "
              "https://www.openstreetmap.org/copyright")
WEAKEST_ROWS = 10
WEAKEST_DESIGN_ROWS = 5
CHANGED_ROWS = 10


# --- Data -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ScenarioResult:
    items: list[str]                 # what was added, in order
    before: float                    # walkability score, road average (length-weighted)
    after: float
    improved_segments: int
    improved_km: float
    streets: pd.DataFrame            # Street, Before, After, Change
    map_png: bytes


@dataclass(frozen=True)
class Report:
    place: str
    persona: Persona
    created: str
    area_km2: float
    network_km: float
    road_mean: float                 # walkability score, roads, length-weighted
    pedestrian_share: float          # footways and paths, share of network length
    pedestrian_mean: float
    indicators: pd.DataFrame         # Indicator, Road average, Weight (%)
    weakest: pd.DataFrame            # Street, Score, Weakest indicator, Road type
    weakest_design: pd.DataFrame     # same, ranked by the street design score
    completeness: Completeness
    speed_tagged: float
    caution: str | None
    map_png: bytes
    colour_note: str
    scenario: ScenarioResult | None = None
    notes: list[str] = field(default_factory=list)


def _weakest_table(seg: pd.DataFrame, column: str, columns: Sequence[str], n: int) -> pd.DataFrame:
    table = weakest(seg, column, n=n)
    rows = seg.loc[table["segment_id"]]
    return pd.DataFrame({
        "Street": table["street"],
        "Score": table[column].round().astype(int),
        "Weakest indicator": weakest_indicator(rows, columns).to_numpy(),
        "Road type": table["type"],
    })


def build_report(net: Network, place: str, persona: Persona, edits: Iterable[Edit] = (),
                 relative: bool = True, params: dict[str, Any] | None = None,
                 today: date | None = None) -> Report:
    """Everything the report shows, for one persona, with `edits` as the what-if scenario."""
    params = params or load_indicator_params()
    pedestrian_only = params["pedestrian_only_highways"]
    seg = apply_persona(net.segments, persona)
    scored = seg[seg["scored"]]
    summary = area_summary(seg, "score", pedestrian_only)
    roads = scored[~pedestrian_mask(scored, pedestrian_only)]

    indicators = pd.DataFrame({
        "Indicator": [INDICATOR_LABELS[i] for i in INDICATORS],
        "Road average": [round(area_summary(seg, i, pedestrian_only).road_mean) for i in INDICATORS],
        "Weight (%)": [round(persona.weights[i]) for i in INDICATORS],
    })
    data = completeness(seg, pedestrian_only)
    vmin, vmax = colour_range(scored["score"], relative)
    colour_note = (f"Colours relative to this area: red at {vmin:.0f} or below, blue at {vmax:.0f} or above "
                   "(5th to 95th percentile of the area's scores)." if relative
                   else "Colours on the absolute scale: red at 0, blue at 100.")

    edits = list(edits)
    scenario = None
    if edits:
        after_raw, changed = apply_edits(net, edits, params)
        after = apply_persona(after_raw, persona)
        ids = changed.intersection(scored.index)
        improved = ids[(after.loc[ids, "score"] - seg.loc[ids, "score"]) > 0.5]
        markers = [(to_network_crs(net, e.lon, e.lat), e.kind) for e in edits]
        scenario = ScenarioResult(
            items=[e.label() for e in edits],
            before=summary.road_mean,
            after=area_summary(after, "score", pedestrian_only).road_mean,
            improved_segments=len(improved),
            improved_km=float(seg.loc[improved, "length"].sum() / 1000),
            streets=changed_streets(seg, after, changed, "score", n=CHANGED_ROWS),
            map_png=map_image(after, "score", net.area, vmin, vmax, markers=markers),
        )

    return Report(
        place=place,
        persona=persona,
        created=(today or date.today()).isoformat(),
        area_km2=float(net.area.area / 1e6),
        network_km=summary.road_km + summary.pedestrian_km,
        road_mean=summary.road_mean,
        pedestrian_share=summary.pedestrian_share,
        pedestrian_mean=summary.pedestrian_mean,
        indicators=indicators,
        weakest=_weakest_table(seg, "score", tuple(INDICATOR_LABELS), WEAKEST_ROWS),
        weakest_design=_weakest_table(seg, "street_design", STREET_DESIGN, WEAKEST_DESIGN_ROWS),
        completeness=data,
        speed_tagged=length_weighted_mean(roads["speed_tagged_share"], roads["length"]),
        caution=caution_message(data, persona, params["completeness"]["caution_below"]),
        map_png=map_image(seg, "score", net.area, vmin, vmax),
        colour_note=colour_note,
        scenario=scenario,
    )


# --- Map image ------------------------------------------------------------------------

MARKER_FILL = {"crossing": (255, 255, 255), "amenity": (123, 50, 148)}


def _lines(geom: BaseGeometry) -> list[LineString]:
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    return []


def _rings(geom: BaseGeometry) -> list:
    polys = [geom] if isinstance(geom, Polygon) else list(geom.geoms) if isinstance(geom, MultiPolygon) else []
    return [p.exterior for p in polys]


def map_image(segments: pd.DataFrame, column: str, area: BaseGeometry, vmin: float, vmax: float,
              markers: Sequence[tuple[BaseGeometry, str]] = (), width_px: int = 1200,
              max_height_px: int = 1400) -> bytes:
    """PNG of the scored segments coloured by `column` (red at vmin, blue at vmax), with the study-area
    outline, added what-if markers, a colour bar, a scale bar and the OSM credit. Coordinates are the
    network's metric CRS, so the picture is to scale."""
    pad, legend_h = 30, 90
    minx, miny, maxx, maxy = area.bounds
    for point, _ in markers:                       # an item added just outside the area stays in view
        minx, miny = min(minx, point.x - 30), min(miny, point.y - 30)
        maxx, maxy = max(maxx, point.x + 30), max(maxy, point.y + 30)
    span_x, span_y = max(maxx - minx, 1.0), max(maxy - miny, 1.0)
    scale = min((width_px - 2 * pad) / span_x, (max_height_px - 2 * pad - legend_h) / span_y)
    map_w, map_h = int(span_x * scale) + 2 * pad, int(span_y * scale) + 2 * pad
    w, h = max(map_w, 600), map_h + legend_h
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    x_off = (w - map_w) // 2

    def xy(x: float, y: float) -> tuple[float, float]:
        return x_off + pad + (x - minx) * scale, pad + (maxy - y) * scale

    for ring in _rings(area):
        draw.line([xy(*c) for c in ring.coords], fill=(150, 150, 150), width=2)

    cmap = score_colormap("", vmin, vmax)
    seg = segments[segments["scored"]].sort_values(column, ascending=False)   # weakest drawn on top
    pedestrian = seg["highway"].astype(str).str.contains("footway|path|pedestrian|steps|cycleway")
    for (_, row), ped in zip(seg.iterrows(), pedestrian):
        colour = tuple(int(c) for c in cmap.rgb_bytes_tuple(min(max(row[column], vmin), vmax)))
        for line in _lines(row.geometry):
            draw.line([xy(*c) for c in line.coords], fill=colour, width=2 if ped else 4, joint="curve")

    for point, kind in markers:
        cx, cy = xy(point.x, point.y)
        draw.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=MARKER_FILL[kind], outline=(17, 17, 17), width=3)

    font = ImageFont.load_default(size=16)
    small = ImageFont.load_default(size=13)
    # Colour bar
    bar_x, bar_y, bar_w, bar_h = pad, map_h + 18, 320, 14
    for i in range(bar_w):
        value = vmin + (vmax - vmin) * i / (bar_w - 1)
        draw.line([(bar_x + i, bar_y), (bar_x + i, bar_y + bar_h)],
                  fill=tuple(int(c) for c in cmap.rgb_bytes_tuple(value)))
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=(120, 120, 120))
    for frac in (0, 0.5, 1):
        value = vmin + (vmax - vmin) * frac
        draw.text((bar_x + frac * bar_w, bar_y + bar_h + 4), f"{value:.0f}", fill=(60, 60, 60), font=font,
                  anchor="ma")
    # Scale bar: a round length about a fifth of the map width
    target = span_x / 5
    length_m = next(v for v in (2000, 1000, 500, 200, 100, 50) if v <= target) if target >= 50 else 50
    sx = w - pad - length_m * scale
    draw.line([(sx, bar_y + 7), (w - pad, bar_y + 7)], fill=(40, 40, 40), width=3)
    draw.text(((sx + w - pad) / 2, bar_y + bar_h + 4), f"{length_m} m", fill=(60, 60, 60), font=font, anchor="ma")
    draw.text((pad, h - 8), "© OpenStreetMap contributors", fill=(110, 110, 110), font=small, anchor="ld")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# --- Markdown -------------------------------------------------------------------------

def _pct(value: float) -> str:
    return "–" if pd.isna(value) else f"{value:.0%}"


def _num(value: float, fmt: str = ".0f") -> str:
    return "–" if pd.isna(value) else format(value, fmt)


def rounded_change(before: float, after: float, digits: int = 1) -> str:
    """Signed difference of the two values as shown (rounded), so 78.0 -> 78.2 reads +0.2."""
    if pd.isna(before) or pd.isna(after):
        return "–"
    return f"{round(after, digits) - round(before, digits):+.{digits}f}"


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_None._"
    head = "| " + " | ".join(df.columns) + " |"
    rule = "| " + " | ".join("---:" if pd.api.types.is_numeric_dtype(df[c]) else "---" for c in df.columns) + " |"
    rows = ["| " + " | ".join(str(v).replace("|", "/") for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, rule, *rows])


def _completeness_rows(r: Report) -> pd.DataFrame:
    c = r.completeness
    return pd.DataFrame({
        "Data": ["Overall (mean of the next three)", "Sidewalk known (roads)", "Kerb data within 50 m",
                 "Lighting tagged", "Surface tagged", "Speed limit tagged (roads)"],
        "Share of network length": [_pct(c.mean), _pct(c.sidewalk), _pct(c.kerb), _pct(c.lit), _pct(c.surface),
                                    _pct(r.speed_tagged)],
    })


def _weights_line(persona: Persona) -> str:
    return ", ".join(f"{INDICATOR_LABELS[i].lower()} {persona.weights[i]:g} %" for i in INDICATORS)


def sections(r: Report) -> list[tuple[str, list]]:
    """The report as (heading, blocks); a block is ("p", markdown text), ("table", DataFrame),
    ("image", png bytes, caption) or ("list", [items]). Shared by to_markdown and to_pdf."""
    out: list[tuple[str, list]] = []
    out.append(("", [
        ("p", f"**Persona:** {r.persona.label} (weights: {_weights_line(r.persona)}).  \n"
              f"**Area:** {r.area_km2:.2f} km², {r.network_km:.1f} km of scored streets, footways and paths.  \n"
              f"**Created:** {r.created} with WalkScope."),
    ]))
    summary = [
        ("p", f"**Walkability score, roads: {_num(r.road_mean)}** of 100 (average over roads, weighted by "
              f"length). Footways and paths are {_pct(r.pedestrian_share)} of the network length and average "
              f"{_num(r.pedestrian_mean)}; they are reported separately because they score 100 on traffic "
              "stress and infrastructure by definition."),
    ]
    if r.caution:
        summary.append(("p", f"⚠ {r.caution}"))
    out.append(("Summary", summary))
    out.append(("Map", [("image", r.map_png, f"Walkability score for {r.persona.label.lower()}. "
                                             f"{r.colour_note} Thin lines: footways and paths.")]))
    out.append(("Indicators", [
        ("p", "Road averages of the five indicators (0 = worst, 100 = best), weighted by length, and their "
              "weight in this persona's walkability score."),
        ("table", r.indicators),
    ]))
    out.append(("Weakest streets", [
        ("p", "Lowest walkability score, one row per named street (its weakest segment), with the indicator "
              "that pulls it down most."),
        ("table", r.weakest),
        ("p", "**Weak because of the street itself** (traffic stress and pedestrian infrastructure only, "
              "which street design can change):"),
        ("table", r.weakest_design),
    ]))
    completeness_blocks = [
        ("p", "Share of the scored network length where OpenStreetMap has the data. Where it is missing, "
              "scores use stated assumptions (50 = unknown), so a low score may reflect missing data rather "
              "than a bad street."),
        ("table", _completeness_rows(r)),
    ]
    if r.caution:
        completeness_blocks.append(("p", f"⚠ {r.caution}"))
    out.append(("Data completeness", completeness_blocks))
    if r.scenario:
        s = r.scenario
        out.append(("What-if scenario", [
            ("list", s.items),
            ("p", f"**Walkability score, roads: {_num(s.before, '.1f')} before, {_num(s.after, '.1f')} after** "
                  f"({rounded_change(s.before, s.after)}). {s.improved_segments} segments improved "
                  f"({s.improved_km:.1f} km)."),
            ("table", s.streets.assign(Change=s.streets["Change"].map(lambda v: f"{v:+d}"))),
            ("image", s.map_png, "After the scenario, same colours as the map above. "
                                 "Added items: white circles for crossings, purple for amenities."),
        ]))
    out.append(("Method and sources", [
        ("p", "Scores are calculated per street segment from OpenStreetMap tags and walking distances along "
              "the network; the method is documented in WalkScope's docs/method.md. An added crossing counts "
              "as a marked crossing for roads within 50 m; an added amenity counts for streets within an "
              "800 m walk."),
        ("p", OSM_CREDIT),
    ]))
    return out


def to_markdown(r: Report) -> str:
    """The report as Markdown, with the map images embedded as data URIs (one self-contained file)."""
    lines = [f"# WalkScope report: {r.place}", ""]
    for heading, blocks in sections(r):
        if heading:
            lines += [f"## {heading}", ""]
        for block in blocks:
            kind = block[0]
            if kind == "p":
                lines += [block[1], ""]
            elif kind == "table":
                lines += [_md_table(block[1]), ""]
            elif kind == "list":
                lines += [f"- {item}" for item in block[1]] + [""]
            elif kind == "image":
                data = base64.b64encode(block[1]).decode()
                lines += [f"![{block[2]}](data:image/png;base64,{data})", "", f"_{block[2]}_", ""]
    return "\n".join(lines).rstrip() + "\n"


# --- PDF ------------------------------------------------------------------------------

# The PDF uses the built-in Helvetica font, which covers Latin-1 (umlauts, ß, ², ©) only.
PDF_REPLACE = {"–": "-", "—": "-", "≤": "<=", "≥": ">=", "…": "...", "⚠ ": "Caution: ", "⚠": "Caution:",
               "’": "'", "‘": "'", "“": '"', "”": '"', "→": "->", "·": "-"}


def pdf_text(text: str) -> str:
    for a, b in PDF_REPLACE.items():
        text = text.replace(a, b)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _column_width(df: pd.DataFrame, column: str) -> float:
    """Relative PDF column width: the longest text in the column or its heading, at least 6 characters."""
    longest = max([len(str(column))] + [len(str(v)) for v in df[column]])
    return float(max(6, min(longest, 40)))


def to_pdf(r: Report) -> bytes:
    """The report as an A4 PDF (same content as to_markdown)."""
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    width = pdf.w - pdf.l_margin - pdf.r_margin

    pdf.set_font("Helvetica", "B", 18)
    pdf.multi_cell(width, 9, pdf_text(f"WalkScope report: {r.place}"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for heading, blocks in sections(r):
        if heading:
            if pdf.get_y() > pdf.h - 50:
                pdf.add_page()
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(width, 8, pdf_text(heading), new_x="LMARGIN", new_y="NEXT")
        for block in blocks:
            kind = block[0]
            pdf.set_font("Helvetica", "", 10)
            if kind == "p":
                pdf.multi_cell(width, 5, pdf_text(block[1].replace("  \n", "\n")), markdown=True,
                               new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
            elif kind == "list":
                for item in block[1]:
                    pdf.multi_cell(width, 5, pdf_text(f"- {item}"), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1)
            elif kind == "table":
                df = block[1]
                if df.empty:
                    pdf.multi_cell(width, 5, "None.", new_x="LMARGIN", new_y="NEXT")
                    continue
                pdf.set_font("Helvetica", "", 9)
                widths = tuple(_column_width(df, c) for c in df.columns)
                with pdf.table(text_align="LEFT", line_height=5, first_row_as_headings=True,
                               col_widths=widths) as table:
                    table.row([pdf_text(str(c)) for c in df.columns])
                    for row in df.itertuples(index=False):
                        table.row([pdf_text(str(v)) for v in row])
                pdf.ln(2)
            elif kind == "image":
                img = Image.open(io.BytesIO(block[1]))
                img_h = width * img.height / img.width
                max_h = pdf.h - pdf.t_margin - pdf.b_margin - 20
                w = width if img_h <= max_h else width * max_h / img_h
                if pdf.get_y() + min(img_h, max_h) + 12 > pdf.h - pdf.b_margin:
                    pdf.add_page()
                pdf.image(io.BytesIO(block[1]), x=pdf.l_margin + (width - w) / 2, w=w)
                pdf.set_font("Helvetica", "I", 8)
                pdf.multi_cell(width, 4, pdf_text(block[2]), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(2)
    return bytes(pdf.output())
