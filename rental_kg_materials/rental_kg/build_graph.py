"""Rebuild the rental knowledge graphs and verify the published examples."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from owlrl import DeductiveClosure, OWLRL_Semantics
from pyshacl import validate
from rdflib import Graph
from rdflib.namespace import OWL, RDF

from graph_checks import check_graphs, check_queries, negative_checks
from ontology_schema import EX, build_schema, new_graph
from rental_data import build_demo, build_observed, build_osm_pilot, read_csv
from routing import PedestrianNetwork

ROOT = Path(__file__).resolve().parent


def materialize(schema, data):
    full = schema + data
    DeductiveClosure(OWLRL_Semantics).expand(full)
    return full


def validate_graph(graph, shapes, schema, inference):
    conforms, _report, description = validate(
        graph,
        shacl_graph=shapes,
        ont_graph=schema,
        inference=inference,
    )
    if not conforms:
        raise ValueError(description)
    return description


def query_answers(real, demo):
    answers = {}
    for path in sorted((ROOT / "queries").glob("*.rq")):
        graph = real if path.stem.startswith("real_") else demo
        answers[path.stem] = [
            {str(name): str(value) for name, value in row.asdict().items()}
            for row in graph.query(path.read_text(encoding="utf-8"))
        ]
    return answers


def save_artifacts(graphs, texts):
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=ROOT) as directory:
        staging = Path(directory)
        for name, graph in graphs.items():
            rdf_format = "xml" if name.endswith(".owl") else "turtle"
            graph.serialize(staging / Path(name).name, format=rdf_format)
        for name, content in texts.items():
            (staging / Path(name).name).write_text(
                content, encoding="utf-8", newline="\n"
            )
        for name in (*graphs, *texts):
            (staging / Path(name).name).replace(ROOT / name)


def main():
    data = ROOT / "data"
    observed_rows = read_csv(data / "observed_listings.csv")
    observed_rows += read_csv(data / "island_listings.csv")
    if len({row["id"] for row in observed_rows}) != len(observed_rows):
        raise ValueError("Duplicate internal listing identifier")
    demo_rows = read_csv(data / "demo_listings.csv")
    schema = build_schema()
    observed = build_observed(observed_rows)
    demo = build_demo(demo_rows, read_csv(data / "demo_poi.csv"))
    pilot, snapshot = build_osm_pilot(data, observed_rows)
    combined_observed = observed + pilot
    full_observed = materialize(schema, combined_observed)
    full_demo = materialize(schema, demo)
    network = PedestrianNetwork(snapshot)
    network.register(EX)

    shapes = Graph().parse(ROOT / "shapes.ttl")
    report = {}
    validation_texts = {}
    for name, graph in (
        ("observed", observed),
        ("demo", demo),
        ("osm_pilot", combined_observed),
    ):
        validation_texts[f"results/{name}_shacl.txt"] = validate_graph(
            graph,
            shapes,
            schema,
            "rdfs",
        )
        report[name] = {"triples": len(graph), "shacl_conforms": True}
    for graph in (full_demo, full_observed):
        validate_graph(graph, shapes, schema, "none")

    inferred = new_graph()
    for triple in full_demo - schema - demo:
        inferred.add(triple)

    report["reasoning"] = check_graphs(
        schema, demo, full_demo, full_observed, observed_rows
    )
    report["negative_validation_tests"] = negative_checks(demo, schema, shapes)
    report["classes"] = len(set(schema.subjects(RDF.type, OWL.Class)))
    report["object_properties"] = len(
        set(schema.subjects(RDF.type, OWL.ObjectProperty))
    )
    report["datatype_properties"] = len(
        set(schema.subjects(RDF.type, OWL.DatatypeProperty))
    )
    report["osm"] = {
        "features": len(snapshot["features"]),
        "route_nodes": len(snapshot["nodes"]),
        "route_ways": len(snapshot["ways"]),
        "extracted_on": snapshot["extracted_on"],
    }
    answers = query_answers(full_observed, full_demo)
    report["query_checks"] = check_queries(
        answers,
        observed_rows,
        full_demo,
        full_observed,
        ROOT / "queries",
        network,
    )

    save_artifacts(
        {
            "ontology.ttl": schema,
            "ontology.owl": schema,
            "demo.ttl": demo,
            "observed.ttl": observed,
            "osm_pilot.ttl": pilot,
            "demo_with_ontology.owl": schema + demo,
            "observed_with_ontology.owl": schema + combined_observed,
            "results/inferred.ttl": inferred,
        },
        {
            **validation_texts,
            "results/summary.json": json.dumps(report, ensure_ascii=False, indent=2)
            + "\n",
            "results/query_results.json": json.dumps(
                answers, ensure_ascii=False, indent=2
            )
            + "\n",
        },
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
