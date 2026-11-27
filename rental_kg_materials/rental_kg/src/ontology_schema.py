"""Vocabulary and OWL axioms of the rental ontology."""

from itertools import combinations

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS, XSD

from spatial import GEO

EX = Namespace("https://example.org/rental-kg#")

CLASSES = {
    "Place": (
        "Место расположения",
        None,
        (
            "Адресный контейнер, в котором размещено жильё прямо или через цепочку "
            "locatedIn: здание, район или город. Жильё и объекты инфраструктуры в этот "
            "класс не входят."
        ),
    ),
    "Dwelling": (
        "Жильё",
        None,
        (
            "Жилая единица, предлагаемая в аренду снимком объявления через offers. В "
            "текущем проекте представлены целые квартиры; комнаты и отдельные дома "
            "могут быть добавлены при расширении модели."
        ),
    ),
    "Apartment": (
        "Квартира",
        "Dwelling",
        (
            "Отдельная жилая единица внутри здания, предлагаемая целиком в аренду; "
            "характеризуется площадью, числом комнат и этажом. Студия также "
            "является квартирой."
        ),
    ),
    "Building": (
        "Здание",
        "Place",
        (
            "Физическая постройка, содержащая жильё, с адресом и этажностью. Её "
            "точка WGS84 служит приближённой точкой расположения находящегося в ней "
            "жилья."
        ),
    ),
    "District": (
        "Район",
        "Place",
        (
            "Территориальная адресная единица внутри города, в которую входит "
            "здание. Членство определяется уровнем адресной иерархии, а не близостью "
            "к жилью."
        ),
    ),
    "City": (
        "Город",
        "Place",
        (
            "Населённый пункт, содержащий районы и являющийся верхним используемым "
            "уровнем адресной иерархии."
        ),
    ),
    "Amenity": (
        "Объект инфраструктуры",
        None,
        "Объект обслуживания или отдыха, близость которого важна при подборе жилья.",
    ),
    "MetroStation": (
        "Станция метро",
        "Amenity",
        (
            "Объект транспортного обслуживания, предоставляющий доступ к "
            "метрополитену. В текущем наборе представлен одной репрезентативной "
            "точкой, а не всеми входами."
        ),
    ),
    "Supermarket": (
        "Супермаркет",
        "Amenity",
        (
            "Объект розничного обслуживания для покупки продуктов и повседневных "
            "товаров. Обозначает магазин как объект обслуживания, а не его "
            "здание."
        ),
    ),
    "Park": (
        "Парк",
        "Amenity",
        "Озеленённая территория для отдыха. В пилоте представлена опорной точкой.",
    ),
    "ListingSnapshot": (
        "Снимок объявления",
        None,
        (
            "Зафиксированное на дату наблюдения предложение аренды одного "
            "жилья с ценой, условиями и ссылкой на запись источника; не "
            "является самим жильём."
        ),
    ),
    "RenterProfile": (
        "Профиль требований",
        None,
        "Явные требования поиска: бюджет, город, условия аренды и пороги доступности.",
    ),
    "SourceRecord": (
        "Запись источника",
        None,
        (
            "Указатель на исходную запись, из которой получены факты: URL "
            "реальной карточки либо явно учебный идентификатор источника. "
            "Используется как объект hasSource."
        ),
    ),
    "MetroEntrance": (
        "Вход метро",
        None,
        (
            "Отдельный вход или выход станции. Пешеходный маршрут строится до "
            "доступного входа."
        ),
    ),
}

OBJECT_PROPERTIES = {
    "offers": ("ListingSnapshot", "Dwelling", "предлагает жильё"),
    "hasListing": ("Dwelling", "ListingSnapshot", "имеет объявление"),
    "inBuilding": ("Dwelling", "Building", "находится в здании"),
    "inDistrict": ("Building", "District", "находится в районе"),
    "inCity": ("District", "City", "находится в городе"),
    "locatedIn": (None, "Place", "расположено в"),
    "hasSource": (None, "SourceRecord", "источник записи"),
    "preferredCity": ("RenterProfile", "City", "предпочтительный город"),
    "entranceOf": ("MetroEntrance", "MetroStation", "вход станции"),
}

