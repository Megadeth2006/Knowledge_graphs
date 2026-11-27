"""RDF build, SHACL, eight queries, routes, site catalog and a real update run."""

import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import HAS_RDF, ROOT, ecoton_row

import releases
from sources import SourceError, SourceResponse, read_rows, slice_path

AVITO_URL = "https://www.avito.ru/sankt-peterburg/kvartiry/test-1"


def same_answers(left, right):
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            same_answers(left[k], right[k]) for k in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(map(same_answers, left, right))
    try:
        return math.isclose(float(left), float(right), rel_tol=1e-9)
    except ValueError:
        return left == right


@unittest.skipUnless(HAS_RDF, "rdflib, pyshacl, owlrl and geographiclib are required")
class BuildTest(unittest.TestCase):
    def test_build_matches_published_results(self):
        from build_graph import build_all

        report, answers, _graphs, _texts = build_all()
        saved = json.loads(
            (ROOT / "results" / "query_results.json").read_text(encoding="utf-8")
        )
        self.assertTrue(same_answers(answers, saved))
        self.assertEqual(len(report["negative_validation_tests"]), 21)
        self.assertEqual(set(report["query_checks"].values()), {"PASS"})

    def test_shacl_names_rule_and_node(self):
        from shacl_example import explain

        record, problems, after_fix = explain()
        self.assertEqual(len(problems), 1)
        self.assertIn("DwellingShape", problems[0])
        self.assertIn(record["id"] + "_home", problems[0])
        self.assertIn("Этаж квартиры выше этажности здания", problems[0])
        self.assertEqual(after_fix, [])


@unittest.skipUnless(HAS_RDF, "rdflib, pyshacl, owlrl and geographiclib are required")
class SiteAndUpdateTest(unittest.TestCase):
    def test_current_catalog(self):
        from listing_view import SearchFilters, group_offers
        from search_app import Catalog

        catalog = Catalog()
        self.assertEqual(len(catalog.offers), 5)
        kinds = [offer.geometry_kind for offer in catalog.offers]
        self.assertEqual(kinds.count("publisher_pin_approximate"), 2)
        self.assertTrue(all(o.metro and o.shop and o.park for o in catalog.offers))
        counts = []
        for params in (
            {"budget": "220000", "minutes": "8"},
            {"budget": "220000", "minutes": "15"},
            {"budget": "400000", "minutes": "15"},
            {"budget": "400000", "minutes": "15", "pets": "yes"},
        ):
            groups = group_offers(catalog.offers, SearchFilters.from_params(params))
            counts.append(sum(group.matches for group in groups))
        self.assertEqual(counts, [2, 4, 5, 0])

    def test_real_update_with_duplicate_failure_and_bad_record(self):
        from graph_publish import record_checker
        from search_app import Catalog, SearchService
        from update_pipeline import UpdateConfig, UpdateManager

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            sources = base / "sources"
            shutil.copytree(ROOT / "data" / "sources", sources)
            ecoton = read_rows(slice_path("ecoton", sources))
            broken = {**ecoton[1], "floor": "9", "observed_on": "2026-11-27"}
            avito = ecoton_row(
                id="A1",
                platform="avito",
                source_id="1",
                source_url=AVITO_URL,
                address="Ропшинская улица, д. 4",
                rent_rub_month="61000",
            )

            def yandex_down():
                raise SourceError("тестовый сбой источника")

            published = {}
            config = UpdateConfig(
                {
                    "ecoton": lambda: SourceResponse(
                        "ecoton", [ecoton[0], broken, *ecoton[2:]], False
                    ),
                    "avito": lambda: SourceResponse("avito", [avito], False),
                    "yandex": yandex_down,
                },
                record_checker(),
                SearchService.build_and_load,
                state_dir=base / "state",
                releases_root=base / "releases",
                baseline_sources=sources,
                on_published=lambda outcome: published.setdefault(
                    "catalog", outcome.prepared
                ),
                decisions=lambda: {},
            )
            status = UpdateManager(config).run()
            self.assertEqual(status["state"], "succeeded", status.get("message"))
            report = status["report"]
            self.assertEqual(report["sources"]["yandex"]["state"], "failed")
            release = releases.current(base / "releases")
            assert release is not None
            self.assertEqual(
                len(read_rows(slice_path("yandex", release / "sources"))), 6
            )
            self.assertEqual(release.name, status["last_success"]["version"])
            self.assertEqual(report["rejected"][0]["id"], broken["id"])
            self.assertIn(
                "Этаж квартиры выше этажности здания",
                report["rejected"][0]["reasons"][0],
            )
            group = next(iter(report["groups"].values()))
            self.assertEqual(
                (group["source"], sorted(group["ids"])), ("auto", ["A1", "E129891108"])
            )
            catalog = published["catalog"]
            self.assertIsInstance(catalog, Catalog)
            # A restarted site reads the same published version.
            restarted = Catalog(release / releases.GRAPH_NAME)
            self.assertEqual(len(restarted.offers), 6)
            homes = {}
            for offer in catalog.offers:
                homes.setdefault(offer.home, []).append(offer.rent)
            self.assertEqual(len(catalog.offers), 6)
            self.assertIn(
                sorted(map(int, max(homes.values(), key=len))), [[59000, 61000]]
            )


if __name__ == "__main__":
    unittest.main()
