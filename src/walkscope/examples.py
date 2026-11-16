"""Example study areas, shared by the app and scripts/measure_timing.py."""

from shapely.geometry import box

# Aachen's districts are larger than 5 km² or don't resolve to an area, so the
# Aachen example is a drawn area: a 1.5 km² square (about 1.22 x 1.22 km)
# centred between the Dom (50.7747 N, 6.0839 E) and the Markt. Lat/lon.
AACHEN_CENTRE = box(6.0752, 50.76999, 6.0926, 50.78101)
AACHEN_CENTRE_NAME = "Aachen city centre (Dom and Markt, 1.5 km²)"

STERNSCHANZE = "Sternschanze, Hamburg"
ISERBROOK = "Iserbrook, Hamburg"
