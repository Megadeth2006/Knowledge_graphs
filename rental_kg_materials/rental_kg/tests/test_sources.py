"""Common format, slices and the difference between new and repeated cards."""

import tempfile
import unittest
from pathlib import Path

from helpers import ecoton_row

from sources import (
    SourceResponse,
    append_history,
    id_collisions,
    known_ids,
    load_all_slices,
    merge_slice,
    normalize_row,
    normalize_rows,
    read_rows,
)


class NormalizeTest(unittest.TestCase):
    def test_existing_slices_are_already_normalized(self):
        rows = load_all_slices()
        self.assertEqual(len(rows), 11)
        for row in rows:
            self.assertEqual(normalize_row(row, row["platform"]), row)

    def test_types_and_empty_values(self):
        row = normalize_row(
            {
                "source_id": "777",
                "source_url": "https://www.avito.ru/sankt-peterburg/kvartiry/777",
                "observed_on": "2026-11-27",
                "address": "Тест",
                "rent_rub_month": "45 000",
                "pets_allowed": "да",
            },
            "avito",
        )
        self.assertEqual(row["id"], "A777")
        self.assertEqual(row["rent_rub_month"], "45000")
        self.assertEqual(row["pets_allowed"], "true")
        self.assertEqual(row["area_m2"], "")
        self.assertEqual(row["term"], "long_term")

    def test_rejections_have_reasons(self):
        bad = {
            "source_id": "1",
            "source_url": "https://ecoton.spb.ru/x",
            "observed_on": "27.11.2026",
            "address": "Тест",
            "rent_rub_month": "много",
        }
        with self.assertRaises(ValueError) as caught:
            normalize_row(bad, "avito")
        message = str(caught.exception)
        self.assertIn("ссылка не относится к площадке", message)
        self.assertIn("ГГГГ-ММ-ДД", message)
        self.assertIn("rent_rub_month", message)

    def test_pin_outside_island(self):
        with self.assertRaises(ValueError):
            normalize_row(ecoton_row(pin_lon="30.40", pin_lat="59.90"), "ecoton")

    def test_conflicting_copies_of_one_card(self):
        first = ecoton_row()
        second = ecoton_row(rent_rub_month="61000")
        rows, rejected = normalize_rows([first, first, second], "ecoton")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rejected), 1)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.old = [ecoton_row(), ecoton_row(id="E2", source_id="2")]

    def test_repeat_observation_of_same_card(self):
        new = ecoton_row(rent_rub_month="61000", observed_on="2026-11-27")
        rows, statuses, _seen = merge_slice(
            self.old, SourceResponse("ecoton", [new], True), "ecoton"
        )
        by_id = {s.source_id: s for s in statuses}
        self.assertEqual(by_id["129891108"].status, "repeat_observation")
        self.assertIn("rent_rub_month", by_id["129891108"].detail)
        self.assertEqual(by_id["2"].status, "left_slice")
        self.assertEqual([r["id"] for r in rows], ["E129891108"])

    def test_incomplete_response_keeps_missing_cards(self):
        rows, statuses, _seen = merge_slice(
            self.old, SourceResponse("ecoton", [ecoton_row()], False), "ecoton"
        )
        self.assertEqual(len(rows), 2)
        self.assertIn("not_seen", {s.status for s in statuses})

    def test_explicit_withdrawal(self):
        gone = ecoton_row(listing_status="withdrawn")
        rows, statuses, _seen = merge_slice(
            self.old, SourceResponse("ecoton", [gone], False), "ecoton"
        )
        self.assertEqual({s.status for s in statuses}, {"withdrawn", "not_seen"})
        self.assertEqual([r["id"] for r in rows], ["E2"])

    def test_stable_id_and_no_rollback(self):
        again = ecoton_row(id="X1", rent_rub_month="61000", observed_on="2026-11-27")
        rows, _, _ = merge_slice(
            self.old, SourceResponse("ecoton", [again], False), "ecoton"
        )
        self.assertEqual(rows[0]["id"], "E129891108")
        self.assertEqual(rows[0]["rent_rub_month"], "61000")
        older = ecoton_row(rent_rub_month="1", observed_on="2026-01-01")
        rows, statuses, seen = merge_slice(
            self.old, SourceResponse("ecoton", [older], True), "ecoton"
        )
        self.assertEqual(rows[0]["rent_rub_month"], "59000")
        self.assertEqual(statuses[0].status, "older_observation")
        self.assertEqual(seen[0]["observed_on"], "2026-01-01")

    def test_id_from_history_for_returning_card(self):
        ids = known_ids([], [ecoton_row(id="R99")])
        rows, _, _ = merge_slice(
            [], SourceResponse("ecoton", [ecoton_row()], False), "ecoton", known_ids=ids
        )
        self.assertEqual(rows[0]["id"], "R99")

    def test_collisions_are_detected(self):
        other = ecoton_row(platform="avito", source_id="5")
        self.assertEqual(list(id_collisions([ecoton_row(), other])), ["E129891108"])

    def test_failed_source_keeps_slice(self):
        rows, statuses, _seen = merge_slice(self.old, None, "ecoton", "сбой")
        self.assertEqual(rows, self.old)
        self.assertTrue(all(s.status == "kept_previous" for s in statuses))

    def test_history_keeps_each_observation_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            self.assertEqual(append_history(path, [ecoton_row()]), 1)
            self.assertEqual(append_history(path, [ecoton_row()]), 0)
            self.assertEqual(
                append_history(path, [ecoton_row(observed_on="2026-11-27")]), 1
            )
            self.assertEqual(len(read_rows(path)), 2)


if __name__ == "__main__":
    unittest.main()
