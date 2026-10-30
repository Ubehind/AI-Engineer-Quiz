"""Stable creation-time ordering and pagination boundary regression tests."""
import pytest
from fastapi.testclient import TestClient

from .test_filters import catalog as catalog


@pytest.mark.parametrize("q", ["", "harbor"])
@pytest.mark.parametrize("sort", ["oldest", "newest"])
def test_pages_follow_creation_time_with_id_tiebreaker(catalog, q, sort):
    from keepstack import search

    catalog.execute("UPDATE assets SET created_at = '2025-01-01' WHERE uuid = 'match'")
    catalog.execute("UPDATE assets SET created_at = '2027-01-01' WHERE uuid = 'partial'")
    catalog.commit()
    expected = ["match", "internal", "outside", "document", "forest", "prefix", "secret", "special", "partial"]
    if q:
        expected = [uuid for uuid in expected if uuid not in {"forest", "special"}]
    if sort == "newest":
        expected.reverse()
    combined = []
    for offset in range(0, len(expected) + 3, 3):
        result = search.search(q=q, sort=sort, limit=3, offset=offset)
        assert result["total"] == len(expected)
        assert [item["uuid"] for item in result["items"]] == expected[offset:offset + 3]
        combined.extend(item["uuid"] for item in result["items"])
    assert combined == expected
    assert len(combined) == len(set(combined))


@pytest.mark.parametrize("params", [
    {"limit": 0}, {"limit": -1}, {"limit": 201}, {"limit": "bad"}, {"limit": "1.5"},
    {"offset": -1}, {"offset": "bad"}, {"offset": "1.5"},
    {"sort": "created_at; DROP TABLE assets"}, {"mode": "unsupported"},
])
def test_invalid_list_parameters_return_422(catalog, params):
    from keepstack.app import app
    from keepstack.auth import make_token

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + make_token(1, "viewer")
        response = client.get("/api/assets", params=params)
        assert response.status_code == 422


def test_list_api_last_page_and_offset_past_end(catalog):
    from keepstack.app import app
    from keepstack.auth import make_token

    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + make_token(1, "viewer")
        first = client.get("/api/assets", params={"limit": 5, "offset": 0}).json()
        last = client.get("/api/assets", params={"limit": 5, "offset": 5}).json()
        empty = client.get("/api/assets", params={"limit": 5, "offset": 100}).json()
        assert first["total"] == last["total"] == empty["total"] == 9
        assert len(first["items"]) == 5 and len(last["items"]) == 4
        assert empty["items"] == []
        assert not ({item["uuid"] for item in first["items"]} & {item["uuid"] for item in last["items"]})


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (201, 0), (1, -1)])
def test_direct_query_rejects_invalid_pagination(catalog, limit, offset):
    from keepstack import search

    with pytest.raises(ValueError):
        search.search(limit=limit, offset=offset)
