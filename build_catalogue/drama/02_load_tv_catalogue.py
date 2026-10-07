import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

INPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "selections"
    / "korean_drama_tmdb_ids.jsonl"
)

TV_GENRE_NAMES = {
    16: "애니메이션",
    18: "드라마",
    35: "코미디",
    37: "서부",
    80: "범죄",
    99: "다큐멘터리",
    9648: "미스터리",
    10751: "가족",
    10759: "액션/모험",
    10762: "키즈",
    10763: "뉴스",
    10764: "리얼리티",
    10765: "SF/판타지",
    10766: "소프 오페라",
    10767: "토크",
    10768: "전쟁/정치",
}

CREATE_CONTENT_CATALOGUE_SQL = """
CREATE TABLE IF NOT EXISTS content_catalogue (
    content_id BIGSERIAL PRIMARY KEY,

    content_type VARCHAR(20) NOT NULL
        CHECK (content_type IN ('movie', 'drama', 'book')),

    title TEXT NOT NULL,
    overview TEXT,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_SOURCE_MAP_SQL = """
CREATE TABLE IF NOT EXISTS content_source_map (
    content_id BIGINT NOT NULL
        REFERENCES content_catalogue(content_id),

    source VARCHAR(20) NOT NULL,
    source_content_type VARCHAR(20) NOT NULL,
    source_id TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    PRIMARY KEY (
        source,
        source_content_type,
        source_id
    )
);
"""

SELECT_EXISTING_SQL = """
SELECT
    catalogue.content_id,
    catalogue.overview,
    catalogue.keywords
FROM content_source_map AS source_map
JOIN content_catalogue AS catalogue
  ON catalogue.content_id = source_map.content_id
WHERE source_map.source = 'tmdb'
  AND source_map.source_content_type = 'tv'
  AND source_map.source_id = %(tmdb_id)s;
"""

INSERT_CONTENT_SQL = """
INSERT INTO content_catalogue (
    content_type,
    title,
    overview,
    keywords
)
VALUES (
    'drama',
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
    'tv',
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

        if text.lower() in seen:
            continue

        seen.add(text.lower())
        result.append(text)

    return result


def load_dramas():
    with INPUT_PATH.open(encoding="utf-8") as file:
        return [
            json.loads(line)
            for line in file
            if line.strip()
        ]


def make_keywords(drama, existing_keywords=None):
    genre_names = [
        TV_GENRE_NAMES[genre_id]
        for genre_id in drama.get("genre_ids", [])
        if genre_id in TV_GENRE_NAMES
    ]

    return unique_texts(
        [
            *(existing_keywords or []),
            drama.get("title"),
            drama.get("original_title"),
            *genre_names,
        ]
    )


def load_tv_catalogue():
    dramas = load_dramas()

    created_count = 0
    updated_count = 0
    invalid_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_CONTENT_CATALOGUE_SQL)
            cursor.execute(CREATE_SOURCE_MAP_SQL)

            for drama in dramas:
                tmdb_id = drama.get("tmdb_id")
                title = drama.get("title")

                if not tmdb_id or not title:
                    invalid_count += 1
                    continue

                overview = (
                    (drama.get("overview") or "").strip()
                    or None
                )

                cursor.execute(
                    SELECT_EXISTING_SQL,
                    {"tmdb_id": str(tmdb_id)},
                )
                existing = cursor.fetchone()

                if existing is None:
                    cursor.execute(
                        INSERT_CONTENT_SQL,
                        {
                            "title": title,
                            "overview": overview,
                            "keywords": Jsonb(
                                make_keywords(drama)
                            ),
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

                content_id, _, existing_keywords = existing

                if not isinstance(existing_keywords, list):
                    existing_keywords = []

                cursor.execute(
                    UPDATE_CONTENT_SQL,
                    {
                        "content_id": content_id,
                        "overview": overview,
                        "keywords": Jsonb(
                            make_keywords(
                                drama,
                                existing_keywords,
                            )
                        ),
                    },
                )

                updated_count += 1

        connection.commit()

    print(f"새 드라마 카탈로그 추가: {created_count}개")
    print(f"기존 드라마 카탈로그 보강: {updated_count}개")
    print(f"필수값 누락으로 건너뜀: {invalid_count}개")


if __name__ == "__main__":
    load_tv_catalogue()