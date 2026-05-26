from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.models import Base, ConfirmedVehicleSeries
from app.services.confirmed_vehicle_series import query_key
from app.services.series_admin import SeriesMutation, create_series_record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync active confirmed vehicle series rows between databases.")
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--operator", required=True)
    return parser.parse_args()


def require_tables(database_url: str, *table_names: str) -> None:
    engine = create_engine(database_url, future=True)
    inspector = inspect(engine)
    missing = [table_name for table_name in table_names if not inspector.has_table(table_name)]
    if missing:
        raise RuntimeError(f"database is missing required tables: {', '.join(missing)}")


def sync_confirmed_series(*, source_url: str, target_url: str, operator: str) -> dict[str, int]:
    require_tables(source_url, ConfirmedVehicleSeries.__tablename__)
    source_engine = create_engine(source_url, future=True)
    target_engine = create_engine(target_url, future=True)
    Base.metadata.create_all(target_engine)
    SourceSession = sessionmaker(bind=source_engine, future=True)
    TargetSession = sessionmaker(bind=target_engine, future=True)
    summary = {"new": 0, "duplicate": 0, "conflict": 0}

    with SourceSession() as source_db, TargetSession() as target_db:
        rows = (
            source_db.query(ConfirmedVehicleSeries)
            .filter(ConfirmedVehicleSeries.status == "active")
            .order_by(ConfirmedVehicleSeries.id.asc())
            .all()
        )
        for row in rows:
            normalized_query_key = query_key(row.query)
            normalized_platform = row.platform.strip()
            normalized_series_id = row.series_id.strip()
            existing = (
                target_db.query(ConfirmedVehicleSeries)
                .filter(
                    ConfirmedVehicleSeries.query_key == normalized_query_key,
                    ConfirmedVehicleSeries.platform == normalized_platform,
                    ConfirmedVehicleSeries.status == "active",
                )
                .one_or_none()
            )
            if existing is not None and existing.series_id == normalized_series_id:
                summary["duplicate"] += 1
                continue

            mutation = SeriesMutation(
                query=row.query,
                platform=row.platform,
                series_id=row.series_id,
                url=row.url,
                title=row.title,
                source=row.source,
                operator=operator,
                reason="legacy sync",
            )
            create_series_record(target_db, mutation)
            if existing is None:
                summary["new"] += 1
            else:
                summary["conflict"] += 1

        target_db.commit()

    return summary


def main() -> int:
    args = parse_args()
    try:
        summary = sync_confirmed_series(source_url=args.source_url, target_url=args.target_url, operator=args.operator)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"new={summary['new']} duplicate={summary['duplicate']} conflict={summary['conflict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
