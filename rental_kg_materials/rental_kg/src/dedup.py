"""Address keys and conservative grouping of cards that offer one apartment."""

import csv
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations

from paths import DATA
from sources import platform_name

DECISIONS = DATA / "identity_decisions.csv"

STREET_TYPES = {
    "ул": "улица",
    "улица": "улица",
    "пр": "проспект",
    "пр-т": "проспект",
    "просп": "проспект",
    "проспект": "проспект",
    "пер": "переулок",
    "переулок": "переулок",
    "наб": "набережная",
    "набережная": "набережная",
    "ш": "шоссе",
    "шоссе": "шоссе",
    "пл": "площадь",
    "площадь": "площадь",
    "б-р": "бульвар",
    "бульвар": "бульвар",
}
DROPPED = {"г", "город", "санкт-петербург", "спб", "россия", "д", "дом"}


def address_key(address: str) -> str:
    """Normalize spelling of one building address, keeping house parts."""
    text = unicodedata.normalize("NFKC", address).casefold().replace("ё", "е")
    text = re.sub(r"(\d)\s*к(?:орп(?:ус)?)?\.?\s*(\d)", r"\1 корпус \2", text)
    text = re.sub(r"\bлит(?:ера)?\.?\s*", "литера ", text)
    tokens = [token for token in re.split(r"[\s,.;]+", text) if token]
    tokens = [token for token in tokens if token not in DROPPED]
    split = next(
        (i for i, token in enumerate(tokens) if token[0].isdigit()), len(tokens)
    )
    street, house = tokens[:split], tokens[split:]
    kinds = [STREET_TYPES[token] for token in street if token in STREET_TYPES]
    names = sorted(token for token in street if token not in STREET_TYPES)
    return "|".join([kinds[0] if kinds else "", " ".join(names), " ".join(house)])


@dataclass(frozen=True)
class Candidate:
    left: str
    right: str
    reason: str


def _features(row: dict[str, str]) -> tuple[Decimal, int, int] | None:
    if not (row.get("area_m2") and row.get("rooms") and row.get("floor")):
        return None
    return Decimal(row["area_m2"]), int(row["rooms"]), int(row["floor"])


def _compatible_totals(left: dict[str, str], right: dict[str, str]) -> bool:
    a, b = left.get("floors_total"), right.get("floors_total")
    return not a or not b or int(a) == int(b)


def classify_pair(left: dict[str, str], right: dict[str, str]) -> tuple[str, str]:
    """Return confident, candidate or different with a short reason."""
    if address_key(left["address"]) != address_key(right["address"]):
        return "different", "разные дома"
    a, b = _features(left), _features(right)
    if a is None or b is None:
        return "candidate", "один дом, признаки квартиры неполные"
    if not _compatible_totals(left, right):
        return "different", "этажность дома расходится"
    if a[1:] != b[1:]:
        return "different", "этаж или число комнат различаются"
    if a[0] != b[0]:
        if abs(a[0] - b[0]) <= max(a[0], b[0]) * Decimal("0.02"):
            return "candidate", "площадь отличается не более чем на 2%"
        return "different", "площадь различается"
    if left["platform"] == right["platform"]:
        return "candidate", "одна площадка: возможно, разные квартиры одного автора"
    return (
        "confident",
        "дом, этаж, число комнат и площадь совпадают на разных площадках",
    )


