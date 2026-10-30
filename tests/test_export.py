"""Authenticated CSV exports must match the complete ordered query result."""
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from .test_filters import SPECIAL_TAG, catalog as catalog


COLUMNS = ["uuid", "title", "description", "filename", "media_type", "tags", "created_at"]


def parse_csv(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="assets.csv"'
    assert response.content.startswith(b"\xef\xbb\xbf")
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline=""))
    assert reader.fieldnames == COLUMNS
    return list(reader)


@pytest.fixture
def client(catalog):
    from keepstack.app import app
    from keepstack.auth import make_token

    with TestClient(app) as instance:
        instance.headers["Authorization"] = "Bearer " + make_token(1, "viewer")
        yield instance


@pytest.mark.parametrize("sort", ["oldest", "newest"])
@pytest.mark.parametrize("filters", [
    [],
    [("q", "harbor"), ("collection_id", "1"), ("include_tags", "approved"),
     ("include_tags", "campaign"), ("exclude_tags", "internal"), ("media_type", "image")],
    [("q", " \t\n "), ("media_type", "document")],
    [("include_tags", "approved"), ("exclude_tags", "APPROVED")],
    [("include_tags", SPECIAL_TAG)],
    [("q", '"%_*()')],
    [("tag", "approved"), ("include_tags", "campaign"), ("include_tags", "APPROVED")],
])
def test_export_matches_all_pages_in_order(client, filters, sort):
    params = [*filters, ("sort", sort)]
    expected = []
    offset = 0
    while True:
        response = client.get("/api/assets", params=[*params, ("limit", 2), ("offset", offset)])
        assert response.status_code == 200
        result = response.json()
        expected.extend(item["uuid"] for item in result["items"])
        offset += 2
        if offset >= result["total"]:
            break
    # Even if a caller sends list pagination parameters, the export is complete.
    exported = parse_csv(client.get("/api/assets/export.csv", params=[*params, ("limit", 1), ("offset", 999)]))
    assert [row["uuid"] for row in exported] == expected
    assert all(row["uuid"] != "deleted" for row in exported)
    for row in exported:
        assert json.loads(row["tags"]) == sorted(json.loads(row["tags"]))


def test_csv_preserves_unicode_delimiters_quotes_and_newlines(catalog, client):
    title = '中文标题, "精选"\n下一行'
    description = '第一行, "引号"\n第二行\r\n第三行'
    filename = '中文, "片段"\n.dat'
    catalog.execute("UPDATE assets SET title=?, description=?, filename=? WHERE uuid='special'",
                    (title, description, filename))
    catalog.commit()
    rows = parse_csv(client.get("/api/assets/export.csv", params={"include_tags": SPECIAL_TAG}))
    assert len(rows) == 1
    assert rows[0]["title"] == title
    assert rows[0]["description"] == description
    assert rows[0]["filename"] == filename
    assert json.loads(rows[0]["tags"]) == sorted([SPECIAL_TAG, "team red"])


def test_empty_export_still_has_header(client):
    rows = parse_csv(client.get("/api/assets/export.csv", params={"include_tags": "missing"}))
    assert rows == []


def test_export_requires_login_and_rejects_invalid_filters(catalog):
    from keepstack.app import app
    from keepstack.auth import make_token

    with TestClient(app) as instance:
        assert instance.get("/api/assets/export.csv").status_code == 401
        instance.headers["Authorization"] = "Bearer invalid"
        assert instance.get("/api/assets/export.csv").status_code == 401
        instance.headers["Authorization"] = "Bearer " + make_token(1, "viewer")
        for params in [{"status": "deleted"}, {"sort": "bad"}, {"collection_id": -1}, {"mode": "semantic"}]:
            assert instance.get("/api/assets/export.csv", params=params).status_code == 422


def test_export_has_no_list_page_cap_and_batches_all_tags(catalog, client):
    from keepstack import ingest

    for index in range(215):
        uuid = f"bulk-{index:03}"
        cur = catalog.execute(
            "INSERT INTO assets (uuid, sha256, filename, title, media_type, status, storage_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'image', 'active', ?, '2026-01-01', '2026-01-01')",
            (uuid, uuid, uuid + ".jpg", "Harbor bulk", uuid),
        )
        aid = cur.lastrowid
        ingest._set_tags(catalog, aid, [("approved", 1.0), ("campaign", 1.0)], "manual")
        catalog.execute("INSERT INTO collection_assets (collection_id, asset_id) VALUES (1, ?)", (aid,))
        ingest.reindex_fts(aid)
    params = [("q", "harbor"), ("collection_id", 1), ("include_tags", "approved"),
              ("include_tags", "campaign"), ("exclude_tags", "internal"), ("media_type", "image"), ("sort", "oldest")]
    rows = parse_csv(client.get("/api/assets/export.csv", params=params))
    assert len(rows) == 217
    assert [row["uuid"] for row in rows] == ["match", "secret", *[f"bulk-{index:03}" for index in range(215)]]
    for row in rows[2:]:
        assert json.loads(row["tags"]) == ["approved", "campaign"]
