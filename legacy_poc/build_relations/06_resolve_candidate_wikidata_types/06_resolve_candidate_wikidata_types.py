import psycopg
from psycopg.rows import dict_row

from wikidata_604_type_api import (
    claim_qids,
    fetch_type_hierarchy,
    fetch_wikidata_entities,
    labels_for_qids,
)
from wikidata_605_type_classifier import (
    category_from_instance_of,
    is_next_hop_eligible,
)
from wikidata_601_type_config import (
    DATABASE_URL,
    TYPE_SCORE_VERSION,
)
from wikidata_606_type_repository import (
    ensure_columns,
    find_qid_collision,
    load_relations,
    update_entity_type,
    update_relationship_evidence,
)
from wikidata_602_type_utils import (
    get_evidence_dict,
    normalize_title,
)
from wikidata_603_type_wikipedia import (
    get_wikipedia_lookup_title,
    resolve_titles_to_qids,
)


CATEGORIES = (
    "person",
    "work",
    "event",
    "place",
    "organization",
    "award",
    "genre",
    "unresolved",
)


def resolve_entity_qids(rows, conn):
    """
    entity별 QID를 결정하고, 기존 DB 및 이번 처리 대상 사이의
    QID 충돌을 탐지한다.

    충돌이 있어도 QID를 분류에 사용할 수는 있지만,
    충돌한 엔티티에 QID를 새로 저장하거나 다음 홉을 허용하지 않는다.
    """
    row_evidence = {}
    row_lookup_titles = {}
    titles_to_resolve = []

    for row in rows:
        relationship_id = row["relationship_id"]
        evidence = get_evidence_dict(row["evidence"])

        row_evidence[relationship_id] = evidence

        lookup_title = get_wikipedia_lookup_title({
            **row,
            "evidence": evidence,
        })
        row_lookup_titles[relationship_id] = lookup_title

        if not row["qid"] and lookup_title:
            titles_to_resolve.append(lookup_title)

    title_to_qid, disambiguation_titles = (
        resolve_titles_to_qids(titles_to_resolve)
    )

    entity_existing_qids = {}
    entity_resolved_qids = {}

    for row in rows:
        entity_id = row["entity_id"]

        if row["qid"]:
            entity_existing_qids[entity_id] = row["qid"]

    for row in rows:
        entity_id = row["entity_id"]
        relationship_id = row["relationship_id"]

        # 이미 QID가 등록된 엔티티의 QID는 유지한다.
        if entity_existing_qids.get(entity_id):
            continue

        lookup_title = row_lookup_titles[relationship_id]

        if not lookup_title:
            continue

        title_key = normalize_title(lookup_title)

        if title_key in disambiguation_titles:
            continue

        qid = title_to_qid.get(title_key)

        if qid:
            entity_resolved_qids.setdefault(
                entity_id, set()
            ).add(qid)

    entity_qids = {}

    for row in rows:
        entity_id = row["entity_id"]

        if entity_id in entity_qids:
            continue

        existing_qid = entity_existing_qids.get(entity_id)

        if existing_qid:
            entity_qids[entity_id] = existing_qid
            continue

        resolved_qids = entity_resolved_qids.get(
            entity_id, set()
        )

        entity_qids[entity_id] = (
            next(iter(resolved_qids))
            if len(resolved_qids) == 1
            else None
        )

    # 각 QID가 이번 처리 대상의 몇 개 엔티티에 연결되는지 계산한다.
    qid_to_candidate_entities = {}

    for entity_id, qid in entity_qids.items():
        if qid:
            qid_to_candidate_entities.setdefault(
                qid, set()
            ).add(entity_id)

    collision_info = {}

    for entity_id, qid in entity_qids.items():
        if not qid:
            continue

        existing_rows = find_qid_collision(
            conn,
            qid,
            entity_id,
        )

        candidate_entity_ids = qid_to_candidate_entities.get(
            qid, set()
        )

        other_candidate_ids = (
            candidate_entity_ids - {entity_id}
        )

        
        # 이미 DB에 같은 QID가 등록된 엔티티라면 기존 QID를 유지한다.
        is_existing_qid_owner = (
            entity_existing_qids.get(entity_id) == qid
        )

        # 기존 QID 보유자가 아닌데 DB 또는 이번 처리 대상에
        # 같은 QID가 존재하면 충돌로 분류한다.
        if existing_rows or (
            other_candidate_ids and not is_existing_qid_owner
        ):
            collision_info[entity_id] = {
                "qid": qid,
                "existing_rows": existing_rows,
                "other_candidate_ids": sorted(other_candidate_ids),
            }


            print(
                "[WARN] QID 중복 검토 필요: "
                f"entity_id={entity_id}, "
                f"entity_qid={qid}, "
                f"DB 기존 행={existing_rows}, "
                f"이번 처리 내 다른 entity_id={sorted(other_candidate_ids)}"
            )

    row_qids = {
        row["relationship_id"]: entity_qids.get(row["entity_id"])
        for row in rows
    }

    return (
        row_evidence,
        entity_qids,
        row_qids,
        collision_info,
    )


