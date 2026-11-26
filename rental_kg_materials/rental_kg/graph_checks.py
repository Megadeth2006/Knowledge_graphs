"""Regression checks for the ontology, validation and query results."""

from copy import deepcopy
from decimal import Decimal
from math import isclose

from pyshacl import validate
from rdflib import Literal
from rdflib.namespace import OWL, RDF, RDFS, SH, XSD

from ontology_schema import CLASSES, EX, new_graph
from rental_data import add_point, build_observed, building_iri
from routing import WALK_SPEED_M_S, PedestrianNetwork
from spatial import GEO, point_wkt, read_point

NEAREST_SHOP = """
PREFIX ex: <https://example.org/rental-kg#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
PREFIX uom: <http://www.opengis.net/def/uom/OGC/1.0/>
SELECT ?home ?shop WHERE {
    ?home ex:inBuilding/geo:hasGeometry/geo:asWKT ?from .
    ?shop a ex:Supermarket ; geo:hasGeometry/geo:asWKT ?to .
    FILTER(geof:distance(?from, ?to, uom:metre) <= 700)
}
"""


def check_graphs(schema, demo, full_demo, full_observed, rows):
    direct = schema + demo
    inferred = {
        "inverse": (EX.D06_home, EX.hasListing, EX.D06),
        "transitive": (EX.D06_home, EX.locatedIn, EX.DemoCity),
        "subclass": (EX.M1, RDF.type, EX.Amenity),
    }
    for name, triple in inferred.items():
        assert triple not in direct and triple in full_demo, name

    assert len(set(full_demo.subjects(RDF.type, EX.Place))) == 14
    assert len(set(full_demo.subjects(RDF.type, EX.Dwelling))) == 12
    assert len(set(full_demo.subjects(RDF.type, EX.Amenity))) == 6
    assert len(set(full_observed.subjects(RDF.type, EX.Building))) == len(
        {building_iri(row, False) for row in rows}
    )
    assert len(set(full_observed.subjects(RDF.type, EX.Dwelling))) == len(
        {r.get("verified_home_id") or r["id"] for r in rows}
    )
    for row in rows:
        home = full_observed.value(EX[row["id"]], EX.offers)
        assert (home, EX.locatedIn, EX.SaintPetersburg) in full_observed
    assert full_observed.value(EX.R05_home, EX.inBuilding) == full_observed.value(
        EX.R06_home,
        EX.inBuilding,
    )
    assert full_observed.value(EX.R04_home, EX.inBuilding) == building_iri(
        next(row for row in rows if row["id"] == "R04"),
        False,
    )

    for graph in (full_demo, full_observed):
        assert not list(graph.subjects(RDF.type, OWL.Nothing))
        for class_name in CLASSES:
            for node in graph.subjects(RDF.type, EX[class_name]):
                assert any(
                    label.language == "ru" and str(label).strip()
                    for label in graph.objects(node, RDFS.label)
                ), node
    changed_date = build_observed([{**rows[0], "observed_on": "2026-11-04"}])
    assert changed_date.value(EX[rows[0]["id"]], EX.observedOn) == Literal(
        "2026-11-04",
        datatype=XSD.date,
    )
    return {name: "PASS" for name in inferred}


