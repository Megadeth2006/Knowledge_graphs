"""Offline apartment search, update button and executable classroom examples."""

import argparse
import threading
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socket import socket
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pyshacl import validate
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import RDFS, SH
from rdflib.query import Result, ResultRow
from rdflib.term import Identifier

import releases
from graph_publish import build_published_graph
from listing_view import (
    Nearby,
    Offer,
    SearchFilters,
    group_card,
    group_offers,
    render,
    text,
)
from ontology_schema import EX
from paths import APP_QUERIES, CQ_QUERIES, DEMO_GRAPH, SHAPES, WEB
from rental_data import read_snapshot
from routing import PedestrianNetwork
from shacl_example import explain
from sources import PLATFORMS, platform_name, read_meta
from update_pipeline import BuildOutcome, UpdateManager, default_config

WALK_QUERY = CQ_QUERIES / "real_metro_walk.rq"
EXAMPLES = {
    "budget": ("Бюджет по всем наблюдениям", CQ_QUERIES / "real_budget.rq"),
    "shop": (
        "Ближайший супермаркет на реальных данных",
        APP_QUERIES / "nearest_shop.rq",
    ),
    "walk": ("Маршруты до станций", WALK_QUERY),
    "error": ("Ошибка в учебной карточке", None),
    "record": ("Запись отклонена до публикации", None),
}
STATE_NAMES = {
    "idle": "обновление не запускалось",
    "running": "идёт обновление",
    "succeeded": "последнее обновление успешно",
    "failed": "последнее обновление не удалось, опубликован прежний срез",
    "unchanged": "новых данных не получено, срез не менялся",
}
SOURCE_STATES = {
    "parsed": "страница разобрана; полнота среза не подтверждена",
    "parsed_with_errors": "страница разобрана частично",
    "complete": "полный ответ источника",
    "partial_file": "подготовленный файл без признака полноты",
    "failed": "сбой, сохранён прежний срез",
    "skipped": "пропущен",
    "no_new_data": "новых данных нет",
    "not_connected": "не подключён",
}
CARD_STATES = {
    "new": "новая карточка",
    "repeat_observation": "повторное наблюдение той же карточки",
    "kept_previous": "оставлено прежнее наблюдение",
    "left_slice": "вышла из среза (снятие не подтверждено)",
    "not_seen": "не встретилась в прочитанной части; прежнее наблюдение сохранено",
    "older_observation": "более раннее наблюдение; срез не изменён",
    "withdrawn": "снята по сообщению источника",
}


def page(title: str, body: str = "") -> str:
    return render("page.html", title=text(title), body=body)


def select_rows(result: Result) -> Iterator[ResultRow]:
    for row in result:
        if not isinstance(row, ResultRow):
            raise TypeError("Expected a SPARQL SELECT row")
        yield row


def python_value(row: ResultRow, name: str) -> Any:
    value = row.get(name)
    return value.toPython() if isinstance(value, Literal) else value


def optional_int(row: ResultRow, name: str) -> int | None:
    value = python_value(row, name)
    return None if value is None else int(value)


