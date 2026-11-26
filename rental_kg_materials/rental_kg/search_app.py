"""Offline apartment search and executable classroom examples."""

import argparse
import gzip
import json
from collections.abc import Iterator
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socket import socket
from string import Template
from urllib.parse import parse_qsl, urlsplit

from pyshacl import validate
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS, SH
from rdflib.query import Result, ResultRow
from rdflib.term import Identifier

from ontology_schema import EX
from routing import PedestrianNetwork

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
WALK_QUERY = ROOT / "queries/real_metro_walk.rq"
EXAMPLES = {
    "budget": ("Бюджет по всем наблюдениям", ROOT / "queries/real_budget.rq"),
    "shop": ("Ближайший супермаркет на реальных данных", WEB / "shop.rq"),
    "walk": ("Маршруты до станций", WALK_QUERY),
    "error": ("Ошибка в учебной карточке", None),
}


def escape_html(value: str | Identifier | None) -> str:
    return escape(str(value), quote=True)


def render(name: str, **values: str | int) -> str:
    # Escape data at the call site. Some placeholders contain rendered HTML.
    source = (WEB / "templates" / name).read_text(encoding="utf-8")
    return Template(source).substitute(values)


def page(title: str, body: str = "") -> str:
    return render("page.html", title=escape_html(title), body=body)


def select_rows(result: Result) -> Iterator[ResultRow]:
    for row in result:
        if not isinstance(row, ResultRow):
            raise TypeError("Expected a SPARQL SELECT row")
        yield row


def literal(row: ResultRow, name: str) -> Literal:
    value = row.get(name)
    if not isinstance(value, Literal):
        raise TypeError(f"Expected a literal in ?{name}")
    return value


@dataclass(frozen=True)
class SearchFilters:
    budget: int
    minutes: str
    require_pets: bool
    time_limit_seconds: int

    @classmethod
    def from_params(cls, params: dict[str, str]) -> "SearchFilters":
        budget = int(params.get("budget", "220000"))
        minutes = params.get("minutes", "8")
        limit = int(minutes) if minutes else 0
        pets = params.get("pets", "any")
        if not 0 <= budget <= 100_000_000 or not 0 <= limit <= 1440:
            raise ValueError("Проверьте бюджет и время.")
        if pets not in {"any", "yes"}:
            raise ValueError("Проверьте условие о животных.")
        return cls(budget, minutes, pets == "yes", limit * 60)

    def bindings(self) -> dict[str, Literal]:
        return {
            "budget": Literal(self.budget),
            "timeLimit": Literal(self.time_limit_seconds),
            "noLimit": Literal(not self.minutes),
            "needPets": Literal(self.require_pets),
        }


def exclusion_reasons(row: ResultRow) -> list[str]:
    reasons = []
    if not literal(row, "priceOK").toPython():
        reasons.append("Цена выше бюджета")
    if not literal(row, "petsOK").toPython():
        reasons.append("Разрешение на животных не подтверждено")
    if not literal(row, "walkOK").toPython():
        if row.get("seconds") is None:
            reasons.append("Время пути неизвестно")
        else:
            reasons.append("Путь дольше заданного")
    return reasons


def walking_description(row: ResultRow) -> str:
    seconds = row.get("seconds")
    if not isinstance(seconds, Literal):
        return "Маршрут неизвестен"

    minutes, remainder = divmod(round(float(seconds)), 60)
    station = escape_html(row.get("station"))
    route = f"{station} · {minutes} мин {remainder:02} с пешком"
    if str(row.get("geometryKind")) == "publisher_pin_approximate":
        route += "<br>Приблизительно по метке объявления; подъезд уточняется"
    return route


def listing_card(row: ResultRow, reasons: list[str]) -> str:
    pets = row.get("pets")
    pet_text = "не указано"
    if isinstance(pets, Literal):
        pet_text = "разрешены" if pets.toPython() else "запрещены"

    price = f"{int(literal(row, 'rent')):,}".replace(",", " ")
    reason = "<p>Укладывается в заданные условия.</p>"
    if reasons:
        reason_text = escape_html(". ".join(reasons))
        reason = f'<p class="reason">{reason_text}.</p>'
    observed_on = literal(row, "date").toPython()
    return render(
        "listing.html",
        address=escape_html(row.get("address")),
        price=price,
        route=walking_description(row),
        pets=pet_text,
        url=escape_html(row.get("url")),
        identifier=escape_html(row.get("identifier")),
        date=f"{observed_on:%d.%m.%Y}",
        reason=reason,
    )


