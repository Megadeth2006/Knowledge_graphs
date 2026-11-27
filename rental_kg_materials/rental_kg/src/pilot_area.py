"""Check whether a building's reference point is on Petrogradsky Island."""

import itertools
import json
from functools import cache

from paths import ISLAND_BOUNDARY


def inside_ring(point, ring):
    longitude, latitude = point
    inside = False
    for (x1, y1), (x2, y2) in itertools.pairwise(ring):
        if (y1 > latitude) != (y2 > latitude):
            crossing = x1 + (latitude - y1) * (x2 - x1) / (y2 - y1)
            if longitude < crossing:
                inside = not inside
    return inside


@cache
def boundary_rings():
    boundary = json.loads(ISLAND_BOUNDARY.read_text(encoding="utf-8"))
    return boundary["geometry"]["coordinates"]


def on_petrograd_island(point):
    exterior, *holes = boundary_rings()
    return inside_ring(point, exterior) and not any(
        inside_ring(point, hole) for hole in holes
    )


if __name__ == "__main__":
    print(on_petrograd_island((30.3051, 59.9635)))
