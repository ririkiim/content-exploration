
import json
import os
import re
from pathlib import Path

import psycopg
from dotenv import load_dotenv


# =========================================================
# 1. 환경 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

load_dotenv(ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]


# =========================================================
# 2. 공통 유틸리티
# =========================================================

def normalize_person_name(name: str | None) -> str | None:
    """인물 이름의 앞뒤 및 연속 공백을 정리한다."""
    if not name:
        return None

    normalized = " ".join(str(name).split())
    return normalized or None


def parse_json_payload(payload) -> dict | None:
    """DB에서 가져온 JSONB 값을 dict로 통일한다."""
    if isinstance(payload, dict):
        return payload

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None

        return parsed if isinstance(parsed, dict) else None

    return None


def merge_evidence(existing_evidence, new_evidence: dict) -> dict:
    """
    기존 근거와 새 근거를 sources 목록으로 통합한다.

    기존 관계가 다른 출처 또는 다른 역할로 다시 발견되어도
    이전 근거를 덮어쓰지 않는다.
    """
    if isinstance(existing_evidence, str):
        try:
            existing_evidence = json.loads(existing_evidence)
        except json.JSONDecodeError:
            existing_evidence = None

    if isinstance(existing_evidence, dict):
        if isinstance(existing_evidence.get("sources"), list):
            sources = list(existing_evidence["sources"])
        elif existing_evidence:
            # 이전 버전처럼 근거가 단일 dict인 경우도 지원한다.
            sources = [existing_evidence]
        else:
            sources = []
    else:
        sources = []

    # 같은 근거가 반복해서 추가되는 것을 방지한다.
    if new_evidence not in sources:
        sources.append(new_evidence)

    return {"sources": sources}


# =========================================================
# 3. 원천 DB에서 콘텐츠와 매핑 조회
# =========================================================

def load_mapped_tmdb_rows(cur) -> list[dict]:
    """
    raw_tmdb_content에서 최신 원본을 읽는다.

    content_source_map을 통해 이미 content에 등록된
    영화·TV만 가져온다.

    raw 테이블은 수정하지 않는다.
    """
    cur.execute(
        """
        WITH latest_raw AS (
            SELECT DISTINCT ON (media_type, tmdb_id)
                raw_id,
                media_type,
                tmdb_id,
                title,
                payload,
                fetched_at
            FROM raw_tmdb_content
            WHERE media_type IN ('movie', 'tv')
            ORDER BY
                media_type,
                tmdb_id,
                fetched_at DESC,
                raw_id DESC
        )
        SELECT
            c.content_id,
            r.media_type,
            r.tmdb_id,
            r.title,
            r.payload
        FROM latest_raw AS r
        JOIN content_source_map AS sm
          ON sm.source_name = 'tmdb'
         AND sm.source_id = r.tmdb_id::text
        JOIN content AS c
          ON c.content_id = sm.content_id
         AND c.content_type = r.media_type
        ORDER BY c.content_id
        """
    )

    return [
        {
            "content_id": row[0],
            "media_type": row[1],
            "tmdb_id": row[2],
            "title": row[3],
            "payload": row[4],
        }
        for row in cur.fetchall()
    ]


def load_mapped_book_rows(cur) -> list[dict]:
    """
    raw_yes24_item에서 도서 원본을 읽는다.

    content_source_map을 통해 이미 content에 등록된
    도서만 가져온다.

    raw 테이블은 수정하지 않는다.
    """
    cur.execute(
        """
        SELECT
            c.content_id,
            r.yes24_item_id,
            r.title,
            r.payload
        FROM raw_yes24_item AS r
        JOIN content_source_map AS sm
          ON sm.source_name = 'yes24'
         AND sm.source_id = r.yes24_item_id::text
        JOIN content AS c
          ON c.content_id = sm.content_id
         AND c.content_type = 'book'
        ORDER BY r.yes24_item_id
        """
    )

    return [
        {
            "content_id": row[0],
            "item_id": row[1],
            "title": row[2],
            "payload": row[3],
        }
        for row in cur.fetchall()
    ]


# =========================================================
# 4. 엔티티 관리
# =========================================================

