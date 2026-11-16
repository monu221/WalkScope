"""WalkScope Streamlit app. Start with:  streamlit run app/streamlit_app.py

Layout: inputs in the sidebar; in the main area a summary row, the map at
full width with its legend, the weakest-streets table and the scoring notes.
Each part is one render_* function. To add something the map can show, add
a View to VIEWS. The selected persona is applied once per run
(scoring.apply_persona) and everything below works on that copy.
"""

from __future__ import annotations

import logging
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from walkscope import pipeline
from walkscope.completeness import completeness
from walkscope.config import Persona, load_indicator_params, load_personas
from walkscope.fetch import AreaError, ServerBusyError
from walkscope.mapview import legend_html, make_map
from walkscope.network import Network
from walkscope.scoring import apply_persona, area_summary, display_name, length_weighted_mean, weakest
from walkscope.tags import is_pedestrian_only

log = logging.getLogger(__name__)

EXAMPLES = {
    "St. Pauli": "St. Pauli, Hamburg",
    "Sternschanze": "Sternschanze, Hamburg",
    "Burtscheid (Aachen)": "Burtscheid, Aachen",
}
MAP_HEIGHT = 600
WEAKEST_ROWS = 15
CACHED_PLACES = 5
BUSY_HINT = "Wait a minute, then press **Score area** again. Places you have scored before open instantly."
FIRST_RUN_NOTE = ("The first run for a place can take a few minutes while map data downloads. "
                  "After that it opens instantly.")


@st.cache_resource(show_spinner=False)
def _params() -> dict:
    return load_indicator_params()


# --- What the map can show ----------------------------------------------------
# Each function turns segment columns into short text for the click popup or
# the weakest-streets table.

def _speed_kmh(seg: pd.DataFrame) -> pd.Series:
    """'30 km/h', or '30–50 km/h' for a segment merged from ways with different limits."""
    lo = seg["speed_min_kmh"].round().astype(int).astype(str)
    hi = seg["speed_max_kmh"].round().astype(int).astype(str)
    return (lo + "–" + hi).where(lo != hi, lo) + " km/h"


def _source(seg: pd.DataFrame) -> pd.Series:
    share = seg["speed_tagged_share"]
    return pd.Series("partly tagged", index=seg.index).mask(share >= 0.999, "tagged").mask(share <= 0.001, "assumed")


def speed_popup(seg: pd.DataFrame) -> pd.Series:
    """Speed limit used for traffic stress, and whether it was tagged or assumed."""
    note = _source(seg).map({
        "tagged": "tagged in OSM",
        "assumed": "assumed (no speed limit tagged; default for the road type)",
        "partly tagged": "partly tagged in OSM, rest assumed",
    })
    return (_speed_kmh(seg) + ", " + note).where(~pedestrian_only(seg), "none (no motor traffic)")


def speed_used(seg: pd.DataFrame) -> pd.Series:
    return _speed_kmh(seg).where(~pedestrian_only(seg), "–")


def speed_source(seg: pd.DataFrame) -> pd.Series:
    return _source(seg).where(~pedestrian_only(seg), "–")


def _for_roads(seg: pd.DataFrame, text: pd.Series) -> pd.Series:
    return text.where(~pedestrian_only(seg), "– (footway or path)")


def sidewalk_text(seg: pd.DataFrame) -> pd.Series:
    text = seg["infra_sidewalk"].map({100: "both sides", 60: "one side", 50: "not tagged", 0: "none"})
    return _for_roads(seg, text.fillna(seg["infra_sidewalk"].round().astype(int).astype(str)))


def crossing_text(seg: pd.DataFrame) -> pd.Series:
    text = seg["infra_crossing"].map({100: "marked", 50: "unmarked only", 0: "none"})
    return _for_roads(seg, text.fillna(seg["infra_crossing"].round().astype(int).astype(str)))


def density_text(seg: pd.DataFrame) -> pd.Series:
    return seg["intersection_density"].round().astype(int).astype(str) + " per km²"


def _walk_text(dist: pd.Series, reach_m: float) -> pd.Series:
    """'250 m walk' (rounded to 10 m), or 'none within <reach> m' for NaN."""
    text = dist.round(-1).astype("Int64").astype(str) + " m walk"
    return text.where(dist.notna(), f"none within {reach_m:,.0f} m")


def stop_text(seg: pd.DataFrame) -> pd.Series:
    return _walk_text(seg["transit_distance_m"], _params()["transit"]["stop"]["zero_score_m"])


