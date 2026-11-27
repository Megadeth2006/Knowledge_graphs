"""Create separate rental, teaching and OSM pilot graphs."""

import csv
import gzip
import json
from decimal import Decimal
from hashlib import sha256

from rdflib import Literal, URIRef
from rdflib.namespace import DCTERMS, RDF, XSD

from dedup import address_key
from ontology_schema import DATA_PROPERTIES, EX, add_label, new_graph
from paths import FIXTURES, OSM_SNAPSHOT
from paths import GEO as GEO_DIR
from pilot_area import on_petrograd_island
from spatial import GEO, point_wkt

DEMO_DATE = "2026-10-22"
FIELDS = {
    "rent_rub_month": "rentRubMonth",
    "utilities_rub_month": "utilitiesRubMonth",
    "deposit_rub": "depositRub",
    "commission_rub": "commissionRub",
    "pets_allowed": "petsAllowed",
    "term": "term",
    "min_months": "minMonths",
    "quality_note": "qualityNote",
}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def add_value(graph, subject, property_name, value):
    if value is None or value == "":
        return
    datatype = DATA_PROPERTIES[property_name][1]
    if datatype == "decimal":
        value = Decimal(str(value))
    elif datatype == "integer":
        value = int(value)
    elif datatype == "boolean" and not isinstance(value, bool):
        text = str(value).lower()
        if text not in {"true", "false"}:
            raise ValueError(f"{subject}: invalid boolean {value!r}")
        value = text == "true"
    graph.add((subject, EX[property_name], Literal(value, datatype=XSD[datatype])))


def add_instance(graph, node, class_name, label, synthetic):
    graph.add((node, RDF.type, EX[class_name]))
    add_label(graph, node, label)
    add_value(graph, node, "isSynthetic", synthetic)


def add_point(graph, owner, coordinates, kind, source=None):
    geometry = URIRef(f"{owner}_point")
    graph.add((owner, GEO.hasGeometry, geometry))
    graph.add((geometry, RDF.type, GEO.Geometry))
    graph.add((geometry, GEO.asWKT, point_wkt(*coordinates)))
    add_label(graph, geometry, "Опорная точка")
    add_value(graph, geometry, "geometryKind", kind)
    if source is not None:
        graph.add((geometry, EX.hasSource, source))


def building_iri(row, synthetic):
    if synthetic:
        return EX[f"{row['id']}_building"]
    # Different spellings of one address must give one building node.
    normalized = address_key(row["address"])
    key = sha256(f"Санкт-Петербург|{normalized}".encode()).hexdigest()[:20]
    return EX[f"Building_{key}"]


def add_listing(graph, row, synthetic):
    key = row["id"]
    listing = EX[key]
    # Only a curator can assign a shared home key after verifying the identity.
    home_key = row.get("verified_home_id") or key
    if home_key != key and not row.get("identity_evidence"):
        raise ValueError(f"Missing evidence for shared dwelling: {key}")
    home = EX[f"{home_key}_home"]
    building = building_iri(row, synthetic)
    source = EX[f"{key}_source"]
    address = row.get("address", f"Учебный адрес {key}")
    add_instance(
        graph,
        listing,
        "ListingSnapshot",
        f"Учебное объявление {key}" if synthetic else f"Объявление {key}",
        synthetic,
    )
    add_instance(
        graph,
        home,
        "Apartment",
        f"Учебная квартира {key}" if synthetic else f"Квартира {home_key}",
        synthetic,
    )
    if (building, RDF.type, EX.Building) not in graph:
        add_instance(
            graph,
            building,
            "Building",
            f"Учебное здание {key}" if synthetic else f"Здание — {address}",
            synthetic,
        )
        add_value(graph, building, "address", address)
        add_value(graph, building, "floorsTotal", row.get("floors_total"))
    else:
        known = graph.value(building, EX.floorsTotal)
        if (
            known is not None
            and row.get("floors_total")
            and int(known) != int(row["floors_total"])
        ):
            raise ValueError(f"Conflicting floor counts for {address}")

    add_instance(
        graph,
        source,
        "SourceRecord",
        f"Учебный источник {key}" if synthetic else f"Источник объявления {key}",
        synthetic,
    )
    graph.add((listing, EX.offers, home))
    graph.add((home, EX.inBuilding, building))
    for node in (listing, home, building):
        graph.add((node, EX.hasSource, source))

    for field, property_name in FIELDS.items():
        add_value(graph, listing, property_name, row.get(field))
    date = DEMO_DATE if synthetic else row["observed_on"]
    add_value(graph, listing, "observedOn", date)
    for field, property_name in (
        ("area_m2", "areaM2"),
        ("rooms", "rooms"),
        ("floor", "floor"),
    ):
        add_value(graph, home, property_name, row.get(field))
    source_url = (
        f"https://example.org/synthetic/{key}" if synthetic else row["source_url"]
    )
    add_value(graph, source, "sourceUrl", source_url)
    if not synthetic:
        graph.add((source, DCTERMS.identifier, Literal(row["source_id"])))
        if row.get("identity_evidence"):
            graph.add(
                (
                    source,
                    DCTERMS.description,
                    Literal(row["identity_evidence"], lang="ru"),
                )
            )
    if row.get("pin_lon"):
        if not on_petrograd_island((float(row["pin_lon"]), float(row["pin_lat"]))):
            raise ValueError(f"Offer pin outside island: {key}")
        graph.add((EX.IslandSample, DCTERMS.hasPart, listing))
    return building


