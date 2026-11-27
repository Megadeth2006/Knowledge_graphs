"""RDF steps of the update: per-record SHACL and the graph to be published."""

import argparse
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import SH
from rdflib.query import ResultRow

import releases
from build_graph import build_observed_bundle, materialize, shacl_report
from ontology_schema import EX, build_schema
from paths import CQ_QUERIES, SHAPES
from rental_data import build_observed, read_snapshot
from routing import PedestrianNetwork
from sources import load_all_slices
from update_pipeline import BuildOutcome


def short(value) -> str:
    if value is None:
        return ""
    text = str(value)
    for prefix in (str(EX), str(SH), "http://www.opengis.net/ont/geosparql#"):
        text = text.removeprefix(prefix)
    return text


def describe_report(report: Graph) -> list[str]:
    """One line per violation: rule, node, property and message."""
    lines = []
    for result in report.objects(None, SH.result):
        fields = {
            "правило": report.value(result, SH.sourceShape),
            "ограничение": report.value(result, SH.sourceConstraintComponent),
            "узел": report.value(result, SH.focusNode),
            "свойство": report.value(result, SH.resultPath),
            "значение": report.value(result, SH.value),
            "сообщение": report.value(result, SH.resultMessage),
        }
        shape = fields["правило"]
        if not isinstance(shape, URIRef):
            # Property shapes are blank nodes; name the node shape that holds them.
            owner = (
                report.value(None, SH.property, shape) if shape is not None else None
            )
            fields["правило"] = owner or shape
        lines.append(
            "; ".join(
                f"{name}: {short(value)}"
                for name, value in fields.items()
                if value is not None
            )
        )
    return sorted(lines)


def record_checker():
    """Validate one record alone before it may enter the new slice."""
    schema = build_schema()
    shapes = Graph().parse(SHAPES)

    def check(row: dict[str, str]) -> list[str]:
        try:
            graph = build_observed([row])
        except (ValueError, ArithmeticError) as error:
            return [f"запись не преобразуется в RDF: {error}"]
        conforms, report, _text = shacl_report(graph, shapes, schema, "rdfs")
        return [] if conforms else describe_report(report)

    return check


def build_published_graph(
    rows: list[dict[str, str]], target: Path, check_routes: bool = True
) -> BuildOutcome:
    """Build schema + listings + OSM pilot, validate and write to target."""
    snapshot = read_snapshot()
    bundle = build_observed_bundle(rows, snapshot)
    shapes = Graph().parse(SHAPES)
    conforms, report, text = shacl_report(
        bundle.combined, shapes, bundle.schema, "rdfs"
    )
    if not conforms:
        raise ValueError("SHACL: " + (" | ".join(describe_report(report)) or text))
    full = materialize(bundle.schema, bundle.combined)
    conforms, report, text = shacl_report(full, shapes, bundle.schema, "none")
    if not conforms:
        raise ValueError(
            "SHACL после OWL RL: " + (" | ".join(describe_report(report)) or text)
        )
    if check_routes:
        network = PedestrianNetwork(snapshot)
        network.register(EX)
        query = (CQ_QUERIES / "real_metro_walk.rq").read_text(encoding="utf-8")
        for row in full.query(query):
            if not isinstance(row, ResultRow):
                raise TypeError("Expected SELECT rows from the walk query")
    published = bundle.schema + bundle.combined
    target.parent.mkdir(parents=True, exist_ok=True)
    published.serialize(target, format="xml")
    return BuildOutcome(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # Publication happens only through update_pipeline.py; this writes a check copy.
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    outcome = build_published_graph(
        load_all_slices(releases.sources_root()), args.output
    )
    print("Граф проверен и записан:", outcome.staged_graph)


if __name__ == "__main__":
    main()
