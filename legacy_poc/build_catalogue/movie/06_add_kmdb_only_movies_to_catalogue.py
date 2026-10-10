import os
import re

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


SELECT_KMDB_ONLY_MOVIES_SQL = """
SELECT
    kmdb.kmdb_source_id,
    kmdb.title,
    kmdb.genre
FROM raw_kmdb_catalogue AS kmdb
WHERE NOT EXISTS (
    SELECT 1
    FROM content_source_map AS source_map
    WHERE source_map.source = 'kmdb'
      AND source_map.source_content_type = 'movie'
      AND source_map.source_id = kmdb.kmdb_source_id
)
ORDER BY kmdb.kmdb_source_id;
"""

INSERT_CONTENT_SQL = """
INSERT INTO content_catalogue (
    content_type,
    title,
    overview,
    keywords
)
VALUES (
    'movie',
    %(title)s,
    NULL,
    %(keywords)s
)
RETURNING content_id;
"""

INSERT_SOURCE_MAP_SQL = """
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


def unique_texts(values):
    result = []
    seen = set()

    for value in values:
        if not value:
            continue

        text = str(value).strip()

        if not text:
            continue

        if text.lower() in seen:
            continue

        seen.add(text.lower())
        result.append(text)

    return result


def split_genres(genre_text):
    if not genre_text:
        return []

    return [
        genre.strip()
        for genre in re.split(r"[,|/]", genre_text)
        if genre.strip()
    ]


def add_kmdb_only_movies():
    created_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SELECT_KMDB_ONLY_MOVIES_SQL)
            movies = cursor.fetchall()

            print(
                f"새 content_id 생성 대상: {len(movies)}개"
            )

            for kmdb_source_id, title, genre in movies:
                cursor.execute(
                    INSERT_CONTENT_SQL,
                    {
                        "title": title,
                        "keywords": Jsonb(
                            unique_texts(
                                split_genres(genre)
                            )
                        ),
                    },
                )

                content_id = cursor.fetchone()[0]

                cursor.execute(
                    INSERT_SOURCE_MAP_SQL,
                    {
                        "content_id": content_id,
                        "kmdb_source_id": kmdb_source_id,
                    },
                )

                created_count += 1

                if created_count % 1000 == 0:
                    connection.commit()
                    print(
                        f"KMDb 전용 영화 적재: "
                        f"{created_count}개"
                    )

        connection.commit()

    print()
    print(
        f"KMDb 전용 영화 카탈로그 추가 완료: "
        f"{created_count}개"
    )


if __name__ == "__main__":
    add_kmdb_only_movies()