"""Fetch, normalize, match, validate and publish a new listing slice."""

import json
import os
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import releases
from dedup import address_key, group_cards, read_decisions
from paths import SOURCES, STATE
from sources import (
    PLATFORMS,
    CardStatus,
    SourceError,
    SourceResponse,
    card_key,
    history_path,
    id_collisions,
    known_ids,
    merge_slice,
    meta_path,
    platform_name,
    read_meta,
    read_rows,
    slice_path,
    write_rows,
)

STALE_LOCK_SECONDS = 3600
Connector = Callable[[], SourceResponse | None]
# Source states that deliver new observations to this run.
FRESH_STATES = {"parsed", "parsed_with_errors", "complete", "partial_file"}


@dataclass
class BuildOutcome:
    staged_graph: Path
    prepared: Any = None


@dataclass
class UpdateConfig:
    connectors: dict[str, Connector]
    # Returns readable SHACL problems of one record; empty means valid.
    check_record: Callable[[dict[str, str]], list[str]]
    # Builds and validates the whole graph into the given file of the new version.
    build: Callable[[list[dict[str, str]], Path], BuildOutcome]
    state_dir: Path = STATE
    releases_root: Path = releases.RELEASES
    # Used until the first version is published.
    baseline_sources: Path = SOURCES
    on_published: Callable[[BuildOutcome], None] | None = None
    # Sources deliberately not queried in this run, with the reason.
    skipped: dict[str, str] = field(default_factory=dict)
    decisions: Callable[[], dict[frozenset[str], str]] = read_decisions


@dataclass
class Rejection:
    id: str
    platform: str
    source_url: str
    reasons: list[str]


@dataclass
class UpdateReport:
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    cards: list[CardStatus] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)


