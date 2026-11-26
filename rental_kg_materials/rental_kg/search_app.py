"""Offline apartment search and executable classroom examples."""

import argparse
import gzip
import json
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from pyshacl import validate
from rdflib import Graph, Literal
from rdflib.namespace import RDFS, SH

from ontology_schema import EX
from routing import PedestrianNetwork

ROOT = Path(__file__).resolve().parent
EXAMPLES = {
    "budget": ("Бюджет по всем наблюдениям", ROOT / "queries/real_budget.rq"),
    "shop": ("Ближайший супермаркет на реальных данных", ROOT / "web/shop.rq"),
    "walk": ("Маршруты до станций", ROOT / "queries/real_metro_walk.rq"),
    "error": ("Ошибка в учебной карточке", None),
}


def safe(value):
    return escape(str(value), quote=True)


def template(title, body):
    return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe(title)} · Граф аренды</title>
<link href="/style.css" rel="stylesheet"><header><div><strong>Граф аренды · Санкт-Петербург</strong>
<nav><a href="/">Поиск</a><a href="/examples">Запросы и SHACL</a></nav></div></header>
<main><h1>{safe(title)}</h1>{body}<footer>Геоданные © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a></footer></main></html>"""


def duration(seconds):
    minutes, rest = divmod(round(float(seconds)), 60)
    return f"{minutes} мин {rest:02} с"


class Catalog:
    def __init__(self):
        self.graph = Graph().parse(ROOT / "observed_with_ontology.owl")
        with gzip.open(
            ROOT / "data/osm_pilot.json.gz", "rt", encoding="utf-8"
        ) as source:
            network = PedestrianNetwork(json.load(source))
        network.register(EX)
        # The walk query orders each listing's stations by shortest time.
        for row in self.graph.query(EXAMPLES["walk"][1].read_text(encoding="utf-8")):
            if (row.listing, EX.nearestWalkSeconds, None) in self.graph:
                continue
            self.graph.add((row.listing, EX.nearestWalkSeconds, row.seconds))
            self.graph.add(
                (
                    row.listing,
                    EX.nearestStationLabel,
                    self.graph.value(row.station, RDFS.label),
                )
            )
        self.search_query = (ROOT / "web/search.rq").read_text(encoding="utf-8")

    def search(self, params):
        budget = int(params.get("budget", "220000"))
        minutes = params.get("minutes", "8")
        pets = params.get("pets", "any")
        limit = int(minutes) if minutes else 0
        if (
            not 0 <= budget <= 100000000
            or not 0 <= limit <= 1440
            or pets not in {"any", "yes"}
        ):
            raise ValueError("Проверьте бюджет, время и условие о животных.")
        bindings = {
            "budget": Literal(budget),
            "timeLimit": Literal(limit * 60),
            "noLimit": Literal(not minutes),
            "needPets": Literal(pets == "yes"),
        }
        rows = list(self.graph.query(self.search_query, initBindings=bindings))
        matches, excluded = [], []
        for row in rows:
            reasons = []
            if not row.priceOK.toPython():
                reasons.append("Цена выше бюджета")
            if not row.petsOK.toPython():
                reasons.append("Разрешение на животных не подтверждено")
            if not row.walkOK.toPython():
                reasons.append(
                    "Время пути неизвестно"
                    if row.seconds is None
                    else "Путь дольше заданного"
                )
            pet_text = (
                "не указано"
                if row.pets is None
                else ("разрешены" if row.pets.toPython() else "запрещены")
            )
            route = (
                "Маршрут неизвестен"
                if row.seconds is None
                else f"{safe(row.station)} · {duration(row.seconds)} пешком"
            )
            price = f"{int(row.rent):,}".replace(",", " ")
            why = (
                f'<p class="reason">{". ".join(reasons)}.</p>'
                if reasons
                else "<p>Укладывается в заданные условия.</p>"
            )
            card = f"""<article><h3>{safe(row.address)}</h3><p class="price">{price} ₽/мес.</p>
