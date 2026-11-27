"""Grouping of separate cards about one apartment."""

import unittest

from helpers import ecoton_row

from dedup import address_key, classify_pair, group_cards
from sources import load_all_slices

AVITO_URL = "https://www.avito.ru/sankt-peterburg/kvartiry/test-1"


def avito_copy(**changes):
    # Test record only: the Ropshinskaya card as if another platform published it.
    return ecoton_row(
        id="A1",
        platform="avito",
        source_id="1",
        source_url=AVITO_URL,
        address="Ропшинская улица, д. 4",
        rent_rub_month="61000",
        **changes,
    )


class AddressTest(unittest.TestCase):
    def test_spelling_variants(self):
        self.assertEqual(
            address_key("Ропшинская ул., 4"), address_key("улица Ропшинская, д. 4")
        )
        self.assertEqual(
            address_key("Пулковское шоссе, 42к6"),
            address_key("Пулковское ш., 42 корп. 6"),
        )
        self.assertNotEqual(
            address_key("Мичуринская ул., 6, литера Б"),
            address_key("Мичуринская ул., 6"),
        )
        self.assertNotEqual(
            address_key("Ропшинская ул., 4"), address_key("Ропшинская ул., 14")
        )


class MatchTest(unittest.TestCase):
    def test_cross_platform_exact_match_is_grouped(self):
        rows, candidates = group_cards([ecoton_row(), avito_copy()])
        self.assertEqual(rows[0]["verified_home_id"], rows[1]["verified_home_id"])
        self.assertIn("Автоматическое совпадение", rows[0]["identity_evidence"])
        self.assertEqual(rows[0]["rent_rub_month"], "59000")
        self.assertEqual(rows[1]["rent_rub_month"], "61000")
        self.assertEqual(candidates, [])

    def test_address_alone_is_not_enough(self):
        verdict, _ = classify_pair(ecoton_row(), avito_copy(floor="", area_m2=""))
        self.assertEqual(verdict, "candidate")
        rows, _ = group_cards([ecoton_row(), avito_copy(floor="3")])
        self.assertFalse(any(r["verified_home_id"] for r in rows))

    def test_near_area_is_only_a_candidate(self):
        self.assertEqual(
            classify_pair(ecoton_row(), avito_copy(area_m2="49.0"))[0], "candidate"
        )

    def test_same_platform_twins_stay_apart(self):
        rows = {r["id"]: r for r in load_all_slices()}
        verdict, reason = classify_pair(rows["R05"], rows["R06"])
        self.assertEqual(verdict, "candidate")
        self.assertIn("одна площадка", reason)

    def test_manual_decision_blocks_merge(self):
        rows, _ = group_cards(
            [ecoton_row(), avito_copy()], {frozenset({"E129891108", "A1"}): "different"}
        )
        self.assertFalse(any(r["verified_home_id"] for r in rows))

    def test_twins_on_one_platform_make_match_ambiguous(self):
        twin = ecoton_row(id="E2", source_id="2")
        rows, candidates = group_cards([ecoton_row(), twin, avito_copy()])
        self.assertFalse(any(r["verified_home_id"] for r in rows))
        pairs = {(c.left, c.right) for c in candidates}
        self.assertIn(("A1", "E129891108"), pairs)
        self.assertIn(("A1", "E2"), pairs)

    def test_negative_decision_holds_on_indirect_path(self):
        cian = ecoton_row(
            id="C1",
            platform="cian",
            source_id="1",
            source_url="https://www.cian.ru/x/1",
        )
        rows, _ = group_cards([ecoton_row(), avito_copy(), cian])
        self.assertEqual(len({r["verified_home_id"] for r in rows}), 1)
        rows, candidates = group_cards(
            [ecoton_row(), avito_copy(), cian],
            {frozenset({"E129891108", "C1"}): "different"},
        )
        self.assertFalse(any(r["verified_home_id"] for r in rows))
        self.assertTrue(candidates)

    def test_manual_and_automatic_groups_differ(self):
        manual = [
            ecoton_row(
                verified_home_id="FLAT1",
                identity_evidence="звонок агенту",
                identity_source="manual",
            ),
            avito_copy(
                verified_home_id="FLAT1",
                identity_evidence="звонок агенту",
                identity_source="manual",
            ),
        ]
        rows, _ = group_cards(manual)
        self.assertEqual(
            {(r["verified_home_id"], r["identity_source"]) for r in rows},
            {("FLAT1", "manual")},
        )
        rows, _ = group_cards([ecoton_row(), avito_copy()])
        self.assertEqual({r["identity_source"] for r in rows}, {"auto"})
        # An earlier automatic group is recomputed, not trusted as manual.
        stale = [dict(r) for r in rows] + [ecoton_row(id="E2", source_id="2")]
        rows, _ = group_cards(stale)
        self.assertFalse(any(r["verified_home_id"] for r in rows))

    def test_manual_same_decision_and_contradiction(self):
        rows, _ = group_cards(
            [ecoton_row(), avito_copy(area_m2="49.0")],
            {frozenset({"E129891108", "A1"}): "same"},
        )
        self.assertFalse(any(r["verified_home_id"] for r in rows))
        rows, _ = group_cards(
            [ecoton_row(), avito_copy()], {frozenset({"E129891108", "A1"}): "same"}
        )
        self.assertEqual({r["identity_source"] for r in rows}, {"manual"})

    def test_duplicate_internal_ids_are_refused(self):
        with self.assertRaises(ValueError):
            group_cards([ecoton_row(), ecoton_row(platform="avito", source_id="7")])

    def test_current_data_has_no_automatic_groups(self):
        rows, candidates = group_cards(load_all_slices())
        self.assertFalse(any(r["verified_home_id"] for r in rows))
        self.assertEqual({(c.left, c.right) for c in candidates}, {("R05", "R06")})


if __name__ == "__main__":
    unittest.main()
