from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys

from openpyxl import Workbook, load_workbook
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.db import init_db, reset_engine_cache
from app.main import create_app
from app.models import ConfirmedVehicleSeries, SeriesConflict, SeriesImportBatch
from app.services.passphrase import hash_passphrase
from app.services.confirmed_vehicle_series import query_key
from app.services.series_admin import SeriesMutation, create_series_record
from app.services.series_import_export import commit_series_import, export_series_excel, preview_series_import


def _session(tmp_path: Path):
    reset_engine_cache()
    settings = Settings(app_env="test", database_url=f"sqlite+pysqlite:///{tmp_path / 'series.db'}")
    init_db(settings)
    engine = create_engine(settings.database_url, future=True)
    return sessionmaker(bind=engine, future=True)


def _admin_client(tmp_path: Path, *, access_control_enabled: bool = False) -> TestClient:
    reset_engine_cache()
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+pysqlite:///{tmp_path / 'series-api.db'}",
        access_control_enabled=access_control_enabled,
        pass_phrase_hash=hash_passphrase("weekly-secret"),
    )
    return TestClient(create_app(settings))


def _csv(rows: list[dict[str, str]]) -> bytes:
    header = ["query", "platform", "series_id", "url", "title", "source"]
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(row.get(column, "") for column in header))
    return ("\ufeff" + "\n".join(lines) + "\n").encode("utf-8")


