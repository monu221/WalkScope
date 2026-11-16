"""WalkScope Streamlit app. Start with:  streamlit run app/streamlit_app.py

Layout: inputs in the sidebar; in the main area a summary row, the map at
full width with its legend, the weakest-streets table and the scoring notes.
Each part is one render_* function. To add an indicator to the map, add a
View to VIEWS (a selector appears in the sidebar once there are two); a
persona selector and a data-completeness panel go in the sidebar and
render_summary.
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
from walkscope.config import load_indicator_params
from walkscope.fetch import AreaError, ServerBusyError
from walkscope.mapview import legend_html, make_map
from walkscope.network import Network
from walkscope.scoring import display_name, weakest
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


# --- What the map can show ----------------------------------------------------

def speed_popup(seg: pd.DataFrame) -> pd.Series:
    """Speed limit used for traffic stress, and whether it was tagged or assumed."""
    ped = pedestrian_only(seg)
    text = seg["speed_kmh"].round().astype(int).astype(str) + " km/h, " + seg["speed_tagged"].map(
        {True: "tagged in OSM", False: "assumed (no speed limit tagged; German default for the road type)"})
    return text.where(~ped, "none (no motor traffic)")


def speed_table(seg: pd.DataFrame) -> pd.Series:
    ped = pedestrian_only(seg)
    text = seg["speed_kmh"].round().astype(int).astype(str) + " km/h" + seg["speed_tagged"].map(
        {True: "", False: " (assumed)"})
    return text.where(~ped, "–")


@dataclass(frozen=True)
class View:
    column: str                 # score column in the segments table
    label: str                  # shown in legend, tooltip and table
    # Extra lines in the click popup and extra table columns: label -> function(segments) -> text.
    popup: dict[str, Callable[[pd.DataFrame], pd.Series]] = field(default_factory=dict)
    table: dict[str, Callable[[pd.DataFrame], pd.Series]] = field(default_factory=dict)


VIEWS = [
    View("traffic_stress", "Traffic stress",
         popup={"Speed limit": speed_popup}, table={"Speed used": speed_table}),
]


# --- Data -----------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def _params() -> dict:
    return load_indicator_params()


def pedestrian_only(seg: pd.DataFrame) -> pd.Series:
    allowed = _params()["pedestrian_only_highways"]
    return seg["highway"].map(lambda h: is_pedestrian_only(h, allowed)).astype(bool)


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


def render_sidebar() -> View:
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

        view = VIEWS[0]
        if len(VIEWS) > 1:
            st.divider()
            i = st.selectbox("Map shows", range(len(VIEWS)), format_func=lambda i: VIEWS[i].label, key="view")
            view = VIEWS[i]
        # Persona selector goes here (build step 7).
    return view


def render_summary(net: Network, view: View) -> None:
    s = net.segments[net.segments["scored"]]
    roads = s[~pedestrian_only(s)]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Area", f"{net.area.area / 1e6:.1f} km²")           # net.area is in metres (UTM)
    c2.metric("Streets scored", f"{len(s):,}")
    c3.metric(f"Mean {view.label.lower()}", f"{s[view.column].mean():.0f}",
              help="0 = worst, 100 = best, averaged over all scored segments.")
    c4.metric("Speed limit tagged", f"{roads['speed_tagged'].mean():.0%}" if len(roads) else "–",
              help="Share of roads with a speed limit in OpenStreetMap; the rest use German default speeds.")
    # Data-completeness panel goes here (build step 6).


def weakest_table(net: Network, view: View) -> pd.DataFrame:
    seg = net.segments
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


def table_key(place: str, view: View) -> str:
    return f"weakest|{place}|{view.column}"


def selected_street(place: str, view: View, table: pd.DataFrame) -> str | None:
    state = st.session_state.get(table_key(place, view))
    rows = state.selection.rows if state is not None else []
    return table["Street"].iloc[rows[0]] if rows and rows[0] < len(table) else None


def render_map(net: Network, view: View, street: str | None) -> None:
    seg = net.segments
    highlight = None
    if street:
        names = seg["name"].map(display_name)
        highlight = seg.index[seg["scored"] & (names == street)]
    details = {name: fn(seg) for name, fn in view.popup.items()}
    m = make_map(seg, view.column, view.label, net.area, details=details, highlight=highlight,
                 height_px=MAP_HEIGHT)
    st_folium(m, height=MAP_HEIGHT, use_container_width=True, returned_objects=[])
    st.html(legend_html(view.label))


def render_weakest(place: str, view: View, table: pd.DataFrame) -> None:
    st.subheader("Weakest streets")
    st.caption("Lowest-scoring named streets, one row per street. Click a row to outline the street on the map.")
    st.dataframe(
        table,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=table_key(place, view),
        column_config={
            "Street": st.column_config.TextColumn(width="large"),
            "Road type": st.column_config.TextColumn(width="medium"),
            view.label: st.column_config.NumberColumn(format="%d", width="small"),
        },
    )


def render_about() -> None:
    with st.expander("How is this scored?"):
        st.markdown(
            "Each street segment gets a score from 0 (worst) to 100 (best), calculated from "
            "OpenStreetMap data. The map currently shows traffic stress: it falls as the speed limit "
            "rises (the German default for the road type is assumed where no limit is tagged) and for "
            "each lane beyond two, while footways and pedestrian streets score 100. Later versions "
            "combine five indicators (amenities, street connectivity, pedestrian infrastructure, "
            "traffic stress and transit), weighted for the selected persona."
        )
    st.caption("Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors, "
               "available under the ODbL.")


# --- Page -----------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="WalkScope", page_icon="🚶", layout="wide",
                       initial_sidebar_state="expanded")
    view = render_sidebar()
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

    st.subheader(place)
    render_summary(net, view)
    table = weakest_table(net, view)
    render_map(net, view, selected_street(place, view, table))
    render_weakest(place, view, table)
    render_about()


main()
