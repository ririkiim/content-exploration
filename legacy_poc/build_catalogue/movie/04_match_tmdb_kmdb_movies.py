import json
import os
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import psycopg
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

TMDB_DISCOVERY_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "selections"
    / "korean_movie_tmdb_ids.jsonl"
)

CREATE_MATCH_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS movie_catalogue_source_match (
    tmdb_id BIGINT PRIMARY KEY,

    tmdb_title TEXT NOT NULL,
    tmdb_release_year INTEGER,

    kmdb_source_id TEXT,
    kmdb_title TEXT,
    kmdb_production_year INTEGER,

    match_method TEXT NOT NULL,
    candidate_count INTEGER NOT NULL DEFAULT 0,

    match_status TEXT NOT NULL
        CHECK (
            match_status IN (
                'matched',
                'review',
                'unmatched'
            )
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

UPSERT_MATCH_SQL = """
INSERT INTO movie_catalogue_source_match (
    tmdb_id,
    tmdb_title,
    tmdb_release_year,
    kmdb_source_id,
    kmdb_title,
    kmdb_production_year,
    match_method,
    candidate_count,
    match_status,
    updated_at
)
VALUES (
    %(tmdb_id)s,
    %(tmdb_title)s,
    %(tmdb_release_year)s,
    %(kmdb_source_id)s,
    %(kmdb_title)s,
    %(kmdb_production_year)s,
    %(match_method)s,
    %(candidate_count)s,
    %(match_status)s,
    NOW()
)
ON CONFLICT (tmdb_id)
DO UPDATE SET
    tmdb_title = EXCLUDED.tmdb_title,
    tmdb_release_year = EXCLUDED.tmdb_release_year,
    kmdb_source_id = EXCLUDED.kmdb_source_id,
    kmdb_title = EXCLUDED.kmdb_title,
    kmdb_production_year = EXCLUDED.kmdb_production_year,
    match_method = EXCLUDED.match_method,
    candidate_count = EXCLUDED.candidate_count,
    match_status = EXCLUDED.match_status,
    updated_at = NOW();
"""


def clean_text(value):
    if value is None:
        return ""

    value = str(value).strip()

    if value.lower() in {"", "null", "none"}:
        return ""

    return value


def normalize_title(title):
    title = unicodedata.normalize(
        "NFKC",
        clean_text(title),
    ).lower()

    return "".join(
        character
        for character in title
        if character.isalnum()
    )


def parse_year(date_text):
    date_text = clean_text(date_text)

    match = re.match(r"^(\d{4})", date_text)

    return int(match.group(1)) if match else None


def load_tmdb_movies():
    movies = []

    with TMDB_DISCOVERY_PATH.open(
        encoding="utf-8",
    ) as file:
        for line in file:
            if not line.strip():
                continue

            movie = json.loads(line)

            if movie.get("tmdb_id") and movie.get("title"):
                movies.append(movie)

    return movies


def load_kmdb_movies(cursor):
    cursor.execute(
        """
        SELECT
            kmdb_source_id,
            title,
            english_title,
            original_title,
            production_year,
            director
        FROM raw_kmdb_catalogue;
        """
    )

    return cursor.fetchall()


def build_kmdb_title_index(kmdb_movies):
    title_index = defaultdict(dict)

    for (
        kmdb_source_id,
        title,
        english_title,
        original_title,
        production_year,
        director,
    ) in kmdb_movies:
        movie = {
            "kmdb_source_id": kmdb_source_id,
            "title": title,
            "production_year": production_year,
            "director": director,
        }

        title_variants = {
            normalize_title(title),
            normalize_title(english_title),
            normalize_title(original_title),
        }

        for normalized_title in title_variants:
            if normalized_title:
                title_index[normalized_title][
                    kmdb_source_id
                ] = movie

    return title_index


def find_match(tmdb_movie, title_index):
    tmdb_title = clean_text(tmdb_movie.get("title"))
    tmdb_original_title = clean_text(
        tmdb_movie.get("original_title")
    )
    tmdb_year = parse_year(
        tmdb_movie.get("release_date")
    )

    candidate_map = {}

    for title in {tmdb_title, tmdb_original_title}:
        normalized_title = normalize_title(title)

        if not normalized_title:
            continue

        candidate_map.update(
            title_index.get(normalized_title, {})
        )

    candidates = list(candidate_map.values())

    if not candidates:
        return {
            "kmdb_source_id": None,
            "kmdb_title": None,
            "kmdb_production_year": None,
            "match_method": "no_title_candidate",
            "candidate_count": 0,
            "match_status": "unmatched",
        }

    exact_year_candidates = [
        candidate
        for candidate in candidates
        if (
            tmdb_year is not None
            and candidate["production_year"] == tmdb_year
        )
    ]

    if len(exact_year_candidates) == 1:
        candidate = exact_year_candidates[0]

        return {
            "kmdb_source_id": candidate["kmdb_source_id"],
            "kmdb_title": candidate["title"],
            "kmdb_production_year": (
                candidate["production_year"]
            ),
            "match_method": "normalized_title_exact_year",
            "candidate_count": len(candidates),
            "match_status": "matched",
        }

    near_year_candidates = [
        candidate
        for candidate in candidates
        if (
            tmdb_year is not None
            and candidate["production_year"] is not None
            and abs(
                candidate["production_year"] - tmdb_year
            ) <= 1
        )
    ]

    if near_year_candidates:
        candidate = near_year_candidates[0]

        return {
            "kmdb_source_id": candidate["kmdb_source_id"],
            "kmdb_title": candidate["title"],
            "kmdb_production_year": (
                candidate["production_year"]
            ),
            "match_method": "title_near_year_review",
            "candidate_count": len(candidates),
            "match_status": "review",
        }

    return {
        "kmdb_source_id": None,
        "kmdb_title": None,
        "kmdb_production_year": None,
        "match_method": "title_candidate_wrong_year",
        "candidate_count": len(candidates),
        "match_status": "review",
    }


def match_tmdb_kmdb_catalogues():
    tmdb_movies = load_tmdb_movies()

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_MATCH_TABLE_SQL)

            kmdb_movies = load_kmdb_movies(cursor)
            title_index = build_kmdb_title_index(
                kmdb_movies
            )

            batch = []

            for tmdb_movie in tmdb_movies:
                match = find_match(
                    tmdb_movie,
                    title_index,
                )

                batch.append(
                    {
                        "tmdb_id": tmdb_movie["tmdb_id"],
                        "tmdb_title": tmdb_movie["title"],
                        "tmdb_release_year": parse_year(
                            tmdb_movie.get("release_date")
                        ),
                        **match,
                    }
                )

                if len(batch) >= 1000:
                    cursor.executemany(
                        UPSERT_MATCH_SQL,
                        batch,
                    )
                    connection.commit()
                    batch = []

            if batch:
                cursor.executemany(
                    UPSERT_MATCH_SQL,
                    batch,
                )

        connection.commit()

    print("TMDb-KMDb 매칭 완료")
    print(f"TMDb 대상: {len(tmdb_movies)}개")


if __name__ == "__main__":
    match_tmdb_kmdb_catalogues()