def _multipart_body(
    parts: list[tuple[str, str, bytes, str, dict[str, str] | None]],
    *,
    boundary: str = "series-boundary",
) -> tuple[bytes, str]:
    body = bytearray()
    for name, filename, content, content_type, extra_headers in parts:
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {content_type}\r\n".encode("utf-8"))
        for key, value in (extra_headers or {}).items():
            body.extend(f"{key}: {value}\r\n".encode("utf-8"))
        body.extend(b"\r\n")
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _xlsx(rows: list[dict[str, str]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["query", "platform", "series_id", "url", "title", "source"])
    for row in rows:
        ws.append([row.get("query"), row.get("platform"), row.get("series_id"), row.get("url"), row.get("title"), row.get("source")])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _xlsx_with_header(header: list[str]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _empty_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.delete_rows(1, ws.max_row)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _seed_existing(db) -> None:
    create_series_record(
        db,
        SeriesMutation(
            query="风云T11",
            platform="autohome",
            series_id="7411",
            url="https://k.autohome.com.cn/7411",
            title="风云T11",
            source="manual",
            operator="tester",
            reason="seed",
        ),
    )
    db.commit()


def test_preview_counts_new_duplicate_and_conflict(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        _seed_existing(db)

        preview = preview_series_import(
            db,
            filename="series.csv",
            content=_csv(
                [
                    {"query": "风云T11", "platform": "autohome", "series_id": "7411"},
                    {"query": "风云T11", "platform": "autohome", "series_id": "9999"},
                    {"query": "风云T11", "platform": "dongchedi", "series_id": "5498"},
                ]
            ),
        )

        assert preview.summary == {"new": 1, "duplicate": 1, "conflict": 1, "invalid": 0}
        assert [row.status for row in preview.rows] == ["duplicate", "conflict", "new"]
        conflict = preview.rows[1]
        assert conflict.existing_value_json["series_id"] == "7411"
        assert conflict.incoming_value_json["series_id"] == "9999"


def test_commit_import_writes_only_new_rows_and_conflicts(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        _seed_existing(db)

        preview = commit_series_import(
            db,
            filename="series.csv",
            operator="operator-1",
            content=_csv(
                [
                    {"query": "风云T11", "platform": "autohome", "series_id": "7411"},
                    {"query": "风云T11", "platform": "autohome", "series_id": "9999", "url": "https://example.test/9999", "title": "T11 alt", "source": "sheet"},
                    {"query": "风云T11", "platform": "dongchedi", "series_id": "5498", "url": "https://example.test/5498", "title": "T11 DCD", "source": "sheet"},
                ]
            ),
        )

        assert preview.summary == {"new": 1, "duplicate": 1, "conflict": 1, "invalid": 0}
        batch = db.query(SeriesImportBatch).one()
        assert batch.source == "import"
        assert batch.filename == "series.csv"
        assert batch.operator == "operator-1"
        assert batch.summary_json == preview.summary
        records = db.query(ConfirmedVehicleSeries).order_by(ConfirmedVehicleSeries.platform).all()
        assert [(row.platform, row.series_id, row.import_batch_id) for row in records] == [
            ("autohome", "7411", None),
            ("dongchedi", "5498", batch.id),
        ]
        conflicts = db.query(SeriesConflict).all()
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "series_import"
        assert conflicts[0].status == "open"
        assert conflicts[0].import_batch_id == batch.id
        assert conflicts[0].existing_value_json["series_id"] == "7411"
        assert conflicts[0].incoming_value_json["series_id"] == "9999"
        assert conflicts[0].incoming_value_json["url"] == "https://example.test/9999"


def test_export_series_excel_contains_active_rows(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        _seed_existing(db)

        content = export_series_excel(db)

    wb = load_workbook(BytesIO(content))
    ws = wb["confirmed_vehicle_series"]
    assert [cell.value for cell in ws[1]] == ["query", "platform", "series_id", "url", "title", "source", "status", "updated_at"]
    assert [cell.value for cell in ws[2][:7]] == ["风云T11", "autohome", "7411", "https://k.autohome.com.cn/7411", "风云T11", "manual", "active"]


def test_preview_supports_xlsx_with_same_columns(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        preview = preview_series_import(
            db,
            filename="series.xlsx",
            content=_xlsx([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}]),
        )

        assert preview.summary == {"new": 1, "duplicate": 0, "conflict": 0, "invalid": 0}
        assert preview.rows[0].query_key == query_key("风云T11")


def test_invalid_rows_are_counted_but_not_committed(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        preview = commit_series_import(
            db,
            filename="series.csv",
            operator="operator-1",
            content=_csv(
                [
                    {"query": "", "platform": "autohome", "series_id": "7411"},
                    {"query": "风云T11", "platform": "bad-platform", "series_id": "7411"},
                    {"query": "风云T11", "platform": "autohome", "series_id": ""},
                ]
            ),
        )

        assert preview.summary == {"new": 0, "duplicate": 0, "conflict": 0, "invalid": 3}
        assert db.query(ConfirmedVehicleSeries).count() == 0
        assert db.query(SeriesConflict).count() == 0
        assert db.query(SeriesImportBatch).one().summary_json == preview.summary


def test_duplicate_rows_are_counted_but_not_stored_as_conflicts(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        _seed_existing(db)

        preview = commit_series_import(
            db,
            filename="series.csv",
            operator="operator-1",
            content=_csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}]),
        )

        assert preview.summary == {"new": 0, "duplicate": 1, "conflict": 0, "invalid": 0}
        assert db.query(ConfirmedVehicleSeries).count() == 1
        assert db.query(SeriesConflict).count() == 0


def test_in_file_duplicate_same_id_counts_duplicate_and_commits_one_record(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    content = _csv(
        [
            {"query": "风云T11", "platform": "autohome", "series_id": "7411"},
            {"query": " 风云T11 ", "platform": "autohome", "series_id": "7411"},
        ]
    )
    with SessionLocal() as db:
        preview = preview_series_import(db, filename="series.csv", content=content)

        assert preview.summary == {"new": 1, "duplicate": 1, "conflict": 0, "invalid": 0}
        assert [row.status for row in preview.rows] == ["new", "duplicate"]

        committed = commit_series_import(db, filename="series.csv", content=content, operator="operator-1")

        assert committed.summary == {"new": 1, "duplicate": 1, "conflict": 0, "invalid": 0}
        assert db.query(ConfirmedVehicleSeries).count() == 1
        assert db.query(SeriesConflict).count() == 0


def test_in_file_conflict_different_id_creates_series_import_conflict(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    content = _csv(
        [
            {"query": "风云T11", "platform": "autohome", "series_id": "7411", "url": "https://example.test/7411"},
            {"query": "风云T11", "platform": "autohome", "series_id": "9999", "url": "https://example.test/9999"},
        ]
    )
    with SessionLocal() as db:
        preview = preview_series_import(db, filename="series.csv", content=content)

        assert preview.summary == {"new": 1, "duplicate": 0, "conflict": 1, "invalid": 0}
        assert [row.status for row in preview.rows] == ["new", "conflict"]

        committed = commit_series_import(db, filename="series.csv", content=content, operator="operator-1")

        assert committed.summary == {"new": 1, "duplicate": 0, "conflict": 1, "invalid": 0}
        batch = db.query(SeriesImportBatch).one()
        records = db.query(ConfirmedVehicleSeries).all()
        assert len(records) == 1
        assert records[0].series_id == "7411"
        assert records[0].import_batch_id == batch.id
        conflict = db.query(SeriesConflict).one()
        assert conflict.conflict_type == "series_import"
        assert conflict.import_batch_id == batch.id
        assert conflict.existing_value_json["series_id"] == "7411"
        assert conflict.incoming_value_json["series_id"] == "9999"


def test_commit_reclassifies_duplicate_after_db_drift_without_mutating_existing_batch(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])
    with SessionLocal() as db:
        assert preview_series_import(db, filename="series.csv", content=content).summary == {
            "new": 1,
            "duplicate": 0,
            "conflict": 0,
            "invalid": 0,
        }
        existing = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7411", operator="drift", reason="drift"),
        )
        db.commit()

        committed = commit_series_import(db, filename="series.csv", content=content, operator="operator-1")

        batch = db.query(SeriesImportBatch).one()
        assert committed.summary == {"new": 0, "duplicate": 1, "conflict": 0, "invalid": 0}
        assert batch.summary_json == committed.summary
        assert db.get(ConfirmedVehicleSeries, existing.id).import_batch_id is None
        assert db.query(ConfirmedVehicleSeries).count() == 1
        assert db.query(SeriesConflict).count() == 0


def test_commit_reclassifies_conflict_after_db_drift_without_mutating_existing_batch(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])
    with SessionLocal() as db:
        assert preview_series_import(db, filename="series.csv", content=content).summary["new"] == 1
        existing = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="9999", operator="drift", reason="drift"),
        )
        db.commit()

        committed = commit_series_import(db, filename="series.csv", content=content, operator="operator-1")

        batch = db.query(SeriesImportBatch).one()
        assert committed.summary == {"new": 0, "duplicate": 0, "conflict": 1, "invalid": 0}
        assert batch.summary_json == committed.summary
        assert db.get(ConfirmedVehicleSeries, existing.id).import_batch_id is None
        assert db.query(ConfirmedVehicleSeries).count() == 1
        conflict = db.query(SeriesConflict).one()
        assert conflict.conflict_type == "series_import"
        assert conflict.import_batch_id == batch.id
        assert conflict.existing_value_json["series_id"] == "9999"
        assert conflict.incoming_value_json["series_id"] == "7411"


def test_missing_headers_are_rejected_for_csv_and_xlsx(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="missing columns.*series_id"):
            preview_series_import(db, filename="series.csv", content=b"query,platform\nA,autohome\n")
        with pytest.raises(ValueError, match="missing columns.*platform"):
            preview_series_import(db, filename="series.xlsx", content=_xlsx_with_header(["query", "series_id"]))


def test_empty_workbook_is_rejected(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="missing header"):
            preview_series_import(db, filename="series.xlsx", content=_empty_xlsx())


def test_xls_is_rejected_with_clean_value_error(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match=r"supports \.csv, \.xlsx, and \.xlsm"):
            preview_series_import(db, filename="series.xls", content=b"not an xls")


def test_invalid_utf8_csv_is_rejected_with_clean_value_error(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="invalid csv encoding"):
            preview_series_import(db, filename="series.csv", content=b"\xff\xfe\x80")


def test_parse_errors_happen_before_autoflush_of_pending_duplicates(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        _seed_existing(db)
        db.add(
            ConfirmedVehicleSeries(
                query_key=query_key("风云T11"),
                query="风云T11",
                platform="autohome",
                series_id="9999",
                status="active",
            )
        )

        with pytest.raises(ValueError, match="invalid csv encoding"):
            preview_series_import(db, filename="series.csv", content=b"\xff\xfe\x80")
        db.rollback()

    with SessionLocal() as db:
        _seed_existing(db)
        db.add(
            ConfirmedVehicleSeries(
                query_key=query_key("风云T11"),
                query="风云T11",
                platform="autohome",
                series_id="9999",
                status="active",
            )
        )

        with pytest.raises(ValueError, match="invalid excel file"):
            commit_series_import(db, filename="series.xlsx", content=b"not a workbook", operator="operator-1")


def test_csv_headers_are_stripped_before_row_mapping(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    content = b" query, platform, series_id\n \xe9\xa3\x8e\xe4\xba\x91T11 , autohome , 7411\n"
    with SessionLocal() as db:
        preview = preview_series_import(db, filename="series.csv", content=content)

        assert preview.summary == {"new": 1, "duplicate": 0, "conflict": 0, "invalid": 0}
        assert preview.rows[0].query == "风云T11"
        assert preview.rows[0].platform == "autohome"
        assert preview.rows[0].series_id == "7411"


def test_corrupt_xlsx_is_rejected_with_clean_value_error(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        with pytest.raises(ValueError, match="invalid excel file"):
            preview_series_import(db, filename="series.xlsx", content=b"not a workbook")
        with pytest.raises(ValueError, match="invalid excel file"):
            preview_series_import(db, filename="series.xlsm", content=b"not a workbook")


def test_commit_series_import_does_not_commit_unrelated_pending_data(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        db.add(
            ConfirmedVehicleSeries(
                query_key=query_key("未提交车型"),
                query="未提交车型",
                platform="autohome",
                series_id="1000",
                status="active",
            )
        )
        committed = commit_series_import(
            db,
            filename="series.csv",
            content=_csv([{"query": "风云T11", "platform": "dongchedi", "series_id": "5498"}]),
            operator="operator-1",
        )

        assert committed.summary == {"new": 1, "duplicate": 0, "conflict": 0, "invalid": 0}
        assert db.query(ConfirmedVehicleSeries).count() == 2
        db.rollback()

    with SessionLocal() as db:
        assert db.query(ConfirmedVehicleSeries).count() == 0
        assert db.query(SeriesImportBatch).count() == 0


def test_preview_series_import_does_not_autoflush_unrelated_pending_data(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        pending = ConfirmedVehicleSeries(
            query_key=query_key("未提交车型"),
            query="未提交车型",
            platform="autohome",
            series_id="1000",
            status="active",
        )
        db.add(pending)

        preview = preview_series_import(
            db,
            filename="series.csv",
            content=_csv([{"query": "风云T11", "platform": "dongchedi", "series_id": "5498"}]),
        )

        assert preview.summary["new"] == 1
        assert pending.id is None
        db.rollback()


def test_export_order_is_stable_for_duplicate_identity_history_and_nullable_updated_at(tmp_path: Path) -> None:
    SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        active = create_series_record(
            db,
            SeriesMutation(query="风云T11", platform="autohome", series_id="7411", operator="tester", reason="seed"),
        )
        deleted = ConfirmedVehicleSeries(
            query_key=query_key("风云T11"),
            query="风云T11",
            platform="autohome",
            series_id="7400",
            status="deleted",
        )
        other = ConfirmedVehicleSeries(
            query_key=query_key("风云T11"),
            query="风云T11",
            platform="dongchedi",
            series_id="5498",
            status="active",
        )
        db.add_all([deleted, other])
        db.commit()
        db.refresh(active)
        db.refresh(deleted)
        db.refresh(other)
        active.updated_at = None

        with db.no_autoflush:
            content = export_series_excel(db)

    ws = load_workbook(BytesIO(content))["confirmed_vehicle_series"]
    rows = [tuple(cell.value for cell in row) for row in ws.iter_rows(min_row=2)]
    assert [(row[1], row[6], row[2]) for row in rows] == [
        ("autohome", "active", "7411"),
        ("autohome", "deleted", "7400"),
        ("dongchedi", "active", "5498"),
    ]
    assert rows[0][7] is None


def test_series_import_preview_api_accepts_multipart_file(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])

    response = client.post(
        "/api/admin/series/import/preview",
        files={"file": ("series.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["new"] == 1
    assert body["rows"][0]["query_key"] == "风云t11"
    assert body["rows"][0]["status"] == "new"


def test_series_import_commit_api_persists_multipart_file(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])

    response = client.post(
        "/api/admin/series/import/commit?operator=tester",
        files={"file": ("series.csv", content, "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["summary"]["new"] == 1
    list_response = client.get("/api/admin/series?search=风云T11&platform=autohome&status=active")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert list_response.json()["items"][0]["series_id"] == "7411"


def test_series_import_api_rejects_bad_file_and_blank_operator(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])

    bad_file = client.post(
        "/api/admin/series/import/preview",
        files={"file": ("series.csv", b"query,platform\nA,autohome\n", "text/csv")},
    )
    blank_operator = client.post(
        "/api/admin/series/import/commit?operator=%20%20%20",
        files={"file": ("series.csv", content, "text/csv")},
    )

    assert bad_file.status_code == 400
    assert "missing columns" in bad_file.json()["detail"]
    assert blank_operator.status_code == 400
    assert blank_operator.json()["detail"] == "operator must not be blank"


def test_series_import_api_rejects_oversized_upload_by_content_length(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)

    response = client.post(
        "/api/admin/series/import/preview",
        content=b"",
        headers={
            "x-filename": "series.csv",
            "content-type": "text/csv",
            "content-length": str(5 * 1024 * 1024 + 1),
        },
    )

    assert response.status_code == 413


def test_series_import_api_rejects_ambiguous_multipart_uploads(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    first = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])
    second = _csv([{"query": "风云X3L", "platform": "autohome", "series_id": "8208"}])

    duplicate_files = client.post(
        "/api/admin/series/import/preview",
        files=[
            ("file", ("series-a.csv", first, "text/csv")),
            ("file", ("series-b.csv", second, "text/csv")),
        ],
    )
    blank_filename = client.post(
        "/api/admin/series/import/preview",
        files={"file": ("", first, "text/csv")},
    )

    assert duplicate_files.status_code == 400
    assert duplicate_files.json()["detail"] == "multiple file fields are not supported"
    assert blank_filename.status_code == 400
    assert blank_filename.json()["detail"] == "missing filename"


def test_series_import_api_rejects_content_transfer_encoding_and_bad_multipart(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])
    encoded_body, content_type = _multipart_body(
        [("file", "series.csv", content, "text/csv", {"Content-Transfer-Encoding": "base64"})]
    )

    encoded_response = client.post(
        "/api/admin/series/import/preview",
        content=encoded_body,
        headers={"content-type": content_type},
    )
    bad_multipart = client.post(
        "/api/admin/series/import/preview",
        content=b"not multipart",
        headers={"content-type": "multipart/form-data"},
    )

    assert encoded_response.status_code == 400
    assert encoded_response.json()["detail"] == "content-transfer-encoding is not supported"
    assert bad_multipart.status_code == 400
    assert bad_multipart.json()["detail"] == "invalid multipart upload"


def test_series_export_api_returns_xlsx_after_route_seed(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    create_response = client.post(
        "/api/admin/series",
        json={
            "query": "风云T11",
            "platform": "autohome",
            "series_id": "7411",
            "operator": "tester",
            "reason": "seed for export",
        },
    )
    assert create_response.status_code == 201

    response = client.get("/api/admin/series/export.xlsx")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "attachment" in response.headers["content-disposition"]
    assert len(response.content) > 1000
    ws = load_workbook(BytesIO(response.content))["confirmed_vehicle_series"]
    assert [cell.value for cell in ws[2][:3]] == ["风云T11", "autohome", "7411"]


def test_series_import_export_static_routes_are_not_captured_by_record_id_route(tmp_path: Path) -> None:
    client = _admin_client(tmp_path)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])

    preview = client.post(
        "/api/admin/series/import/preview",
        files={"file": ("series.csv", content, "text/csv")},
    )
    export = client.get("/api/admin/series/export.xlsx")

    assert preview.status_code == 200
    assert export.status_code == 200
    assert preview.json()["summary"]["new"] == 1
    assert export.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_series_import_export_routes_require_admin_access_when_enabled(tmp_path: Path) -> None:
    client = _admin_client(tmp_path, access_control_enabled=True)
    content = _csv([{"query": "风云T11", "platform": "autohome", "series_id": "7411"}])

    unauthorized_preview = client.post(
        "/api/admin/series/import/preview",
        files={"file": ("series.csv", content, "text/csv")},
    )
    unauthorized_export = client.get("/api/admin/series/export.xlsx")

    assert unauthorized_preview.status_code == 401
    assert unauthorized_export.status_code == 401

    verify_response = client.post("/api/access/verify", json={"passphrase": "weekly-secret"})
    assert verify_response.status_code == 200

    authorized_export = client.get("/api/admin/series/export.xlsx")
    assert authorized_export.status_code == 200