class Catalog:
    def __init__(self) -> None:
        self.graph = Graph().parse(ROOT / "observed_with_ontology.owl")
        with gzip.open(ROOT / "data/osm_pilot.json.gz", "rt", encoding="utf-8") as file:
            network = PedestrianNetwork(json.load(file))
        network.register(EX)
        self.add_nearest_stations()
        self.search_query = (WEB / "search.rq").read_text(encoding="utf-8")

    def add_nearest_stations(self) -> None:
        # The walk query orders each listing's stations by shortest time.
        result = self.graph.query(WALK_QUERY.read_text(encoding="utf-8"))
        for row in select_rows(result):
            listing = row.get("listing")
            station = row.get("station")
            if not isinstance(listing, URIRef) or not isinstance(station, URIRef):
                raise TypeError("Expected listing and station IRIs")
            if (listing, EX.nearestWalkSeconds, None) in self.graph:
                continue
            label = self.graph.value(station, RDFS.label)
            if not isinstance(label, Literal):
                raise TypeError(f"Expected a literal label for station {station}")
            self.graph.add((listing, EX.nearestWalkSeconds, literal(row, "seconds")))
            self.graph.add((listing, EX.nearestStationLabel, label))

    def search(self, filters: SearchFilters) -> str:
        result = self.graph.query(self.search_query, initBindings=filters.bindings())
        matches = []
        excluded = []
        for row in select_rows(result):
            reasons = exclusion_reasons(row)
            card = listing_card(row, reasons)
            (excluded if reasons else matches).append(card)

        return page(
            "Аренда на Петроградском острове",
            render(
                "search.html",
                total=len(matches) + len(excluded),
                budget=filters.budget,
                minutes=escape_html(filters.minutes),
                pets_selected="selected" if filters.require_pets else "",
                match_count=len(matches),
                excluded_count=len(excluded),
                matches="".join(matches)
                or "<p>По заданным условиям подтверждённых вариантов нет.</p>",
                excluded="".join(excluded),
            ),
        )

    def query_cell(self, value: Identifier | None) -> str:
        if value is None:
            return ""
        if isinstance(value, Literal):
            parsed = value.toPython()
            if isinstance(parsed, float):
                return f"{parsed:.2f}"
        if isinstance(value, URIRef) and value.startswith(
            "https://www.openstreetmap.org/"
        ):
            label = self.graph.value(value, RDFS.label)
            if isinstance(label, Literal):
                return escape_html(label)
        return escape_html(str(value).removeprefix(str(EX)))

    def query_example(self, path: Path) -> str:
        query = path.read_text(encoding="utf-8")
        result = self.graph.query(query)
        columns = result.vars or []
        headers = "".join(f"<th>{escape_html(column)}</th>" for column in columns)
        rows = []
        for row in select_rows(result):
            cells = "".join(
                f"<td>{self.query_cell(row.get(str(column)))}</td>"
                for column in columns
            )
            rows.append(f"<tr>{cells}</tr>")
        return (
            f"<pre>{escape_html(query)}</pre>"
            f'<div class="table-wrap"><table><tr>{headers}</tr>'
            f"{''.join(rows)}</table></div>"
        )

    def example(self, choice: str) -> str:
        label, path = EXAMPLES[choice]
        options = []
        for key, (name, _) in EXAMPLES.items():
            selected = " selected" if key == choice else ""
            options.append(
                f'<option value="{key}"{selected}>{escape_html(name)}</option>'
            )
        return page(
            "Запросы и SHACL",
            render(
                "examples.html",
                options="".join(options),
                label=escape_html(label),
                output=self.query_example(path) if path else validation_example(),
            ),
        )


def validation_example() -> str:
    data = Graph().parse(ROOT / "demo_with_ontology.owl")
    data.remove((EX.D01_source, EX.sourceUrl, None))
    conforms, report, description = validate(
        data, shacl_graph=str(ROOT / "shapes.ttl"), inference="rdfs"
    )
    if not isinstance(report, Graph):
        raise TypeError(description)
    findings = []
    for result in report.objects(None, SH.result):
        fields = (SH.focusNode, SH.resultPath, SH.sourceConstraintComponent)
        findings.append(
            " · ".join(str(report.value(result, field)) for field in fields)
        )
    details = escape_html("\n".join(findings))
    return (
        "<p>В копии учебного графа удалена ссылка на источник D01. "
        "Для каждого источника форма требует хотя бы одно значение sourceUrl.</p>"
        f"<pre>Conforms: {conforms}\n{details}</pre>"
    )


class SearchHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        request: socket,
        client_address: tuple[str, int],
        server: HTTPServer,
        catalog: Catalog,
    ) -> None:
        self.catalog = catalog
        super().__init__(request, client_address, server)

    def handle_get(self) -> None:
        target = urlsplit(str(self.path))
        params = dict(parse_qsl(str(target.query), keep_blank_values=True))
        if target.path == "/":
            try:
                filters = SearchFilters.from_params(params)
            except ValueError:
                body = page(
                    "Некорректные параметры",
                    '<p><a href="/">Вернуться к поиску</a></p>',
                )
                self.send_page(HTTPStatus.BAD_REQUEST, body)
            else:
                self.send_page(HTTPStatus.OK, self.catalog.search(filters))
        elif target.path == "/examples" and params.get("case", "budget") in EXAMPLES:
            self.send_page(
                HTTPStatus.OK, self.catalog.example(params.get("case", "budget"))
            )
        elif target.path == "/style.css":
            styles = (WEB / "style.css").read_text(encoding="utf-8")
            self.send_page(HTTPStatus.OK, styles, "text/css")
        else:
            self.send_page(HTTPStatus.NOT_FOUND, page("Страница не найдена"))

    # BaseHTTPRequestHandler dispatches to this name for GET requests.
    do_GET = handle_get

    def send_page(
        self, status: HTTPStatus, body: str, content_type: str = "text/html"
    ) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    catalog = Catalog()

    def make_handler(
        request: socket, address: tuple[str, int], server: HTTPServer
    ) -> BaseHTTPRequestHandler:
        return SearchHandler(request, address, server, catalog)

    with HTTPServer(("127.0.0.1", args.port), make_handler) as http_server:
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        http_server.serve_forever()


if __name__ == "__main__":
    main()
