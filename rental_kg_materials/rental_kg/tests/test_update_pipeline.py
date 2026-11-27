"""Update orchestration with stub RDF steps: versions, failures, IDs and locking."""

import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, ecoton_row

import releases
from sources import (
    SourceError,
    SourceResponse,
    history_path,
    meta_path,
    read_rows,
    slice_path,
)
from update_pipeline import BuildOutcome, UpdateConfig, UpdateManager

AVITO_URL = "https://www.avito.ru/sankt-peterburg/kvartiry/test-1"


def write_stub_graph(rows, target):
    target.write_text(json.dumps(sorted(r["id"] for r in rows)), encoding="utf-8")
    return BuildOutcome(target)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.baseline = base / "sources"
        shutil.copytree(ROOT / "data" / "sources", self.baseline)
        self.releases = base / "releases"
        self.state = base / "state"

    def tearDown(self):
        self.directory.cleanup()

    def manager(
        self, connectors, check=lambda row: [], build=write_stub_graph, **extra
    ):
        config = UpdateConfig(
            connectors,
            check,
            build,
            state_dir=self.state,
            releases_root=self.releases,
            baseline_sources=self.baseline,
            decisions=lambda: {},
            **extra,
        )
        return UpdateManager(config)

    def published(self, platform="ecoton"):
        """Slice as a freshly started process would read it."""
        return read_rows(slice_path(platform, self.manager({}).sources_root()))

    def ecoton(self, rows):
        return {"ecoton": lambda: SourceResponse("ecoton", rows, False)}

    def price_update(self):
        rows = self.published()
        rows[0] = {**rows[0], "rent_rub_month": "360000", "observed_on": "2026-11-27"}
        return rows

    # Publication is one switch of the pointer.

    def test_version_holds_graph_slices_history_and_meta(self):
        status = self.manager(self.ecoton(self.price_update())).run()
        self.assertEqual(status["state"], "succeeded")
        release = releases.current(self.releases)
        assert release is not None
        self.assertEqual(status["last_success"]["version"], release.name)
        self.assertTrue((release / releases.GRAPH_NAME).is_file())
        sources = release / "sources"
        self.assertEqual(
            read_rows(slice_path("ecoton", sources))[0]["rent_rub_month"], "360000"
        )
        self.assertEqual(len(read_rows(history_path("ecoton", sources))), 6)
        meta = json.loads(meta_path("ecoton", sources).read_text(encoding="utf-8"))
        self.assertEqual(meta["last_success_observed_on"], "2026-11-27")
        self.assertEqual(len(read_rows(slice_path("yandex", sources))), 6)
        # The Git baseline is never written by an update.
        self.assertEqual(
            read_rows(slice_path("ecoton", self.baseline))[0]["rent_rub_month"],
            "350000",
        )

    def test_failure_while_publishing_keeps_old_version(self):
        self.manager(self.ecoton(self.price_update())).run()
        before = releases.current(self.releases)
        old_rows = self.published()

        def directory_instead_of_graph(rows, target):
            target.mkdir()  # nothing can be written where the OWL must go
            return BuildOutcome(target)

        newer = [
            {**old_rows[0], "rent_rub_month": "1", "observed_on": "2026-11-28"},
            *old_rows[1:],
        ]
        status = self.manager(
            self.ecoton(newer), build=directory_instead_of_graph
        ).run()
        self.assertEqual(status["state"], "failed")
        self.assertEqual(releases.current(self.releases), before)
        self.assertEqual(self.published(), old_rows)

    def test_pointer_switch_failure_keeps_old_version(self):
        self.manager(self.ecoton(self.price_update())).run()
        before = releases.current(self.releases)
        old_rows = self.published()
        newer = [
            {**old_rows[0], "rent_rub_month": "1", "observed_on": "2026-11-28"},
            *old_rows[1:],
        ]
        real_replace = releases.replace_file

        def fail_on_pointer(source, target):
            if target.name == releases.POINTER:
                raise OSError("disk full")
            real_replace(source, target)

        with mock.patch.object(releases, "replace_file", side_effect=fail_on_pointer):
            status = self.manager(self.ecoton(newer)).run()
        self.assertEqual(status["state"], "failed")
        self.assertIn("disk full", status["message"])
        self.assertEqual(releases.current(self.releases), before)
        self.assertEqual(self.published(), old_rows)
        assert before is not None
        names = [p.name for p in self.releases.iterdir() if p.is_dir()]
        self.assertEqual(names, [before.name])

    def test_first_failed_update_leaves_baseline(self):
        status = self.manager(
            self.ecoton(self.price_update()),
            build=lambda rows, target: (_ for _ in ()).throw(
                ValueError("SHACL: нарушение")
            ),
        ).run()
        self.assertEqual(status["state"], "failed")
        self.assertIsNone(releases.current(self.releases))
        self.assertEqual(self.published()[0]["rent_rub_month"], "350000")

    # Sources and coverage.

    def test_failed_source_keeps_previous_slice(self):
        def broken():
            raise SourceError("timeout")

        status = self.manager(
            {"ecoton": broken, "avito": lambda: SourceResponse("avito", [], False)}
        ).run()
        self.assertEqual(status["report"]["sources"]["ecoton"]["state"], "failed")
        self.assertEqual(len(self.published()), 5)

    def test_no_new_data_publishes_nothing(self):
        def broken():
            raise SourceError("timeout")

        status = self.manager({"ecoton": broken}).run()
        self.assertEqual(status["state"], "unchanged")
        self.assertIn("Новых данных ни от одного источника", status["message"])
        self.assertIsNone(releases.current(self.releases))

    def test_first_page_does_not_drop_missing_cards(self):
        status = self.manager(self.ecoton(self.published()[:2])).run()
        self.assertEqual(status["report"]["sources"]["ecoton"]["state"], "parsed")
        not_seen = [c for c in status["report"]["cards"] if c["status"] == "not_seen"]
        self.assertEqual(len(not_seen), 3)
        self.assertEqual(len(self.published()), 5)

    def test_complete_file_drops_missing_cards(self):
        rows = self.published()[:2]
        status = self.manager(
            {"ecoton": lambda: SourceResponse("ecoton", rows, True)}
        ).run()
        left = [c for c in status["report"]["cards"] if c["status"] == "left_slice"]
        self.assertEqual(len(left), 3)
        self.assertEqual(len(self.published()), 2)

    # Identity of cards.

    def test_reimport_without_id_keeps_historical_id(self):
        old = read_rows(slice_path("yandex", self.baseline))[0]
        again = {
            **old,
            "id": "Y" + old["source_id"],
            "rent_rub_month": "46000",
            "observed_on": "2026-11-27",
        }
        status = self.manager(
            {"yandex": lambda: SourceResponse("yandex", [again], False)}
        ).run()
        self.assertEqual(status["state"], "succeeded")
        rows = {r["id"]: r for r in self.published("yandex")}
        self.assertEqual(rows["R01"]["rent_rub_month"], "46000")
        self.assertNotIn("Y" + old["source_id"], rows)

    def test_older_observation_does_not_roll_back(self):
        self.manager(self.ecoton(self.price_update())).run()
        old = {
            **self.published()[0],
            "rent_rub_month": "1",
            "observed_on": "2026-01-01",
        }
        status = self.manager(self.ecoton([old])).run()
        self.assertEqual(self.published()[0]["rent_rub_month"], "360000")
        statuses = {c["status"] for c in status["report"]["cards"]}
        self.assertIn("older_observation", statuses)
        history = read_rows(history_path("ecoton", self.manager({}).sources_root()))
        self.assertIn("2026-01-01", {r["observed_on"] for r in history})

    def test_internal_id_collision_is_rejected_not_lost(self):
        intruder = ecoton_row(
            id="E129891108",
            platform="avito",
            source_id="9",
            source_url=AVITO_URL,
            address="Ропшинская улица, д. 4",
        )
        status = self.manager(
            {"avito": lambda: SourceResponse("avito", [intruder], False)}
        ).run()
        self.assertEqual(status["state"], "succeeded")
        rejected = status["report"]["rejected"]
        self.assertEqual(rejected[0]["platform"], "avito")
        self.assertIn("внутренний ID E129891108 уже занят", rejected[0]["reasons"][0])
        self.assertEqual(len(self.published()), 5)

    def test_rejected_record_keeps_verified_version(self):
        rows = self.published()
        broken = {**rows[1], "floor": "9", "observed_on": "2026-11-27"}
        status = self.manager(
            self.ecoton([rows[0], broken, *rows[2:]]),
            check=lambda row: ["этаж выше этажности"] if row["floor"] == "9" else [],
        ).run()
        self.assertEqual(status["report"]["rejected"][0]["id"], broken["id"])
        self.assertEqual(self.published()[1]["floor"], rows[1]["floor"])

    def test_duplicate_cards_are_grouped_and_regrouped(self):
        avito = ecoton_row(
            id="A1",
            platform="avito",
            source_id="1",
            source_url=AVITO_URL,
            address="Ропшинская улица, д. 4",
            rent_rub_month="61000",
        )
        status = self.manager(
            {"avito": lambda: SourceResponse("avito", [avito], False)}
        ).run()
        group = next(iter(status["report"]["groups"].values()))
        self.assertEqual(
            (group["source"], sorted(group["ids"])), ("auto", ["A1", "E129891108"])
        )
        # A later twin on Ecoton makes the match ambiguous: the auto group is dropped.
        twin = ecoton_row(
            id="",
            source_id="2",
            source_url="https://ecoton.spb.ru/detal-object.html?itemid=2",
            observed_on="2026-11-27",
        )
        status = self.manager(self.ecoton([*self.published(), twin])).run()
        self.assertEqual(status["report"]["groups"], {})
        self.assertFalse(any(r["verified_home_id"] for r in self.published("avito")))

    def test_only_one_update_at_a_time(self):
        release = threading.Event()

        def slow(rows, target):
            release.wait(5)
            return write_stub_graph(rows, target)

        manager = self.manager(self.ecoton(self.published()), build=slow)
        self.assertTrue(manager.start())
        self.assertFalse(manager.start())
        other_process = self.manager(self.ecoton(self.published()))
        self.assertFalse(other_process.start())
        release.set()
        assert manager.thread is not None
        manager.thread.join(5)
        self.assertEqual(manager.status()["state"], "succeeded")
        self.assertTrue(other_process.start())
        assert other_process.thread is not None
        other_process.thread.join(5)


if __name__ == "__main__":
    unittest.main()
