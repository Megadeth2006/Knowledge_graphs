"""Import factual fields from one public page of the long-term rental catalogue."""

import argparse
import csv
import re
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Tag

from pilot_area import on_petrograd_island

CATALOG = "https://ecoton.spb.ru/rental.html"
ROOT = Path(__file__).resolve().parent
FIELDS = [
    "id",
    "source_id",
    "address",
    "rent_rub_month",
    "area_m2",
    "rooms",
    "floor",
    "floors_total",
    "pets_allowed",
    "term",
    "source_url",
    "observed_on",
    "is_synthetic",
    "pin_lon",
    "pin_lat",
]


def required_tag(section: Tag, selector: str) -> Tag:
    tag = section.select_one(selector)
    if tag is None:
        raise ValueError(f"Missing catalogue element: {selector}")
    return tag


def text_attribute(tag: Tag, name: str) -> str:
    value = tag.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing catalogue attribute: {name}")
    return value


def parse_catalog(html: bytes, observed_on: date) -> list[dict[str, str | int | float]]:
    page = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    selected = page.select_one("#frenttype option[selected]")
    if selected is None or selected.get("value") != "long":
        raise ValueError("Expected the long-term rental catalogue")
    rows: dict[str, dict[str, str | int | float]] = {}
    for section in page.select("section.search-result_items_object"):
        title = required_tag(section, ".search-result_items_object_name_link")
        name = " ".join(title.stripped_strings)
        flat = re.fullmatch(r"(\d+)\s+ккв\s+(.+)", name)
        lon_text, lat_text = title.get("data-lng"), title.get("data-lat")
        if not flat or not lon_text or not lat_text:
            continue
        if not isinstance(lon_text, str) or not isinstance(lat_text, str):
            raise TypeError(f"Expected coordinate strings for {name}")
        lon, lat = float(lon_text), float(lat_text)
        if not on_petrograd_island((lon, lat)):
            continue
        favourite = required_tag(
            section, ".search-result_items_object_name_add-favourites"
        )
        identifier = text_attribute(favourite, "data-objectnumber")
        link_tag = required_tag(section, ".search-result_items_object_name_newtab")
        link = text_attribute(link_tag, "href")
        if (
            not identifier.isdecimal()
            or link != "/detal-object.html?itemid=" + identifier
        ):
            raise ValueError(f"Unexpected source link for {identifier}")

        squares = required_tag(section, ".search-result_items_object_squares")
        area = re.search(r"общая:\s*([\d.,]+)", squares.get_text(" ", strip=True))
        if area is None:
            raise ValueError(f"Missing total area for {identifier}")

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
        row = {
            "id": "E" + identifier,
            "source_id": identifier,
            "address": flat[2].strip(),
            "rent_rub_month": int("".join(rent.split())),
            "area_m2": area[1].replace(",", "."),
            "rooms": int(flat[1]),
            "floor": int(floor),
            "floors_total": int(total),
            "pets_allowed": pets,
            "term": "long_term",
            "source_url": "https://ecoton.spb.ru" + link,
            "observed_on": observed_on.isoformat(),
            "is_synthetic": "false",
            "pin_lon": lon,
            "pin_lat": lat,
        }
        if identifier in rows and rows[identifier] != row:
            raise ValueError(f"Conflicting records for {identifier}")
        rows[identifier] = row
    if not rows:
        raise ValueError(
            "No island apartments found. Check the page before replacing the sample."
        )
    return [rows[identifier] for identifier in sorted(rows)]


def read_catalog(saved_page: Path | None) -> bytes:
    if saved_page is not None:
        return saved_page.read_bytes()

    request = Request(
        CATALOG,
        headers={"User-Agent": "RentalKG/1.0 (academic catalogue sample)"},
    )
    with urlopen(request, timeout=30) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--html", type=Path, help="saved rental.html, paired with --date"
    )
    parser.add_argument(
        "--date", type=date.fromisoformat, help="date of the saved observation"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data" / "island_listings.csv"
    )
    args = parser.parse_args()
    if bool(args.html) != bool(args.date):
        parser.error("--html and --date must be supplied together")
    rows = parse_catalog(read_catalog(args.html), args.date or date.today())
    with NamedTemporaryFile(
        "w", dir=args.output.parent, encoding="utf-8", newline="", delete=False
    ) as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    Path(output.name).replace(args.output)
    print(f"{len(rows)} listings -> {args.output}")


if __name__ == "__main__":
    main()
