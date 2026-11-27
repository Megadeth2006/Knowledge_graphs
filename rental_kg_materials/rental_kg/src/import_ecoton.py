"""Import factual fields from one public page of the long-term rental catalogue."""

import argparse
import re
from datetime import date
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

from pilot_area import on_petrograd_island
from sources import SourceError, SourceResponse, normalize_rows, write_rows

CATALOG = "https://ecoton.spb.ru/rental.html"
COVERAGE = "первая страница каталога long, точки внутри острова"


class CardError(ValueError):
    """One catalogue card could not be read completely."""


def required_tag(section: Tag, selector: str) -> Tag:
    tag = section.select_one(selector)
    if tag is None:
        raise CardError(f"Missing catalogue element: {selector}")
    return tag


def text_attribute(tag: Tag, name: str) -> str:
    value = tag.get(name)
    if not isinstance(value, str) or not value:
        raise CardError(f"Missing catalogue attribute: {name}")
    return value


def parse_card(section: Tag, observed_on: date) -> dict[str, str] | None:
    """Read one card; None means a room, a house or a point outside the island."""
    title = required_tag(section, ".search-result_items_object_name_link")
    name = " ".join(title.stripped_strings)
    flat = re.fullmatch(r"(\d+)\s+ккв\s+(.+)", name)
    lon_text, lat_text = title.get("data-lng"), title.get("data-lat")
    if not flat or not lon_text or not lat_text:
        return None
    if not isinstance(lon_text, str) or not isinstance(lat_text, str):
        raise CardError(f"Expected coordinate strings for {name}")
    lon, lat = float(lon_text), float(lat_text)
    if not on_petrograd_island((lon, lat)):
        return None
    favourite = required_tag(section, ".search-result_items_object_name_add-favourites")
    identifier = text_attribute(favourite, "data-objectnumber")
    link_tag = required_tag(section, ".search-result_items_object_name_newtab")
    link = text_attribute(link_tag, "href")
    if not identifier.isdecimal() or link != "/detal-object.html?itemid=" + identifier:
        raise CardError(f"Unexpected source link for {identifier}")

    squares = required_tag(section, ".search-result_items_object_squares")
    area = re.search(r"общая:\s*([\d.,]+)", squares.get_text(" ", strip=True))
    if area is None:
        raise CardError(f"Missing total area for {identifier}")

    storey = required_tag(section, ".search-result_items_object_storey")
    floor, total = storey.get_text(strip=True).split("/")
    price = required_tag(section, ".ps-price-wrap span")
    rent = "".join(price.stripped_strings)
    comments = required_tag(section, ".search-result_items_object_comments")
    description = " ".join(comments.stripped_strings).casefold()
    # Conservative, complete statements. Conditional permission stays unknown.
    sentences = {part.strip() for part in re.split(r"[.!?\n]+", description)}
    positive = bool(sentences & {"можно с животными", "животные разрешены"})
    negative = bool(
        sentences
        & {"без животных", "для нанимателей без животных", "животные запрещены"}
    )
    pets = ("true" if positive else "false") if positive != negative else ""
    return {
        "id": "E" + identifier,
        "platform": "ecoton",
        "source_id": identifier,
        "address": flat[2].strip(),
        "rent_rub_month": "".join(rent.split()),
        "area_m2": area[1].replace(",", "."),
        "rooms": flat[1],
        "floor": floor.strip(),
        "floors_total": total.strip(),
        "pets_allowed": pets,
        "term": "long_term",
        "source_url": "https://ecoton.spb.ru" + link,
        "observed_on": observed_on.isoformat(),
        "is_synthetic": "false",
        "pin_lon": lon_text,
        "pin_lat": lat_text,
    }


def parse_catalog(html: bytes, observed_on: date) -> SourceResponse:
    """Parse the page; unreadable cards make the response incomplete."""
    page = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    selected = page.select_one("#frenttype option[selected]")
    if selected is None or selected.get("value") != "long":
        raise SourceError("Expected the long-term rental catalogue")
    sections = page.select("section.search-result_items_object")
    if not sections:
        raise SourceError("The catalogue page contains no cards")
    raw_rows, errors = [], []
    for number, section in enumerate(sections, 1):
        try:
            row = parse_card(section, observed_on)
        except (CardError, ValueError) as error:
            errors.append(f"карточка {number}: {error}")
            continue
        if row is not None:
            raw_rows.append(row)
    rows, rejected = normalize_rows(raw_rows, "ecoton")
    errors += [f"{raw.get('source_id')}: {reason}" for raw, reason in rejected]
    if not rows and not errors:
        raise SourceError(
            "No island apartments found. Check the page before replacing the sample."
        )
    # One page without pagination never proves that missing cards were removed.
    note = COVERAGE + ("; часть карточек не разобрана" if errors else "")
    return SourceResponse("ecoton", rows, complete=False, note=note, errors=errors)


def read_catalog(saved_page: Path | None) -> bytes:
    if saved_page is not None:
        return saved_page.read_bytes()

    request = Request(
        CATALOG,
        headers={"User-Agent": "RentalKG/1.0 (academic catalogue sample)"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return response.read()
    except (URLError, TimeoutError, OSError) as error:
        raise SourceError(f"Каталог недоступен: {error}") from error


def fetch(
    saved_page: Path | None = None, observed_on: date | None = None
) -> SourceResponse:
    return parse_catalog(read_catalog(saved_page), observed_on or date.today())


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Публикация среза — только через update_pipeline.py или кнопку «Обновить».",
    )
    parser.add_argument(
        "--html", type=Path, help="saved rental.html, paired with --date"
    )
    parser.add_argument(
        "--date", type=date.fromisoformat, help="date of the saved observation"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="separate CSV for inspection"
    )
    args = parser.parse_args()
    if bool(args.html) != bool(args.date):
        parser.error("--html and --date must be supplied together")
    if args.output.resolve().name == "slice.csv":
        parser.error("published slices change only through update_pipeline.py")
    response = fetch(args.html, args.date)
    for error in response.errors:
        print("Карточка не разобрана:", error)
    write_rows(args.output, response.rows)
    print(f"{len(response.rows)} listings -> {args.output} (срез не изменён)")


if __name__ == "__main__":
    main()
