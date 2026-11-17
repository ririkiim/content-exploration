"""DB는 읽기만 수행. 상세 JSON과 출처 ID를 그대로 가져온다."""
import os
import psycopg
from psycopg.rows import dict_row
from ..wikidata import config


def load_catalogue(content_id):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM content WHERE content_id=%s", (content_id,))
            content = cur.fetchone()
            if not content:
                raise ValueError(f"작품이 없습니다: {content_id}")
            table = {"movie": "movie_detail", "tv": "tv_detail", "book": "book_edition"}[content["content_type"]]
            condition = " AND is_representative=TRUE" if table == "book_edition" else ""
            cur.execute(f"SELECT * FROM {table} WHERE content_id=%s{condition}", (content_id,))
            detail = cur.fetchone()
            cur.execute("SELECT * FROM content_source_map WHERE content_id=%s", (content_id,))
            sources = cur.fetchall()
            cur.execute("""
                SELECT r.raw_kmdb_movie_id, r.raw_payload
                FROM content_source_map m JOIN raw_kmdb_movie r
                ON concat(r.raw_payload->>'영화등록번호ID', ':', r.raw_payload->>'영화등록번호NO')=m.source_id
                WHERE m.content_id=%s AND m.source_name='kmdb_movie'
                ORDER BY length(coalesce(trim(r.raw_payload->>'줄거리'),'')) DESC, r.raw_kmdb_movie_id
                LIMIT 1
            """, (content_id,))
            raw = cur.fetchone()
    return {"content": dict(content), "detail": dict(detail or {}),
            "sources": [dict(s) for s in sources], "kmdb_raw": dict(raw) if raw else None}


def match_target_content(qid=None, external_ids=None):
    pairs = []
    if qid:
        pairs.append(("wikidata", qid))
    external_ids = external_ids or {}
    for key, source in (("tmdb_movie", "tmdb_movie"), ("tmdb_tv", "tmdb_tv")):
        if external_ids.get(key):
            pairs.append((source, str(external_ids[key])))
    if not pairs:
        return []
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            conditions = " OR ".join("(source_name=%s AND source_id=%s)" for _ in pairs)
            cur.execute("SELECT DISTINCT content_id FROM content_source_map WHERE " + conditions,
                        [value for pair in pairs for value in pair])
            return sorted(row[0] for row in cur.fetchall())
