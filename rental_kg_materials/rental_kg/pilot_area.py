"""Check whether a building's reference point is on Petrogradsky Island."""

import itertools
import json
from pathlib import Path

BOUNDARY = Path(__file__).resolve().parent / "data" / "petrograd_island.geojson"


def inside_ring(point, ring):
    longitude, latitude = point
    inside = False
    for (x1, y1), (x2, y2) in itertools.pairwise(ring):
        if (y1 > latitude) != (y2 > latitude):
            crossing = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing:
                inside = not inside
    return inside


def on_petrograd_island(point):
    boundary = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    exterior, *holes = boundary["geometry"]["coordinates"]
    return inside_ring(point, exterior) and not any(
        inside_ring(point, hole) for hole in holes
    )
