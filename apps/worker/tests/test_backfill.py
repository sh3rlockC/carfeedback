from __future__ import annotations

import json
import sys
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worker_app.backfill import backfill_raw_excels
from worker_app.corpus import AUTOHOME_HEADERS, DCD_HEADERS, load_platform_state


def _write_workbook(path: Path, headers: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "口碑明细"
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    workbook.save(path)


def test_backfill_imports_autohome_workbook_into_raw_corpus(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'corpus.db'}"
    autohome_xlsx = tmp_path / "autohome.xlsx"
    _write_workbook(
        autohome_xlsx,
        AUTOHOME_HEADERS,
        [
            {
                "用户名": "车主A",
                "发表日期": "2026-05-01",
                "车型": "测试车 2026款",
                "评价详情": "汽车之家历史评论",
                "来源链接": "https://k.autohome.com.cn/detail/view_01abc.html",
                "抓取页码": "1",
            }
        ],
    )

    summary = backfill_raw_excels(
        database_url=database_url,
        corpus_root=tmp_path / "corpus",
        model_name="测试车",
        query="测试车",
        autohome_series_id="8089",
        autohome_xlsx=autohome_xlsx,
        dcd_series_id="25398",
        dcd_xlsx=None,
    )

    assert summary["platforms"]["autohome"]["inserted_count"] == 1
    assert summary["platforms"]["autohome"]["total_count"] == 1
    assert load_platform_state(database_url, query="测试车", platform="autohome", series_id="8089").existing_count == 1
    assert Path(summary["corpus"]["platforms"]["autohome"]["raw_path"]).exists()
    assert Path(summary["summary_json_path"]).exists()


def test_backfill_imports_dongchedi_workbook_into_raw_corpus(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'corpus.db'}"
    dcd_xlsx = tmp_path / "dongchedi.xlsx"
    _write_workbook(
        dcd_xlsx,
        DCD_HEADERS,
        [
            {
                "用户名": "车主B",
                "评价车型": "测试车 2026款",
                "发布时间": "2026-05-02",
                "评价全文": "懂车帝历史评论",
                "来源链接": "https://www.dongchedi.com/koubei/123",
                "抓取页码": "2",
            }
        ],
    )

    summary = backfill_raw_excels(
        database_url=database_url,
        corpus_root=tmp_path / "corpus",
        model_name="测试车",
        query="测试车",
        autohome_series_id="8089",
        autohome_xlsx=None,
        dcd_series_id="25398",
        dcd_xlsx=dcd_xlsx,
    )

    assert summary["platforms"]["dongchedi"]["inserted_count"] == 1
    assert summary["platforms"]["dongchedi"]["total_count"] == 1
    assert load_platform_state(database_url, query="测试车", platform="dongchedi", series_id="25398").existing_count == 1
    assert Path(summary["corpus"]["platforms"]["dongchedi"]["known_links_path"]).read_text(encoding="utf-8").splitlines() == [
        "https://www.dongchedi.com/koubei/123"
    ]


def test_backfill_skips_missing_platform_without_fabricating_rows(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'corpus.db'}"
    autohome_xlsx = tmp_path / "autohome.xlsx"
    missing_dcd_xlsx = tmp_path / "missing-dcd.xlsx"
    _write_workbook(
        autohome_xlsx,
        AUTOHOME_HEADERS,
        [
            {
                "用户名": "车主A",
                "发表日期": "2026-05-01",
                "评价详情": "只有汽车之家",
                "来源链接": "https://k.autohome.com.cn/detail/view_01abc.html",
            }
        ],
    )

    summary = backfill_raw_excels(
        database_url=database_url,
        corpus_root=tmp_path / "corpus",
        model_name="测试车",
        query="测试车",
        autohome_series_id="8089",
        autohome_xlsx=autohome_xlsx,
        dcd_series_id="25398",
        dcd_xlsx=missing_dcd_xlsx,
    )

    assert summary["platforms"]["dongchedi"]["skipped"] is True
    assert summary["platforms"]["dongchedi"]["reason"] == "missing_file"
    assert summary["platforms"]["dongchedi"]["inserted_count"] == 0
    assert load_platform_state(database_url, query="测试车", platform="dongchedi", series_id="25398").existing_count == 0


def test_backfill_writes_event_summary_json(tmp_path: Path) -> None:
    database_url = f"sqlite+pysqlite:///{tmp_path / 'corpus.db'}"
    autohome_xlsx = tmp_path / "autohome.xlsx"
    summary_json = tmp_path / "events" / "backfill-summary.json"
    _write_workbook(
        autohome_xlsx,
        AUTOHOME_HEADERS,
        [
            {
                "用户名": "车主A",
                "发表日期": "2026-05-01",
                "评价详情": "需要记录事件",
                "来源链接": "https://k.autohome.com.cn/detail/view_01abc.html",
            }
        ],
    )

    summary = backfill_raw_excels(
        database_url=database_url,
        corpus_root=tmp_path / "corpus",
        model_name="测试车",
        query="测试车",
        autohome_series_id="8089",
        autohome_xlsx=autohome_xlsx,
        dcd_series_id="25398",
        dcd_xlsx=None,
        summary_json_path=summary_json,
    )

    persisted = json.loads(summary_json.read_text(encoding="utf-8"))
    assert summary["summary_json_path"] == str(summary_json)
    assert persisted["event_type"] == "raw_excel_backfill"
    assert persisted["query"] == "测试车"
    assert persisted["platforms"]["autohome"]["source_path"] == str(autohome_xlsx)
    assert persisted["platforms"]["autohome"]["inserted_count"] == 1
    assert persisted["platforms"]["dongchedi"]["skipped"] is True