def get_or_create_entity(
    cur,
    entity_type: str,
    name: str,
) -> int | None:
    """
    같은 entity_type·entity_name의 엔티티를 재사용한다.

    entity 테이블에 UNIQUE(entity_type, entity_name)
    제약이 있다는 전제에서 ON CONFLICT를 사용한다.
    """
    name = normalize_person_name(name)

    if not name:
        return None

    cur.execute(
        """
        INSERT INTO entity (
            entity_type,
            entity_name
        )
        VALUES (%s, %s)
        ON CONFLICT (entity_type, entity_name)
        DO UPDATE
            SET entity_name = EXCLUDED.entity_name
        RETURNING entity_id
        """,
        (entity_type, name),
    )

    row = cur.fetchone()
    return row[0] if row else None


# =========================================================
# 5. 관계 적재
# =========================================================

def upsert_relationship(
    cur,
    content_id: int,
    entity_id: int,
    relationship_type: str,
    source_name: str,
    evidence: dict,
) -> tuple[int, str]:
    """
    콘텐츠 -> 엔티티 관계를 적재한다.

    동일한 콘텐츠·관계 유형·대상 엔티티의 관계가 있으면
    근거를 병합하고, 없으면 새 관계를 삽입한다.

    relationship 테이블에 복합 UNIQUE 제약을 요구하지 않는다.
    """
    cur.execute(
        """
        SELECT relationship_id, evidence
        FROM relationship
        WHERE source_type = 'content'
          AND source_id = %s
          AND relationship_type = %s
          AND target_type = 'entity'
          AND target_id = %s
        ORDER BY relationship_id
        LIMIT 1
        """,
        (
            content_id,
            relationship_type,
            entity_id,
        ),
    )

    row = cur.fetchone()

    if row:
        relationship_id = row[0]
        existing_evidence = row[1]

        merged_evidence = merge_evidence(
            existing_evidence,
            evidence,
        )

        cur.execute(
            """
            UPDATE relationship
            SET source_name = %s,
                fact_status = 'verified',
                evidence = %s::jsonb,
                visibility = 'shown',
                next_hop_eligible = TRUE
            WHERE relationship_id = %s
            """,
            (
                source_name,
                json.dumps(
                    merged_evidence,
                    ensure_ascii=False,
                ),
                relationship_id,
            ),
        )

        return relationship_id, "updated"

    initial_evidence = merge_evidence(None, evidence)

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
            %s,
            'verified',
            %s::jsonb,
            'shown',
            TRUE
        )
        RETURNING relationship_id
        """,
        (
            content_id,
            relationship_type,
            entity_id,
            source_name,
            json.dumps(
                initial_evidence,
                ensure_ascii=False,
            ),
        ),
    )

    return cur.fetchone()[0], "inserted"


# =========================================================
# 6. 근거 생성
# =========================================================

def build_tmdb_evidence(
    media_type: str,
    tmdb_id: int | str,
    relationship_type: str,
    person_name: str,
    original_field: str,
    role_label: str,
    person_tmdb_id: int | str | None = None,
) -> dict:
    """TMDb 원본의 어느 필드에서 관계를 추출했는지 기록한다."""
    return {
        "source_url": (
            f"https://www.themoviedb.org/"
            f"{media_type}/{tmdb_id}"
        ),
        "source_name": "tmdb",
        "source_id": str(tmdb_id),
        "original_field": original_field,
        "role_label": role_label,
        "relationship_type": relationship_type,
        "normalized_person_name": person_name,
        "person_tmdb_id": (
            str(person_tmdb_id)
            if person_tmdb_id is not None
            else None
        ),
        "excerpt": (
            f"TMDb {original_field}: "
            f"{role_label} = {person_name}"
        ),
    }


def build_yes24_evidence(
    item_id: int | str,
    raw_author: str,
    relationship_type: str,
    person_name: str,
    role_label: str,
) -> dict:
    """YES24 원본 저자 필드의 관계 근거를 기록한다."""
    return {
        "source_url": (
            f"https://www.yes24.com/product/goods/{item_id}"
        ),
        "source_name": "yes24",
        "source_id": str(item_id),
        "original_field": "author",
        "original_value": raw_author,
        "role_label": role_label,
        "relationship_type": relationship_type,
        "normalized_person_name": person_name,
    }


# =========================================================
# 7. 관계 적재 공통 처리
# =========================================================

def save_person_relationship(
    cur,
    content_id: int,
    media_type: str,
    content_source_id: int | str,
    person: dict,
    relationship_type: str,
    source_field: str,
    role_label: str,
    stats: dict,
    stat_prefix: str,
) -> None:
    """인물 엔티티와 콘텐츠-인물 관계를 저장한다."""
    name = normalize_person_name(person.get("name"))

    if not name:
        stats["person_name_missing"] += 1
        return

    entity_id = get_or_create_entity(
        cur,
        "person",
        name,
    )

    if entity_id is None:
        stats["entity_failed"] += 1
        return

    evidence = build_tmdb_evidence(
        media_type=media_type,
        tmdb_id=content_source_id,
        relationship_type=relationship_type,
        person_name=name,
        original_field=source_field,
        role_label=role_label,
        person_tmdb_id=person.get("id"),
    )

    _, action = upsert_relationship(
        cur,
        content_id,
        entity_id,
        relationship_type,
        "tmdb",
        evidence,
    )

    stats[f"{stat_prefix}_relations"] += 1
    stats[f"{stat_prefix}_{action}"] += 1


# =========================================================
# 8. 영화·TV 구조화 관계 적재
# =========================================================

def process_movie_tv(
    cur,
    row: dict,
    stats: dict,
) -> None:
    """
    TMDb 원본에서 다음 관계를 추출한다.

    영화:
      credits.cast -> acted_by
      credits.crew 중 job == Director -> created_by

    TV:
      credits.cast -> acted_by
      credits.crew 중 job == Director -> created_by
      created_by -> created_by

    감독과 창작자 관계는 created_by로 통합한다.
    배우 관계는 acted_by로 유지한다.
    그 밖의 제작진 역할은 적재하지 않는다.
    """
    content_id = row["content_id"]
    media_type = row["media_type"]
    tmdb_id = row["tmdb_id"]
    title = row.get("title") or ""

    payload = parse_json_payload(row.get("payload"))

    if payload is None:
        stats["tmdb_invalid_payload"] += 1
        print(
            f"[TMDb payload 오류] {media_type} | "
            f"{title} | TMDb {tmdb_id}"
        )
        return

    stats["tmdb_contents"] += 1

    credits = payload.get("credits") or {}

    if not isinstance(credits, dict):
        credits = {}

    # -----------------------------------------------------
    # 8-1. 출연진: acted_by
    # -----------------------------------------------------
    cast = credits.get("cast") or []

    if isinstance(cast, list):
        for person in cast:
            if not isinstance(person, dict):
                continue

            save_person_relationship(
                cur=cur,
                content_id=content_id,
                media_type=media_type,
                content_source_id=tmdb_id,
                person=person,
                relationship_type="acted_by",
                source_field="credits.cast",
                role_label="Cast",
                stats=stats,
                stat_prefix="actor",
            )

    # -----------------------------------------------------
    # 8-2. 감독: created_by
    # -----------------------------------------------------
    crew = credits.get("crew") or []

    if isinstance(crew, list):
        for person in crew:
            if not isinstance(person, dict):
                continue

            if person.get("job") != "Director":
                continue

            save_person_relationship(
                cur=cur,
                content_id=content_id,
                media_type=media_type,
                content_source_id=tmdb_id,
                person=person,
                relationship_type="created_by",
                source_field="credits.crew",
                role_label="Director",
                stats=stats,
                stat_prefix="director",
            )

    # -----------------------------------------------------
    # 8-3. TV 창작자: created_by
    # -----------------------------------------------------
    if media_type == "tv":
        creators = payload.get("created_by") or []

        if isinstance(creators, list):
            for person in creators:
                if not isinstance(person, dict):
                    continue

                save_person_relationship(
                    cur=cur,
                    content_id=content_id,
                    media_type=media_type,
                    content_source_id=tmdb_id,
                    person=person,
                    relationship_type="created_by",
                    source_field="created_by",
                    role_label="Creator",
                    stats=stats,
                    stat_prefix="creator",
                )


# =========================================================
# 9. YES24 저자 필드 파싱
# =========================================================

# 저자 관계로 인정하는 명시적 역할
BOOK_AUTHOR_ROLES = {
    "저",
    "지음",
    "글",
    "원작",
}

# 명시적으로 확인되면 관계를 생성하지 않는 역할
BOOK_NON_AUTHOR_ROLES = {
    "역",
    "그림",
    "편",
    "엮음",
    "감수",
}

BOOK_ROLE_MAP = {
    **{role: "created_by" for role in BOOK_AUTHOR_ROLES},
    **{role: None for role in BOOK_NON_AUTHOR_ROLES},
}

BOOK_ROLE_PATTERN = "|".join(
    re.escape(role)
    for role in sorted(BOOK_ROLE_MAP, key=len, reverse=True)
)

BOOK_ROLE_SUFFIX = re.compile(
    rf"^(?P<name>.+?)\s+(?P<role>{BOOK_ROLE_PATTERN})$"
)


def parse_book_contributors(
    raw_author: str | None,
) -> tuple[list[dict], list[str]]:
    """
    YES24 author 필드를 파싱한다.

    규칙:
    1. 저/지음/글/원작 -> created_by
    2. 역/그림/편/엮음/감수 -> 관계를 생성하지 않음
    3. 어떤 세그먼트에도 역할 표기가 없으면:
       - '/'로 나뉜 이름이 여러 개일 때 첫 번째만 저자로 처리
       - 이름이 하나면 해당 이름을 저자로 처리
    4. 역할이 명시된 문자열에서는 역할 없는 세그먼트를
       저자로 추정하지 않고 검토 대상으로 남긴다.

    반환값:
        (저자 관계 후보 목록, 검토가 필요한 원문 조각)
    """
    if not raw_author or not str(raw_author).strip():
        return [], []

    segments = [
        " ".join(segment.split())
        for segment in str(raw_author).split("/")
    ]
    segments = [segment for segment in segments if segment]

    if not segments:
        return [], []

    parsed_segments = []
    has_known_role = False

    for segment in segments:
        match = BOOK_ROLE_SUFFIX.fullmatch(segment)

        if match:
            has_known_role = True
            parsed_segments.append(
                {
                    "segment": segment,
                    "name": normalize_person_name(match.group("name")),
                    "role_label": match.group("role"),
                }
            )
        else:
            parsed_segments.append(
                {
                    "segment": segment,
                    "name": None,
                    "role_label": None,
                }
            )

    contributors = []
    unparsed_segments = []

    if has_known_role:
        # 역할이 명시된 경우: 저자 역할만 관계로 저장한다.
        for parsed in parsed_segments:
            role_label = parsed["role_label"]

            if role_label is None:
                unparsed_segments.append(parsed["segment"])
                continue

            if role_label in BOOK_NON_AUTHOR_ROLES:
                continue

            name = parsed["name"]

            if not name:
                unparsed_segments.append(parsed["segment"])
                continue

            contributors.append(
                {
                    "name": name,
                    "role_label": role_label,
                    "relationship_type": "created_by",
                }
            )

        return contributors, unparsed_segments

    # 역할 표기가 전혀 없는 경우에만 제한적으로 추정한다.
    # 여러 이름이 '/'로 구분되어 있으면 첫 번째만 사용한다.
    first_name = normalize_person_name(segments[0])

    if first_name:
        contributors.append(
            {
                "name": first_name,
                "role_label": "role_not_specified",
                "relationship_type": "created_by",
            }
        )
    else:
        unparsed_segments.append(segments[0])

    # 두 번째 이후의 이름은 공동 저자로 추정하지 않는다.
    # 별도 관계도 생성하지 않는다.

    return contributors, unparsed_segments


# =========================================================
# 10. 도서 구조화 관계 적재
# =========================================================

def process_book(
    cur,
    row: dict,
    stats: dict,
) -> None:
    """YES24 원본에서 확인한 저자 관계만 적재한다."""
    content_id = row["content_id"]
    item_id = row["item_id"]
    title = row.get("title") or ""

    payload = parse_json_payload(row.get("payload"))

    if payload is None:
        stats["book_invalid_payload"] += 1
        print(
            f"[YES24 payload 오류] {title} | YES24 {item_id}"
        )
        return

    stats["books"] += 1

    raw_author = payload.get("author") or ""

    if not isinstance(raw_author, str):
        raw_author = str(raw_author)

    contributors, unparsed = parse_book_contributors(raw_author)

    if unparsed:
        stats["book_unparsed_segments"] += len(unparsed)
        print(
            f"[기여자 역할 확인 필요] {title} | "
            f"YES24 {item_id} | 원문: {unparsed}"
        )

    if not contributors:
        stats["book_no_parsed_authors"] += 1

    for contributor in contributors:
        name = contributor["name"]
        role_label = contributor["role_label"]
        relationship_type = contributor["relationship_type"]

        entity_id = get_or_create_entity(
            cur,
            "person",
            name,
        )

        if entity_id is None:
            stats["book_entity_failed"] += 1
            continue

        evidence = build_yes24_evidence(
            item_id=item_id,
            raw_author=raw_author,
            relationship_type=relationship_type,
            person_name=name,
            role_label=role_label,
        )

        _, action = upsert_relationship(
            cur,
            content_id,
            entity_id,
            relationship_type,
            "yes24",
            evidence,
        )

        stats["book_author_relations"] += 1
        stats[f"book_{action}"] += 1

        stats["book_relation_types"][relationship_type] = (
            stats["book_relation_types"].get(relationship_type, 0) + 1
        )


# =========================================================
# 11. 실행 및 통계
# =========================================================

def create_stats() -> dict:
    return {
        "tmdb_contents": 0,
        "tmdb_invalid_payload": 0,
        "actor_relations": 0,
        "actor_inserted": 0,
        "actor_updated": 0,
        "director_relations": 0,
        "director_inserted": 0,
        "director_updated": 0,
        "creator_relations": 0,
        "creator_inserted": 0,
        "creator_updated": 0,
        "person_name_missing": 0,
        "entity_failed": 0,
        "books": 0,
        "book_invalid_payload": 0,
        "book_unparsed_segments": 0,
        "book_no_parsed_authors": 0,
        "book_entity_failed": 0,
        "book_author_relations": 0,
        "book_inserted": 0,
        "book_updated": 0,
        "book_relation_types": {},
    }


def main() -> None:
    stats = create_stats()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # 영화·TV: raw_tmdb_content에서 직접 읽는다.
            tmdb_rows = load_mapped_tmdb_rows(cur)

            for row in tmdb_rows:
                process_movie_tv(cur, row, stats)

            # 도서: raw_yes24_item에서 직접 읽는다.
            book_rows = load_mapped_book_rows(cur)

            for row in book_rows:
                process_book(cur, row, stats)

        # 연결 컨텍스트를 정상 종료하면 트랜잭션이 커밋된다.
        # 처리 중 예외가 발생하면 트랜잭션은 롤백된다.

    print("\n=== 구조화 사실 관계 적재 완료 ===")

    print(f"영화·TV 콘텐츠 처리: {stats['tmdb_contents']}개")
    print(f"TMDb payload 오류: {stats['tmdb_invalid_payload']}개")

    print(f"\n출연 관계(acted_by) 처리: {stats['actor_relations']}개")
    print(f"  신규 생성: {stats['actor_inserted']}개")
    print(f"  갱신: {stats['actor_updated']}개")

    print(f"\n감독 관계(created_by) 처리: {stats['director_relations']}개")
    print(f"  신규 생성: {stats['director_inserted']}개")
    print(f"  갱신: {stats['director_updated']}개")

    print(f"\nTV 창작자 관계(created_by) 처리: {stats['creator_relations']}개")
    print(f"  신규 생성: {stats['creator_inserted']}개")
    print(f"  갱신: {stats['creator_updated']}개")

    print(f"\n인물 이름 누락: {stats['person_name_missing']}개")
    print(f"엔티티 생성 실패: {stats['entity_failed']}개")

    print(f"\n매핑된 도서 처리: {stats['books']}개")
    print(f"YES24 payload 오류: {stats['book_invalid_payload']}개")
    print(
        "역할 확인이 필요한 조각: "
        f"{stats['book_unparsed_segments']}개"
    )
    print(
        "저자를 추출하지 못한 도서: "
        f"{stats['book_no_parsed_authors']}개"
    )
    print(f"도서 저자 관계(created_by) 처리: {stats['book_author_relations']}개")
    print(f"  신규 생성: {stats['book_inserted']}개")
    print(f"  갱신: {stats['book_updated']}개")
    print(f"도서 엔티티 생성 실패: {stats['book_entity_failed']}개")

    print("\n도서 관계 유형별 처리 건수:")

    for relationship_type, count in sorted(
        stats["book_relation_types"].items()
    ):
        print(f"  {relationship_type}: {count}개")


if __name__ == "__main__":
    main()