def negative_checks(demo, schema, shapes):
    cases = {
        "negative_rent": (
            [(EX.D01, EX.rentRubMonth, Literal(Decimal(-1)))],
            [],
            [],
            SH.MinExclusiveConstraintComponent,
        ),
        "amenity_as_building": ([(EX.D01_home, EX.inBuilding, EX.M1)], [], [], None),
        "district_as_building": (
            [(EX.D01_home, EX.inBuilding, EX.DemoDistrict)],
            [],
            [],
            None,
        ),
        "building_as_city": (
            [(EX.DemoDistrict, EX.inCity, EX.D01_building)],
            [],
            [],
            None,
        ),
        "amenity_as_location": ([], [(EX.D01_home, EX.locatedIn, EX.S1)], [], None),
        "dwelling_as_location": (
            [],
            [(EX.D01_home, EX.locatedIn, EX.D02_home)],
            [],
            None,
        ),
        "amenity_as_location_subject": (
            [],
            [(EX.M1, EX.locatedIn, EX.DemoCity)],
            [],
            None,
        ),
        "city_in_building": (
            [],
            [(EX.DemoCity, EX.locatedIn, EX.D01_building)],
            [],
            None,
        ),
        "district_in_building": (
            [],
            [(EX.DemoDistrict, EX.locatedIn, EX.D01_building)],
            [],
            None,
        ),
        "building_in_building": (
            [],
            [(EX.D01_building, EX.locatedIn, EX.D02_building)],
            [],
            None,
        ),
        "geometry_on_dwelling": (
            [],
            [(EX.D01_home, GEO.hasGeometry, EX.D01_building_point)],
            [],
            SH.OrConstraintComponent,
        ),
        "geometry_on_city": (
            [],
            [(EX.DemoCity, GEO.hasGeometry, EX.D01_building_point)],
            [],
            SH.OrConstraintComponent,
        ),
        "latitude_out_of_range": (
            [
                (
                    EX.D01_building_point,
                    GEO.asWKT,
                    Literal(
                        "POINT (30.0 91.0)",
                        datatype=GEO.wktLiteral,
                    ),
                )
            ],
            [],
            [],
            SH.SPARQLConstraintComponent,
        ),
        "missing_wkt": (
            [],
            [],
            [(EX.D01_building_point, GEO.asWKT, None)],
            SH.MinCountConstraintComponent,
        ),
        "incompatible_amenity_types": ([], [(EX.M1, RDF.type, EX.Park)], [], None),
        "missing_building_height": (
            [],
            [],
            [(EX.D01_building, EX.floorsTotal, None)],
            None,
        ),
        "building_as_source": ([(EX.D01, EX.hasSource, EX.D01_building)], [], [], None),
        "negative_profile_budget": (
            [(EX.StudentWithPet, EX.maxRentRubMonth, Literal(Decimal(-1)))],
            [],
            [],
            SH.MinExclusiveConstraintComponent,
        ),
        "negative_walk_limit": (
            [(EX.StudentWithPet, EX.maxMetroWalkSeconds, Literal(-1))],
            [],
            [],
            SH.MinInclusiveConstraintComponent,
        ),
        "missing_source_url": (
            [],
            [],
            [(EX.D01_source, EX.sourceUrl, None)],
            SH.MinCountConstraintComponent,
        ),
        "missing_synthetic_flag": (
            [],
            [],
            [(EX.D01, EX.isSynthetic, None)],
            SH.MinCountConstraintComponent,
        ),
    }
    passed = {}
    for name, (replacements, additions, removals, component) in cases.items():
        graph = new_graph() + demo
        for triple in replacements:
            graph.set(triple)
        for triple in additions:
            graph.add(triple)
        for triple in removals:
            graph.remove(triple)
        conforms, report, _ = validate(
            graph,
            shacl_graph=shapes,
            ont_graph=schema,
            inference="rdfs",
        )
        assert not conforms, name
        if component is not None:
            assert (None, SH.sourceConstraintComponent, component) in report, name
        passed[name] = "PASS"
    return passed


def check_shop_lifecycle():
    graph = new_graph()
    add_point(graph, EX.PilotBuilding, (30.2741, 59.9630), "synthetic")
    add_point(graph, EX.ShopA, (30.2741, 59.9650), "synthetic")
    add_point(graph, EX.ShopB, (30.2941, 59.9630), "synthetic")
    for shop in (EX.ShopA, EX.ShopB):
        graph.add((shop, RDF.type, EX.Supermarket))
    for number in range(1000):
        graph.add((EX[f"PilotHome{number}"], EX.inBuilding, EX.PilotBuilding))
    assert len(list(graph.query(NEAREST_SHOP))) == 1000
    graph.set((EX.ShopA_point, GEO.asWKT, point_wkt(30.3041, 59.9630)))
    assert not list(graph.query(NEAREST_SHOP))
    graph.set((EX.ShopA_point, GEO.asWKT, point_wkt(30.2741, 59.9650)))
    graph.remove((EX.ShopA, None, None))
    assert not list(graph.query(NEAREST_SHOP))
    add_point(graph, EX.ShopA, (30.2741, 59.9650), "synthetic")
    graph.add((EX.ShopA, RDF.type, EX.Supermarket))
    assert len(list(graph.query(NEAREST_SHOP))) == 1000


def check_walking_boundaries():
    snapshot = {
        "nodes": {
            "1": [30.0, 60.0],
            "2": [30.0, 60.001],
            "3": [30.0, 60.002],
            "4": [30.0, 60.003],
        },
        "ways": [[["1", "2", "3", "4"], "no"]],
        "blocked": [],
        "features": [
            {
                "type": "MetroEntrance",
                "point": [30.0, 60.001],
                "osm_id": "2",
                "usable": True,
            },
            {
                "type": "MetroEntrance",
                "point": [30.0, 60.002],
                "osm_id": "3",
                "usable": True,
            },
            {
                "type": "MetroEntrance",
                "point": [30.0, 60.003],
                "osm_id": "4",
                "usable": False,
            },
        ],
    }
    network = PedestrianNetwork(snapshot)
    network.edges["1"]["2"] = 840 * WALK_SPEED_M_S
    network.edges["2"]["3"] = 60 * WALK_SPEED_M_S
    origin = (30.0, 60.0)
    assert network.length(origin, (30.0, 60.001)) / WALK_SPEED_M_S <= 900
    assert isclose(network.length(origin, (30.0, 60.002)) / WALK_SPEED_M_S, 900)
    assert network.length(origin, (30.0, 60.003)) is None
    assert network.length((40.0, 60.0), (30.0, 60.001)) is None
    reversed_path = deepcopy(snapshot)
    reversed_path["ways"][0][1] = "-1"
    one_way = PedestrianNetwork(reversed_path)
    assert "1" in one_way.search((30.0, 60.002))
    assert "2" not in one_way.search(origin)
    longer = deepcopy(snapshot)
    longer_network = PedestrianNetwork(longer)
    longer_network.edges["1"]["2"] = 1080 * WALK_SPEED_M_S
    assert longer_network.length(origin, (30.0, 60.001)) / WALK_SPEED_M_S > 900


