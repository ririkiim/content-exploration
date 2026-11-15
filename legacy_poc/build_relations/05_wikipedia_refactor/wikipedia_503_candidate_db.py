
"""05 후보를 PostgreSQL entity/relationship 테이블에 저장한다."""

import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from common import normalize_name, require_normalized_name
from candidate_scoring import SCORE_VERSION
from wikipedia_501_candidate_config import REFINED_RELATION_TYPES


def db_content_type(item_content_type):
    return "tv" if item_content_type == "drama" else item_content_type


def get_content_id(conn, item):
    source_name = item["source_name"]
    source_id = str(item["source_id"])

    row = conn.execute(
        """
        SELECT c.content_id
        FROM content AS c
        JOIN content_source_map AS sm
          ON sm.content_id = c.content_id
        WHERE sm.source_name = %s
          AND sm.source_id = %s
          AND c.content_type = %s
        ORDER BY c.content_id
        LIMIT 1
        """,
        (
            source_name,
            source_id,
            db_content_type(item["content_type"]),
        ),
    ).fetchone()

    return row["content_id"] if row else None


def get_verified_entity_names(conn, content_id):
    rows = conn.execute(
        """
        SELECT e.entity_name
        FROM relationship AS r
        JOIN entity AS e
          ON r.target_type = 'entity'
         AND e.entity_id = r.target_id
        WHERE r.source_type = 'content'
          AND r.source_id = %s
          AND r.target_type = 'entity'
          AND r.fact_status = 'verified'
          AND r.visibility = 'shown'
        """,
        (content_id,),
    ).fetchall()

    return {
        normalize_name(row["entity_name"])
        for row in rows
        if row["entity_name"]
    }


def get_or_create_entity(conn, entity_type, name):
    # 이름이 비어 있거나 정규화 불가능한 후보는 DB에 넣지 않는다.
    name = " ".join(str(name or "").split()).strip()
    require_normalized_name(name)

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

    row = conn.execute(
        """
        INSERT INTO entity (
            entity_type,
            entity_name,
            qid,
            entity_description
        )
        VALUES (%s, %s, NULL, NULL)
        ON CONFLICT (entity_type, entity_name)
        DO UPDATE SET entity_name = EXCLUDED.entity_name
        RETURNING entity_id
        """,
        (entity_type, name),
    ).fetchone()

    return row["entity_id"]


def get_link_target_title(candidate):
    """후보에서 실제 Wikipedia 링크 대상 제목을 가져온다."""

    # 502에서 생성한 대표 후보 필드를 우선 사용한다.
    target_title = candidate.get("link_target_title")

    if target_title:
        return str(target_title).strip() or None

    # 대표 필드에 없으면 관찰 문맥에서 확인한다.
    for observation in candidate.get("observations", []):
        target_title = observation.get("link_target_title")

        if target_title:
            return str(target_title).strip() or None

    return None


def upsert_candidate_relation(conn, content_id, candidate, source_url):
    entity_id = get_or_create_entity(
        conn,
        candidate["entity_type"],
        candidate["name"],
    )

    link_target_title = get_link_target_title(candidate)

    evidence = {
        "source_url": source_url,
        "source_section": candidate["section"],
        "excerpt": candidate["excerpt"],
        "source_quality": "curated_community",

        # 실제 Wikipedia 링크 대상 문서 제목.
        # 인포박스의 일반 텍스트 등에서 직접 추출되지 않았다면 None.
        "link_target_title": link_target_title,

        "score_features": candidate.get("score_features", {}),
        "source_score": candidate.get("source_score"),
        "rule_score": candidate.get("rule_score"),
        "exploration_branch": candidate.get("exploration_branch"),
        "mention_reason": candidate.get("mention_reason"),
        "relation_hint": candidate.get("relation_hint"),
        "observations": candidate.get("observations", []),
    }

    row = conn.execute(
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
            next_hop_eligible,
            candidate_score,
            score_version,
            scored_at
        )
        VALUES (
            'content',
            %s,
            %s,
            'entity',
            %s,
            'wikipedia_section',
            'proposed',
            %s::jsonb,
            'shown',
            %s,
            %s,
            %s,
            NOW()
        )
        ON CONFLICT (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id
        )
        DO UPDATE SET
            evidence = EXCLUDED.evidence,
            visibility = 'shown',
            next_hop_eligible = EXCLUDED.next_hop_eligible,
            candidate_score = EXCLUDED.candidate_score,
            score_version = EXCLUDED.score_version,
            scored_at = NOW()
        WHERE relationship.fact_status = 'proposed'
          AND relationship.source_name = 'wikipedia_section'
        RETURNING relationship_id
        """,
        (
            content_id,
            candidate["relation_type"],
            entity_id,
            json.dumps(evidence, ensure_ascii=False),
            bool(candidate.get("next_hop_eligible", False)),
            candidate.get("score"),
            SCORE_VERSION,
        ),
    ).fetchone()

    # 검증된 관계 또는 다른 출처의 관계는 덮어쓰지 않는다.
    return row["relationship_id"] if row else None


def clear_previous_refined_candidates(conn, content_id):
    """해당 콘텐츠의 이전 Wikipedia 정제 후보만 숨긴다."""

    conn.execute(
        """
        UPDATE relationship
        SET visibility = 'hidden'
        WHERE source_type = 'content'
          AND source_id = %s
          AND relationship_type = ANY(%s)
          AND fact_status = 'proposed'
          AND source_name = 'wikipedia_section'
        """,
        (content_id, REFINED_RELATION_TYPES),
    )
