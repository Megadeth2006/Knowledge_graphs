"""Read a prepared observation file for a platform without verified automatic access."""

import argparse
import json
import shutil
from pathlib import Path

from paths import IMPORT
from sources import PLATFORMS, SourceError, SourceResponse, normalize_rows, read_rows

FILE_NAME = "observations.csv"
MANIFEST = "manifest.json"


def inbox(platform: str, root: Path = IMPORT) -> Path:
    return root / platform


def fetch(platform: str, root: Path = IMPORT) -> SourceResponse | None:
    """Return the prepared response or None when no file was supplied."""
    folder = inbox(platform, root)
    path = folder / FILE_NAME
    if not path.exists():
        return None
    manifest_path = folder / MANIFEST
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SourceError(f"{manifest_path.name}: {error}") from error
    try:
        raw_rows = read_rows(path)
    except (OSError, UnicodeDecodeError) as error:
        raise SourceError(f"{path.name}: {error}") from error
    for row in raw_rows:
        row["platform"] = platform
    rows, rejected = normalize_rows(raw_rows, platform)
    errors = [f"{raw.get('source_id') or '?'}: {reason}" for raw, reason in rejected]
    # Missing cards count as left only when the preparer declared a full export.
    complete = bool(manifest.get("complete")) and not errors
    note = str(manifest.get("note") or "подготовленный файл")
    return SourceResponse(platform, rows, complete, note, errors)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=sorted(PLATFORMS))
    parser.add_argument("csv", type=Path, help="file in the common observation format")
    parser.add_argument("--complete", action="store_true", help="file is a full export")
    parser.add_argument("--note", default="подготовленный файл")
    args = parser.parse_args()
    folder = inbox(args.platform)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.csv, folder / FILE_NAME)
    (folder / MANIFEST).write_text(
        json.dumps({"complete": args.complete, "note": args.note}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    response = fetch(args.platform)
    assert response is not None
    print(f"{len(response.rows)} записей принято, отклонено {len(response.errors)}")
    for error in response.errors:
        print(" ", error)


if __name__ == "__main__":
    main()
