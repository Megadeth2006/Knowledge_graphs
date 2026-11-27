"""Site filters, explicit unknowns and duplicate groups without RDF."""

import unittest
from datetime import date
from decimal import Decimal
from typing import Any

from helpers import ROOT  # noqa: F401

from listing_view import (
    Nearby,
    Offer,
    SearchFilters,
    exclusion_reasons,
    group_card,
    group_offers,
)

# Walking seconds of the five November cards as measured by search_app.py.
CARDS = [
    ("E129891108", 59000, 430, None),
    ("E132735378", 200000, 443, None),
    ("E64293061", 70000, 491, "publisher_pin_approximate"),
    ("E146482554", 220000, 534, None),
    ("E128027065", 350000, 627, "publisher_pin_approximate"),
]


def offer(key, rent, seconds, kind=None, **changes):
    values: dict[str, Any] = dict(
        listing=key,
        home=key + "_home",
        address="Адрес " + key,
        rent=Decimal(rent),
        observed_on=date(2026, 11, 26),
        url=f"https://ecoton.spb.ru/detal-object.html?itemid={key[1:]}",
        identifier=key[1:],
        area=Decimal(50),
        rooms=2,
        floor=2,
        floors_total=5,
        geometry_kind=kind,
        metro=Nearby("Станция", seconds),
        shop=Nearby("Магазин", 150),
        park=Nearby("Сквер", 300),
        park_point_kind="bbox_centre",
    )
    values.update(changes)
    return Offer(**values)


def matching(offers, **params):
    filters = SearchFilters.from_params({k: str(v) for k, v in params.items()})
    return sum(group.matches for group in group_offers(offers, filters))


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.offers = [offer(*card) for card in CARDS]

    def test_budget_and_walk_scenario(self):
        self.assertEqual(matching(self.offers, budget=220000, minutes=8), 2)
        self.assertEqual(matching(self.offers, budget=220000, minutes=15), 4)
        self.assertEqual(matching(self.offers, budget=400000, minutes=15), 5)
        self.assertEqual(
            matching(self.offers, budget=400000, minutes=15, pets="yes"), 0
        )

    def test_unknown_values_fail_active_filters_with_reason(self):
        unknown = offer(
            "E1",
            50000,
            300,
            area=None,
            rooms=None,
            floor=None,
            metro=None,
            shop=None,
            park=None,
        )
        filters = SearchFilters.from_params(
            {
                "area_min": "30",
                "rooms_min": "1",
                "floor_min": "2",
                "shop_m": "500",
                "park_m": "500",
            }
        )
        reasons = exclusion_reasons(unknown, filters)
        for expected in (
            "Время пешком до метро неизвестно",
            "Площадь не указана",
            "Число комнат не указано",
            "Этаж не указан",
            "Расстояние до супермаркета неизвестно",
            "Расстояние до парка неизвестно",
        ):
            self.assertIn(expected, reasons)
        self.assertEqual(
            exclusion_reasons(unknown, SearchFilters.from_params({"minutes": ""})), []
        )

    def test_boundaries(self):
        card = offer("E1", 50000, 900)
        self.assertEqual(
            matching([card], minutes=15, area_min=50, shop_m=150, park_m=300), 1
        )
        self.assertEqual(matching([card], minutes=14), 0)
        self.assertEqual(matching([card], floor_min=3), 0)
        self.assertEqual(matching([card], floor_max=1), 0)

    def test_invalid_parameters(self):
        for params in ({"budget": "x"}, {"area_min": "-1"}, {"pets": "maybe"}):
            with self.assertRaises(ValueError):
                SearchFilters.from_params(params)


class CardTest(unittest.TestCase):
    def test_distance_kinds_and_unknowns_are_named(self):
        html = group_card(
            group_offers(
                [offer("E64293061", 70000, 491, "publisher_pin_approximate")],
                SearchFilters(),
            )[0]
        )
        self.assertIn("пешком по сети OSM", html)
        self.assertIn("по прямой (геодезически)", html)
        self.assertIn("центр рамки контура", html)
        self.assertIn("приблизительно по метке объявления", html)
        unknown = offer(
            "E1",
            1,
            1,
            area=None,
            rooms=None,
            floor=None,
            metro=None,
            shop=None,
            park=None,
        )
        html = group_card(group_offers([unknown], SearchFilters())[0])
        for text in (
            "площадь не указана",
            "комнат не указано",
            "этаж не указан",
            "маршрут по сети OSM не найден",
        ):
            self.assertIn(text, html)

    def test_group_keeps_separate_prices_and_sources(self):
        first = offer("E1", 59000, 430)
        second = offer(
            "A1",
            61000,
            430,
            home="E1_home",
            url="https://www.avito.ru/sankt-peterburg/kvartiry/test-1",
        )
        groups = group_offers(
            [first, second], SearchFilters.from_params({"budget": "60000"})
        )
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0].matches)
        html = group_card(groups[0])
        self.assertIn("Одна квартира в 2 карточках", html)
        self.assertIn("59 000", html)
        self.assertIn("61 000", html)
        self.assertIn("Экотон", html)
        self.assertIn("Авито", html)
        self.assertIn("Цена выше бюджета", html)


if __name__ == "__main__":
    unittest.main()