DATA_PROPERTIES = {
    "rentRubMonth": ("ListingSnapshot", "decimal", "аренда, руб./месяц"),
    "utilitiesRubMonth": (
        "ListingSnapshot",
        "decimal",
        "дополнительные КУ, руб./месяц",
    ),
    "depositRub": ("ListingSnapshot", "decimal", "залог, руб."),
    "commissionRub": ("ListingSnapshot", "decimal", "разовая комиссия, руб."),
    "petsAllowed": ("ListingSnapshot", "boolean", "животные разрешены"),
    "observedOn": ("ListingSnapshot", "date", "дата наблюдения"),
    "term": ("ListingSnapshot", "string", "категория срока аренды"),
    "minMonths": ("ListingSnapshot", "integer", "минимальный срок, месяцев"),
    "areaM2": ("Dwelling", "decimal", "площадь, м²"),
    "rooms": ("Dwelling", "integer", "число комнат, студия = 0"),
    "floor": ("Dwelling", "integer", "этаж"),
    "floorsTotal": ("Building", "integer", "этажей в доме"),
    "address": ("Building", "string", "адрес"),
    "sourceUrl": ("SourceRecord", "anyURI", "ссылка на источник"),
    "qualityNote": (None, "string", "замечание к качеству"),
    "isSynthetic": (None, "boolean", "синтетические данные"),
    "maxRentRubMonth": ("RenterProfile", "decimal", "лимит арендной платы"),
    "maxMetroDistanceM": ("RenterProfile", "decimal", "порог до метро по прямой, м"),
    "requiresPets": ("RenterProfile", "boolean", "нужно разрешение на животных"),
    "maxMetroWalkSeconds": ("RenterProfile", "integer", "порог пешком до метро, с"),
    "usableEntrance": ("MetroEntrance", "boolean", "доступен для входа в пилоте"),
    "geometryKind": (None, "string", "способ получения опорной точки"),
}


def new_graph():
    graph = Graph()
    graph.bind("ex", EX)
    graph.bind("geo", GEO)
    graph.bind("owl", OWL)
    return graph


def add_label(graph, node, label):
    graph.add((node, RDFS.label, Literal(label, lang="ru")))


def build_schema():
    graph = new_graph()
    ontology = URIRef(str(EX).rstrip("#"))
    graph.add((ontology, RDF.type, OWL.Ontology))
    graph.add((ontology, OWL.versionInfo, Literal("2.0.0")))
    for name, (label, parent, definition) in CLASSES.items():
        node = EX[name]
        graph.add((node, RDF.type, OWL.Class))
        add_label(graph, node, label)
        graph.add((node, RDFS.comment, Literal(definition, lang="ru")))
        if parent:
            graph.add((node, RDFS.subClassOf, EX[parent]))

    for name, (domain, range_class, label) in OBJECT_PROPERTIES.items():
        node = EX[name]
        graph.add((node, RDF.type, OWL.ObjectProperty))
        graph.add((node, RDFS.range, EX[range_class]))
        add_label(graph, node, label)
        if domain:
            graph.add((node, RDFS.domain, EX[domain]))

    for name, (domain, datatype, label) in DATA_PROPERTIES.items():
        node = EX[name]
        graph.add((node, RDF.type, OWL.DatatypeProperty))
        graph.add((node, RDFS.range, XSD[datatype]))
        add_label(graph, node, label)
        if domain:
            graph.add((node, RDFS.domain, EX[domain]))

    # Local declarations of the GeoSPARQL terms used by the project.
    graph.add((GEO.Geometry, RDF.type, OWL.Class))
    graph.add((GEO.hasGeometry, RDF.type, OWL.ObjectProperty))
    graph.add((GEO.hasGeometry, RDFS.range, GEO.Geometry))
    graph.add((GEO.asWKT, RDF.type, OWL.DatatypeProperty))
    graph.add((GEO.asWKT, RDFS.domain, GEO.Geometry))
    graph.add((GEO.asWKT, RDFS.range, GEO.wktLiteral))
    graph.add((GEO.wktLiteral, RDF.type, RDFS.Datatype))
    add_label(graph, GEO.Geometry, "Геометрия")
    add_label(graph, GEO.hasGeometry, "имеет геометрию")
    add_label(graph, GEO.asWKT, "геометрия в WKT")

    graph.add((EX.offers, OWL.inverseOf, EX.hasListing))
    graph.add((EX.locatedIn, RDF.type, OWL.TransitiveProperty))
    for name in ("inBuilding", "inDistrict", "inCity"):
        graph.add((EX[name], RDFS.subPropertyOf, EX.locatedIn))

    groups = (
        (
            "Place",
            "Dwelling",
            "Amenity",
            "ListingSnapshot",
            "SourceRecord",
            "RenterProfile",
            "MetroEntrance",
        ),
        ("City", "District", "Building"),
        ("MetroStation", "Supermarket", "Park"),
    )
    for group in groups:
        for left, right in combinations(group, 2):
            graph.add((EX[left], OWL.disjointWith, EX[right]))
    return graph
