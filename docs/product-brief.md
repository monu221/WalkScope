# WalkScope – Product Brief

WalkScope is a web tool that scores how walkable any neighbourhood is, street by street, and lets a planner test changes.

## Problem

Walkability studies are done by hand in GIS, city by city. Existing scores give one number per address and ignore who is walking.

## Users

- Transport planners and students who need a quick first assessment of an area
- Residents and advocacy groups who want evidence for a missing crossing or footpath

## What version 1 does

1. The user types a place name or draws an area on a map.
2. The tool downloads OpenStreetMap data for that area and scores every street from 0 to 100.
3. It shows the scores on an interactive map, with the weakest streets listed.
4. The user can switch persona (general adult, wheelchair user, child, night-time) and see the scores change.
5. The user can add a crossing or an amenity and see the score recalculate.
6. A one-page report downloads as PDF or Markdown.

## Out of scope for version 1

- Street-image analysis
- An AI-written report
- Timetable-based public transport scores

These are listed as later milestones in the backlog.

## Success metric

- Scoring an area takes about 4 s once its map data is there (measured on a 1.5 km² area with about 1,600 scored segments, 2026-11-16).
- Downloading a new area's map data from the public OpenStreetMap server typically takes 3–5 minutes. That time is set by the server's load and is outside the tool's control; the app shows progress while it waits.
- The bundled example areas open in 5–6 s, without any download.
- The score for one validation city correlates with an independent measure such as pedestrian counts.

## Stack

Python 3.11, OSMnx, NetworkX, GeoPandas, Shapely, Streamlit, Folium, streamlit-folium, fpdf2, PyYAML, pytest, GitHub Actions, Docker.
