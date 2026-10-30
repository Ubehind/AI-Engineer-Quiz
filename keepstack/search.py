"""Search and discovery.

Three complementary modes over one catalog:

  * keyword  - FTS5 full-text across title, description, tags, metadata
  * filter   - faceted structured filters (media type, tag, status, dates)
  * semantic - embedding cosine ranking ("natural language" / more-like-this)

Facet counts describe all active assets, independently of the current filters.
"""
from __future__ import annotations

from typing import Optional

from . import ai
from .db import get_conn

SORTS = {
    "newest": "a.created_at DESC",
    "oldest": "a.created_at ASC",
    "name": "a.title ASC",
    "largest": "a.size DESC",
}


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query."""
    terms = [t for t in "".join(c if c.isalnum() or c.isspace() else " " for c in q).split() if t]
    return " ".join(f'"{t}"*' for t in terms)


def _normalize_tags(names: list[str]) -> list[str]:
    return sorted({name.strip().lower() for name in names if name.strip()})


def _filter_sql(*, q, media_type, tag, include_tags, exclude_tags, collection_id, status):
    """Shared predicates for list queries and future unpaginated exports.

    EXISTS keeps each asset unique even when several tags/collections match.
    All user-provided values are bound parameters, including full tag names.
    """
    where = ["a.status = ?"]
    params: list = [status]
    join = ""
    if q.strip():
        fts_query = _fts_query(q)
        if fts_query:
            join = " JOIN asset_fts ON asset_fts.rowid = a.id"
            where.append("asset_fts MATCH ?")
            params.append(fts_query)
        else:
            # Nonblank punctuation-only input has no searchable terms.
            where.append("0")
    if media_type:
        where.append("a.media_type = ?")
        params.append(media_type)
    if collection_id is not None:
        where.append("EXISTS (SELECT 1 FROM collection_assets ca "
                     "WHERE ca.asset_id = a.id AND ca.collection_id = ?)")
        params.append(collection_id)

    included = _normalize_tags([*(include_tags or []), *([tag] if tag else [])])
    excluded = _normalize_tags(exclude_tags or [])
    for name in included:
        where.append("EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                     "WHERE at.asset_id = a.id AND t.name = ?)")
        params.append(name)
    if excluded:
        placeholders = ",".join("?" for _ in excluded)
        where.append("NOT EXISTS (SELECT 1 FROM asset_tags at JOIN tags t ON t.id = at.tag_id "
                     f"WHERE at.asset_id = a.id AND t.name IN ({placeholders}))")
        params.extend(excluded)
    return join, " AND ".join(where), params


def search(
    *,
    q: str = "",
    media_type: Optional[str] = None,
    tag: Optional[str] = None,
    include_tags: Optional[list[str]] = None,
    exclude_tags: Optional[list[str]] = None,
    collection_id: Optional[int] = None,
    status: str = "active",
    sort: str = "newest",
    mode: str = "keyword",
    limit: int = 60,
    offset: int = 0,
) -> dict:
    conn = get_conn()

    filters = dict(media_type=media_type, tag=tag, include_tags=include_tags,
                   exclude_tags=exclude_tags, collection_id=collection_id, status=status)
    # Semantic mode uses the same structured filters, without the FTS predicate.
    if mode == "semantic" and q.strip():
        return _semantic_search(q, filters, limit, offset)

    join, where_sql, params = _filter_sql(q=q, **filters)
    order = "bm25(asset_fts)" if join else SORTS.get(sort, SORTS["newest"])

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM assets a{join} WHERE {where_sql}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""SELECT a.* FROM assets a{join} WHERE {where_sql}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    return {
        "total": total,
        "items": [_card(dict(r)) for r in rows],
        "facets": _facets(status),
        "mode": mode,
    }


def _semantic_search(q, filters, limit, offset) -> dict:
    conn = get_conn()
    qvec = ai.embed(q)
    _, where_sql, params = _filter_sql(q="", **filters)
    rows = conn.execute(
        f"SELECT a.* FROM assets a WHERE {where_sql} AND a.embedding IS NOT NULL", params
    ).fetchall()
    scored = []
    for r in rows:
        score = ai.cosine(qvec, ai.unpack_embedding(r["embedding"]))
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    page = scored[offset:offset + limit]
    items = []
    for score, r in page:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        items.append(card)
    return {"total": len(scored), "items": items, "facets": _facets(filters["status"]), "mode": "semantic"}


def more_like(asset_id: int, limit: int = 12) -> list[dict]:
    conn = get_conn()
    base = conn.execute("SELECT embedding FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if not base or not base["embedding"]:
        return []
    bvec = ai.unpack_embedding(base["embedding"])
    rows = conn.execute(
        "SELECT * FROM assets WHERE id != ? AND status = 'active' AND embedding IS NOT NULL",
        (asset_id,),
    ).fetchall()
    scored = [(ai.cosine(bvec, ai.unpack_embedding(r["embedding"])), r) for r in rows]
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, r in scored[:limit]:
        card = _card(dict(r))
        card["score"] = round(score, 4)
        out.append(card)
    return out


def _facets(status: str) -> dict:
    conn = get_conn()
    media = conn.execute(
        "SELECT media_type, COUNT(*) c FROM assets WHERE status = ? GROUP BY media_type ORDER BY c DESC",
        (status,),
    ).fetchall()
    tags = conn.execute(
        """SELECT t.name, COUNT(*) c FROM tags t
           JOIN asset_tags at ON at.tag_id = t.id
           JOIN assets a ON a.id = at.asset_id AND a.status = ?
           GROUP BY t.name ORDER BY c DESC LIMIT 25""",
        (status,),
    ).fetchall()
    return {
        "media_types": [{"value": r["media_type"], "count": r["c"]} for r in media],
        "tags": [{"value": r["name"], "count": r["c"]} for r in tags],
    }


def _card(a: dict) -> dict:
    """Compact asset representation for grid/list views."""
    return {
        "id": a["id"],
        "uuid": a["uuid"],
        "title": a["title"],
        "filename": a["filename"],
        "media_type": a["media_type"],
        "mime_type": a["mime_type"],
        "ext": a["ext"],
        "size": a["size"],
        "width": a["width"],
        "height": a["height"],
        "alt_text": a["alt_text"],
        "status": a["status"],
        "created_at": a["created_at"],
    }
