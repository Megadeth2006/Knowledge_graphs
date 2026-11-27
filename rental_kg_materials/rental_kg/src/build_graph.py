"""Rebuild the rental knowledge graphs and verify the published examples."""

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from owlrl import DeductiveClosure, OWLRL_Semantics
from pyshacl import validate
from rdflib import Graph
from rdflib.namespace import OWL, RDF
from rdflib.query import ResultRow

from graph_checks import check_graphs, check_queries, negative_checks
from ontology_schema import EX, build_schema, new_graph
from paths import CQ_QUERIES, ROOT, SHAPES
from rental_data import (
    build_demo,
    build_observed,
    build_osm_pilot,
    load_demo,
    read_snapshot,
)
from routing import PedestrianNetwork
from sources import load_all_slices


@dataclass
class ObservedBundle:
    schema: Graph
    observed: Graph
    pilot: Graph
    combined: Graph
    snapshot: dict


def materialize(schema, data):
    full = schema + data
    DeductiveClosure(OWLRL_Semantics).expand(full)
    return full


def shacl_report(graph, shapes, schema, inference):
    """Return conformance, the report graph and its text."""
    conforms, report, description = validate(
        graph,
        shacl_graph=shapes,
        ont_graph=schema,
        inference=inference,
    )
    if not isinstance(report, Graph):
        raise TypeError("pySHACL did not return a report graph")
    return bool(conforms), report, str(description)


def validate_graph(graph, shapes, schema, inference):
    conforms, _report, description = shacl_report(graph, shapes, schema, inference)
    if not conforms:
        raise ValueError(description)
    return description


def build_observed_bundle(rows, snapshot=None) -> ObservedBundle:
    """Build the real-data graph exactly as the search page loads it."""
    schema = build_schema()
    observed = build_observed(rows)
    pilot, snapshot = build_osm_pilot(rows, snapshot)
    return ObservedBundle(schema, observed, pilot, observed + pilot, snapshot)


def query_answers(real: Graph, demo: Graph) -> dict[str, list[dict[str, str]]]:
    answers = {}
    for path in sorted(CQ_QUERIES.glob("*.rq")):
        graph = real if path.stem.startswith("real_") else demo
        rows = []
        for row in graph.query(path.read_text(encoding="utf-8")):
            if not isinstance(row, ResultRow):
                raise TypeError(f"Expected SELECT rows from {path.name}")
            rows.append({name: str(value) for name, value in row.asdict().items()})
        answers[path.stem] = rows
    return answers


def save_artifacts(graphs, texts):
    """Write all outputs to a temporary folder first, then move them in place."""
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
            target = ROOT / name
            target.parent.mkdir(parents=True, exist_ok=True)
            (staging / Path(name).name).replace(target)


def build_all():
    """Build every graph and run all checks; nothing is written."""
    observed_rows = load_all_slices()
    demo_rows, demo_poi = load_demo()
    bundle = build_observed_bundle(observed_rows, read_snapshot())
    schema = bundle.schema
    demo = build_demo(demo_rows, demo_poi)
    full_observed = materialize(schema, bundle.combined)
    full_demo = materialize(schema, demo)
    network = PedestrianNetwork(bundle.snapshot)
    network.register(EX)

    shapes = Graph().parse(SHAPES)
    report = {}
    validation_texts = {}
    for name, graph in (
        ("observed", bundle.observed),
        ("demo", demo),
        ("osm_pilot", bundle.combined),
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
    snapshot = bundle.snapshot
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
        CQ_QUERIES,
        network,
    )

    graphs = {
        "ontology/ontology.ttl": schema,
        "graphs/demo_with_ontology.owl": schema + demo,
        "graphs/observed_with_ontology.owl": schema + bundle.combined,
        "results/inferred.ttl": inferred,
    }
    texts = {
        **validation_texts,
        "results/summary.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        "results/query_results.json": json.dumps(answers, ensure_ascii=False, indent=2)
        + "\n",
    }
    return report, answers, graphs, texts


def main():
    report, _answers, graphs, texts = build_all()
    save_artifacts(graphs, texts)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
