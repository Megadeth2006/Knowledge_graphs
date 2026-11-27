"""Published versions: graph, slices, history and metadata switched by one pointer."""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from paths import PUBLISHED_GRAPH, SOURCES, STATE

RELEASES = STATE / "releases"
POINTER = "CURRENT"
GRAPH_NAME = "observed_with_ontology.owl"
KEEP = 3


def current(root: Path = RELEASES) -> Path | None:
    """Directory of the published version or None before the first update."""
    pointer = root / POINTER
    if not pointer.exists():
        return None
    name = pointer.read_text(encoding="utf-8").strip()
    release = root / name
    if not name or not (release / GRAPH_NAME).is_file():
        raise RuntimeError(f"{pointer} points to a missing version: {name!r}")
    return release


def sources_root(root: Path = RELEASES) -> Path:
    release = current(root)
    return release / "sources" if release else SOURCES


def graph_path(root: Path = RELEASES) -> Path:
    release = current(root)
    return release / GRAPH_NAME if release else PUBLISHED_GRAPH


def new_staging(root: Path = RELEASES) -> tuple[str, Path]:
    """Empty folder for the next version; invisible until published."""
    root.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    staging = root / f".staging-{name}"
    staging.mkdir()
    (staging / "sources").mkdir()
    return name, staging


def replace_file(source: Path, target: Path) -> None:
    for attempt in range(5):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            # Windows refuses to replace a file another thread is reading.
            if attempt == 4:
                raise
            time.sleep(0.05)


def publish(name: str, staging: Path, root: Path = RELEASES) -> Path:
    """Make the staged version current; the pointer switch is the only commit."""
    if not (staging / GRAPH_NAME).is_file():
        raise RuntimeError("The staged version has no graph")
    release = root / name
    staging.rename(release)
    pointer = root / f".{POINTER}.tmp"
    try:
        pointer.write_text(name + "\n", encoding="utf-8")
        replace_file(pointer, root / POINTER)
    except BaseException:
        # The pointer still names the old version; drop the unpublished copy.
        shutil.rmtree(release, ignore_errors=True)
        pointer.unlink(missing_ok=True)
        raise
    return release


def cleanup(root: Path = RELEASES, keep: int = KEEP) -> None:
    """Remove old versions and abandoned staging folders; never the current one."""
    if not root.exists():
        return
    active = current(root)
    versions = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    for path in versions[:-keep]:
        if path != active:
            shutil.rmtree(path, ignore_errors=True)
    for path in root.glob(".staging-*"):
        shutil.rmtree(path, ignore_errors=True)


def export(
    target_sources: Path = SOURCES, target_graph: Path = PUBLISHED_GRAPH
) -> Path:
    """Copy the published version into data/ and graphs/ for a Git commit."""
    release = current()
    if release is None:
        raise RuntimeError("No published version yet")
    for folder in (release / "sources").iterdir():
        destination = target_sources / folder.name
        destination.mkdir(parents=True, exist_ok=True)
        for file in folder.iterdir():
            shutil.copyfile(file, destination / file.name)
    shutil.copyfile(release / GRAPH_NAME, target_graph)
    return release


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", action="store_true", help="copy into data/ and graphs/"
    )
    args = parser.parse_args()
    if args.export:
        print("Скопирована версия", export().name)
    else:
        print(
            "Текущая версия:", current() or "нет, используется data/sources и graphs/"
        )
