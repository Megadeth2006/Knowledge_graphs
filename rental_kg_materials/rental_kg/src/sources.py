"""Common observation format, per-platform slices and observation history."""

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from paths import SOURCES
from pilot_area import on_petrograd_island

# Platform key -> (display name, internal ID prefix, expected URL host suffix).
PLATFORMS = {
    "ecoton": ("Экотон", "E", "ecoton.spb.ru"),
    "yandex": ("Яндекс Недвижимость", "Y", "realty.yandex.ru"),
    "avito": ("Авито", "A", "avito.ru"),
    "cian": ("ЦИАН", "C", "cian.ru"),
}
FIELDS = [
    "id",
    "platform",
    "source_id",
    "address",
    "rent_rub_month",
    "utilities_rub_month",
    "deposit_rub",
    "commission_rub",
    "area_m2",
    "rooms",
    "floor",
    "floors_total",
    "pets_allowed",
    "term",
    "min_months",
    "quality_note",
    "source_url",
    "observed_on",
    "is_synthetic",
    "pin_lon",
    "pin_lat",
    "verified_home_id",
    "identity_evidence",
    "identity_source",
    "listing_status",
]
IDENTITY_SOURCES = {"", "manual", "auto"}
DECIMAL_FIELDS = (
    "rent_rub_month",
    "utilities_rub_month",
    "deposit_rub",
    "commission_rub",
    "area_m2",
)
INTEGER_FIELDS = ("rooms", "floor", "floors_total", "min_months")
TRUE_WORDS = {"true", "1", "yes", "да"}
FALSE_WORDS = {"false", "0", "no", "нет"}
LISTING_STATES = {"", "active", "withdrawn"}


class SourceError(Exception):
    """The source could not be read; its previous slice stays published."""


@dataclass
class SourceResponse:
    """complete: the source confirmed that rows cover its whole offer."""

    platform: str
    rows: list[dict[str, str]]
    complete: bool
    note: str = ""
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CardStatus:
    platform: str
    source_id: str
    status: str
    detail: str = ""


def platform_name(platform: str) -> str:
    return PLATFORMS[platform][0] if platform in PLATFORMS else platform