def station_text(seg: pd.DataFrame) -> pd.Series:
    return _walk_text(seg["station_distance_m"], _params()["transit"]["rail_station"]["zero_score_m"])


CATEGORY_LABELS = {"cafe": "café"}


def _categories() -> list[str]:
    return list(_params()["amenity"]["categories"])


def furthest_amenity(seg: pd.DataFrame) -> pd.Series:
    """What pulls the amenity score down: categories out of reach, else the furthest one."""
    cats = _categories()
    reach = _params()["amenity"]["zero_score_m"]
    names = [CATEGORY_LABELS.get(c, c) for c in cats]
    dist = seg[[f"amenity_{c}_m" for c in cats]].to_numpy(dtype=float)
    text = []
    for row in dist:
        missing = [n for n, d in zip(names, row) if pd.isna(d)]
        if missing:
            text.append(f"none within {reach:,.0f} m: " + ", ".join(missing))
        else:
            i = int(row.argmax())
            text.append(f"{names[i]}, {round(row[i], -1):.0f} m walk")
    return pd.Series(text, index=seg.index)


def amenity_popup() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    """One popup line per category: walking distance to the nearest one."""
    reach = _params()["amenity"]["zero_score_m"]
    return {CATEGORY_LABELS.get(c, c).capitalize(): (lambda seg, c=c: _walk_text(seg[f"amenity_{c}_m"], reach))
            for c in _categories()}


INDICATOR_LABELS = {
    "amenity": "Amenity access",
    "connectivity": "Connectivity",
    "infrastructure": "Pedestrian infrastructure",
    "traffic_stress": "Traffic stress",
    "transit": "Transit access",
}
INDICATOR_SHORT = {
    "amenity": "amenity",
    "connectivity": "connectivity",
    "infrastructure": "infrastructure",
    "traffic_stress": "traffic",
    "transit": "transit",
}


def _indicator_line(column: str) -> Callable[[pd.DataFrame], pd.Series]:
    return lambda seg: seg[column].round().astype(int).astype(str)


def weakest_indicator(seg: pd.DataFrame) -> pd.Series:
    """The lowest of the five indicator scores, e.g. 'Traffic stress 11', or why the segment scores 0."""
    scores = seg[list(INDICATOR_LABELS)]
    lowest = scores.idxmin(axis=1)
    text = lowest.map(INDICATOR_LABELS) + " " + scores.min(axis=1).round().astype(int).astype(str)
    return text.where(~seg["impassable"], "impassable for this persona (steps)")


@dataclass(frozen=True)
class View:
    column: str                 # score column in the segments table
    label: str                  # shown in selector, legend, tooltip and table
    # Extra lines in the click popup and extra table columns: label -> function(segments) -> text.
    popup: dict[str, Callable[[pd.DataFrame], pd.Series]] = field(default_factory=dict)
    table: dict[str, Callable[[pd.DataFrame], pd.Series]] = field(default_factory=dict)


VIEWS = [
    View("score", "Walkability score",
         popup={**{label: _indicator_line(col) for col, label in INDICATOR_LABELS.items()},
                "Weakest": weakest_indicator},
         table={"Weakest indicator": weakest_indicator}),
    View("traffic_stress", "Traffic stress",
         popup={"Speed limit": speed_popup},
         table={"Speed used": speed_used, "Source": speed_source}),
    View("infrastructure", "Pedestrian infrastructure",
         popup={"Sidewalk": sidewalk_text, "Crossing within 50 m": crossing_text},
         table={"Sidewalk": sidewalk_text, "Crossing within 50 m": crossing_text}),
    View("connectivity", "Connectivity",
         popup={"Intersections within 400 m": density_text},
         table={"Intersections within 400 m": density_text}),
    View("transit", "Transit access",
         popup={"Nearest stop": stop_text, "Nearest rail station": station_text},
         table={"Nearest stop": stop_text, "Nearest rail station": station_text}),
    View("amenity", "Amenity access",
         popup=amenity_popup(),
         table={"Furthest category": furthest_amenity}),
]


# --- Data -----------------------------------------------------------------------

def pedestrian_only(seg: pd.DataFrame) -> pd.Series:
    allowed = _params()["pedestrian_only_highways"]
    return seg["highway"].map(lambda h: is_pedestrian_only(h, allowed)).astype(bool)


@st.cache_resource(show_spinner=False)
def _personas() -> dict[str, Persona]:
    return load_personas()


@st.cache_resource(show_spinner=False)
def _results() -> OrderedDict[str, Network]:
    """Scored places shared across sessions, most recently used last."""
    return OrderedDict()


