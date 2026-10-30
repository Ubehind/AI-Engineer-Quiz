"""Combination filters against reproducible, isolated SQLite fixtures.

Run with: uv run --no-sync python -m pytest tests/test_filters.py -q
No local catalog, uploaded files or external services are used.
"""
import threading

import pytest
from fastapi.testclient import TestClient

SPECIAL_TAG = '研发, "精选" / %_ + & <x>'
ALL_ACTIVE = {"match", "internal", "partial", "outside", "document", "forest", "prefix", "secret", "special"}
APPROVED_CAMPAIGN = {"match", "internal", "outside", "document", "forest", "secret"}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    # Delay project imports until collection finishes: test_core configures its
    # own temporary catalog at import time and must never inherit a live path.
    from keepstack import ai, db, ingest
    from keepstack.config import config

    # Separate connections (including TestClient worker threads) and paths per test.
    monkeypatch.setattr(db, "_local", threading.local())
    for attr, path in {
        "data_dir": tmp_path, "db_path": tmp_path / "catalog.db",
        "blob_dir": tmp_path / "blobs", "thumb_dir": tmp_path / "thumbs",
        "cache_dir": tmp_path / "cache",
    }.items():
        monkeypatch.setattr(config, attr, path)
    db.init_db()
    conn = db.get_conn()
    conn.execute("INSERT INTO users (id, username, password_hash, role, created_at) "
                 "VALUES (1, 'tester', 'unused', 'viewer', '2026-01-01')")
    conn.executemany("INSERT INTO collections (id, uuid, name, created_at) VALUES (?, ?, ?, '2026-01-01')",
                     [(1, "campaign", "Campaign"), (2, "other", "Other")])
    rows = [
        ("match", "Harbor launch", ["approved", "campaign"], 1, "image", "active"),
        ("internal", "Harbor internal", ["approved", "campaign", "internal"], 1, "image", "active"),
        ("partial", "Harbor draft", ["approved"], 1, "image", "active"),
        ("outside", "Harbor other collection", ["approved", "campaign"], 2, "image", "active"),
        ("document", "Harbor report", ["approved", "campaign"], 1, "document", "active"),
        ("deleted", "Harbor deleted", ["approved", "campaign"], 1, "image", "deleted"),
        ("forest", "Forest launch", ["approved", "campaign"], 1, "image", "active"),
        ("prefix", "Harbor partial tag", ["approved-plus", "campaign"], 1, "image", "active"),
        ("secret", "Harbor confidential", ["approved", "campaign", "secret"], 1, "image", "active"),
        ("special", "中文素材", [SPECIAL_TAG, "team red"], 1, "image", "active"),
    ]
    for uuid, title, tags, collection, media, status in rows:
        cur = conn.execute(
            "INSERT INTO assets (uuid, sha256, filename, title, description, media_type, status, "
            "storage_key, created_at, updated_at, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid, uuid, uuid + ".dat", title, "fixture description", media, status,
             uuid, "2026-01-01", "2026-01-01", ai.pack_embedding([1.0, 0.0])),
        )
        aid = cur.lastrowid
        ingest._set_tags(conn, aid, [(t, 1.0) for t in tags], "manual")
        conn.execute("INSERT INTO collection_assets (collection_id, asset_id) VALUES (?, ?)",
                     (collection, aid))
        ingest.reindex_fts(aid)
    conn.commit()
    yield conn
    conn.close()


