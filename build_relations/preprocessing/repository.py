"""기존 DB의 설명·키워드 조회. 읽기 전용."""
import os
import psycopg
from psycopg.rows import dict_row
from ..wikidata import config  # 프로젝트 루트의 .env 로드


def load_base(content_id):
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.content_id, c.content_type, c.title, c.release_year,
                    CASE c.content_type WHEN 'movie' THEN m.overview
                        WHEN 'tv' THEN t.overview ELSE b.description END AS description,
                    CASE c.content_type WHEN 'movie' THEN m.keywords
                        WHEN 'tv' THEN t.keywords ELSE '[]'::jsonb END AS keywords,
                    CASE c.content_type WHEN 'movie' THEN m.genres
                        WHEN 'tv' THEN t.genres ELSE '[]'::jsonb END AS genres,
                    b.description_source, b.book_edition_id
                FROM content c
                LEFT JOIN movie_detail m ON m.content_id = c.content_id
                LEFT JOIN tv_detail t ON t.content_id = c.content_id
                LEFT JOIN book_edition b ON b.content_id = c.content_id
                    AND b.is_representative = TRUE
                WHERE c.content_id = %s
                """, (content_id,),
            )
            row = cur.fetchone()
    if row is None:
        raise ValueError(f"DB에 작품이 없습니다: {content_id}")
    return dict(row)