def load(place: str) -> Network:
    """Scored network for a place, from memory or freshly, showing progress steps."""
    results = _results()
    if place in results:
        results.move_to_end(place)
        return results[place]

    box = st.empty()
    with box.container():
        with st.status(f"Scoring {place}…", expanded=True) as status:
            st.caption(FIRST_RUN_NOTE)

            def progress(step: str) -> None:
                status.update(label=f"{step}…")
                st.write(f"{step}…")

            try:
                net = pipeline.score_place(place, progress=progress)
            except Exception:
                status.update(label=f"Could not score {place}", state="error", expanded=False)
                raise
    box.empty()

    results[place] = net
    while len(results) > CACHED_PLACES:
        results.popitem(last=False)
    return net


def show_error(message: str, hint: str) -> None:
    st.error(f"**{message}**\n\n{hint}")


# --- Page parts -----------------------------------------------------------------

def choose_place(place: str) -> None:
    st.session_state["place_input"] = place
    st.session_state["selected_place"] = place


def persona_caption(persona: Persona) -> str:
    """The persona's weights in one short line, plus its infrastructure mix and impassable ways."""
    weights = " · ".join(f"{INDICATOR_SHORT[k]} {v:g}" for k, v in persona.weights.items())
    lines = [f"Weights: {weights}"]
    if set(persona.infrastructure_mix) != {"base"}:
        mix = ", ".join(f"{k} {v:g} %" for k, v in persona.infrastructure_mix.items())
        lines.append(f"Infrastructure: {mix}")
    if persona.impassable_highways:
        lines.append("Impassable: " + ", ".join(persona.impassable_highways) + " (score 0)")
    return "  \n".join(lines)


def render_sidebar() -> tuple[View, Persona]:
    with st.sidebar:
        st.header("Study area")
        with st.form("place_form", border=False):
            typed = st.text_input("Place", key="place_input", placeholder="e.g. Sternschanze, Hamburg",
                                  help="A neighbourhood or district, up to 5 km².")
            if st.form_submit_button("Score area", type="primary", width="stretch") and typed.strip():
                st.session_state["selected_place"] = typed.strip()

        st.caption("Examples")
        for label, place in EXAMPLES.items():
            st.button(label, key=f"example_{label}", on_click=choose_place, args=(place,), width="stretch")

        st.divider()
        personas = _personas()
        key = st.selectbox("Persona", list(personas), format_func=lambda k: personas[k].label, key="persona",
                           help="Who is walking. Changes the weights of the walkability score and, for some "
                                "personas, what the infrastructure score includes.")
        persona = personas[key]
        st.caption(persona_caption(persona))

        i = st.selectbox("Map shows", range(len(VIEWS)), format_func=lambda i: VIEWS[i].label, key="view")
    return VIEWS[i], persona


def _fmt(value: float, fmt: str) -> str:
    return "–" if pd.isna(value) else format(value, fmt)


def render_summary(seg: pd.DataFrame, area_km2: float, view: View) -> None:
    """Area figures, by the rule in docs/method.md ("Area summaries"): length-weighted,
    headline over roads, footways and paths reported separately."""
    summary = area_summary(seg, view.column, _params()["pedestrian_only_highways"])
    roads = seg[seg["scored"] & ~pedestrian_only(seg)]
    tagged = length_weighted_mean(roads["speed_tagged_share"], roads["length"])

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Area", f"{area_km2:.1f} km²")
    c2.metric("Street network", f"{summary.road_km + summary.pedestrian_km:.1f} km",
              help="Length of all scored streets, footways and paths.")
    c3.metric(f"{view.label}, roads", _fmt(summary.road_mean, ".0f"),
              help="Average over roads, weighted by length (0 = worst, 100 = best). "
                   "Footways and paths are left out; see the next figure.")
    c4.metric("Footways and paths", _fmt(summary.pedestrian_share, ".0%"),
              help=f"Share of the scored network length. Their average {view.label.lower()} is "
                   f"{_fmt(summary.pedestrian_mean, '.0f')}; on traffic stress and infrastructure "
                   "they score 100 by definition.")
    render_completeness(seg, tagged)


