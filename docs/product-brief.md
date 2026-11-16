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

- A new area of about 2 km² is scored in under 60 seconds.
- The score for one validation city correlates with an independent measure such as pedestrian counts.

## Stack

Python 3.11, OSMnx, NetworkX, GeoPandas, Shapely, Streamlit, Folium, streamlit-folium, fpdf2, PyYAML, pytest, GitHub Actions, Docker.
