"""Mutations through existing routes must update both keyword queries and CSV."""
import json
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from .test_export import parse_csv
from .test_filters import catalog as catalog


@pytest.fixture
def editor(catalog):
    from keepstack.app import app
    from keepstack.auth import make_token

    catalog.execute("UPDATE users SET role='contributor' WHERE id=1")
    catalog.commit()
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + make_token(1, "contributor")
        yield client


def assert_matching_ids(client, params, expected):
    response = client.get("/api/assets", params=params)
    assert response.status_code == 200
    result = response.json()
    assert result["total"] == len(expected)
    assert [item["uuid"] for item in result["items"]] == expected
    rows = parse_csv(client.get("/api/assets/export.csv", params=params))
    assert [row["uuid"] for row in rows] == expected
    return rows


def test_title_and_description_edits_replace_search_terms_and_export_values(editor):
    assert_matching_ids(editor, {"q": "launch", "include_tags": "approved", "collection_id": 1, "sort": "oldest"}, ["match", "forest"])
    title = '新标题, "港口" TitleNeedle'
    description = '新描述, "活动"\n换行 DescriptionNeedle'
    response = editor.patch("/api/assets/match", json={"title": title, "description": description})
    assert response.status_code == 200
    assert_matching_ids(editor, {"q": "launch", "include_tags": "approved", "collection_id": 1}, ["forest"])
    for keyword in ["TitleNeedle", "DescriptionNeedle"]:
        rows = assert_matching_ids(editor, {"q": keyword}, ["match"])
        assert rows[0]["title"] == title
        assert rows[0]["description"] == description
    assert editor.patch("/api/assets/match", json={"title": "Replaced", "description": ""}).status_code == 200
    assert_matching_ids(editor, {"q": "TitleNeedle"}, [])
    assert_matching_ids(editor, {"q": "DescriptionNeedle"}, [])


@pytest.mark.parametrize("tag", ["TagNeedle", '标签, "精选" / + & %_ #'])
def test_added_and_removed_tags_update_keyword_include_exclude_and_csv(editor, tag):
    normalized = tag.lower()
    assert_matching_ids(editor, {"include_tags": tag}, [])
    assert editor.post("/api/assets/match/tags", json={"name": " " + tag + " "}).status_code == 200
    rows = assert_matching_ids(editor, {"include_tags": tag}, ["match"])
    assert normalized in json.loads(rows[0]["tags"])
    keyword = "TagNeedle" if tag == "TagNeedle" else "标签"
    assert_matching_ids(editor, {"q": keyword}, ["match"])
    params = [("q", "harbor"), ("collection_id", 1), ("include_tags", "approved"),
              ("include_tags", "campaign"), ("media_type", "image"),
              ("exclude_tags", "internal"), ("exclude_tags", "secret"), ("exclude_tags", tag)]
    assert_matching_ids(editor, params, [])
    assert editor.delete("/api/assets/match/tags/" + quote(tag, safe="")).status_code == 200
    assert_matching_ids(editor, {"include_tags": tag}, [])
    assert_matching_ids(editor, {"q": keyword}, [])
    rows = assert_matching_ids(editor, params, ["match"])
    assert normalized not in json.loads(rows[0]["tags"])


def test_deactivated_asset_disappears_from_next_query_and_export(editor):
    assert_matching_ids(editor, {"include_tags": "approved", "q": "launch", "sort": "oldest"}, ["match", "forest"])
    assert editor.patch("/api/assets/match", json={"status": "deleted"}).status_code == 200
    assert_matching_ids(editor, {"include_tags": "approved", "q": "launch"}, ["forest"])
