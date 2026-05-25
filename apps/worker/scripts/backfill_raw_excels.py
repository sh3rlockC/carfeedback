from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKER_ROOT))

from worker_app.backfill import backfill_raw_excels


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical raw koubei Excel files into the long-term corpus.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--corpus-root", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--autohome-series-id", required=True)
    parser.add_argument("--autohome-xlsx")
    parser.add_argument("--dcd-series-id", required=True)
    parser.add_argument("--dcd-xlsx")
    parser.add_argument("--summary-json")
    parser.add_argument("--job-id")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    summary = backfill_raw_excels(
        database_url=args.database_url,
        corpus_root=args.corpus_root,
        model_name=args.model_name,
        query=args.query,
        autohome_series_id=args.autohome_series_id,
        autohome_xlsx=args.autohome_xlsx,
        dcd_series_id=args.dcd_series_id,
        dcd_xlsx=args.dcd_xlsx,
        summary_json_path=args.summary_json,
        job_id=args.job_id,
    )
    print(json.dumps({"summary_json_path": summary["summary_json_path"], "platforms": summary["platforms"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
