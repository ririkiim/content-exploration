import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL


def load_content(content_id):
    if not DATABASE_URL:
        raise ValueError(".env에 DATABASE_URL을 설정하세요.")

    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.content_id, c.content_type, c.title,
                       c.original_title, c.release_year,
                       be.isbn13, be.pub_year, be.contributors
                FROM content c
                LEFT JOIN book_edition be
                    ON be.content_id = c.content_id
                   AND be.is_representative = TRUE
                WHERE c.content_id = %s
                """,
                (content_id,),
            )
            content = cur.fetchone()
            if content is None:
                raise ValueError(f"작품이 없습니다: content_id={content_id}")

            cur.execute(
                """
                SELECT source_name, source_id
                FROM content_source_map
                WHERE content_id = %s
                ORDER BY source_name, source_id
                """,
                (content_id,),
            )
            source_rows = cur.fetchall()

    sources = {}
    for row in source_rows:
        sources.setdefault(row["source_name"], []).append(str(row["source_id"]))

    authors = [
        item["name"].strip()
        for item in (content.pop("contributors") or [])
        if isinstance(item, dict)
        and item.get("role") == "author"
        and isinstance(item.get("name"), str)
        and item["name"].strip()
    ]
    content["sources"] = sources
    content["authors"] = list(dict.fromkeys(authors))
    return content
