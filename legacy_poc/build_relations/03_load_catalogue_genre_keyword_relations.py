
import json
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv
import os


# =========================================================
# 1. 환경 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

SELECTION_PATH = (
    ROOT / "data" / "selections" / "selected_movie_drama_contexts.jsonl"
)

MAX_KEYWORDS = 40

BLOCKED_KEYWORDS = {
    "한국",
    "대한민국",
    "south korea",
    "korea",
    "seoul",
    "서울",
    "movie",
    "film",
    "tv",
    "drama",
}

# 02의 탐색 정책과 동일한 기준을 사용한다.
# 장르와 일반 키워드는 다음 탐색의 출발점으로 사용하지 않는다.
NEXT_HOP_CATEGORIES = {"person", "event", "work"}

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL이 .env에 설정되어 있지 않습니다.")


# =========================================================
# 2. 공통 유틸리티
# =========================================================

def clean_text(value):
    """문자열 앞뒤 및 연속 공백을 정리한다."""
    if value is None:
        return ""

    return re.sub(r"\s+", " ", str(value)).strip()


def normalize(value):
    """비교용 문자열을 정규화한다."""
    return clean_text(value).casefold()


def unique(values):
    """정규화된 문자열 기준으로 중복을 제거한다."""
    result = []
    seen = set()

    for value in values:
        text = clean_text(value)

        if not text:
            continue

        key = normalize(text)

        if key in seen:
            continue

        seen.add(key)
        result.append(text)

    return result


def extract_names(values):
    """
    선택 JSONL의 장르·키워드가 문자열 또는 객체 목록인 경우를 처리한다.

    예:
        ["액션", "드라마"]
        [{"name": "액션"}, {"name": "드라마"}]
    """
    if not isinstance(values, list):
        return []

    names = []

    for value in values:
        if isinstance(value, str):
            name = clean_text(value)

        elif isinstance(value, dict):
            name = clean_text(value.get("name"))

        else:
            continue

        if name:
            names.append(name)

    return unique(names)


def tmdb_url(item):
    """TMDb 콘텐츠 상세 페이지 URL을 생성한다."""
    source_ids = item.get("source_ids") or {}
    tmdb_id = source_ids.get("tmdb")

    if not tmdb_id:
        return None

    content_type = clean_text(item.get("content_type")).lower()

    if content_type == "movie":
        media_type = "movie"
    elif content_type in {"drama", "tv"}:
        media_type = "tv"
    else:
        return None

    return f"https://www.themoviedb.org/{media_type}/{tmdb_id}"


# =========================================================
# 3. 다음 탐색 가능 여부 판단
# =========================================================

def is_next_hop_eligible(relationship_type, category, evidence):
    """
    엔티티 관계를 다음 탐색 단계의 출발점으로 사용할 수 있는지 판단한다.

    02의 정책을 반영한다.
    - 허용 엔티티 유형: person, event, work
    - Wikipedia 언급 후보는 제외
    - 언급 사유가 있는 관계는 제외
    - 비교 맥락의 관계는 제외

    03에서 생성하는 genre / concept 엔티티는
    다음 탐색의 출발점으로 사용하지 않는다.
    """
    if category not in NEXT_HOP_CATEGORIES:
        return False

    if relationship_type == "wikipedia_mention_candidate":
        return False

    if evidence.get("mention_reason"):
        return False

    if evidence.get("relation_hint") == "comparison":
        return False

    return True


# =========================================================
# 4. 콘텐츠 및 엔티티 조회
# =========================================================

