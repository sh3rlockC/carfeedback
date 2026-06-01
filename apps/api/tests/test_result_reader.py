from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.services.result_reader import read_wordcloud_terms_workbook


def test_read_wordcloud_terms_workbook_merges_outer_quoted_keyword_variants(tmp_path: Path) -> None:
    workbook_path = tmp_path / "terms.xlsx"
    workbook = Workbook()
    positive = workbook.active
    positive.title = "positive_terms"
    positive.append(["term", "weight"])
    positive.append(["空间宽敞", 48])
    positive.append(["「空间宽敞」", 43])
    positive.append(["动力顺", 12])

    negative = workbook.create_sheet("negative_terms")
    negative.append(["term", "weight"])
    negative.append(["「车机卡顿」", 8])
    negative.append(["车机卡顿", 5])
    workbook.save(workbook_path)

    rankings = read_wordcloud_terms_workbook(workbook_path, limit=10)

    assert rankings["positive"][:2] == [
        {"term": "空间宽敞", "count": 91},
        {"term": "动力顺", "count": 12},
    ]
    assert rankings["negative"] == [{"term": "车机卡顿", "count": 13}]
    assert rankings["combined"][:3] == [
        {"term": "空间宽敞", "count": 91},
        {"term": "车机卡顿", "count": 13},
        {"term": "动力顺", "count": 12},
    ]
