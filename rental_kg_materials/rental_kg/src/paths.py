"""Project directories and files used by the scripts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
ONTOLOGY = ROOT / "ontology"
SHAPES = ONTOLOGY / "shapes.ttl"
QUERIES = ROOT / "queries"
CQ_QUERIES = QUERIES / "cq"
APP_QUERIES = QUERIES / "app"
DATA = ROOT / "data"
SOURCES = DATA / "sources"
IMPORT = DATA / "import"
FIXTURES = DATA / "fixtures"
GEO = DATA / "geo"
OSM_SNAPSHOT = DATA / "osm" / "osm_pilot.json.gz"
ISLAND_BOUNDARY = GEO / "petrograd_island.geojson"
GRAPHS = ROOT / "graphs"
PUBLISHED_GRAPH = GRAPHS / "observed_with_ontology.owl"
DEMO_GRAPH = GRAPHS / "demo_with_ontology.owl"
RESULTS = ROOT / "results"
STATE = ROOT / "state"
WEB = ROOT / "web"


if __name__ == "__main__":
    for name, value in sorted(globals().items()):
        if isinstance(value, Path):
            print(f"{name:16} {value}")
