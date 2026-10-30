"""Generate repeatable search acceptance data without external services."""
from __future__ import annotations

import io
import uuid

from PIL import Image, ImageDraw

from . import ingest
from .auth import hash_password
from .config import config
from .db import get_conn, init_db


def _uuid(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "keepstack-search-acceptance/" + key))


def _image(index: int) -> bytes:
    image = Image.new("RGB", (320, 220), ((index * 17) % 256, 50, 110))
    ImageDraw.Draw(image).text((24, 96), f"Search acceptance {index:02d}", fill="white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def run_seed() -> dict:
    """Reset only these named fixture assets; reruns keep UUIDs and row counts."""
    init_db()
    conn = get_conn()
    admin = conn.execute("SELECT * FROM users WHERE username=?", (config.admin_user,)).fetchone()
    if admin is None:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, 'admin', ?)",
            (config.admin_user, hash_password(config.admin_password), "2026-01-01T00:00:00+00:00"),
        )
        conn.commit()
        admin = conn.execute("SELECT * FROM users WHERE username=?", (config.admin_user,)).fetchone()
    collections = []
    for key, name in [("main", "Search Acceptance"), ("other", "Search Acceptance Other")]:
        collection_uuid = _uuid("collection/" + key)
        conn.execute(
            "INSERT OR IGNORE INTO collections (uuid, name, description, created_at) VALUES (?, ?, ?, ?)",
            (collection_uuid, name, "Generated search acceptance data", "2026-01-01T00:00:00+00:00"),
        )
        collections.append(conn.execute("SELECT id FROM collections WHERE uuid=?", (collection_uuid,)).fetchone()["id"])
    conn.commit()

    rows = [(f"image-{i:02d}", f"Harbor {i + 1:02d}", ["approved", "campaign"], 0, "active") for i in range(31)]
    rows += [
        ("internal-1", "Harbor internal 1", ["approved", "campaign", "internal"], 0, "active"),
        ("internal-2", "Harbor internal 2", ["approved", "campaign", "internal"], 0, "active"),
        ("partial", "Harbor partial", ["campaign"], 0, "active"),
        ("forest", "Forest scenery", ["approved", "campaign"], 0, "active"),
        ("outside", "Harbor outside collection", ["approved", "campaign"], 1, "active"),
        ("document", "Harbor briefing", ["approved", "campaign"], 0, "active"),
        ("deleted", "Harbor deleted", ["approved", "campaign"], 0, "deleted"),
    ]
    for index, (key, title, tags, collection, status) in enumerate(rows):
        asset_uuid = _uuid("asset/" + key)
        existing = conn.execute("SELECT id FROM assets WHERE uuid=?", (asset_uuid,)).fetchone()
        filename = "acceptance-" + key + (".txt" if key == "document" else ".png")
        description = "Generated acceptance fixture."
        if index == 0:
            title = 'Harbor 01 港口, "活动"'
            filename = '港口, "活动".png'
            description = '第一行, "中文说明"\n第二行'
            tags = [*tags, '宣传, "精选"']
        if existing is None:
            data = b"Search acceptance briefing" if key == "document" else _image(index)
            asset = ingest.ingest_stream(io.BytesIO(data), filename, user=dict(admin), run_ai=False, title=title)
            aid = asset["id"]
            conn.execute("UPDATE assets SET uuid=? WHERE id=?", (asset_uuid, aid))
        else:
            aid = existing["id"]
        created = "2026-01-01T12:00:00+00:00"
        if index == 0:
            created = "2025-01-01T12:00:00+00:00"
        elif index == 30:
            created = "2027-01-01T12:00:00+00:00"
        conn.execute(
            "UPDATE assets SET title=?, description=?, filename=?, status=?, created_at=?, updated_at=? WHERE id=?",
            (title, description, filename, status, created, created, aid),
        )
        conn.execute("DELETE FROM asset_tags WHERE asset_id=?", (aid,))
        ingest._set_tags(conn, aid, [(tag, 1.0) for tag in tags], "manual")
        conn.execute("DELETE FROM collection_assets WHERE asset_id=?", (aid,))
        conn.execute("INSERT INTO collection_assets (collection_id, asset_id) VALUES (?, ?)", (collections[collection], aid))
        conn.commit()
        ingest.reindex_fts(aid)
        ingest.reindex_embedding(aid)
    result = {"collection_id": collections[0], "generated_assets": len(rows), "matching_images": 31, "matching_all_types": 32}
    print("Search acceptance data:", result)
    return result