@pytest.mark.parametrize(("filters", "expected"), [
    ({}, ALL_ACTIVE),
    ({"q": " \t\n "}, ALL_ACTIVE),
    ({"q": " \t\n ", "include_tags": ["approved", "campaign"]}, APPROVED_CAMPAIGN),
    ({"include_tags": ["approved", "campaign"]}, APPROVED_CAMPAIGN),
    ({"exclude_tags": ["internal", "secret"]}, ALL_ACTIVE - {"internal", "secret"}),
    ({"q": "harbor", "collection_id": 1, "media_type": "image",
      "include_tags": ["approved", "campaign"], "exclude_tags": ["internal", "secret"]}, {"match"}),
    ({"include_tags": [" APPROVED ", "approved", "CAMPAIGN", " "]}, APPROVED_CAMPAIGN),
    ({"include_tags": ["approved"], "exclude_tags": ["APPROVED", "approved"]}, set()),
    ({"include_tags": ["does-not-exist"]}, set()),
    ({"exclude_tags": ["does-not-exist"]}, ALL_ACTIVE),
    ({"exclude_tags": [" APPROVED ", "approved"]}, {"prefix", "special"}),
    ({"include_tags": ["approve"]}, set()),
    ({"include_tags": [SPECIAL_TAG, "TEAM RED"]}, {"special"}),
    ({"exclude_tags": [SPECIAL_TAG]}, ALL_ACTIVE - {"special"}),
    ({"include_tags": ["' OR 1=1 --"]}, set()),
    ({"q": 'harbor" OR --'}, set()),
    ({"q": '"%_*() + --'}, set()),
    ({"q": "中文"}, {"special"}),
    ({"collection_id": 999}, set()),
    ({"tag": "approved", "include_tags": ["campaign"], "exclude_tags": ["internal"]},
     APPROVED_CAMPAIGN - {"internal"}),
])
def test_combination_filters(catalog, filters, expected):
    from keepstack import search

    result = search.search(**filters)
    assert result["total"] == len(expected)
    assert len(result["items"]) == len(expected)
    assert {item["uuid"] for item in result["items"]} == expected
    assert all(item["status"] == "active" for item in result["items"])


def test_existing_fts_fields_and_tokenizer_are_preserved(catalog):
    from keepstack import ingest, search

    aid = catalog.execute("SELECT id FROM assets WHERE uuid = 'match'").fetchone()["id"]
    catalog.execute("UPDATE assets SET description = 'Running waterfront inspection', "
                    "alt_text = 'NeedleAlt' WHERE id = ?", (aid,))
    ingest._set_tags(catalog, aid, [("needletag", 1.0)], "manual")
    catalog.execute("INSERT INTO asset_metadata (asset_id, raw_json) VALUES (?, ?)",
                    (aid, '{"exif": {"camera": "NeedleMuseum"}}'))
    ingest.reindex_fts(aid)
    for keyword in ["harb", "run", "waterfront", "match", "needletag", "NeedleAlt", "NeedleMuseum"]:
        result = search.search(q=keyword, include_tags=["approved", "campaign"],
                               collection_id=1, exclude_tags=["internal", "secret"], media_type="image")
        assert {item["uuid"] for item in result["items"]} == {"match"}, keyword


def test_semantic_mode_cannot_bypass_structured_filters(catalog, monkeypatch):
    from keepstack import ai, search

    monkeypatch.setattr(ai, "embed", lambda _: [1.0, 0.0])
    result = search.search(q="harbor", mode="semantic", collection_id=1, media_type="image",
                           include_tags=["approved", "campaign"], exclude_tags=["internal", "secret"])
    assert {item["uuid"] for item in result["items"]} == {"match", "forest"}


def test_list_api_accepts_repeated_tag_parameters_and_requires_auth(catalog):
    from keepstack.app import app
    from keepstack.auth import make_token

    with TestClient(app) as client:
        assert client.get("/api/assets").status_code == 401
        client.headers["Authorization"] = "Bearer " + make_token(1, "viewer")
        response = client.get("/api/assets", params=[
            ("q", "harbor"), ("collection_id", "1"), ("media_type", "image"),
            ("include_tags", "APPROVED"), ("include_tags", "campaign"), ("include_tags", "approved"),
            ("exclude_tags", "internal"), ("exclude_tags", "secret"),
        ])
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert response.json()["items"][0]["uuid"] == "match"
        response = client.get("/api/assets", params=[("include_tags", SPECIAL_TAG), ("include_tags", "team red")])
        assert response.status_code == 200
        assert response.json()["items"][0]["uuid"] == "special"
        assert client.get("/api/assets", params={"status": "deleted"}).status_code == 422
        assert client.get("/api/assets", params={"collection_id": 0}).status_code == 422
