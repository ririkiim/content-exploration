import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
SELECTION_PATH = (
    ROOT / "data" / "selections" / "selected_movie_drama_contexts.jsonl"
)

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

MAX_KEYWORDS = 40

BLOCKED_KEYWORDS = {
    "한국", "대한민국", "south korea", "korea", "seoul", "서울",
    "movie", "film", "tv", "drama",
}


# =========================================================
# 1. 텍스트 정리 및 중복 제거
# =========================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def normalize(value):
    value = clean_text(value)

    if not value:
        return None

    return re.sub(r"\s+", " ", value).casefold()


def unique(values):
    seen = set()
    result = []

    for value in values:
        value = clean_text(value)

        if not value:
            continue

        normalized_value = normalize(value)

        if not normalized_value or normalized_value in seen:
            continue

        seen.add(normalized_value)
        result.append(value)

    return result


# =========================================================
# 2. TMDb 출처 정보
# =========================================================

def tmdb_url(item):
    tmdb_id = item.get("source_ids", {}).get("tmdb")

    if not tmdb_id:
        return None

    if item["content_type"] == "movie":
        return f"https://www.themoviedb.org/movie/{tmdb_id}"

    return f"https://www.themoviedb.org/tv/{tmdb_id}"


# =========================================================
# 3. 작품 조회
# =========================================================

def get_content_id(conn, item):
    tmdb_id = item.get("source_ids", {}).get("tmdb")

    if not tmdb_id:
        return None

    # JSONL의 drama는 DB에서 tv로 저장됨
    content_type = (
        "tv" if item["content_type"] == "drama"
        else item["content_type"]
    )

    row = conn.execute(
        """
        SELECT c.content_id
        FROM content AS c
        JOIN content_source_map AS sm
          ON sm.content_id = c.content_id
        WHERE sm.source_name = 'tmdb'
          AND sm.source_id = %s
          AND c.content_type = %s
        ORDER BY c.content_id
        LIMIT 1
        """,
        (str(tmdb_id), content_type),
    ).fetchone()

    return row["content_id"] if row else None


# =========================================================
# 4. 엔티티 생성 또는 재사용
# =========================================================

def get_or_create_entity(conn, entity_type, name):
    name = clean_text(name)

    if not name:
        return None

    # 현재 DB의 고유 조건: (entity_type, entity_name)
    row = conn.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND entity_name = %s
        LIMIT 1
        """,
        (entity_type, name),
    ).fetchone()

    if row:
        return row["entity_id"]

    # 동시 실행 등으로 중복 생성되는 상황도 고려
    row = conn.execute(
        """
        INSERT INTO entity (
            entity_type,
            entity_name
        )
        VALUES (%s, %s)
        ON CONFLICT (entity_type, entity_name)
        DO UPDATE SET entity_name = EXCLUDED.entity_name
        RETURNING entity_id
        """,
        (entity_type, name),
    ).fetchone()

    return row["entity_id"]


# =========================================================
# 5. 작품-엔티티 관계 저장
# =========================================================

def upsert_relation(
    conn,
    content_id,
    entity_id,
    relation_type,
    fact_status,
    source_url,
    value,
):
    evidence = {
        "source_url": source_url,
        "source_field": (
            "genres" if relation_type == "has_genre"
            else "keywords"
        ),
        "source_value": value,
        "source_name": "tmdb",
    }

    # 장르는 구조적 관계로 활용하고,
    # 검증되지 않은 키워드는 다음 탐색 경로에 바로 사용하지 않음
    next_hop_eligible = relation_type == "has_genre"

    conn.execute(
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
            %s,
            %s::jsonb,
            'shown',
            %s
        )
        ON CONFLICT (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id
        )
        DO UPDATE SET
            fact_status = CASE
                WHEN relationship.fact_status = 'verified'
                  OR EXCLUDED.fact_status = 'verified'
                THEN 'verified'
                ELSE 'proposed'
            END,
            evidence = COALESCE(
                relationship.evidence,
                '{}'::jsonb
            ) || EXCLUDED.evidence,
            next_hop_eligible =
                relationship.next_hop_eligible
                OR EXCLUDED.next_hop_eligible
        """,
        (
            content_id,
            relation_type,
            entity_id,
            fact_status,
            json.dumps(evidence, ensure_ascii=False),
            next_hop_eligible,
        ),
    )


# =========================================================
# 6. 메인 실행
# =========================================================

def main():
    with SELECTION_PATH.open(encoding="utf-8") as file:
        items = [
            json.loads(line)
            for line in file
            if line.strip()
        ]

    genre_count = 0
    keyword_count = 0
    missing_contents = []

    with psycopg.connect(
        DATABASE_URL,
        row_factory=psycopg.rows.dict_row,
    ) as conn:

        for item in items:
            content_id = get_content_id(conn, item)

            if content_id is None:
                missing_contents.append(item.get("title", "(제목 없음)"))
                continue

            source_url = tmdb_url(item)

            # -------------------------------------------------
            # 장르 관계
            # -------------------------------------------------

            genres = unique(item.get("genres") or [])

            for genre in genres:
                entity_id = get_or_create_entity(
                    conn,
                    entity_type="concept",
                    name=genre,
                )

                if entity_id is None:
                    continue

                upsert_relation(
                    conn=conn,
                    content_id=content_id,
                    entity_id=entity_id,
                    relation_type="has_genre",
                    fact_status="verified",
                    source_url=source_url,
                    value=genre,
                )

                genre_count += 1

            # -------------------------------------------------
            # 키워드 관계
            # -------------------------------------------------

            blocked_normalized = {
                normalize(keyword)
                for keyword in BLOCKED_KEYWORDS
            }

            keywords = [
                keyword
                for keyword in unique(item.get("keywords") or [])
                if normalize(keyword) not in blocked_normalized
            ][:MAX_KEYWORDS]

            for keyword in keywords:
                entity_id = get_or_create_entity(
                    conn,
                    entity_type="concept",
                    name=keyword,
                )

                if entity_id is None:
                    continue

                upsert_relation(
                    conn=conn,
                    content_id=content_id,
                    entity_id=entity_id,
                    relation_type="has_keyword",
                    fact_status="proposed",
                    source_url=source_url,
                    value=keyword,
                )

                keyword_count += 1

        conn.commit()

    print("\n=== 카탈로그 장르·키워드 관계 적재 완료 ===")
    print(f"처리 작품 수: {len(items)}")
    print(f"장르 관계 처리: {genre_count}")
    print(f"키워드 관계 처리: {keyword_count}")
    print(f"작품 매칭 실패: {len(missing_contents)}")

    if missing_contents:
        print("content_id를 찾지 못한 작품:")
        for title in missing_contents:
            print(f"- {title}")


if __name__ == "__main__":
    main()