def platform_of_url(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").casefold()
    for key, (_name, _prefix, domain) in PLATFORMS.items():
        if host == domain or host.endswith("." + domain):
            return key
    return None


def card_key(row: dict[str, str]) -> tuple[str, str]:
    return row["platform"], row["source_id"]


def _decimal(value: str) -> str:
    text = value.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation as error:
        raise ValueError(f"не число: {value!r}") from error
    if not number.is_finite():
        raise ValueError(f"не число: {value!r}")
    return str(number)


def _integer(value: str) -> str:
    text = value.replace(" ", "")
    if not re.fullmatch(r"[+-]?\d+", text):
        raise ValueError(f"не целое число: {value!r}")
    return str(int(text))


def _boolean(value: str) -> str:
    text = value.strip().casefold()
    if text in TRUE_WORDS:
        return "true"
    if text in FALSE_WORDS:
        return "false"
    raise ValueError(f"не логическое значение: {value!r}")


def normalize_row(raw: dict[str, str], platform: str) -> dict[str, str]:
    """Return one record in the common format or raise ValueError with reasons."""
    if platform not in PLATFORMS:
        raise ValueError(f"неизвестная площадка {platform!r}")
    row = {name: str(raw.get(name) or "").strip() for name in FIELDS}
    row["platform"] = platform
    errors = []
    for name in ("source_id", "source_url", "observed_on", "address", "rent_rub_month"):
        if not row[name]:
            errors.append(f"нет поля {name}")
    if row["source_url"] and platform_of_url(row["source_url"]) != platform:
        errors.append("ссылка не относится к площадке")
    if row["observed_on"]:
        try:
            row["observed_on"] = date.fromisoformat(row["observed_on"]).isoformat()
        except ValueError:
            errors.append("дата наблюдения не в формате ГГГГ-ММ-ДД")
    for names, convert in (
        (DECIMAL_FIELDS, _decimal),
        (INTEGER_FIELDS, _integer),
        (("pets_allowed", "is_synthetic"), _boolean),
    ):
        for name in names:
            if row[name]:
                try:
                    row[name] = convert(row[name])
                except ValueError as error:
                    errors.append(f"{name}: {error}")
    row["is_synthetic"] = row["is_synthetic"] or "false"
    row["term"] = row["term"] or "long_term"
    if row["listing_status"] not in LISTING_STATES:
        errors.append("listing_status: ожидается active или withdrawn")
    if bool(row["pin_lon"]) != bool(row["pin_lat"]):
        errors.append("координаты метки заданы не полностью")
    elif row["pin_lon"]:
        try:
            point = float(row["pin_lon"]), float(row["pin_lat"])
        except ValueError:
            errors.append("координаты метки не числа")
        else:
            if not on_petrograd_island(point):
                errors.append("метка вне Петроградского острова")
    if row["verified_home_id"] and not row["identity_evidence"]:
        errors.append("общая квартира без основания identity_evidence")
    if row["identity_source"] not in IDENTITY_SOURCES:
        errors.append("identity_source: ожидается manual или auto")
    if row["verified_home_id"] and row["identity_source"] == "":
        # A group supplied with the record was made by a person.
        row["identity_source"] = "manual"
    if errors:
        raise ValueError("; ".join(errors))
    if not row["id"]:
        row["id"] = PLATFORMS[platform][1] + re.sub(r"\W", "_", row["source_id"])
    return row


def normalize_rows(
    rows: list[dict[str, str]], platform: str
) -> tuple[list[dict[str, str]], list[tuple[dict[str, str], str]]]:
    valid, rejected = [], []
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for raw in rows:
        try:
            row = normalize_row(raw, platform)
        except ValueError as error:
            rejected.append((raw, str(error)))
            continue
        key = card_key(row)
        if key in seen:
            if seen[key] != row:
                rejected.append(
                    (raw, "две разные записи одной карточки в одном ответе")
                )
            continue
        seen[key] = row
        valid.append(row)
    return valid, rejected


def merge_slice(
    previous: list[dict[str, str]],
    response: SourceResponse | None,
    platform: str,
    reason: str = "источник недоступен",
    known_ids: dict[tuple[str, str], str] | None = None,
) -> tuple[list[dict[str, str]], list[CardStatus], list[dict[str, str]]]:
    """Combine the published slice with a new response of the same source.

    Returns the new slice, card statuses and observations for the history.
    """
    old = {card_key(row): row for row in previous}
    ids = {**(known_ids or {}), **{key: row["id"] for key, row in old.items()}}
    if response is None:
        statuses = [
            CardStatus(
                platform,
                key[1],
                "kept_previous",
                f"{reason}; наблюдение {row['observed_on']}",
            )
            for key, row in old.items()
        ]
        return list(previous), statuses, []
    rows, statuses, observed = [], [], []
    seen = set()
    for incoming in response.rows:
        key = card_key(incoming)
        # One card keeps one internal ID whatever the import file says.
        row = {**incoming, "id": ids.get(key, incoming["id"])}
        seen.add(key)
        observed.append(row)
        before = old.get(key)
        if before is not None and row["observed_on"] < before["observed_on"]:
            statuses.append(
                CardStatus(
                    platform,
                    key[1],
                    "older_observation",
                    f"наблюдение {row['observed_on']} старше опубликованного {before['observed_on']}; срез не изменён",
                )
            )
            rows.append(before)
            continue
        if row["listing_status"] == "withdrawn":
            statuses.append(
                CardStatus(
                    platform, key[1], "withdrawn", "источник явно сообщил о снятии"
                )
            )
            continue
        if before is None:
            statuses.append(CardStatus(platform, key[1], "new"))
        else:
            changed = [
                name
                for name in FIELDS
                if name
                not in {
                    "observed_on",
                    "id",
                    "verified_home_id",
                    "identity_evidence",
                    "identity_source",
                }
                and before.get(name, "") != row[name]
            ]
            detail = "изменено: " + ", ".join(changed) if changed else "без изменений"
            statuses.append(CardStatus(platform, key[1], "repeat_observation", detail))
        rows.append(row)
    for key, row in old.items():
        if key in seen:
            continue
        if response.complete:
            statuses.append(
                CardStatus(
                    platform,
                    key[1],
                    "left_slice",
                    "нет в ответе с подтверждённой полнотой; снятие не подтверждено",
                )
            )
        else:
            statuses.append(
                CardStatus(
                    platform,
                    key[1],
                    "not_seen",
                    f"нет в прочитанной части ответа, полнота не подтверждена; сохранено наблюдение {row['observed_on']}",
                )
            )
            rows.append(row)
    rows.sort(key=lambda item: item["id"])
    return rows, statuses, observed


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as source:
        return [
            {name: row.get(name) or "" for name in FIELDS}
            for row in csv.DictReader(source)
        ]


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", dir=path.parent, encoding="utf-8", newline="", delete=False, suffix=".tmp"
    ) as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({name: row.get(name, "") for name in FIELDS} for row in rows)
    Path(output.name).replace(path)


def known_ids(*row_lists: list[dict[str, str]]) -> dict[tuple[str, str], str]:
    """Internal IDs already given to cards in slices and history."""
    ids: dict[tuple[str, str], str] = {}
    for rows in row_lists:
        for row in rows:
            ids.setdefault(card_key(row), row["id"])
    return ids


def id_collisions(rows: list[dict[str, str]]) -> dict[str, list[tuple[str, str]]]:
    """Internal IDs shared by different cards."""
    owners: dict[str, set[tuple[str, str]]] = {}
    for row in rows:
        owners.setdefault(row["id"], set()).add(card_key(row))
    return {key: sorted(cards) for key, cards in owners.items() if len(cards) > 1}


def append_history(path: Path, rows: list[dict[str, str]]) -> int:
    known = {(*card_key(row), row["observed_on"]) for row in read_rows(path)}
    fresh = [row for row in rows if (*card_key(row), row["observed_on"]) not in known]
    if fresh:
        write_rows(path, read_rows(path) + fresh)
    return len(fresh)


def slice_path(platform: str, root: Path = SOURCES) -> Path:
    return root / platform / "slice.csv"


def history_path(platform: str, root: Path = SOURCES) -> Path:
    return root / platform / "history.csv"


def meta_path(platform: str, root: Path = SOURCES) -> Path:
    return root / platform / "meta.json"


def read_meta(platform: str, root: Path = SOURCES) -> dict[str, str]:
    path = meta_path(platform, root)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_all_slices(root: Path = SOURCES) -> list[dict[str, str]]:
    rows = []
    for platform in PLATFORMS:
        rows += read_rows(slice_path(platform, root))
    ids = [row["id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate internal listing identifier")
    return rows


if __name__ == "__main__":
    for platform in PLATFORMS:
        slice_rows = read_rows(slice_path(platform))
        print(f"{platform:7} {len(slice_rows):3} записей  {read_meta(platform)}")