def get_content_id(conn, item):
    """TMDb source ID와 콘텐츠 유형으로 content_id를 조회한다."""
    source_ids = item.get("source_ids") or {}
    tmdb_id = source_ids.get("tmdb")

    if tmdb_id is None:
        return None

    item_type = clean_text(item.get("content_type")).lower()

    if item_type == "drama":
        db_content_type = "tv"
    elif item_type in {"movie", "tv"}:
        db_content_type = item_type
    else:
        return None

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT csm.content_id
            FROM content_source_map AS csm
            JOIN content AS c
              ON c.content_id = csm.content_id
            WHERE csm.source_name = 'tmdb'
              AND csm.source_id = %s
              AND c.content_type = %s
            """,
            (str(tmdb_id), db_content_type),
        )

        row = cur.fetchone()

    return row[0] if row else None


def get_or_create_entity(conn, entity_type, name):
    """엔티티 유형과 이름을 기준으로 엔티티를 조회하거나 생성한다."""
    name = clean_text(name)

    if not name:
        raise ValueError("빈 엔티티 이름은 저장할 수 없습니다.")

    with conn.cursor() as cur:
        cur.execute(
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
        )

        return cur.fetchone()[0]


# =========================================================
# 5. 근거 데이터 병합
# =========================================================

def normalize_evidence(evidence):
    """
    새 형식과 기존 평면 형식의 evidence를 모두 처리한다.

    새 형식:
        {"sources": [{...}]}

    기존 형식:
        {"source_url": "...", "source_field": "...", ...}
    """
    if not isinstance(evidence, dict):
        return {"sources": []}

    if isinstance(evidence.get("sources"), list):
        sources = [
            source
            for source in evidence["sources"]
            if isinstance(source, dict)
        ]

        return {"sources": sources}

    # 기존 평면 형식의 근거도 보존한다.
    if evidence:
        return {"sources": [evidence]}

    return {"sources": []}


def merge_evidence(existing_evidence, new_evidence):
    """기존 근거를 보존하면서 중복되지 않는 근거를 추가한다."""
    existing = normalize_evidence(existing_evidence)
    incoming = normalize_evidence(new_evidence)

    merged_sources = list(existing["sources"])
    seen = {
        json.dumps(source, ensure_ascii=False, sort_keys=True)
        for source in merged_sources
    }

    for source in incoming["sources"]:
        key = json.dumps(
            source,
            ensure_ascii=False,
            sort_keys=True,
        )

        if key not in seen:
            merged_sources.append(source)
            seen.add(key)

    return {"sources": merged_sources}


# =========================================================
# 6. 관계 적재
# =========================================================

def upsert_relation(
    conn,
    content_id,
    entity_id,
    relationship_type,
    entity_type,
    source_url,
    value,
    fact_status,
):
    """
    콘텐츠 → 엔티티 관계를 저장한다.

    UNIQUE 제약조건에 맞춰 기존 관계가 있으면 근거를 병합한다.
    다음 탐색 가능 여부는 함수로 판단하고 DB 컬럼에 저장한다.
    """
    source_field = (
        "genres"
        if relationship_type == "has_genre"
        else "keywords"
    )

    evidence = {
        "sources": [
            {
                "source_name": "tmdb",
                "source_url": source_url,
                "source_field": source_field,
                "source_value": value,
            }
        ]
    }

    next_hop_eligible = is_next_hop_eligible(
        relationship_type=relationship_type,
        category=entity_type,
        evidence=evidence,
    )

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                relationship_id,
                fact_status,
                evidence
            FROM relationship
            WHERE source_type = 'content'
              AND source_id = %s
              AND relationship_type = %s
              AND target_type = 'entity'
              AND target_id = %s
            FOR UPDATE
            """,
            (
                content_id,
                relationship_type,
                entity_id,
            ),
        )

        existing = cur.fetchone()

        if existing:
            relationship_id, existing_status, existing_evidence = existing

            merged = merge_evidence(
                existing_evidence,
                evidence,
            )

            # 검증된 관계는 제안 상태로 강등하지 않는다.
            merged_status = (
                "verified"
                if "verified" in {existing_status, fact_status}
                else "proposed"
            )

            cur.execute(
                """
                UPDATE relationship
                SET source_name = %s,
                    fact_status = %s,
                    evidence = %s::jsonb,
                    visibility = 'shown',
                    next_hop_eligible = %s
                WHERE relationship_id = %s
                """,
                (
                    "tmdb",
                    merged_status,
                    json.dumps(merged, ensure_ascii=False),
                    next_hop_eligible,
                    relationship_id,
                ),
            )

            return "updated"

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
                %s,
                %s::jsonb,
                'shown',
                %s
            )
            """,
            (
                content_id,
                relationship_type,
                entity_id,
                fact_status,
                json.dumps(evidence, ensure_ascii=False),
                next_hop_eligible,
            ),
        )

        return "inserted"


# =========================================================
# 7. 실행
# =========================================================

def main():
    if not SELECTION_PATH.exists():
        raise FileNotFoundError(
            f"선택 JSONL 파일을 찾을 수 없습니다: {SELECTION_PATH}"
        )

    with SELECTION_PATH.open("r", encoding="utf-8") as file:
        items = [
            json.loads(line)
            for line in file
            if line.strip()
        ]

    stats = {
        "processed_contents": 0,
        "missing_contents": 0,
        "genre_inserted": 0,
        "genre_updated": 0,
        "keyword_inserted": 0,
        "keyword_updated": 0,
        "blocked_keywords": 0,
    }

    with psycopg.connect(DATABASE_URL) as conn:
        try:
            for item in items:
                content_id = get_content_id(conn, item)

                if content_id is None:
                    stats["missing_contents"] += 1
                    print(
                        "[SKIP] DB 콘텐츠 매핑 없음:",
                        item.get("title"),
                    )
                    continue

                stats["processed_contents"] += 1

                source_url = tmdb_url(item)

                # -----------------------------
                # 장르 관계
                # -----------------------------

                genres = extract_names(item.get("genres"))

                for genre in genres:
                    entity_id = get_or_create_entity(
                        conn,
                        entity_type="genre",
                        name=genre,
                    )

                    result = upsert_relation(
                        conn=conn,
                        content_id=content_id,
                        entity_id=entity_id,
                        relationship_type="has_genre",
                        entity_type="genre",
                        source_url=source_url,
                        value=genre,
                        fact_status="verified",
                    )

                    stats[f"genre_{result}"] += 1

                # -----------------------------
                # 키워드 관계
                # -----------------------------

                raw_keywords = extract_names(item.get("keywords"))

                accepted_keywords = []

                for keyword in raw_keywords:
                    if normalize(keyword) in {
                        normalize(value)
                        for value in BLOCKED_KEYWORDS
                    }:
                        stats["blocked_keywords"] += 1
                        continue

                    accepted_keywords.append(keyword)

                keywords = unique(accepted_keywords)[:MAX_KEYWORDS]

                for keyword in keywords:
                    entity_id = get_or_create_entity(
                        conn,
                        entity_type="concept",
                        name=keyword,
                    )

                    result = upsert_relation(
                        conn=conn,
                        content_id=content_id,
                        entity_id=entity_id,
                        relationship_type="has_keyword",
                        entity_type="concept",
                        source_url=source_url,
                        value=keyword,
                        fact_status="proposed",
                    )

                    stats[f"keyword_{result}"] += 1

            conn.commit()

        except Exception:
            conn.rollback()
            raise

    print("\n===== 카탈로그 관계 적재 결과 =====")

    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