def listing_ids(rows):
    return {row["listing"].rsplit("#", 1)[-1] for row in rows}


def check_queries(answers, rows, demo, real, queries, network):
    assert len(answers) == 8
    expected = {
        "real_budget": {
            r["id"]
            for r in rows
            if r["term"] == "long_term" and Decimal(r["rent_rub_month"]) <= 40000
        },
        "real_space_floor": {
            r["id"]
            for r in rows
            if r["term"] == "long_term"
            and Decimal(r["area_m2"]) >= 40
            and int(r["rooms"]) >= 2
            and int(r["floor"]) < int(r["floors_total"])
        },
        "demo_missing_pets": {"D02", "D05", "D08", "D11"},
        "demo_pet_budget": {"D12"},
        "demo_shop_park": {"D04", "D05", "D06", "D07", "D09", "D10", "D11", "D12"},
    }
    for name, identifiers in expected.items():
        assert listing_ids(answers[name]) == identifiers, (name, answers[name])
    assert {
        row["listing"].rsplit("#", 1)[-1]: row["knownInitial"]
        for row in answers["real_initial_payment"]
    } == {
        r["id"]: str(
            sum(
                Decimal(r[f])
                for f in ("rent_rub_month", "deposit_rub", "commission_rub")
            )
        )
        for r in rows
        if r.get("deposit_rub") and r.get("commission_rub")
    }
    assert {
        row["listing"]: (row["observedOn"], row["sourceUrl"])
        for row in answers["real_sources"]
    } == {str(EX[row["id"]]): (row["observed_on"], row["source_url"]) for row in rows}

    walk = answers["real_metro_walk"]
    expected_walk = {}
    for item in rows:
        building = building_iri(item, False)
        geometry = real.value(building, GEO.hasGeometry)
        if geometry is None:
            continue
        origin = read_point(real.value(geometry, GEO.asWKT))
        for entrance in real.subjects(EX.usableEntrance, Literal(True)):
            station = real.value(entrance, EX.entranceOf)
            target = real.value(real.value(entrance, GEO.hasGeometry), GEO.asWKT)
            length = network.length(origin, read_point(target))
            if length is None:
                continue
            key = str(EX[item["id"]]), str(station)
            expected_walk[key] = min(
                expected_walk.get(key, float("inf")), length / WALK_SPEED_M_S
            )
    assert len(walk) == len(expected_walk)
    limit = float(real.value(EX.WalkingProfile, EX.maxMetroWalkSeconds))
    for r in walk:
        seconds = expected_walk[r["listing"], r["station"]]
        assert isclose(float(r["seconds"]), seconds)
        assert (seconds <= limit) == (r["withinLimit"] == "true")
    walk_query = (queries / "real_metro_walk.rq").read_text(encoding="utf-8")
    higher_limit = real + new_graph()
    higher_limit.set((EX.WalkingProfile, EX.maxMetroWalkSeconds, Literal(960)))
    for entry in higher_limit.query(walk_query):
        assert bool(entry.withinLimit) == (float(entry.seconds) <= 960)
    mixed = real + demo
    for path in queries.glob("real_*.rq"):
        result = [
            {str(name): str(value) for name, value in row.asdict().items()}
            for row in mixed.query(path.read_text(encoding="utf-8"))
        ]
        assert result == answers[path.stem], path.stem

    near_query = (queries / "demo_pet_budget.rq").read_text(encoding="utf-8")
    unfiltered = near_query.replace(" && ?distance <= ?limit", "")
    assert {str(row.listing) for row in demo.query(unfiltered)} == {
        str(EX.D06),
        str(EX.D09),
        str(EX.D12),
    }
    check_shop_lifecycle()
    check_walking_boundaries()
    return {
        "all_eight_answers": "PASS",
        "distance_filters_candidates": "PASS",
        "mixed_data_real_queries": "PASS",
        "shop_move_remove_reimport_1000_homes": "PASS",
        "walking_limit_and_missing_routes": "PASS",
    }