def render_completeness(seg: pd.DataFrame, speed_tagged: float) -> None:
    """Data completeness (docs/method.md): how much of the scores rests on data, not assumptions."""
    data = completeness(seg, _params()["pedestrian_only_highways"])
    with st.container(border=True):
        st.markdown("**Data completeness** · share of network length where OpenStreetMap has the data. "
                    "Where it is missing, scores use stated assumptions, so a low score may reflect "
                    "missing data rather than a bad street.")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Overall", _fmt(data.mean, ".0%"), help="Mean of sidewalk, kerb and lighting.")
        c2.metric("Sidewalk known", _fmt(data.sidewalk, ".0%"),
                  help="Roads only. Unknown sidewalks score 50 (unknown).")
        c3.metric("Kerb data nearby", _fmt(data.kerb, ".0%"),
                  help="A kerb point with a known type within 50 m. Without one, kerbs score 50 (unknown).")
        c4.metric("Lighting tagged", _fmt(data.lit, ".0%"), help="Unknown lighting scores 50 (unknown).")
        c5.metric("Speed limit tagged", _fmt(speed_tagged, ".0%"),
                  help="Share of road length with a speed limit in OpenStreetMap; the rest use default "
                       "speeds. Not part of the overall figure.")


def weakest_table(seg: pd.DataFrame, view: View) -> pd.DataFrame:
    table = weakest(seg, view.column, n=WEAKEST_ROWS)
    rows = seg.loc[table["segment_id"]]
    out = pd.DataFrame({
        "Street": table["street"],
        "Road type": table["type"],
        view.label: table[view.column].round().astype(int),
    })
    for name, fn in view.table.items():
        out[name] = fn(rows).to_numpy()
    return out


def table_key(place: str, view: View, persona: Persona) -> str:
    return f"weakest|{place}|{view.column}|{persona.key}"


def selected_street(key: str, table: pd.DataFrame) -> str | None:
    state = st.session_state.get(key)
    rows = state.selection.rows if state is not None else []
    return table["Street"].iloc[rows[0]] if rows and rows[0] < len(table) else None


def render_map(seg: pd.DataFrame, area, view: View, street: str | None) -> None:
    highlight = None
    if street:
        names = seg["name"].map(display_name)
        highlight = seg.index[seg["scored"] & (names == street)]
    details = {name: fn(seg) for name, fn in view.popup.items()}
    m = make_map(seg, view.column, view.label, area, details=details, highlight=highlight,
                 height_px=MAP_HEIGHT)
    st_folium(m, height=MAP_HEIGHT, use_container_width=True, returned_objects=[])
    st.html(legend_html(view.label))


def render_weakest(key: str, view: View, table: pd.DataFrame) -> None:
    st.subheader("Weakest streets")
    st.caption("Lowest-scoring named streets, one row per street. Click a row to outline the street on the map.")
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=key,
        column_config={
            "Street": st.column_config.TextColumn(width="large"),
            "Road type": st.column_config.TextColumn(width="medium"),
            view.label: st.column_config.NumberColumn(format="%d", width="small"),
        },
    )


def render_about() -> None:
    with st.expander("How is this scored?"):
        st.markdown(
            "Each street segment gets five indicator scores from 0 (worst) to 100 (best), calculated from "
            "OpenStreetMap data: amenity access, connectivity, pedestrian infrastructure, traffic stress "
            "and transit access. The walkability score is their weighted average, with the weights of the "
            "persona chosen in the sidebar; for wheelchair users the infrastructure score also counts kerbs "
            "and surface and steps score 0, and at night it also counts lighting. Use **Map shows** to "
            "colour the streets by the walkability score or by one indicator, and click a street for details."
        )
    st.caption("Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, "
               "available under the ODbL.")


# --- Page -----------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="WalkScope", page_icon="🚶", layout="wide",
                       initial_sidebar_state="expanded")
    view, persona = render_sidebar()
    st.title("WalkScope")
    st.caption("Street-by-street walkability from OpenStreetMap.")

    place = st.session_state.get("selected_place")
    if not place:
        st.info("Type a neighbourhood or district in the sidebar (up to 5 km²) and press **Score area**, "
                "or pick one of the examples.")
        render_about()
        return

    try:
        net = load(place)
    except ServerBusyError as e:
        show_error(str(e), BUSY_HINT)
        return
    except AreaError as e:
        show_error(str(e), e.hint)
        return
    except Exception:
        log.exception("Scoring %r failed", place)
        show_error("Something went wrong while scoring this place.",
                   "Try again. If it keeps happening, try a nearby neighbourhood.")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())
        return

    seg = apply_persona(net.segments, persona)        # a copy: the cached network is shared
    st.subheader(f"{place} · {persona.label}")
    render_summary(seg, net.area.area / 1e6, view)     # net.area is in metres (UTM)
    key = table_key(place, view, persona)
    table = weakest_table(seg, view)
    render_map(seg, net.area, view, selected_street(key, table))
    render_weakest(key, view, table)
    render_about()


main()
