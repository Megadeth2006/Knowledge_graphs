"""Search filters, duplicate groups and HTML cards; no RDF dependencies."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from html import escape
from string import Template

from paths import WEB
from sources import platform_name, platform_of_url

# Integer filters: query name -> (upper bound, message).
OPTIONAL_LIMITS = {
    "minutes": (1440, "время до метро"),
    "area_min": (10000, "площадь"),
    "rooms_min": (20, "число комнат"),
    "floor_min": (200, "этаж"),
    "floor_max": (200, "этаж"),
    "shop_m": (100000, "расстояние до супермаркета"),
    "park_m": (100000, "расстояние до парка"),
}


@dataclass(frozen=True)
class Nearby:
    label: str
    value: float


@dataclass
class Offer:
    listing: str
    home: str
    address: str
    rent: Decimal
    observed_on: date
    url: str
    identifier: str
    pets: bool | None = None
    area: Decimal | None = None
    rooms: int | None = None
    floor: int | None = None
    floors_total: int | None = None
    geometry_kind: str | None = None
    metro: Nearby | None = None
    shop: Nearby | None = None
    park: Nearby | None = None
    park_point_kind: str | None = None


@dataclass(frozen=True)
class SearchFilters:
    budget: int = 220000
    require_pets: bool = False
    limits: dict[str, int | None] = field(default_factory=dict)

    @classmethod
    def from_params(cls, params: dict[str, str]) -> "SearchFilters":
        try:
            budget = int(params.get("budget", "220000"))
            limits = {}
            for name, (upper, message) in OPTIONAL_LIMITS.items():
                text = params.get(name, "8" if name == "minutes" else "").strip()
                value = int(text) if text else None
                if value is not None and not 0 <= value <= upper:
                    raise ValueError(f"Проверьте {message}.")
                limits[name] = value
        except ValueError as error:
            raise ValueError(
                str(error) if "Проверьте" in str(error) else "Проверьте числа."
            ) from error
        pets = params.get("pets", "any")
        if not 0 <= budget <= 100_000_000:
            raise ValueError("Проверьте бюджет.")
        if pets not in {"any", "yes"}:
            raise ValueError("Проверьте условие о животных.")
        return cls(budget, pets == "yes", limits)

    def limit(self, name: str) -> int | None:
        return self.limits.get(name)


def exclusion_reasons(offer: Offer, filters: SearchFilters) -> list[str]:
    """Unknown values never pass an active filter; the reason says so."""
    reasons = []
    if offer.rent > filters.budget:
        reasons.append("Цена выше бюджета")
    if filters.require_pets and offer.pets is not True:
        reasons.append("Разрешение на животных не подтверждено")
    minutes = filters.limit("minutes")
    if minutes is not None:
        if offer.metro is None:
            reasons.append("Время пешком до метро неизвестно")
        elif offer.metro.value > minutes * 60:
            reasons.append("Путь до метро дольше заданного")
    area = filters.limit("area_min")
    if area is not None:
        if offer.area is None:
            reasons.append("Площадь не указана")
        elif offer.area < area:
            reasons.append("Площадь меньше заданной")
    rooms = filters.limit("rooms_min")
    if rooms is not None:
        if offer.rooms is None:
            reasons.append("Число комнат не указано")
        elif offer.rooms < rooms:
            reasons.append("Комнат меньше заданного")
    low, high = filters.limit("floor_min"), filters.limit("floor_max")
    if low is not None or high is not None:
        if offer.floor is None:
            reasons.append("Этаж не указан")
        elif (low is not None and offer.floor < low) or (
            high is not None and offer.floor > high
        ):
            reasons.append("Этаж вне заданного диапазона")
    for name, nearby, unknown, too_far in (
        (
            "shop_m",
            offer.shop,
            "Расстояние до супермаркета неизвестно",
            "Ближайший супермаркет дальше заданного",
        ),
        (
            "park_m",
            offer.park,
            "Расстояние до парка неизвестно",
            "Ближайший парк дальше заданного",
        ),
    ):
        metres = filters.limit(name)
        if metres is None:
            continue
        if nearby is None:
            reasons.append(unknown)
        elif nearby.value > metres:
            reasons.append(too_far)
    return reasons


@dataclass
class OfferGroup:
    home: str
    offers: list[tuple[Offer, list[str]]]

    @property
    def matches(self) -> bool:
        return any(not reasons for _offer, reasons in self.offers)

    @property
    def first(self) -> Offer:
        return self.offers[0][0]


def group_offers(offers: list[Offer], filters: SearchFilters) -> list[OfferGroup]:
    """One group per apartment; separate cards of one flat keep their own prices."""
    groups: dict[str, OfferGroup] = {}
    for offer in sorted(offers, key=lambda item: (item.rent, item.listing)):
        group = groups.setdefault(offer.home, OfferGroup(offer.home, []))
        group.offers.append((offer, exclusion_reasons(offer, filters)))
    return list(groups.values())


def render(name: str, **values: str | int) -> str:
    # Escape data at the call site. Some placeholders contain rendered HTML.
    source = (WEB / "templates" / name).read_text(encoding="utf-8")
    return Template(source).substitute(values)


def text(value: object) -> str:
    return escape(str(value), quote=True)


def money(value: Decimal | int) -> str:
    return f"{int(value):,}".replace(",", " ")


def duration(seconds: float) -> str:
    minutes, remainder = divmod(round(seconds), 60)
    return f"{minutes} мин {remainder:02} с"


def apartment_line(offer: Offer) -> str:
    area = (
        f"{offer.area.normalize():f} м²"
        if offer.area is not None
        else "площадь не указана"
    )
    if offer.rooms is None:
        rooms = "комнат не указано"
    else:
        rooms = "студия" if offer.rooms == 0 else f"{offer.rooms} комн."
    if offer.floor is None:
        floor = "этаж не указан"
    elif offer.floors_total is None:
        floor = f"этаж {offer.floor}, этажность не указана"
    else:
        floor = f"этаж {offer.floor} из {offer.floors_total}"
    return text(f"{area} · {rooms} · {floor}")


def surroundings(offer: Offer) -> str:
    lines = []
    if offer.metro is None:
        lines.append("Метро: пешеходный маршрут по сети OSM не найден")
    else:
        lines.append(
            f"Метро: {text(offer.metro.label)} · {duration(offer.metro.value)} пешком "
            "по сети OSM"
        )
    if offer.shop is None:
        lines.append("Супермаркет: расстояние неизвестно")
    else:
        lines.append(
            f"Супермаркет: {text(offer.shop.label)} · {round(offer.shop.value)} м "
            "по прямой (геодезически)"
        )
    if offer.park is None:
        lines.append("Парк: расстояние неизвестно")
    else:
        point = (
            "центр рамки контура"
            if offer.park_point_kind == "bbox_centre"
            else "опорная точка"
        )
        name = offer.park.label
        if name.startswith("Объект OSM "):
            name = "без названия, OSM " + name.removeprefix("Объект OSM ")
        lines.append(
            f"Парк: {text(name)} · {round(offer.park.value)} м по прямой "
            f"до точки парка ({point})"
        )
    if offer.geometry_kind == "publisher_pin_approximate":
        lines.append(
            "Дом привязан приблизительно по метке объявления; подъезд уточняется"
        )
    return "<br>".join(lines)


def pets_text(value: bool | None) -> str:
    if value is None:
        return "не указано"
    return "разрешены" if value else "запрещены"


def offer_line(offer: Offer, reasons: list[str]) -> str:
    platform = platform_of_url(offer.url)
    source = platform_name(platform) if platform else "источник"
    verdict = (
        '<span class="ok">подходит</span>'
        if not reasons
        else f'<span class="reason">{text(". ".join(reasons))}.</span>'
    )
    return (
        f"<li><strong>{money(offer.rent)} ₽/мес.</strong> · животные: {pets_text(offer.pets)}"
        f' · <a href="{text(offer.url)}">{text(source)} № {text(offer.identifier)}</a>'
        f" · наблюдение {offer.observed_on:%d.%m.%Y}<br>{verdict}</li>"
    )


def group_card(group: OfferGroup) -> str:
    offer = group.first
    count = len(group.offers)
    title = text(offer.address)
    note = (
        f'<p class="group">Одна квартира в {count} карточках: цены и источники раздельно.</p>'
        if count > 1
        else ""
    )
    return render(
        "listing.html",
        address=title,
        apartment=apartment_line(offer),
        surroundings=surroundings(offer),
        group_note=note,
        offers="".join(offer_line(item, reasons) for item, reasons in group.offers),
    )


if __name__ == "__main__":
    sample = Offer(
        "E1",
        "E1_home",
        "Учебный адрес",
        Decimal(50000),
        date(2026, 11, 26),
        "https://ecoton.spb.ru/detal-object.html?itemid=1",
        "1",
    )
    print(exclusion_reasons(sample, SearchFilters.from_params({"area_min": "40"})))
