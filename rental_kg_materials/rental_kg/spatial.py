"""Geospatial functions for the project's CRS84 point geometries."""

import math
import re

from geographiclib.geodesic import Geodesic
from rdflib import Literal, Namespace
from rdflib.namespace import XSD
from rdflib.plugins.sparql.operators import custom_function
from rdflib.plugins.sparql.sparql import SPARQLError

GEO = Namespace("http://www.opengis.net/ont/geosparql#")
GEOF = Namespace("http://www.opengis.net/def/function/geosparql/")
METRE = "http://www.opengis.net/def/uom/OGC/1.0/metre"
CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?"
POINT = re.compile(rf"POINT\s*\(\s*({NUMBER})\s+({NUMBER})\s*\)", re.IGNORECASE)


def point_wkt(longitude, latitude):
    lon, lat = float(longitude), float(latitude)
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"Invalid CRS84 coordinates: {lon}, {lat}")
    return Literal(f"POINT ({lon:.8f} {lat:.8f})", datatype=GEO.wktLiteral)


def read_point(geometry):
    if not isinstance(geometry, Literal) or geometry.datatype != GEO.wktLiteral:
        raise ValueError("Expected a geo:wktLiteral")
    wkt = str(geometry).strip()
    if wkt.startswith(f"<{CRS84}>"):
        wkt = wkt[len(CRS84) + 2 :].strip()
    match = POINT.fullmatch(wkt)
    if not match:
        raise ValueError("Only CRS84 POINT geometries are supported")
    lon, lat = map(float, match.groups())
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError(f"Invalid CRS84 coordinates: {lon}, {lat}")
    return lon, lat


def distance_metres(first, second):
    lon1, lat1 = first
    lon2, lat2 = second
    return Geodesic.WGS84.Inverse(lat1, lon1, lat2, lon2)["s12"]


@custom_function(GEOF.distance)
def geosparql_distance(first, second, unit):
    """Execute the metre-valued, point-to-point subset of geof:distance."""
    if str(unit) != METRE:
        raise SPARQLError("Only metre units are supported")
    try:
        distance = distance_metres(read_point(first), read_point(second))
    except ValueError as error:
        raise SPARQLError(str(error)) from error
    if not math.isfinite(distance):
        raise SPARQLError("Distance is not finite")
    return Literal(distance, datatype=XSD.double)
