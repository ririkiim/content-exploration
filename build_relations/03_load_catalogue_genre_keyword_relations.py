import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from common import require_normalized_name


ROOT = Path(__file__).resolve().parents[1]
SELECTION_PATH = ROOT / "data" / "selections" / "selected_movie_drama_contexts.jsonl"

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

MAX_KEYWORDS = 40

BLOCKED_KEYWORDS = {
    "한국", "대한민국", "south korea", "korea", "seoul", "서울",
    "movie", "film", "tv", "drama",
}


def clean_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def normalize(value):
    return require_normalized_name(clean_text(value))


def unique(values):
    seen = set()
    result = []

    for value in values:
        value = clean_text(value)
        if not value:
            continue

        key = normalize(value)
        if not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def tmdb_url(item):
    tmdb_id = item["source_ids"]["tmdb"]

    if item["content_type"] == "movie":
        return f"https://www.themoviedb.org/movie/{tmdb_id}"

    return f"https://www.themoviedb.org/tv/{tmdb_id}"


def get_content_id(conn, item):
    tmdb_id = str(item["source_ids"]["tmdb"])

    row = conn.execute(
        """
        SELECT catalogue.content_id
        FROM content_catalogue AS catalogue
        JOIN content_source_map AS source_map
          ON source_map.content_id = catalogue.content_id
        WHERE source_map.source = 'tmdb'
          AND source_map.source_id = %s
          AND catalogue.content_type = %s
        ORDER BY catalogue.content_id
        LIMIT 1
        """,
        (tmdb_id, item["content_type"]),
    ).fetchone()

    return row["content_id"] if row else None


def get_or_create_entity(conn, entity_type, name):
    normalized_name = require_normalized_name(name)

    row = conn.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND normalized_name = %s
        ORDER BY entity_id
        LIMIT 1
        """,
        (entity_type, normalized_name),
    ).fetchone()

    if row:
        return row["entity_id"]

    row = conn.execute(
        """
        INSERT INTO entity (
            entity_type,
            name,
            normalized_name
        )
        VALUES (%s, %s, %s)
        RETURNING entity_id
        """,
        (entity_type, name, normalized_name),
    ).fetchone()

    return row["entity_id"]


def upsert_relation(
    conn,
    content_id,
    entity_id,
    relation_type,
    fact_status,
    review_status,
):
    row = conn.execute(
        """
        INSERT INTO content_entity_relation (
            content_id,
            entity_id,
            relation_type,
            fact_status,
            review_status
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (content_id, entity_id, relation_type)
        DO UPDATE SET
            fact_status = EXCLUDED.fact_status,
            updated_at = NOW()
        RETURNING content_entity_relation_id
        """,
        (
            content_id,
            entity_id,
            relation_type,
            fact_status,
            review_status,
        ),
    ).fetchone()

    return row["content_entity_relation_id"]


def upsert_evidence(conn, relation_id, source_url, excerpt):
    conn.execute(
        """
        DELETE FROM relation_evidence
        WHERE content_entity_relation_id = %s
        """,
        (relation_id,),
    )

    conn.execute(
        """
        INSERT INTO relation_evidence (
            content_entity_relation_id,
            source_name,
            source_url,
            excerpt,
            source_quality
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            relation_id,
            "catalogue_context",
            source_url,
            excerpt,
            "curated_community",
        ),
    )


def main():
    with SELECTION_PATH.open(encoding="utf-8") as file:
        items = [json.loads(line) for line in file if line.strip()]

    genre_count = 0
    keyword_count = 0
    missing_contents = []

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        for item in items:
            content_id = get_content_id(conn, item)

            if not content_id:
                missing_contents.append(item["title"])
                continue

            url = tmdb_url(item)

            for genre in unique(item.get("genres", [])):
                entity_id = get_or_create_entity(conn, "concept", genre)

                relation_id = upsert_relation(
                    conn=conn,
                    content_id=content_id,
                    entity_id=entity_id,
                    relation_type="has_genre",
                    fact_status="verified",
                    review_status="auto_checked",
                )

                upsert_evidence(
                    conn,
                    relation_id,
                    url,
                    f"TMDb/KMDb 카탈로그 장르: {genre}",
                )
                genre_count += 1

            keywords = [
                keyword
                for keyword in unique(item.get("keywords", []))
                if normalize(keyword) not in {normalize(x) for x in BLOCKED_KEYWORDS}
            ][:MAX_KEYWORDS]

            for keyword in keywords:
                entity_id = get_or_create_entity(conn, "concept", keyword)

                relation_id = upsert_relation(
                    conn=conn,
                    content_id=content_id,
                    entity_id=entity_id,
                    relation_type="has_keyword",
                    fact_status="proposed",
                    review_status="auto_checked",
                )

                upsert_evidence(
                    conn,
                    relation_id,
                    url,
                    f"TMDb/KMDb 카탈로그 키워드: {keyword}",
                )
                keyword_count += 1

        conn.commit()

    print(f"처리 작품 수: {len(items)}")
    print(f"장르 관계 저장: {genre_count}")
    print(f"키워드 관계 저장: {keyword_count}")

    if missing_contents:
        print("content_id를 찾지 못한 작품:")
        print(", ".join(missing_contents))


if __name__ == "__main__":
    main()