def read_decisions(path=DECISIONS) -> dict[frozenset[str], str]:
    """Curator decisions: same or different for a pair of internal IDs."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    decisions = {}
    for row in rows:
        if row["decision"] not in {"same", "different"}:
            raise ValueError(f"{path.name}: decision must be same or different")
        decisions[frozenset((row["left_id"], row["right_id"]))] = row["decision"]
    return decisions


def identity_fields(row: dict[str, str]) -> tuple:
    """Fields that must agree inside one apartment group of the graph."""
    return (
        address_key(row["address"]),
        Decimal(row["area_m2"]) if row.get("area_m2") else None,
        int(row["rooms"]) if row.get("rooms") else None,
        int(row["floor"]) if row.get("floor") else None,
    )


class Groups:
    """Union-find over internal listing IDs."""

    def __init__(self, keys) -> None:
        self.parent = {key: key for key in keys}

    def root(self, key: str) -> str:
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def join(self, left: str, right: str) -> None:
        self.parent[self.root(right)] = self.root(left)

    def members(self) -> list[list[str]]:
        result: dict[str, list[str]] = {}
        for key in self.parent:
            result.setdefault(self.root(key), []).append(key)
        return [sorted(items) for items in result.values()]


def group_cards(
    rows: list[dict[str, str]], decisions: dict[frozenset[str], str] | None = None
) -> tuple[list[dict[str, str]], list[Candidate]]:
    """Group cards of one apartment; doubtful or ambiguous links stay candidates.

    Manual groups come from verified_home_id with identity_source=manual and from
    decisions "same". Automatic groups are rebuilt on every run and are made only
    when every pair matches confidently and each platform appears once.
    "different" blocks a group even through an indirect path.
    """
    decisions = decisions or {}
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Internal listing IDs must be unique before matching")
    by_id = {}
    for row in rows:
        item = dict(row)
        if item.get("identity_source") == "auto":
            item.update(verified_home_id="", identity_evidence="", identity_source="")
        by_id[item["id"]] = item
    different = {pair for pair, value in decisions.items() if value == "different"}
    candidates: list[Candidate] = []

    def blocked(members: list[str]) -> bool:
        return any(frozenset(pair) in different for pair in combinations(members, 2))

    # Manual groups first: a person's decision is kept unless it contradicts data.
    manual = Groups(by_id)
    homes: dict[str, str] = {}
    for key, item in by_id.items():
        if item["verified_home_id"]:
            first = homes.setdefault(item["verified_home_id"], key)
            manual.join(first, key)
    for pair, value in decisions.items():
        if value == "same" and pair <= by_id.keys():
            manual.join(*sorted(pair))
    in_manual = set()
    for members in manual.members():
        if len(members) < 2:
            continue
        items = [by_id[key] for key in members]
        if blocked(members) or len({identity_fields(item) for item in items}) > 1:
            reason = "ручные решения противоречат друг другу или признакам квартиры"
            candidates.append(Candidate(members[0], members[1], reason))
            for item in items:
                item.update(
                    verified_home_id="", identity_evidence="", identity_source=""
                )
            continue
        home = next((i["verified_home_id"] for i in items if i["verified_home_id"]), "")
        home = home or "M_" + "_".join(members)
        evidence = next(
            (i["identity_evidence"] for i in items if i["identity_evidence"]), ""
        )
        for item in items:
            item.update(
                verified_home_id=home,
                identity_evidence=item["identity_evidence"]
                or evidence
                or "ручное решение",
                identity_source="manual",
            )
        in_manual.update(members)

    confident = Groups(by_id)
    edges = set()
    for left, right in combinations(sorted(by_id), 2):
        if (
            left in in_manual
            or right in in_manual
            or frozenset((left, right)) in different
        ):
            continue
        verdict, reason = classify_pair(by_id[left], by_id[right])
        if verdict == "confident":
            confident.join(left, right)
            edges.add(frozenset((left, right)))
        elif verdict == "candidate":
            candidates.append(Candidate(left, right, reason))
    for members in confident.members():
        if len(members) < 2:
            continue
        items = [by_id[key] for key in members]
        pairs = [frozenset(pair) for pair in combinations(members, 2)]
        clique = all(pair in edges for pair in pairs)
        distinct = len({item["platform"] for item in items}) == len(items)
        if not (clique and distinct) or blocked(members):
            reason = "неоднозначно: несколько карточек одной площадки подходят к одной карточке или есть запрет"
            for pair in pairs:
                if pair in edges:
                    left, right = sorted(pair)
                    candidates.append(Candidate(left, right, reason))
            continue
        first = items[0]
        platforms = ", ".join(
            sorted({platform_name(item["platform"]) for item in items})
        )
        evidence = (
            f"Автоматическое совпадение: {first['address']}, этаж {first['floor']}, "
            f"комнат {first['rooms']}, {first['area_m2']} м²; {platforms}"
        )
        for item in items:
            item.update(
                verified_home_id="H_" + "_".join(members),
                identity_evidence=evidence,
                identity_source="auto",
            )
    return [by_id[key] for key in ids], sorted(
        candidates, key=lambda item: (item.left, item.right)
    )


if __name__ == "__main__":
    from sources import load_all_slices

    grouped, doubtful = group_cards(load_all_slices(), read_decisions())
    shared = sorted(
        {row["verified_home_id"] for row in grouped if row["verified_home_id"]}
    )
    print("Группы:", shared or "нет")
    for item in doubtful:
        print("Кандидат:", item.left, item.right, "—", item.reason)
