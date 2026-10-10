import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

INPUT_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "selections"
    / "korean_movie_tmdb_ids.jsonl"
)

GENRE_NAMES = {
    28: "액션",
    12: "모험",
    16: "애니메이션",
    35: "코미디",
    80: "범죄",
    99: "다큐멘터리",
    18: "드라마",
    10751: "가족",
    14: "판타지",
    36: "역사",
    27: "공포",
    10402: "음악",
    9648: "미스터리",
    10749: "로맨스",
    878: "SF",
    10770: "TV 영화",
    53: "스릴러",
    10752: "전쟁",
    37: "서부",
}

SELECT_CATALOGUE_SQL = """
SELECT
    c.content_id,
    c.overview,
    c.keywords
FROM content_source_map AS sm
JOIN content_catalogue AS c
  ON c.content_id = sm.content_id
WHERE sm.source = 'tmdb'
  AND sm.source_content_type = 'movie'
  AND sm.source_id = %(tmdb_id)s;
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
    %(overview)s,
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
    'tmdb',
    'movie',
    %(tmdb_id)s
);
"""

UPDATE_CONTENT_SQL = """
UPDATE content_catalogue
SET
    overview = CASE
        WHEN NULLIF(BTRIM(overview), '') IS NULL
        THEN %(overview)s
        ELSE overview
    END,
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

        text = str(value).strip()

        if not text:
            continue

        normalized = text.lower()

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(text)

    return result


def load_movies():
    with INPUT_PATH.open(encoding="utf-8") as input_file:
        return [
            json.loads(line)
            for line in input_file
            if line.strip()
        ]


def get_catalogue_row(cursor, tmdb_id):
    cursor.execute(
        SELECT_CATALOGUE_SQL,
        {"tmdb_id": str(tmdb_id)},
    )
    return cursor.fetchone()


def make_keywords(movie, existing_keywords=None):
    genre_names = [
        GENRE_NAMES[genre_id]
        for genre_id in movie.get("genre_ids", [])
        if genre_id in GENRE_NAMES
    ]

    return unique_texts(
        [
            *(existing_keywords or []),
            movie.get("title"),
            movie.get("original_title"),
            *genre_names,
        ]
    )


def build_movie_catalogue_from_discovery():
    movies = load_movies()

    created_count = 0
    updated_count = 0
    invalid_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            for movie in movies:
                tmdb_id = movie.get("tmdb_id")
                title = movie.get("title")

                if not tmdb_id or not title:
                    invalid_count += 1
                    continue

                overview = (movie.get("overview") or "").strip() or None
                row = get_catalogue_row(cursor, tmdb_id)

                if row is None:
                    keywords = make_keywords(movie)

                    cursor.execute(
                        INSERT_CONTENT_SQL,
                        {
                            "title": title,
                            "overview": overview,
                            "keywords": Jsonb(keywords),
                        },
                    )

                    content_id = cursor.fetchone()[0]

                    cursor.execute(
                        INSERT_SOURCE_MAP_SQL,
                        {
                            "content_id": content_id,
                            "tmdb_id": str(tmdb_id),
                        },
                    )

                    created_count += 1
                    continue

                content_id, _, existing_keywords = row

                if not isinstance(existing_keywords, list):
                    existing_keywords = []

                cursor.execute(
                    UPDATE_CONTENT_SQL,
                    {
                        "content_id": content_id,
                        "overview": overview,
                        "keywords": Jsonb(
                            make_keywords(movie, existing_keywords)
                        ),
                    },
                )

                updated_count += 1

        connection.commit()

    print(f"새 카탈로그 추가: {created_count}개")
    print(f"기존 카탈로그 보강: {updated_count}개")
    print(f"필수값 누락으로 건너뜀: {invalid_count}개")


if __name__ == "__main__":
    build_movie_catalogue_from_discovery()