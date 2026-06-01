from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.abspath(sys.path[0]) == script_dir:
        sys.path.pop(0)
    parent_dir = os.path.dirname(script_dir)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

from worker_app.comparison_outputs import VehicleSnapshot, generate_comparison_outputs
from worker_app.hermes_outputs import generate_time_report_outputs


def _load_snapshots(path: Path) -> list[VehicleSnapshot]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("snapshots") if isinstance(payload, dict) else payload
    snapshots: list[VehicleSnapshot] = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        snapshots.append(
            VehicleSnapshot(
                model_name=str(item["model_name"]),
                source_job_id=str(item["source_job_id"]),
                final_report_path=Path(item["final_report_path"]),
                analysis_facts_path=Path(item["analysis_facts_path"]),
                llm_metrics_path=Path(item["llm_metrics_path"]) if item.get("llm_metrics_path") else None,
            )
        )
    return snapshots


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate batch AI artifacts for OpenClaw/Hermes stages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    time_report = subparsers.add_parser("time-report")
    time_report.add_argument("--autohome-input", required=True)
    time_report.add_argument("--dcd-input")
    time_report.add_argument("--output-dir", required=True)
    time_report.add_argument("--model-name", required=True)
    time_report.add_argument("--start-date", required=True)
    time_report.add_argument("--end-date", required=True)
    time_report.add_argument("--progress-file", required=True)
    time_report.add_argument("--summary-script", required=True)
    time_report.add_argument("--wordcloud-script", required=True)
    time_report.add_argument("--hermes-command", default=os.getenv("HERMES_COMMAND", "hermes"))
    time_report.add_argument("--font-path")
    time_report.add_argument("--skill-first", action="store_true")
    time_report.add_argument("--source-label")

    comparison = subparsers.add_parser("comparison")
    comparison.add_argument("--snapshots-json", required=True)
    comparison.add_argument("--output-dir", required=True)
    comparison.add_argument("--start-date")
    comparison.add_argument("--end-date")
    comparison.add_argument("--source-label")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.command == "time-report":
        result = generate_time_report_outputs(
            autohome_input=args.autohome_input,
            dcd_input=args.dcd_input,
            output_dir=args.output_dir,
            model_name=args.model_name,
            start_date=args.start_date,
            end_date=args.end_date,
            summary_script=args.summary_script,
            wordcloud_script=args.wordcloud_script,
            hermes_command=args.hermes_command,
            font_path=args.font_path,
            env=dict(os.environ),
            progress_file=args.progress_file,
            skill_first=args.skill_first,
            source_label=args.source_label,
        )
    else:
        result = generate_comparison_outputs(
            snapshots=_load_snapshots(Path(args.snapshots_json)),
            output_dir=Path(args.output_dir),
            start_date=args.start_date,
            end_date=args.end_date,
            env=dict(os.environ),
            source_label=args.source_label,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