<p>{route}<br>Животные · {pet_text}</p><p class="source"><a href="{safe(row.url)}">Экотон № {safe(row.identifier)}</a>
<br>Наблюдение {row.date.toPython():%d.%m.%Y}</p>{why}</article>"""
            (excluded if reasons else matches).append(card)
        body = f"""<p class="lead">Долгосрочная аренда на Петроградском острове. В срезе {len(rows)} предложений.
Время до метро рассчитано до ближайшего доступного входа при скорости 1,4 м/с.</p>
<form method="get"><label>Бюджет, ₽/мес.<input name="budget" type="number" min="0" max="100000000" required value="{budget}"></label>
<label>Животные<select name="pets"><option value="any">Любые условия</option>
<option value="yes" {"selected" if pets == "yes" else ""}>Нужно разрешение</option></select></label>
<label>До метро, мин<input name="minutes" type="number" min="0" max="1440" placeholder="Без лимита" value="{safe(minutes)}"></label>
<button>Найти</button></form><h2>Подходят · {len(matches)}</h2>
<div class="cards">{"".join(matches) or "<p>По заданным условиям подтверждённых вариантов нет.</p>"}</div>
<details><summary>Почему исключены · {len(excluded)}</summary><div class="cards rejected">{"".join(excluded)}</div></details>"""
        return template("Аренда на Петроградском острове", body)

    def example(self, choice):
        label, path = EXAMPLES[choice]
        if path:
            query = path.read_text(encoding="utf-8")
            result = self.graph.query(query)
            headers = "".join(f"<th>{safe(v)}</th>" for v in result.vars)
            rows = []
            for record in result:
                cells = []
                for v in record:
                    text = str(v).removeprefix(str(EX))
                    if isinstance(v, Literal) and isinstance(v.toPython(), float):
                        text = f"{v.toPython():.2f}"
                    elif (v, RDFS.label, None) in self.graph and str(v).startswith(
                        "https://www.openstreetmap.org/"
                    ):
                        text = self.graph.value(v, RDFS.label)
                    cells.append(f"<td>{safe(text)}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            output = f'<pre>{safe(query)}</pre><div class="tablewrap"><table><tr>{headers}</tr>{"".join(rows)}</table></div>'
        else:
            data = Graph().parse(ROOT / "demo_with_ontology.owl")
            data.remove((EX.D01_source, EX.sourceUrl, None))
            conforms, report, _ = validate(
                data, shacl_graph=str(ROOT / "shapes.ttl"), inference="rdfs"
            )
            findings = [
                f"{report.value(r, SH.focusNode)} · {report.value(r, SH.resultPath)} · {report.value(r, SH.sourceConstraintComponent)}"
                for r in report.objects(None, SH.result)
            ]
            output = f"""<p>В копии учебного графа удалена ссылка на источник D01. Для каждого источника форма требует хотя бы одно значение sourceUrl.</p>
<pre>Conforms: {conforms}\n{safe(chr(10).join(findings))}</pre>"""
        options = "".join(
            f'<option value="{key}" {"selected" if key == choice else ""}>{safe(name)}</option>'
            for key, (name, _) in EXAMPLES.items()
        )
        return template(
            "Запросы и SHACL",
            f'<form action="/examples"><label>Пример<select name="case">{options}</select></label><button>Выполнить</button></form><h2>{safe(label)}</h2>{output}',
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    catalog = Catalog()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            target = urlsplit(self.path)
            params = {
                k: v[-1]
                for k, v in parse_qs(target.query, keep_blank_values=True).items()
            }
            status, content_type = 200, "text/html"
            try:
                if target.path == "/":
                    body = catalog.search(params)
                elif (
                    target.path == "/examples"
                    and params.get("case", "budget") in EXAMPLES
                ):
                    body = catalog.example(params.get("case", "budget"))
                elif target.path == "/style.css":
                    content_type, body = (
                        "text/css",
                        (ROOT / "web/style.css").read_text(encoding="utf-8"),
                    )
                else:
                    status, body = 404, template("Страница не найдена", "")
            except ValueError:
                status, body = (
                    400,
                    template(
                        "Некорректные параметры",
                        '<p><a href="/">Вернуться к поиску</a></p>',
                    ),
                )
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    with HTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
