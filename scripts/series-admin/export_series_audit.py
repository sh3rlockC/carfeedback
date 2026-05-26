from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.models import Base, SeriesAuditLog, SeriesConflict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export series audit and conflict rows to an Excel workbook.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def cell_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def append_model_rows(worksheet, model: type[Any], rows: list[Any]) -> None:
    columns = [column.name for column in model.__table__.columns]
    worksheet.append(columns)
    for row in rows:
        worksheet.append([cell_value(getattr(row, column)) for column in columns])


def export_series_audit(*, database_url: str, output: Path) -> Path:
    engine = create_engine(database_url, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    workbook = Workbook()
    audit_sheet = workbook.active
    audit_sheet.title = "series_audit"
    conflict_sheet = workbook.create_sheet("series_conflicts")

    with SessionLocal() as db:
        audit_rows = db.query(SeriesAuditLog).order_by(SeriesAuditLog.id.asc()).all()
        conflict_rows = db.query(SeriesConflict).order_by(SeriesConflict.id.asc()).all()

    append_model_rows(audit_sheet, SeriesAuditLog, audit_rows)
    append_model_rows(conflict_sheet, SeriesConflict, conflict_rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def main() -> None:
    args = parse_args()
    output = export_series_audit(database_url=args.database_url, output=Path(args.output))
    print(output)


if __name__ == "__main__":
    main()
