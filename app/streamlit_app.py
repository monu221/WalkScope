"""WalkScope Streamlit app. Start with:  streamlit run app/streamlit_app.py"""

import streamlit as st
from streamlit_folium import st_folium

from walkscope.fetch import AreaError, ServerBusyError
from walkscope.mapview import make_map
from walkscope.pipeline import score_place
from walkscope.scoring import weakest

INDICATOR = "traffic_stress"
INDICATOR_LABEL = "Traffic stress"

st.set_page_config(page_title="WalkScope", page_icon="🚶", layout="wide")
st.title("WalkScope")
st.caption("Street-by-street walkability from OpenStreetMap. "
           "Preview: streets are coloured by traffic stress only; the other indicators follow.")


@st.cache_resource(show_spinner=False, max_entries=5)
def scored(place: str):
    return score_place(place)


with st.form("place_form"):
    typed = st.text_input("Place", placeholder="e.g. St. Pauli, Hamburg")
    if st.form_submit_button("Score area") and typed.strip():
        st.session_state["selected_place"] = typed.strip()

place = st.session_state.get("selected_place")
if not place:
    st.info("Type a neighbourhood or district (up to 5 km²) and press **Score area**.")
    st.stop()

try:
    with st.spinner(f"Downloading OpenStreetMap data for {place} and scoring streets…"):
        net = scored(place)
except ServerBusyError as e:
    st.error(str(e))
    st.stop()
except AreaError as e:
    st.error(str(e))
    st.stop()

seg = net.segments
s = seg[seg["scored"]]
roads = s[s[INDICATOR] < 100]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Area", f"{net.area.area / 1e6:.1f} km²")           # net.area is in metres (UTM)
c2.metric("Streets scored", f"{len(s):,}")
c3.metric(f"Mean {INDICATOR_LABEL.lower()}", f"{s[INDICATOR].mean():.0f}")
c4.metric("Speed limit tagged", f"{roads['speed_tagged'].mean():.0%}" if len(roads) else "–",
          help="Share of roads with a maxspeed tag; the rest use German default speeds.")

left, right = st.columns([3, 1])
with left:
    st_folium(make_map(seg, INDICATOR, INDICATOR_LABEL, net.area),
              height=620, use_container_width=True, returned_objects=[])
with right:
    st.subheader("Weakest streets")
    table = weakest(seg, INDICATOR, n=15).rename(
        columns={"street": "Street", "type": "Type", INDICATOR: INDICATOR_LABEL})
    st.dataframe(table, hide_index=True, use_container_width=True,
                 column_config={INDICATOR_LABEL: st.column_config.NumberColumn(format="%d")})