class NoNewData(Exception):
    """No source delivered observations; the published version stays."""

    def __init__(self, report: UpdateReport) -> None:
        super().__init__("Новых данных ни от одного источника не получено")
        self.report = report


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class FileLock:
    """Cross-process guard: one update for one project folder."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            age = time.time() - self.path.stat().st_mtime
            if age < STALE_LOCK_SECONDS:
                return False
            # A crashed run left the file; remove it once and retry.
            self.path.unlink(missing_ok=True)
            return self.acquire()
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()} {now()}\n")
        return True

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


def building_conflicts(
    rows: list[dict[str, str]], published: set[tuple[str, str]]
) -> dict[str, str]:
    """Reject new records whose floor count contradicts another record of the house."""
    by_house: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row["floors_total"]:
            by_house.setdefault(address_key(row["address"]), []).append(row)
    problems = {}
    for house_rows in by_house.values():
        totals = {row["floors_total"] for row in house_rows}
        if len(totals) < 2:
            continue
        kept = [row for row in house_rows if card_key(row) in published]
        reference = kept[0]["floors_total"] if kept else house_rows[0]["floors_total"]
        for row in house_rows:
            if row["floors_total"] != reference:
                problems[row["id"]] = (
                    f"этажность {row['floors_total']} расходится с {reference} "
                    "у другой записи этого дома"
                )
    return problems


class UpdateManager:
    def __init__(self, config: UpdateConfig) -> None:
        self.config = config
        self.lock = threading.Lock()
        self.file_lock = FileLock(config.state_dir / "update.lock")
        self.status_path = config.state_dir / "update_status.json"
        self.thread: threading.Thread | None = None

    def sources_root(self) -> Path:
        release = releases.current(self.config.releases_root)
        return release / "sources" if release else self.config.baseline_sources

    def status(self) -> dict[str, Any]:
        if self.status_path.exists():
            return json.loads(self.status_path.read_text(encoding="utf-8"))
        return {"state": "idle", "last_success": None}

    def write_status(self, **values: Any) -> None:
        status = self.status() | values
        self.status_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.status_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(status, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        releases.replace_file(temporary, self.status_path)

    def start(self) -> bool:
        """Start a background update; False when one is already running."""
        if not self.lock.acquire(blocking=False):
            return False
        if not self.file_lock.acquire():
            self.lock.release()
            return False
        self.thread = threading.Thread(target=self._run_locked, daemon=True)
        self.thread.start()
        return True

    def run(self) -> dict[str, Any]:
        """Run in the current thread; used by the command line and tests."""
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Обновление уже выполняется")
        if not self.file_lock.acquire():
            self.lock.release()
            raise RuntimeError("Обновление уже выполняется в другом процессе")
        self._run_locked()
        return self.status()

    def slice_dates(self) -> dict[str, str | None]:
        root = self.sources_root()
        return {
            p: read_meta(p, root).get("last_success_observed_on") for p in PLATFORMS
        }

    def _run_locked(self) -> None:
        try:
            self.write_status(
                state="running",
                step="получение",
                started_at=now(),
                finished_at=None,
                message="",
                trace=None,
                report=None,
            )
            report, version = self._update()
            self.write_status(
                state="succeeded",
                step="опубликовано",
                finished_at=now(),
                message=f"Опубликована новая версия {version}",
                last_success={
                    "finished_at": now(),
                    "version": version,
                    "slices": self.slice_dates(),
                },
                report=self._report_dict(report),
            )
        except NoNewData as error:
            self.write_status(
                state="unchanged",
                step="без публикации",
                finished_at=now(),
                message=f"{error}. Опубликованный срез не менялся.",
                report=self._report_dict(error.report),
            )
        except Exception as error:  # noqa: BLE001 - every failure keeps the old version
            self.write_status(
                state="failed",
                finished_at=now(),
                message=f"{type(error).__name__}: {error}",
                trace=traceback.format_exc(limit=5),
                report=self._report_dict(getattr(error, "report", None)),
            )
        finally:
            try:
                releases.cleanup(self.config.releases_root)
            finally:
                self.file_lock.release()
                self.lock.release()

    @staticmethod
    def _report_dict(report: UpdateReport | None) -> dict[str, Any] | None:
        if report is None:
            return None
        return {
            "sources": report.sources,
            "cards": [vars(item) for item in report.cards],
            "rejected": [vars(item) for item in report.rejected],
            "candidates": report.candidates,
            "groups": report.groups,
        }

    def fetch(self, platform: str) -> tuple[SourceResponse | None, str, str, list[str]]:
        """Call one connector; a failure is isolated to its source."""
        config = self.config
        if platform in config.skipped:
            return None, "skipped", config.skipped[platform], []
        connector = config.connectors.get(platform)
        if connector is None:
            return None, "not_connected", "подключение не настроено", []
        try:
            response = connector()
        except SourceError as error:
            return None, "failed", str(error), []
        except Exception as error:  # noqa: BLE001 - isolate one source
            return None, "failed", f"{type(error).__name__}: {error}", []
        if response is None:
            return None, "no_new_data", "новый файл не передан", []
        if response.complete:
            state = "complete"
        elif platform == "ecoton":
            state = "parsed_with_errors" if response.errors else "parsed"
        else:
            state = "partial_file"
        return response, state, response.note, response.errors

    def _update(self) -> tuple[UpdateReport, str]:
        config, report = self.config, UpdateReport()
        root = self.sources_root()
        previous = {p: read_rows(slice_path(p, root)) for p in PLATFORMS}
        history = {p: read_rows(history_path(p, root)) for p in PLATFORMS}
        published = {card_key(row) for rows in previous.values() for row in rows}
        merged: dict[str, list[dict[str, str]]] = {}
        observed: dict[str, list[dict[str, str]]] = {}
        fresh = set()
        for platform in PLATFORMS:
            self.write_status(step=f"получение: {platform_name(platform)}")
            response, state, detail, errors = self.fetch(platform)
            if state in FRESH_STATES:
                fresh.add(platform)
            ids = known_ids(previous[platform], history[platform])
            rows, statuses, seen = merge_slice(
                previous[platform], response, platform, detail, ids
            )
            merged[platform], observed[platform] = rows, seen
            report.cards += statuses
            report.sources[platform] = {
                "name": platform_name(platform),
                "state": state,
                "detail": detail,
                "errors": errors,
                "previous_observed_on": read_meta(platform, root).get(
                    "last_success_observed_on"
                ),
            }
        if not fresh:
            raise NoNewData(report)

        self.write_status(step="проверка ID и записей SHACL")
        old_by_key = {card_key(row): row for rows in previous.values() for row in rows}
        all_rows = [row for rows in merged.values() for row in rows]
        # A shared internal ID would silently merge two cards in the graph.
        clashes = id_collisions(all_rows)
        conflicts = building_conflicts(all_rows, published)
        checked: list[dict[str, str]] = []
        for row in all_rows:
            unchanged = old_by_key.get(card_key(row)) == row
            problems = [] if unchanged else config.check_record(row)
            if row["id"] in clashes and card_key(row) not in published:
                owners = ", ".join(f"{p}:{s}" for p, s in clashes[row["id"]])
                problems.append(f"внутренний ID {row['id']} уже занят: {owners}")
            if row["id"] in conflicts and not unchanged:
                problems.append(conflicts[row["id"]])
            if not problems:
                checked.append(row)
                continue
            report.rejected.append(
                Rejection(row["id"], row["platform"], row["source_url"], problems)
            )
            # A rejected new observation does not remove the verified old one.
            old = old_by_key.get(card_key(row))
            if old is not None:
                checked.append(old)
                report.cards.append(
                    CardStatus(
                        row["platform"],
                        row["source_id"],
                        "kept_previous",
                        "новая запись отклонена проверкой",
                    )
                )
        remaining = id_collisions(checked)
        if remaining:
            raise ValueError(f"Внутренние ID заняты разными карточками: {remaining}")

        self.write_status(step="сопоставление дублей")
        matched, candidates = group_cards(checked, config.decisions())
        report.candidates = [vars(item) for item in candidates]
        for row in matched:
            if row["verified_home_id"]:
                group = report.groups.setdefault(
                    row["verified_home_id"],
                    {"source": row["identity_source"], "ids": []},
                )
                group["ids"].append(row["id"])

        self.write_status(step="сборка версии и проверка графа")
        version, staging = releases.new_staging(config.releases_root)
        try:
            self.stage_sources(staging / "sources", matched, history, observed, fresh)
            outcome = config.build(matched, staging / releases.GRAPH_NAME)
            self.write_status(step="публикация")
            release = releases.publish(version, staging, config.releases_root)
        except Exception as error:
            error.report = report  # type: ignore[attr-defined]
            raise
        outcome.staged_graph = release / releases.GRAPH_NAME
        if config.on_published is not None:
            config.on_published(outcome)
        return report, version

    def stage_sources(
        self,
        target: Path,
        rows: list[dict[str, str]],
        history: dict[str, list[dict[str, str]]],
        observed: dict[str, list[dict[str, str]]],
        fresh: set[str],
    ) -> None:
        """Write slices, history and metadata of every platform into the new version."""
        root = self.sources_root()
        today = now()[:10]
        for platform in PLATFORMS:
            published_rows = [row for row in rows if row["platform"] == platform]
            known = {(*card_key(r), r["observed_on"]) for r in history[platform]}
            additions = []
            for row in [*observed[platform], *published_rows]:
                key = (*card_key(row), row["observed_on"])
                if key not in known:
                    known.add(key)
                    additions.append(row)
            meta = read_meta(platform, root)
            if platform in fresh:
                dates = sorted(row["observed_on"] for row in observed[platform])
                if dates:
                    latest = max(dates[-1], meta.get("last_success_observed_on") or "")
                    meta["last_success_observed_on"] = latest
                meta["last_update"] = today
            if (
                not published_rows
                and not history[platform]
                and not additions
                and not meta
            ):
                continue
            write_rows(slice_path(platform, target), published_rows)
            write_rows(history_path(platform, target), history[platform] + additions)
            meta_path(platform, target).write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )


def default_config(
    offline: bool = False, ecoton_html: Path | None = None, observed_on=None
) -> UpdateConfig:
    """Connectors and RDF checks used by the site and the command line."""
    import import_ecoton
    import import_prepared
    from graph_publish import build_published_graph, record_checker

    connectors: dict[str, Connector] = {
        platform: (lambda platform=platform: import_prepared.fetch(platform))
        for platform in ("yandex", "avito", "cian")
    }
    connectors["ecoton"] = lambda: import_ecoton.fetch(ecoton_html, observed_on)
    skipped = {}
    if offline and ecoton_html is None:
        skipped["ecoton"] = "сетевой запрос отключён параметром --offline"
    return UpdateConfig(
        connectors, record_checker(), build_published_graph, skipped=skipped
    )


def main() -> None:
    import argparse
    from datetime import date

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline", action="store_true", help="skip the Ecoton network request"
    )
    parser.add_argument(
        "--ecoton-html", type=Path, help="saved rental.html instead of the network"
    )
    parser.add_argument(
        "--date", type=date.fromisoformat, help="observation date of the saved page"
    )
    args = parser.parse_args()
    if bool(args.ecoton_html) != bool(args.date):
        parser.error("--ecoton-html and --date must be supplied together")
    config = default_config(args.offline, args.ecoton_html, args.date)
    status = UpdateManager(config).run()
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
