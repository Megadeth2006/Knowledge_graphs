"""Ecoton parser on a hand-written page with the catalogue's CSS classes."""

import unittest
from datetime import date

from helpers import ROOT

from import_ecoton import parse_catalog
from sources import SourceError

# Test markup only; field values are invented, the coordinates lie on the island.
CARD = """
<section class="search-result_items_object">
  <a class="search-result_items_object_name_link" data-lng="{lon}" data-lat="59.9577">
    {rooms} ккв Тестовая ул., 1</a>
  <span class="search-result_items_object_name_add-favourites" data-objectnumber="{id}"></span>
  <a class="search-result_items_object_name_newtab" href="/detal-object.html?itemid={id}"></a>
  <div class="search-result_items_object_squares">общая: 45,5</div>
  <div class="search-result_items_object_storey">3/7</div>
  <div class="ps-price-wrap"><span>65 000</span></div>
  <div class="search-result_items_object_comments">Без животных.</div>
</section>
"""
PAGE = """<html><body><select id="frenttype"><option value="{kind}" selected>x</option></select>
{cards}</body></html>"""


def page(cards, kind="long"):
    return PAGE.format(kind=kind, cards="".join(cards)).encode()


class ParserTest(unittest.TestCase):
    def test_complete_page(self):
        response = parse_catalog(
            page([CARD.format(lon="30.2971", rooms=2, id="11")]), date(2026, 11, 27)
        )
        # A parsed first page is not proof that missing cards left the catalogue.
        self.assertFalse(response.complete)
        self.assertEqual(response.errors, [])
        row = response.rows[0]
        self.assertEqual(
            (row["id"], row["rent_rub_month"], row["area_m2"]), ("E11", "65000", "45.5")
        )
        self.assertEqual(
            (row["floor"], row["floors_total"], row["pets_allowed"]),
            ("3", "7", "false"),
        )
        self.assertEqual(row["observed_on"], "2026-11-27")

    def test_broken_card_makes_response_incomplete(self):
        broken = CARD.format(lon="30.2971", rooms=1, id="12").replace("общая: 45,5", "")
        response = parse_catalog(
            page([CARD.format(lon="30.2971", rooms=2, id="11"), broken]),
            date(2026, 11, 27),
        )
        self.assertEqual([r["id"] for r in response.rows], ["E11"])
        self.assertIn("Missing total area", response.errors[0])

    def test_outside_island_is_skipped(self):
        response = parse_catalog(
            page(
                [
                    CARD.format(lon="30.2971", rooms=2, id="11"),
                    CARD.format(lon="30.4000", rooms=2, id="13"),
                ]
            ),
            date(2026, 11, 27),
        )
        self.assertEqual(len(response.rows), 1)

    def test_wrong_page_is_a_source_error(self):
        with self.assertRaises(SourceError):
            parse_catalog(page([], kind="short"), date(2026, 11, 27))
        with self.assertRaises(SourceError):
            parse_catalog(page([]), date(2026, 11, 27))


if __name__ == "__main__":
    unittest.main()


class CommandLineTest(unittest.TestCase):
    def test_cli_refuses_to_overwrite_published_slice(self):
        import subprocess
        import sys

        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "src" / "import_ecoton.py"),
                "--html",
                "x.html",
                "--date",
                "2026-11-27",
                "--output",
                str(ROOT / "data" / "sources" / "ecoton" / "slice.csv"),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("update_pipeline.py", result.stderr)