class Catalog:
    """Read-only view of one published graph; replaced whole after an update."""

    def __init__(self, graph_path: Path | None = None) -> None:
        # After a restart the site reads the version named by the pointer.
        self.graph = Graph().parse(graph_path or releases.graph_path())
        network = PedestrianNetwork(read_snapshot())
        network.register(EX)
        self.metro = self.nearest_stations()
        self.shops = self.nearest_amenities(EX.Supermarket)
        self.parks = self.nearest_amenities(EX.Park)
        self.offers = self.read_offers()

    def nearest_stations(self) -> dict[str, Nearby]:
        # The walk query orders each listing's stations by shortest time.
        result: dict[str, Nearby] = {}
        for row in select_rows(
            self.graph.query(WALK_QUERY.read_text(encoding="utf-8"))
        ):
            listing, station = str(row.get("listing")), row.get("station")
            seconds = float(python_value(row, "seconds"))
            if listing in result and result[listing].value <= seconds:
                continue
            label = (
                self.graph.value(station, RDFS.label)
                if isinstance(station, URIRef)
                else None
            )
            result[listing] = Nearby(str(label or station), seconds)
        return result

    def nearest_amenities(self, kind: URIRef) -> dict[str, tuple[Nearby, str | None]]:
        query = (APP_QUERIES / "amenity_distances.rq").read_text(encoding="utf-8")
        result: dict[str, tuple[Nearby, str | None]] = {}
        for row in select_rows(self.graph.query(query, initBindings={"kind": kind})):
            metres = python_value(row, "metres")
            if metres is None:
                continue
            building = str(row.get("building"))
            if building in result and result[building][0].value <= float(metres):
                continue
            label = python_value(row, "label") or "без названия в OSM"
            result[building] = (
                Nearby(str(label), float(metres)),
                python_value(row, "pointKind"),
            )
        return result

    def read_offers(self) -> list[Offer]:
        query = (APP_QUERIES / "search.rq").read_text(encoding="utf-8")
        offers = []
        for row in select_rows(self.graph.query(query)):
            building = str(row.get("building"))
            shop = self.shops.get(building)
            park = self.parks.get(building)

            area = python_value(row, "area")
            observed = python_value(row, "date")
            offers.append(
                Offer(
                    listing=str(row.get("listing")),
                    home=str(row.get("home")),
                    address=str(row.get("address")),
                    rent=Decimal(str(python_value(row, "rent"))),
                    observed_on=observed
                    if isinstance(observed, date)
                    else date.fromisoformat(str(observed)),
                    url=str(row.get("url")),
                    identifier=str(row.get("identifier")),
                    pets=python_value(row, "pets"),
                    area=None if area is None else Decimal(str(area)),
                    rooms=optional_int(row, "rooms"),
                    floor=optional_int(row, "floor"),
                    floors_total=optional_int(row, "floorsTotal"),
                    geometry_kind=python_value(row, "geometryKind"),
                    metro=self.metro.get(str(row.get("listing"))),
                    shop=shop[0] if shop else None,
                    park=park[0] if park else None,
                    park_point_kind=park[1] if park else None,
                )
            )
        return offers

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
                return text(label)
        return text(str(value).removeprefix(str(EX)))

    def query_example(self, path: Path) -> str:
        query = path.read_text(encoding="utf-8")
        result = self.graph.query(query)
        columns = result.vars or []
        headers = "".join(f"<th>{text(column)}</th>" for column in columns)
        rows = []
        for row in select_rows(result):
            cells = "".join(
                f"<td>{self.query_cell(row.get(str(column)))}</td>"
                for column in columns
            )
            rows.append(f"<tr>{cells}</tr>")
        return (
            f"<pre>{text(query)}</pre>"
            f'<div class="table-wrap"><table><tr>{headers}</tr>'
            f"{''.join(rows)}</table></div>"
        )


def validation_example() -> str:
    data = Graph().parse(DEMO_GRAPH)
    data.remove((EX.D01_source, EX.sourceUrl, None))
    conforms, report, description = validate(
        data, shacl_graph=str(SHAPES), inference="rdfs"
    )
    if not isinstance(report, Graph):
        raise TypeError(description)
    findings = []
    for result in report.objects(None, SH.result):
        fields = (SH.focusNode, SH.resultPath, SH.sourceConstraintComponent)
        findings.append(
            " · ".join(str(report.value(result, field)) for field in fields)
        )
    details = text("\n".join(findings))
    return (
        "<p>В копии учебного графа удалена ссылка на источник D01. "
        "Для каждого источника форма требует хотя бы одно значение sourceUrl.</p>"
        f"<pre>Conforms: {conforms}\n{details}</pre>"
    )


def record_example() -> str:
    record, problems, after_fix = explain()
    url = text(record["source_url"])
    return (
        f"<p>Копия карточки {text(record['id'])} в памяти: этаж {text(record['floor'])} "
        f"при этажности {text(record['floors_total'])}. Перед публикацией конвейер "
        "проверяет каждую новую запись отдельно. Причина видна в отчёте SHACL и на "
        "странице «Обновление» вместе с источником.</p>"
        f"<pre>{text(chr(10).join(problems))}</pre>"
        f'<p>Источник: <a href="{url}">{url}</a>. После исправления этажа: '
        f"{'ошибок нет' if not after_fix else text(after_fix)}.</p>"
    )


