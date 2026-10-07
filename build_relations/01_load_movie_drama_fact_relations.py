import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from common import require_normalized_name


ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data/selections/selected_movie_drama_contexts.jsonl"

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]


def get_content_id(cur, content_type: str, tmdb_id: int):
    """TMDb ID로 정확한 content_id를 찾는다."""
    cur.execute(
        """
        SELECT catalogue.content_id
        FROM content_catalogue AS catalogue
        JOIN content_source_map AS source_map
          ON source_map.content_id = catalogue.content_id
        WHERE catalogue.content_type = %s
          AND source_map.source = 'tmdb'
          AND source_map.source_id = %s
        LIMIT 1
        """,
        (content_type, str(tmdb_id)),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_or_create_entity(cur, entity_type: str, name: str):
    """같은 타입·이름의 엔티티가 있으면 재사용하고, 없으면 생성한다."""
    normalized_name = require_normalized_name(name)

    cur.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND normalized_name = %s
        ORDER BY entity_id
        LIMIT 1
        """,
        (entity_type, normalized_name),
    )
    row = cur.fetchone()

    if row:
        return row[0]

    cur.execute(
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
    )
    return cur.fetchone()[0]


def upsert_content_entity_relation(
    cur,
    content_id: int,
    entity_id: int,
    relation_type: str,
):
    """작품 → 인물 관계를 중복 없이 저장한다."""
    cur.execute(
        """
        INSERT INTO content_entity_relation (
            content_id,
            entity_id,
            relation_type,
            fact_status,
            review_status
        )
        VALUES (%s, %s, %s, 'verified', 'auto_checked')
        ON CONFLICT (content_id, entity_id, relation_type)
        DO UPDATE SET
            fact_status = EXCLUDED.fact_status,
            updated_at = NOW()
        RETURNING content_entity_relation_id
        """,
        (content_id, entity_id, relation_type),
    )
    return cur.fetchone()[0]


def add_tmdb_evidence(cur, relation_id: int, tmdb_id: int, content_type: str, excerpt: str):
    """TMDb를 관계 근거로 저장한다."""
    tmdb_path = "movie" if content_type == "movie" else "tv"
    source_url = f"https://www.themoviedb.org/{tmdb_path}/{tmdb_id}"

    cur.execute(
        """
        INSERT INTO relation_evidence (
            content_entity_relation_id,
            source_name,
            source_url,
            excerpt,
            source_quality
        )
        SELECT %s, 'tmdb', %s, %s, 'curated_community'
        WHERE NOT EXISTS (
            SELECT 1
            FROM relation_evidence
            WHERE content_entity_relation_id = %s
              AND source_name = 'tmdb'
        )
        """,
        (relation_id, source_url, excerpt, relation_id),
    )


def main():
    rows = [
        json.loads(line)
        for line in INPUT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    stats = {
        "contents": 0,
        "content_not_found": 0,
        "creator_relations": 0,
        "actor_relations": 0,
    }

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            for item in rows:
                content_type = item["content_type"]
                title = item["title"]
                tmdb_id = item["source_ids"]["tmdb"]

                content_id = get_content_id(cur, content_type, tmdb_id)

                if content_id is None:
                    stats["content_not_found"] += 1
                    print(f"[찾지 못함] {content_type} | {title} | TMDb {tmdb_id}")
                    continue

                stats["contents"] += 1

                # 감독 / 크리에이터 관계
                for creator in item.get("creators", []):
                    name = creator.get("name")
                    role = creator.get("role")

                    if not name:
                        continue

                    relation_type = (
                        "directed_by"
                        if role == "director"
                        else "created_by"
                    )

                    entity_id = get_or_create_entity(cur, "person", name)

                    relation_id = upsert_content_entity_relation(
                        cur,
                        content_id,
                        entity_id,
                        relation_type,
                    )

                    add_tmdb_evidence(
                        cur,
                        relation_id,
                        tmdb_id,
                        content_type,
                        f"TMDb credits: {relation_type} = {name}",
                    )

                    stats["creator_relations"] += 1

                # 주요 배우 관계
                for actor in item.get("cast", []):
                    name = actor.get("name")

                    if not name:
                        continue

                    entity_id = get_or_create_entity(cur, "person", name)

                    relation_id = upsert_content_entity_relation(
                        cur,
                        content_id,
                        entity_id,
                        "acted_by",
                    )

                    add_tmdb_evidence(
                        cur,
                        relation_id,
                        tmdb_id,
                        content_type,
                        f"TMDb credits: acted_by = {name}",
                    )

                    stats["actor_relations"] += 1

        conn.commit()

    print("\n=== 자동 사실 관계 적재 완료 ===")
    print(f"작품 확인: {stats['contents']}개")
    print(f"content_id 미확인: {stats['content_not_found']}개")
    print(f"감독/크리에이터 관계 처리: {stats['creator_relations']}개")
    print(f"배우 관계 처리: {stats['actor_relations']}개")


if __name__ == "__main__":
    main()
