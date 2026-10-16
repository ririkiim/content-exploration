import json

from wikidata_601_type_config import REFINED_RELATION_TYPES


def ensure_columns(conn):
    """분류 결과 저장에 필요한 컬럼을 준비한다."""
    conn.execute("""
        ALTER TABLE entity
        ADD COLUMN IF NOT EXISTS wikidata_instance_of JSONB
    """)

    conn.execute("""
        ALTER TABLE entity
        ADD COLUMN IF NOT EXISTS wikidata_category TEXT
    """)

    conn.execute("""
        ALTER TABLE entity
        ADD COLUMN IF NOT EXISTS wikidata_resolved_at TIMESTAMPTZ
    """)


def load_relations(conn):
    """분류 대상인 Wikipedia 후보 관계를 조회한다."""
    return conn.execute(
        """
        SELECT
            r.relationship_id,
            r.relationship_type,
            r.evidence,
            r.next_hop_eligible,
            r.source_type,
            r.source_id,
            r.target_id AS entity_id,
            r.candidate_score,
            r.score_version,
            r.scored_at,
            e.entity_name,
            e.entity_type,
            e.qid
        FROM relationship AS r
        JOIN entity AS e
          ON r.target_type = 'entity'
         AND r.target_id = e.entity_id
        WHERE r.fact_status = 'proposed'
          AND r.source_name = 'wikipedia_section'
          AND r.visibility = 'shown'
          AND r.relationship_type = ANY(%s)
        ORDER BY r.relationship_id
        """,
        (REFINED_RELATION_TYPES,),
    ).fetchall()


def find_qid_collision(conn, qid, entity_id):
    """
    같은 QID를 사용하는 다른 엔티티가 있는지 확인한다.

    충돌한 기존 엔티티의 정보를 반환한다.
    """
    if not qid:
        return []

    return conn.execute(
        """
        SELECT entity_id, entity_type, entity_name, qid
        FROM entity
        WHERE qid = %s
          AND entity_id <> %s
        ORDER BY entity_id
        """,
        (qid, entity_id),
    ).fetchall()


def update_entity_type(
    conn,
    entity_id,
    qid,
    instance_qids,
    category,
):
    """
    엔티티의 Wikidata 분류 결과를 저장한다.

    기존 QID를 덮어쓰지 않고, 다른 엔티티가 사용하는 QID도
    새로 할당하지 않는다.

    반환:
        qid_persisted: 해당 엔티티에 QID가 저장되어 있는지
    """
    if qid:
        conn.execute(
            """
            UPDATE entity
            SET qid = %s
            WHERE entity_id = %s
              AND qid IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM entity AS other
                  WHERE other.qid = %s
                    AND other.entity_id <> %s
              )
            """,
            (qid, entity_id, qid, entity_id),
        )

    conn.execute(
        """
        UPDATE entity
        SET wikidata_instance_of = %s::jsonb,
            wikidata_category = %s,
            wikidata_resolved_at = NOW()
        WHERE entity_id = %s
        """,
        (
            json.dumps(sorted(instance_qids), ensure_ascii=False),
            category,
            entity_id,
        ),
    )

    if not qid:
        return False

    row = conn.execute(
        """
        SELECT qid
        FROM entity
        WHERE entity_id = %s
        """,
        (entity_id,),
    ).fetchone()

    return bool(row and row["qid"] == qid)


def update_relationship_evidence(
    conn,
    relationship_id,
    evidence,
    qid,
    instance_qids,
    classification_qids,
    instance_labels,
    category,
    next_hop_eligible,
    qid_collision=False,
    canonical_entity_id=None,
    collision_entity_ids=None,
):
    """기존 evidence를 유지하면서 분류 결과를 추가한다."""
    updated_evidence = dict(evidence or {})

    updated_evidence.update({
        "wikidata_qid": qid,
        "wikidata_instance_of": sorted(instance_qids),
        "wikidata_type_classification_qids": sorted(
            classification_qids
        ),
        "wikidata_instance_labels": instance_labels,
        "wikidata_category": category,
        "type_source": "wikidata_p31_p279",
        "next_hop_eligible": next_hop_eligible,
        "type_score_version": "v5-wikidata-p31-p279",
        "wikidata_qid_collision": qid_collision,
        "wikidata_qid_collision_entity_ids": sorted(
            set(collision_entity_ids or [])
        ),
    })

    if canonical_entity_id is not None:
        updated_evidence["wikidata_existing_entity_id"] = (
            canonical_entity_id
        )

    conn.execute(
        """
        UPDATE relationship
        SET evidence = %s::jsonb,
            next_hop_eligible = %s
        WHERE relationship_id = %s
          AND fact_status = 'proposed'
          AND source_name = 'wikipedia_section'
        """,
        (
            json.dumps(updated_evidence, ensure_ascii=False),
            next_hop_eligible,
            relationship_id,
        ),
    )