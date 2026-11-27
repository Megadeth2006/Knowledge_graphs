"""Show which SHACL rule rejects a broken record before publication."""

from sources import read_rows, slice_path

EXAMPLE_ID = "E129891108"


def broken_record() -> dict[str, str]:
    """Copy of a real card with an impossible floor; never written to data."""
    row = next(r for r in read_rows(slice_path("ecoton")) if r["id"] == EXAMPLE_ID)
    return {**row, "floor": "9"}


def explain() -> tuple[dict[str, str], list[str], list[str]]:
    from graph_publish import record_checker

    check = record_checker()
    row = broken_record()
    fixed = {**row, "floor": "1"}
    return row, check(row), check(fixed)


if __name__ == "__main__":
    record, problems, after_fix = explain()
    print(f"Запись {record['id']}: этаж {record['floor']} из {record['floors_total']}")
    print("\n".join(problems))
    print("После исправления этажа:", "ошибок нет" if not after_fix else after_fix)