def add_profile(
    graph,
    node,
    label,
    city,
    budget,
    straight_limit,
    walk_limit,
    requires_pets,
    synthetic,
):
    add_instance(graph, node, "RenterProfile", label, synthetic)
    graph.add((node, EX.preferredCity, city))
    add_value(graph, node, "maxRentRubMonth", budget)
    add_value(graph, node, "maxMetroDistanceM", straight_limit)
    add_value(graph, node, "maxMetroWalkSeconds", walk_limit)
    add_value(graph, node, "requiresPets", requires_pets)


def build_observed(rows):
    graph = new_graph()
    add_instance(graph, EX.SaintPetersburg, "City", "Санкт-Петербург", False)
    by_home = {}
    for row in rows:
        home_key = row.get("verified_home_id") or row["id"]
        identity_fields = (
            building_iri(row, False),
            Decimal(row["area_m2"]) if row.get("area_m2") else None,
            int(row["rooms"]) if row.get("rooms") else None,
            int(row["floor"]) if row.get("floor") else None,
        )
        if by_home.setdefault(home_key, identity_fields) != identity_fields:
            raise ValueError(f"Conflicting attributes for verified dwelling {home_key}")
        building = add_listing(graph, row, synthetic=False)
        graph.add((building, EX.locatedIn, EX.SaintPetersburg))
    return graph


def build_demo(rows, amenities):
    graph = new_graph()
    add_instance(graph, EX.DemoCity, "City", "Учебный город", True)
    add_instance(graph, EX.DemoDistrict, "District", "Учебный район", True)
    graph.add((EX.DemoDistrict, EX.inCity, EX.DemoCity))
    add_profile(
        graph,
        EX.StudentWithPet,
        "Студент с питомцем",
        EX.DemoCity,
        budget=40000,
        straight_limit=500,
        walk_limit=None,
        requires_pets=True,
        synthetic=True,
    )
    for row in amenities:
        amenity = EX[row["id"]]
        add_instance(graph, amenity, row["type"], row["label"], True)
        add_point(graph, amenity, (row["lon"], row["lat"]), "synthetic")
    for row in rows:
        building = add_listing(graph, {**row, "term": "long_term"}, synthetic=True)
        graph.add((building, EX.inDistrict, EX.DemoDistrict))
        add_point(graph, building, (row["lon"], row["lat"]), "synthetic")
    return graph


def osm_iri(kind, identifier):
    return URIRef(f"https://www.openstreetmap.org/{kind}/{identifier}")


def read_snapshot():
    with gzip.open(OSM_SNAPSHOT, "rt", encoding="utf-8") as source:
        return json.load(source)


def build_osm_pilot(observed_rows, snapshot=None):
    snapshot = snapshot or read_snapshot()
    geocode = json.loads((GEO_DIR / "osm_geocode_r04.json").read_text(encoding="utf-8"))
    graph = new_graph()
    add_instance(graph, EX.OSMExtract, "SourceRecord", "Выгрузка OpenStreetMap", False)
    add_value(graph, EX.OSMExtract, "sourceUrl", snapshot["source"])
    matches = json.loads(
        (GEO_DIR / "island_buildings.json").read_text(encoding="utf-8")
    )
    matches.append(geocode)
    by_address = {address_key(match["address"]): match for match in matches}
    placed = set()
    for item in observed_rows:
        building = building_iri(item, False)
        # Several offers in one building share one geometry.
        if building in placed:
            continue
        placed.add(building)
        match = by_address.get(address_key(item["address"]))
        if match is None:
            if item.get("pin_lon") and item.get("pin_lat"):
                point = float(item["pin_lon"]), float(item["pin_lat"])
                if not on_petrograd_island(point):
                    raise ValueError(f"Offer pin outside island: {item['id']}")
                add_point(
                    graph,
                    building,
                    point,
                    "publisher_pin_approximate",
                    EX[f"{item['id']}_source"],
                )
            continue
        point = float(match["lon"]), float(match["lat"])
        if not on_petrograd_island(point):
            raise ValueError(f"OSM reference point outside island: {match['address']}")
        source = osm_iri("way", match["osm_url"].rsplit("/", 1)[-1])
        add_instance(graph, source, "SourceRecord", match["matched_address"], False)
        add_value(graph, source, "sourceUrl", match["osm_url"])
        kind = (
            "nominatim_building_point"
            if match == geocode
            else "osm_outline_vertex_mean"
        )
        add_point(graph, building, point, kind, source)
    add_profile(
        graph,
        EX.WalkingProfile,
        "Пешком до метро",
        EX.SaintPetersburg,
        budget=75000,
        straight_limit=None,
        walk_limit=900,
        requires_pets=False,
        synthetic=False,
    )
    for feature in snapshot["features"]:
        node = osm_iri(feature["osm_type"], feature["osm_id"])
        kind = feature["type"]
        label = feature["label"] or f"Объект OSM {feature['osm_id']}"
        if kind == "MetroEntrance":
            label = f"Вход/выход метро — {label}"
        add_instance(graph, node, kind, label, False)
        graph.add((node, EX.hasSource, EX.OSMExtract))
        add_point(graph, node, feature["point"], feature["point_kind"], EX.OSMExtract)
        if kind == "MetroEntrance":
            add_value(graph, node, "usableEntrance", feature["usable"])
            if "station" in feature:
                graph.add((node, EX.entranceOf, osm_iri(*feature["station"])))
    return graph, snapshot


def load_demo():
    return read_csv(FIXTURES / "demo_listings.csv"), read_csv(FIXTURES / "demo_poi.csv")


if __name__ == "__main__":
    from sources import load_all_slices

    graph = build_observed(load_all_slices())
    print(len(graph), "triples in the observed graph")
