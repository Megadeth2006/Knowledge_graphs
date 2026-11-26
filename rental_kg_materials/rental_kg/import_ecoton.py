"""Import factual fields from one public page of the Ecoton long-term catalogue."""

import argparse
import csv
import re
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

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


def parse_catalog(html, observed_on):
    page = BeautifulSoup(html, "html.parser", from_encoding="utf-8")
    selected = page.select_one("#frenttype option[selected]")
    if selected is None or selected.get("value") != "long":
        raise ValueError("Expected the long-term rental catalogue")
    rows = {}
    for section in page.select("section.search-result_items_object"):
        title = section.select_one(".search-result_items_object_name_link")
        name = " ".join(title.stripped_strings)
        flat = re.fullmatch(r"(\d+)\s+ккв\s+(.+)", name)
        if not flat or not title.get("data-lng") or not title.get("data-lat"):
            continue
        lon, lat = float(title["data-lng"]), float(title["data-lat"])
        if not on_petrograd_island((lon, lat)):
            continue
        field = section.select_one
        identifier = field(".search-result_items_object_name_add-favourites")[
            "data-objectnumber"
        ]
        link = field(".search-result_items_object_name_newtab")["href"]
        if (
            not identifier.isdecimal()
            or link != "/detal-object.html?itemid=" + identifier
        ):
            raise ValueError(f"Unexpected source link for {identifier}")
        area = re.search(
            r"общая:\s*([\d.,]+)",
            field(".search-result_items_object_squares").get_text(" ", strip=True),
        )
        floor, total = (
            field(".search-result_items_object_storey").get_text(strip=True).split("/")
        )
        rent = "".join(section.select_one(".ps-price-wrap span").stripped_strings)
        description = " ".join(
            field(".search-result_items_object_comments").stripped_strings
        ).casefold()
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
    return sorted(rows.values(), key=lambda row: row["source_id"])


def main():
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
    if args.html:
        html = args.html.read_bytes()
    else:
        with urlopen(
            Request(
                CATALOG,
                headers={"User-Agent": "RentalKG/1.0 (academic catalogue sample)"},
            ),
            timeout=30,
        ) as response:
            html = response.read()
    rows = parse_catalog(html, args.date or date.today())
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
