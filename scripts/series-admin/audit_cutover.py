#!/usr/bin/env python
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys

from openpyxl import Workbook
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.models import ComparisonJob, ConfirmedVehicleSeries, Job, KoubeiRawComment


@dataclass(frozen=True)
class DiffRow:
    category: str
    key: str
    prod_value: str
    test_value: str
    action: str


REQUIRED_TABLES = (
    ConfirmedVehicleSeries.__tablename__,
    KoubeiRawComment.__tablename__,
    Job.__tablename__,
    ComparisonJob.__tablename__,
)


def require_tables(database_url: str) -> None:
    engine = create_engine(database_url, future=True)
    inspector = inspect(engine)
    missing = [table_name for table_name in REQUIRED_TABLES if not inspector.has_table(table_name)]
    if missing:
        raise RuntimeError(f"database is missing required tables: {', '.join(missing)}")


def session_for(database_url: str) -> Session:
    require_tables(database_url)
    engine = create_engine(database_url, future=True)
    return sessionmaker(bind=engine, future=True)()


def series_rows(db: Session) -> dict[tuple[str, str], ConfirmedVehicleSeries]:
    return {
        (row.query_key, row.platform): row
        for row in db.query(ConfirmedVehicleSeries).filter(ConfirmedVehicleSeries.status == "active").all()
    }


def raw_comment_keys(db: Session) -> set[tuple[str, str, str, str]]:
    return {
        (row.query_key, row.platform, row.series_id, row.dedupe_key)
        for row in db.query(KoubeiRawComment).all()
    }


def task_result_keys(db: Session) -> set[tuple[str, str]]:
    jobs = {("job", row.job_id) for row in db.query(Job).filter(Job.status == "completed").all()}
    comparisons = {
        ("comparison", row.comparison_id)
        for row in db.query(ComparisonJob).filter(ComparisonJob.status == "completed").all()
    }
    return jobs | comparisons


def compare_series(prod: Session, test: Session) -> list[DiffRow]:
    prod_rows = series_rows(prod)
    test_rows = series_rows(test)
    rows: list[DiffRow] = []
    for key, test_row in sorted(test_rows.items()):
        prod_row = prod_rows.get(key)
        label = f"{key[0]}|{key[1]}"
        test_value = f"{test_row.series_id}|{test_row.status}"
        if prod_row is None:
            rows.append(DiffRow("series", label, "", test_value, "new"))
        elif prod_row.series_id != test_row.series_id:
            rows.append(DiffRow("series", label, f"{prod_row.series_id}|{prod_row.status}", test_value, "conflict"))
        else:
            rows.append(DiffRow("series", label, f"{prod_row.series_id}|{prod_row.status}", test_value, "duplicate"))
    for key, prod_row in sorted(prod_rows.items()):
        if key not in test_rows:
            rows.append(DiffRow("series", f"{key[0]}|{key[1]}", f"{prod_row.series_id}|{prod_row.status}", "", "missing_in_test"))
    return rows


def compare_key_sets(category: str, prod_keys: set[tuple[str, ...]], test_keys: set[tuple[str, ...]]) -> list[DiffRow]:
    rows: list[DiffRow] = []
    for key in sorted(test_keys - prod_keys):
        rows.append(DiffRow(category, "|".join(key), "", "present", "new"))
    for key in sorted(prod_keys & test_keys):
        rows.append(DiffRow(category, "|".join(key), "present", "present", "duplicate"))
    for key in sorted(prod_keys - test_keys):
        rows.append(DiffRow(category, "|".join(key), "present", "", "missing_in_test"))
    return rows


def count_action(rows: list[DiffRow], action: str) -> int:
    return sum(1 for row in rows if row.action == action)


def write_workbook(output: Path, series: list[DiffRow], raw_comments: list[DiffRow], task_results: list[DiffRow]) -> None:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "summary"
    summary.append(["metric", "value"])
    all_rows = series + raw_comments + task_results
    for category in ("series", "raw_comments", "task_results"):
        for action in ("new", "duplicate", "conflict", "missing_in_test"):
            summary.append([f"{category}_{action}", sum(1 for row in all_rows if row.category == category and row.action == action)])

    for title, rows in [
        ("series_diff", series),
        ("raw_comment_diff", raw_comments),
        ("task_result_diff", task_results),
    ]:
        sheet = workbook.create_sheet(title)
        sheet.append(["category", "key", "prod_value", "test_value", "action"])
        for row in rows:
            sheet.append([row.category, row.key, row.prod_value, row.test_value, row.action])

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit production/test cutover differences.")
    parser.add_argument("--prod-url", required=True)
    parser.add_argument("--test-url", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        with session_for(args.prod_url) as prod, session_for(args.test_url) as test:
            series = compare_series(prod, test)
            raw_comments = compare_key_sets("raw_comments", raw_comment_keys(prod), raw_comment_keys(test))
            task_results = compare_key_sets("task_results", task_result_keys(prod), task_result_keys(test))
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output = Path(args.output)
    write_workbook(output, series, raw_comments, task_results)
    print(
        " ".join(
            [
                f"series_new={count_action(series, 'new')}",
                f"series_conflict={count_action(series, 'conflict')}",
                f"raw_comments_new={count_action(raw_comments, 'new')}",
                f"task_results_new={count_action(task_results, 'new')}",
                f"output={output}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