def format_time(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")


def format_day(value: str | None) -> str:
    return date.fromisoformat(value).strftime("%d.%m.%Y") if value else "нет"


def slice_info() -> str:
    parts = []
    for platform in PLATFORMS:
        observed = read_meta(platform, releases.sources_root()).get(
            "last_success_observed_on"
        )
        if observed:
            parts.append(f"{platform_name(platform)} — {format_day(observed)}")
    return text("Срезы по источникам: " + "; ".join(parts))


def table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "<p>Нет.</p>"
    head = "".join(f"<th>{text(name)}</th>" for name in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f'<div class="table-wrap"><table><tr>{head}</tr>{body}</table></div>'


def update_page(status: dict[str, Any]) -> str:
    state = status.get("state", "idle")
    report = status.get("report") or {}
    last = status.get("last_success")
    last_html = "<p>Успешных обновлений через сайт ещё не было.</p>"
    if last:
        slices = "; ".join(
            f"{platform_name(key)} — {format_day(value)}"
            for key, value in (last.get("slices") or {}).items()
            if value
        )
        last_html = (
            f"<p>{format_time(last.get('finished_at'))}, версия "
            f"{text(last.get('version', '—'))}. {text(slices)}</p>"
        )
    current = (
        f"<p>Состояние: {text(STATE_NAMES.get(state, state))}. Шаг: {text(status.get('step', '—'))}. "
        f"Начато {format_time(status.get('started_at'))}, завершено {format_time(status.get('finished_at'))}.</p>"
    )
    if status.get("message"):
        current += f"<pre>{text(status['message'])}</pre>"
    sources = table(
        ["Источник", "Результат", "Пояснение", "Прежний срез", "Ошибки в ответе"],
        [
            [
                text(item.get("name")),
                text(SOURCE_STATES.get(item.get("state", ""), item.get("state"))),
                text(item.get("detail")),
                format_day(item.get("previous_observed_on")),
                text("; ".join(item.get("errors") or [])),
            ]
            for item in (report.get("sources") or {}).values()
        ],
    )
    rejected = table(
        ["Запись", "Источник", "Причина"],
        [
            [
                text(item["id"]),
                f'<a href="{text(item["source_url"])}">{text(item["source_url"])}</a>',
                "<br>".join(text(reason) for reason in item["reasons"]),
            ]
            for item in report.get("rejected") or []
        ],
    )
    cards = table(
        ["Площадка", "ID", "Статус", "Пояснение"],
        [
            [
                text(platform_name(item["platform"])),
                text(item["source_id"]),
                text(CARD_STATES.get(item["status"], item["status"])),
                text(item["detail"]),
            ]
            for item in report.get("cards") or []
        ],
    )
    groups = report.get("groups") or {}
    kinds = {"manual": "подтверждена вручную", "auto": "автоматическое совпадение"}
    matching = table(
        ["Квартира", "Основание", "Карточки"],
        [
            [
                text(home),
                text(kinds.get(group["source"], group["source"])),
                text(", ".join(group["ids"])),
            ]
            for home, group in groups.items()
            if len(group["ids"]) > 1
        ],
    ) + table(
        ["Кандидат", "Кандидат", "Почему не объединены"],
        [
            [text(item["left"]), text(item["right"]), text(item["reason"])]
            for item in report.get("candidates") or []
        ],
    )
    disabled = "disabled" if state == "running" else ""
    refresh = '<meta http-equiv="refresh" content="3">' if state == "running" else ""
    return page(
        "Обновление среза",
        refresh
        + render(
            "update.html",
            summary=text(
                "Кнопка запускает получение, нормализацию, сопоставление, проверку SHACL "
                "и сборку графа. Новый граф публикуется только после всех проверок; "
                "до этого поиск работает на прежнем срезе."
            ),
            update_disabled=disabled,
            last_success=last_html,
            current=current,
            sources=sources,
            rejected=rejected,
            cards=cards,
            matching=matching,
        ),
    )


class SearchService:
    """Holds the published catalog and swaps it after a successful update."""

    def __init__(self, offline: bool = False) -> None:
        self.catalog = Catalog()
        self.catalog_lock = threading.Lock()
        config = default_config(offline)
        config.build = self.build_and_load
        config.on_published = self.publish
        self.updates = UpdateManager(config)

    @staticmethod
    def build_and_load(rows: list[dict[str, str]], target: Path) -> BuildOutcome:
        outcome = build_published_graph(rows, target, check_routes=False)
        # Loading the staged graph runs routes and distances before publication.
        outcome.prepared = Catalog(outcome.staged_graph)
        return outcome

    def publish(self, outcome: BuildOutcome) -> None:
        with self.catalog_lock:
            self.catalog = outcome.prepared

    def search(self, filters: SearchFilters) -> str:
        catalog = self.catalog
        groups = group_offers(catalog.offers, filters)
        matches = [group_card(group) for group in groups if group.matches]
        excluded = [group_card(group) for group in groups if not group.matches]
        status = self.updates.status()
        state = status.get("state", "idle")
        values = {
            name: "" if value is None else str(value)
            for name, value in filters.limits.items()
        }
        return page(
            "Аренда на Петроградском острове",
            render(
                "search.html",
                total=len(catalog.offers),
                groups=len(groups),
                slice_info=slice_info(),
                update_disabled="disabled" if state == "running" else "",
                update_state=text(STATE_NAMES.get(state, state)),
                budget=filters.budget,
                pets_selected="selected" if filters.require_pets else "",
                match_count=len(matches),
                excluded_count=len(excluded),
                matches="".join(matches)
                or "<p>По заданным условиям подтверждённых вариантов нет.</p>",
                excluded="".join(excluded),
                **{name: text(value) for name, value in values.items()},
            ),
        )

    def example_output(self, choice: str, path: Path | None) -> str:
        if path is not None:
            return self.catalog.query_example(path)
        return record_example() if choice == "record" else validation_example()

    def example(self, choice: str) -> str:
        label, path = EXAMPLES[choice]
        options = []
        for key, (name, _) in EXAMPLES.items():
            selected = " selected" if key == choice else ""
            options.append(f'<option value="{key}"{selected}>{text(name)}</option>')
        return page(
            "Запросы и SHACL",
            render(
                "examples.html",
                options="".join(options),
                label=text(label),
                output=self.example_output(choice, path),
            ),
        )


class SearchHandler(BaseHTTPRequestHandler):
    def __init__(
        self,
        request: socket,
        client_address: tuple[str, int],
        server: HTTPServer,
        service: SearchService,
    ) -> None:
        self.service = service
        super().__init__(request, client_address, server)

    def handle_get(self) -> None:
        target = urlsplit(str(self.path))
        params = dict(parse_qsl(str(target.query), keep_blank_values=True))
        if target.path == "/":
            try:
                filters = SearchFilters.from_params(params)
            except ValueError as error:
                body = page(
                    "Некорректные параметры",
                    f'<p>{text(error)}</p><p><a href="/">Вернуться к поиску</a></p>',
                )
                self.send_page(HTTPStatus.BAD_REQUEST, body)
            else:
                self.send_page(HTTPStatus.OK, self.service.search(filters))
        elif target.path == "/examples" and params.get("case", "budget") in EXAMPLES:
            self.send_page(
                HTTPStatus.OK, self.service.example(params.get("case", "budget"))
            )
        elif target.path == "/update":
            self.send_page(HTTPStatus.OK, update_page(self.service.updates.status()))
        elif target.path == "/style.css":
            styles = (WEB / "static" / "style.css").read_text(encoding="utf-8")
            self.send_page(HTTPStatus.OK, styles, "text/css")
        else:
            self.send_page(HTTPStatus.NOT_FOUND, page("Страница не найдена"))

    def handle_post(self) -> None:
        if urlsplit(str(self.path)).path != "/update":
            self.send_page(HTTPStatus.NOT_FOUND, page("Страница не найдена"))
            return
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        # A second press while running only shows the current progress.
        self.service.updates.start()
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", "/update")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # BaseHTTPRequestHandler dispatches to these names.
    do_GET = handle_get
    do_POST = handle_post

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
    parser.add_argument(
        "--offline",
        action="store_true",
        help="update without the Ecoton network request",
    )
    args = parser.parse_args()
    service = SearchService(args.offline)

    def make_handler(
        request: socket, address: tuple[str, int], server: HTTPServer
    ) -> BaseHTTPRequestHandler:
        return SearchHandler(request, address, server, service)

    with HTTPServer(("127.0.0.1", args.port), make_handler) as http_server:
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        http_server.serve_forever()


if __name__ == "__main__":
    main()