def classify_rows(rows, row_qids):
    """각 관계 후보의 P31/P279 타입을 수집하고 분류한다."""
    candidate_qids = list(dict.fromkeys(
        qid for qid in row_qids.values() if qid
    ))

    entities = fetch_wikidata_entities(candidate_qids)
    row_type_data = {}
    all_type_qids = set()

    for row in rows:
        relationship_id = row["relationship_id"]
        qid = row_qids[relationship_id]

        if not qid or qid not in entities:
            row_type_data[relationship_id] = {
                "instance_qids": set(),
                "classification_qids": set(),
                "labels": {},
                "category": "unresolved",
            }
            continue

        entity = entities[qid]
        instance_qids = claim_qids(entity, "P31")

        if not instance_qids:
            row_type_data[relationship_id] = {
                "instance_qids": set(),
                "classification_qids": set(),
                "labels": {},
                "category": "unresolved",
            }
            continue

        classification_qids = fetch_type_hierarchy(
            instance_qids,
            entities,
            max_depth=2,
        )

        all_type_qids.update(classification_qids)

        row_type_data[relationship_id] = {
            "instance_qids": instance_qids,
            "classification_qids": classification_qids,
            "labels": {},
            "category": None,
        }

    type_labels = labels_for_qids(all_type_qids, entities)

    for row in rows:
        relationship_id = row["relationship_id"]
        type_data = row_type_data[relationship_id]

        if type_data["category"] == "unresolved":
            continue

        type_data["labels"] = {
            qid: type_labels.get(qid, qid)
            for qid in type_data["classification_qids"]
        }

        type_data["category"] = category_from_instance_of(
            type_data["instance_qids"],
            type_data["classification_qids"],
            type_labels,
        )

    return row_type_data


def main():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL이 설정되지 않았습니다. "
            ".env 파일을 확인하세요."
        )

    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    ) as conn:
        ensure_columns(conn)

        rows = load_relations(conn)

        print(f"[INFO] 처리 대상 후보: {len(rows)}")

        if not rows:
            print(
                "[INFO] 처리할 후보가 없습니다. "
                "05 실행 결과와 relationship 조건을 확인하세요."
            )
            return

        (
            row_evidence,
            entity_qids,
            row_qids,
            collision_info,
        ) = resolve_entity_qids(rows, conn)

        row_type_data = classify_rows(rows, row_qids)

        stats = {
            **{category: 0 for category in CATEGORIES},
            "next_hop_eligible": 0,
            "qid_matched": 0,
            "qid_unmatched": 0,
            "qid_persisted": 0,
            "qid_not_persisted": 0,
            "qid_collision": 0,
        }

        # 한 엔티티가 여러 관계에 나타나도 QID 저장 통계는
        # 엔티티 단위로 한 번만 계산한다.
        entity_persisted = {}

        for row in rows:
            relationship_id = row["relationship_id"]
            entity_id = row["entity_id"]
            qid = row_qids[relationship_id]

            evidence = row_evidence[relationship_id]
            type_data = row_type_data[relationship_id]

            instance_qids = type_data["instance_qids"]
            classification_qids = type_data["classification_qids"]
            instance_labels = type_data["labels"]
            category = type_data["category"] or "unresolved"

            has_collision = entity_id in collision_info

            next_hop_eligible = is_next_hop_eligible(
                row=row,
                category=category,
                evidence=evidence,
                qid=qid,
                qid_collision=has_collision,
            )

            # 충돌이 있는 경우 분류에는 qid를 사용하되,
            # entity.qid 컬럼에는 새로 저장하지 않는다.
            qid_for_db = None if has_collision else qid

            if entity_id not in entity_persisted:
                entity_persisted[entity_id] = update_entity_type(
                    conn=conn,
                    entity_id=entity_id,
                    qid=qid_for_db,
                    instance_qids=instance_qids,
                    category=category,
                )

            collision_entity_ids = []

            if has_collision:
                collision = collision_info[entity_id]

                collision_entity_ids = sorted(
                    set(collision["other_candidate_ids"])
                    | {
                        item["entity_id"]
                        for item in collision["existing_rows"]
                    }
                )

            update_relationship_evidence(
                conn=conn,
                relationship_id=relationship_id,
                evidence=evidence,
                qid=qid,
                instance_qids=instance_qids,
                classification_qids=classification_qids,
                instance_labels=instance_labels,
                category=category,
                next_hop_eligible=next_hop_eligible,
                qid_collision=has_collision,
                collision_entity_ids=collision_entity_ids,
            )

            stats[category] += 1

            if next_hop_eligible:
                stats["next_hop_eligible"] += 1

            if qid:
                stats["qid_matched"] += 1
            else:
                stats["qid_unmatched"] += 1

            if has_collision:
                stats["qid_collision"] += 1

        
        # 실제 QID가 결정된 엔티티만 DB 저장 통계에 포함한다.
        for entity_id, persisted in entity_persisted.items():
            if not entity_qids.get(entity_id):
                continue

            if persisted:
                stats["qid_persisted"] += 1
            else:
                stats["qid_not_persisted"] += 1

        conn.commit()

    print("\n========== Wikidata 분류 결과 ==========")
    print(f"전체 후보 관계: {len(rows)}")
    print(f"QID 매칭 성공(관계 기준): {stats['qid_matched']}")
    print(f"QID 매칭 실패(관계 기준): {stats['qid_unmatched']}")
    print(f"QID DB 저장 성공(엔티티 기준): {stats['qid_persisted']}")
    print(f"QID DB 미저장(엔티티 기준): {stats['qid_not_persisted']}")
    print(f"QID 중복 검토 대상(관계 기준): {stats['qid_collision']}")

    for category in CATEGORIES:
        print(f"{category}: {stats[category]}")

    print(f"다음 홉 탐색 허용: {stats['next_hop_eligible']}")
    print("========================================")


if __name__ == "__main__":
    main()