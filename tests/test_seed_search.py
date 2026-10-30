"""The optional demo generator reproduces the business scenario and pagination."""
import json

from .test_filters import catalog as catalog


def test_search_seed_is_repeatable_and_covers_business_scenario(catalog, monkeypatch):
    from keepstack import search
    from keepstack.config import config
    from keepstack.seed_search import run_seed

    monkeypatch.setattr(config, "ocr_enabled", False)
    result = run_seed()
    assert result["generated_assets"] == 38
    before = [row["uuid"] for row in catalog.execute("SELECT uuid FROM assets ORDER BY id")]
    assert len(before) == 48
    filters = dict(q="harbor", collection_id=result["collection_id"],
                   include_tags=["approved", "campaign"], exclude_tags=["internal"], sort="oldest")
    pages = [search.search(**filters, media_type="image", limit=12, offset=offset) for offset in (0, 12, 24)]
    assert [len(page["items"]) for page in pages] == [12, 12, 7]
    assert all(page["total"] == 31 for page in pages)
    assert len({item["uuid"] for page in pages for item in page["items"]}) == 31
    rows = list(search.export_rows(**filters, media_type="image"))
    assert [row["uuid"] for row in rows] == [item["uuid"] for page in pages for item in page["items"]]
    assert rows[0]["description"] == '第一行, "中文说明"\n第二行'
    assert '宣传, "精选"' in json.loads(rows[0]["tags"])
    assert search.search(**filters)["total"] == 32
    assert run_seed() == result
    assert [row["uuid"] for row in catalog.execute("SELECT uuid FROM assets ORDER BY id")] == before
