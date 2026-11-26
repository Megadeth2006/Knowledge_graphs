"""Normalize a bounded OSM XML extract for the rental pilot."""

import argparse
import gzip
import json
from pathlib import Path
from xml.etree import ElementTree

FOOT_HIGHWAYS = {
    "footway",
    "path",
    "pedestrian",
    "steps",
    "residential",
    "living_street",
    "service",
    "unclassified",
    "tertiary",
    "secondary",
    "primary",
    "track",
    "cycleway",
}
DENIED = {"no", "private", "customers", "permit", "destination"}
ALLOWED = {"yes", "designated", "permissive", "official"}
PASSABLE_BARRIERS = {"bollard", "cycle_barrier", "entrance", "kerb"}


def tags(element):
    return {tag.attrib["k"]: tag.attrib["v"] for tag in element.findall("tag")}


def public_access(properties):
    foot = properties.get("foot")
    return (
        foot not in DENIED
        and (foot in ALLOWED or properties.get("access") not in DENIED)
        and "foot:conditional" not in properties
        and "access:conditional" not in properties
    )


def walkable(properties):
    highway = properties.get("highway")
    return (
        highway in FOOT_HIGHWAYS
        and public_access(properties)
        and properties.get("indoor") != "yes"
        and (highway != "cycleway" or properties.get("foot") in ALLOWED)
        and properties.get("sidewalk") != "separate"
    )


def passable(properties):
    barrier = properties.get("barrier")
    return public_access(properties) and (
        not barrier or barrier in PASSABLE_BARRIERS or properties.get("foot") in ALLOWED
    )


def feature_type(properties):
    if properties.get("railway") == "subway_entrance":
        return "MetroEntrance"
    if properties.get("railway") == "station" and properties.get("station") == "subway":
        return "MetroStation"
    if properties.get("shop") == "supermarket":
        return "Supermarket"
    if properties.get("leisure") == "park":
        return "Park"
    return None


def read_tiles(paths):
    elements = {"node": {}, "way": {}}
    bounds = []
    for path in paths:
        root = ElementTree.parse(path).getroot()
        if root.tag != "osm":
            raise ValueError(f"Expected an OSM map extract: {path}")
        bounds.append(root.find("bounds").attrib)
        for kind, by_id in elements.items():
            for item in root.findall(kind):
                identifier = item.attrib["id"]
                previous = by_id.get(identifier)
                if previous is None or int(item.attrib["version"]) > int(
                    previous.attrib["version"]
                ):
                    by_id[identifier] = item
    return elements, bounds


def normalize(xml_paths, extracted_on, source_url):
    elements, tiles = read_tiles(xml_paths)
    node_elements = list(elements["node"].values())
    way_elements = list(elements["way"].values())
    nodes = {
        node.attrib["id"]: [float(node.attrib["lon"]), float(node.attrib["lat"])]
        for node in node_elements
    }
    blocked = {node.attrib["id"] for node in node_elements if not passable(tags(node))}
    ways = []
    used_nodes = set()
    for way in way_elements:
        properties = tags(way)
        if walkable(properties):
            refs = [nd.attrib["ref"] for nd in way.findall("nd")]
            direction = properties.get("oneway:foot", "no")
            ways.append([refs, direction])
            used_nodes.update(refs)

    features = []
    stations_by_name = {}
    for element in node_elements + way_elements:
        properties = tags(element)
        kind = feature_type(properties)
        if kind is None:
            continue
        osm_id = element.attrib["id"]
        if element.tag == "node":
            point = nodes[osm_id]
        else:
            refs = [nd.attrib["ref"] for nd in element.findall("nd")]
            if not refs or any(ref not in nodes for ref in refs):
                continue
            points = [nodes[ref] for ref in refs]
            point = [(min(axis) + max(axis)) / 2 for axis in zip(*points)]
        feature = {
            "type": kind,
            "osm_type": element.tag,
            "osm_id": osm_id,
            "point": point,
            "point_kind": "osm_node" if element.tag == "node" else "bbox_centre",
            "label": properties.get("name:ru") or properties.get("name"),
        }
        if kind == "MetroEntrance":
            feature["usable"] = (
                element.tag == "node"
                and osm_id in used_nodes
                and osm_id not in blocked
                and properties.get("entrance") != "exit"
                and public_access(properties)
            )
        if kind == "MetroStation" and feature["label"]:
            stations_by_name.setdefault(feature["label"], []).append(feature)
        features.append(feature)

    for feature in features:
        if feature["type"] == "MetroEntrance":
            matches = stations_by_name.get(feature["label"], [])
            if len(matches) == 1:
                station = matches[0]
                feature["station"] = [station["osm_type"], station["osm_id"]]
            else:
                feature["usable"] = False

    return {
        "attribution": "© OpenStreetMap contributors",
        "license": "https://www.openstreetmap.org/copyright",
        "extracted_on": extracted_on,
        "source": source_url,
        "bounds": {
            "minlon": min(float(tile["minlon"]) for tile in tiles),
            "minlat": min(float(tile["minlat"]) for tile in tiles),
            "maxlon": max(float(tile["maxlon"]) for tile in tiles),
            "maxlat": max(float(tile["maxlat"]) for tile in tiles),
        },
        "tiles": tiles,
        "features": features,
        "nodes": {key: nodes[key] for key in sorted(used_nodes & nodes.keys())},
        "ways": ways,
        "blocked": sorted(blocked & used_nodes),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path, nargs="+")
    parser.add_argument("output", type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    snapshot = normalize(args.xml, args.date, args.source)
    content = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    args.output.write_bytes(gzip.compress(content.encode("utf-8"), mtime=0))


if __name__ == "__main__":
    main()
