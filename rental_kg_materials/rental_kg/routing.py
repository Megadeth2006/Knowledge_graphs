"""Calculate pilot walking routes on the extracted OSM pedestrian network."""

import itertools
from collections import defaultdict
from heapq import heappop, heappush

from rdflib import Literal
from rdflib.namespace import XSD
from rdflib.plugins.sparql.operators import register_custom_function
from rdflib.plugins.sparql.sparql import SPARQLError

from spatial import distance_metres, read_point

WALK_SPEED_M_S = 1.4
MAX_SNAP_METRES = 45


class PedestrianNetwork:
    def __init__(self, snapshot):
        self.points = snapshot["nodes"]
        self.edges = defaultdict(dict)
        blocked = set(snapshot["blocked"])
        for nodes, direction in snapshot["ways"]:
            for start, end in itertools.pairwise(nodes):
                if (
                    start in blocked
                    or end in blocked
                    or start not in self.points
                    or end not in self.points
                ):
                    continue
                length = distance_metres(self.points[start], self.points[end])
                if direction != "-1":
                    self.edges[start][end] = length
                    self.edges.setdefault(end, {})
                if direction not in {"yes", "1", "true"}:
                    self.edges[end][start] = length
                    self.edges.setdefault(start, {})
        self.destinations = {
            tuple(feature["point"]): feature["osm_id"]
            for feature in snapshot["features"]
            if feature["type"] == "MetroEntrance" and feature["usable"]
        }
        self.origins = {}

    def search(self, point):
        closest = None
        connector = MAX_SNAP_METRES
        lon, lat = point
        for node in self.edges:
            node_lon, node_lat = self.points[node]
            # Cheap bounding box before computing the short geodesic.
            if abs(lon - node_lon) > 0.002 or abs(lat - node_lat) > 0.001:
                continue
            length = distance_metres(point, self.points[node])
            if length <= connector:
                closest, connector = node, length
        if closest is None:
            return {}

        distances = {closest: connector}
        queue = [(connector, closest)]
        while queue:
            length, node = heappop(queue)
            if length != distances[node]:
                continue
            for neighbour, edge in self.edges.get(node, {}).items():
                candidate = length + edge
                if candidate < distances.get(neighbour, float("inf")):
                    distances[neighbour] = candidate
                    heappush(queue, (candidate, neighbour))
        return distances

    def length(self, origin, destination):
        target = self.destinations.get(destination)
        if target is None:
            return None
        if origin not in self.origins:
            self.origins[origin] = self.search(origin)
        return self.origins[origin].get(target)

    def register(self, namespace):
        def walking_seconds(origin, destination):
            try:
                length = self.length(read_point(origin), read_point(destination))
            except ValueError as error:
                raise SPARQLError(str(error)) from error
            if length is None:
                raise SPARQLError("No pedestrian route in the available extract")
            return Literal(length / WALK_SPEED_M_S, datatype=XSD.double)

        register_custom_function(namespace.walkSeconds, walking_seconds, override=True)
