import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data/selections/selected_movie_drama_contexts.jsonl"

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]


def get_db_content_type(content_type: str) -> str:
    """
    선택 파일의 content_type과 DB의 content_type을 매핑한다.

    입력:
        movie -> movie
        drama -> tv
    """
    if content_type == "movie":
        return "movie"

    if content_type == "drama":
        return "tv"

    raise ValueError(f"지원하지 않는 content_type: {content_type}")


def get_content_id(cur, content_type: str, tmdb_id: int):
    """TMDb ID로 정확한 content_id를 찾는다."""

    db_content_type = get_db_content_type(content_type)

    cur.execute(
        """
        SELECT c.content_id
        FROM content AS c
        JOIN content_source_map AS source_map
          ON source_map.content_id = c.content_id
        WHERE c.content_type = %s
          AND source_map.source_name = 'tmdb'
          AND source_map.source_id = %s
        LIMIT 1
        """,
        (db_content_type, str(tmdb_id)),
    )

    row = cur.fetchone()

    return row[0] if row else None


def get_or_create_entity(cur, entity_type: str, name: str):
    """
    같은 타입·이름의 엔티티가 있으면 재사용하고,
    없으면 새로 생성한다.
    """

    name = str(name).strip()

    if not name:
        return None

    cur.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND entity_name = %s
        LIMIT 1
        """,
        (entity_type, name),
    )

    row = cur.fetchone()

    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO entity (
            entity_type,
            entity_name
        )
        VALUES (%s, %s)
        RETURNING entity_id
        """,
        (entity_type, name),
    )

    return cur.fetchone()[0]


def upsert_relationship(
    cur,
    content_id: int,
    entity_id: int,
    relationship_type: str,
    evidence: dict,
):
    """
    작품 → 엔티티 관계를 relationship 테이블에 저장한다.

    TMDb에서 직접 확인한 구조화 관계이므로:
    - fact_status = verified
    - visibility = shown
    - next_hop_eligible = true
    """

    cur.execute(
        """
        INSERT INTO relationship (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id,
            source_name,
            fact_status,
            evidence,
            visibility,
            next_hop_eligible
        )
        VALUES (
            'content',
            %s,
            %s,
            'entity',
            %s,
            'tmdb',
            'verified',
            %s::jsonb,
            'shown',
            TRUE
        )
        ON CONFLICT (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id
        )
        DO UPDATE SET
            fact_status = EXCLUDED.fact_status,
            evidence = EXCLUDED.evidence,
            visibility = EXCLUDED.visibility,
            next_hop_eligible = EXCLUDED.next_hop_eligible
        RETURNING relationship_id
        """,
        (
            content_id,
            relationship_type,
            entity_id,
            json.dumps(evidence, ensure_ascii=False),
        ),
    )

    return cur.fetchone()[0]


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

                content_id = get_content_id(
                    cur,
                    content_type,
                    tmdb_id,
                )

                if content_id is None:
                    stats["content_not_found"] += 1

                    print(
                        f"[찾지 못함] "
                        f"{content_type} | {title} | TMDb {tmdb_id}"
                    )

                    continue

                stats["contents"] += 1

                # -------------------------------------------------
                # 감독 / 크리에이터 관계
                # -------------------------------------------------

                for creator in item.get("creators", []):
                    name = creator.get("name")
                    role = creator.get("role")

                    if not name:
                        continue

                    if role == "director":
                        relationship_type = "directed_by"

                    else:
                        relationship_type = "created_by"

                    entity_id = get_or_create_entity(
                        cur,
                        "person",
                        name,
                    )

                    if entity_id is None:
                        continue

                    evidence = {
                        "source_url": (
                            f"https://www.themoviedb.org/"
                            f"{'movie' if content_type == 'movie' else 'tv'}"
                            f"/{tmdb_id}"
                        ),
                        "excerpt": (
                            f"TMDb credits: "
                            f"{relationship_type} = {name}"
                        ),
                    }

                    upsert_relationship(
                        cur,
                        content_id,
                        entity_id,
                        relationship_type,
                        evidence,
                    )

                    stats["creator_relations"] += 1

                # -------------------------------------------------
                # 주요 배우 관계
                # -------------------------------------------------

                for actor in item.get("cast", []):
                    name = actor.get("name")

                    if not name:
                        continue

                    entity_id = get_or_create_entity(
                        cur,
                        "person",
                        name,
                    )

                    if entity_id is None:
                        continue

                    evidence = {
                        "source_url": (
                            f"https://www.themoviedb.org/"
                            f"{'movie' if content_type == 'movie' else 'tv'}"
                            f"/{tmdb_id}"
                        ),
                        "excerpt": (
                            f"TMDb credits: acted_by = {name}"
                        ),
                    }

                    upsert_relationship(
                        cur,
                        content_id,
                        entity_id,
                        "acted_by",
                        evidence,
                    )

                    stats["actor_relations"] += 1

        conn.commit()

    print("\n=== TMDb 자동 사실 관계 적재 완료 ===")
    print(f"작품 확인: {stats['contents']}개")
    print(f"content_id 미확인: {stats['content_not_found']}개")
    print(
        f"감독/크리에이터 관계 처리: "
        f"{stats['creator_relations']}개"
    )
    print(
        f"배우 관계 처리: "
        f"{stats['actor_relations']}개"
    )


if __name__ == "__main__":
    main()