import os
import re

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


SELECT_MATCHED_MOVIES_SQL = """
SELECT
    match.tmdb_id,
    match.kmdb_source_id,
    kmdb.genre
FROM movie_catalogue_source_match AS match
JOIN raw_kmdb_catalogue AS kmdb
  ON kmdb.kmdb_source_id = match.kmdb_source_id
WHERE match.match_status = 'matched'
ORDER BY match.tmdb_id;
"""

SELECT_TMDB_CONTENT_SQL = """
SELECT content_id
FROM content_source_map
WHERE source = 'tmdb'
  AND source_content_type = 'movie'
  AND source_id = %(tmdb_id)s;
"""

SELECT_KMDB_CONTENT_SQL = """
SELECT content_id
FROM content_source_map
WHERE source = 'kmdb'
  AND source_content_type = 'movie'
  AND source_id = %(kmdb_source_id)s;
"""

SELECT_KEYWORDS_SQL = """
SELECT keywords
FROM content_catalogue
WHERE content_id = %(content_id)s;
"""

INSERT_KMDB_SOURCE_MAP_SQL = """
INSERT INTO content_source_map (
    content_id,
    source,
    source_content_type,
    source_id
)
VALUES (
    %(content_id)s,
    'kmdb',
    'movie',
    %(kmdb_source_id)s
);
"""

UPDATE_KEYWORDS_SQL = """
UPDATE content_catalogue
SET
    keywords = %(keywords)s,
    updated_at = NOW()
WHERE content_id = %(content_id)s;
"""


def unique_texts(values):
    result = []
    seen = set()

    for value in values:
        if not value:
            continue

        value = str(value).strip()

        if not value:
            continue

        normalized = value.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(value)

    return result


def split_genres(genre_text):
    if not genre_text:
        return []

    return [
        genre.strip()
        for genre in re.split(r"[,|]", genre_text)
        if genre.strip()
    ]


def get_content_id(cursor, sql, params):
    cursor.execute(sql, params)
    row = cursor.fetchone()

    return row[0] if row else None


def merge_kmdb_matches():
    linked_count = 0
    existing_count = 0
    conflict_count = 0
    missing_tmdb_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SELECT_MATCHED_MOVIES_SQL)
            matched_movies = cursor.fetchall()

            for tmdb_id, kmdb_source_id, genre in matched_movies:
                tmdb_content_id = get_content_id(
                    cursor,
                    SELECT_TMDB_CONTENT_SQL,
                    {"tmdb_id": str(tmdb_id)},
                )

                if tmdb_content_id is None:
                    missing_tmdb_count += 1
                    continue

                existing_kmdb_content_id = get_content_id(
                    cursor,
                    SELECT_KMDB_CONTENT_SQL,
                    {"kmdb_source_id": kmdb_source_id},
                )

                if existing_kmdb_content_id is None:
                    cursor.execute(
                        INSERT_KMDB_SOURCE_MAP_SQL,
                        {
                            "content_id": tmdb_content_id,
                            "kmdb_source_id": kmdb_source_id,
                        },
                    )
                    linked_count += 1

                elif existing_kmdb_content_id == tmdb_content_id:
                    existing_count += 1

                else:
                    # 이미 다른 content_id에 연결된 KMDb 작품은
                    # 자동으로 옮기지 않고 충돌로 기록한다.
                    conflict_count += 1
                    continue

                cursor.execute(
                    SELECT_KEYWORDS_SQL,
                    {"content_id": tmdb_content_id},
                )
                row = cursor.fetchone()

                existing_keywords = row[0] if row else []

                if not isinstance(existing_keywords, list):
                    existing_keywords = []

                merged_keywords = unique_texts(
                    [
                        *existing_keywords,
                        *split_genres(genre),
                    ]
                )

                cursor.execute(
                    UPDATE_KEYWORDS_SQL,
                    {
                        "content_id": tmdb_content_id,
                        "keywords": Jsonb(merged_keywords),
                    },
                )

        connection.commit()

    print(f"새 KMDb 연결: {linked_count}개")
    print(f"이미 같은 콘텐츠에 연결됨: {existing_count}개")
    print(f"다른 콘텐츠와 충돌: {conflict_count}개")
    print(f"TMDb content_id를 찾지 못함: {missing_tmdb_count}개")


if __name__ == "__main__":
    merge_kmdb_matches()