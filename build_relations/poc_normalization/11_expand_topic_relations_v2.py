import argparse
import json
import hashlib
from collections import Counter
from pathlib import Path


BASE = Path("data/enrichment/normalization")


def load_json(path, required=True):
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return None

    with path.open(encoding="utf-8") as f:
        return json.load(f)


def unique_records(records):
    result = []
    seen = set()

    for record in records:
        key = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
        )

        if key not in seen:
            seen.add(key)
            result.append(record)

    return result


def make_id(prefix, *parts):
    value = "|".join(str(part) for part in parts)

    digest = hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()[:16]

    return f"{prefix}:{digest}"


def get_topic_origins(topic):
    return topic.get("candidate_origins", [])


def get_qid_candidates(topic):
    return [
        candidate.get("qid")
        for candidate in topic.get("qid_candidates", [])
        if candidate.get("qid")
    ]


def build_content_topic_relations(content_id, topics):
    """
    원본 키워드와 추출 토픽을 모두 보존한다.

    QID가 없어도 관계 생성.
    QID는 선택적 보조 정보로만 저장.
    """
    relations = []

    for topic in topics:
        origins = get_topic_origins(topic)

        relation = {
            "relation_id": make_id(
                "content_topic",
                content_id,
                topic["topic_id"],
            ),
            "source_content_id": content_id,
            "target_topic_id": topic["topic_id"],
            "target_topic_name": topic["display_name"],

            "relation_type": "has_topic",

            "candidate_origins": origins,
            "source_references": topic.get(
                "source_references", []
            ),
            "evidence_document_ids": topic.get(
                "evidence_document_ids", []
            ),

            # QID는 필수가 아님
            "qid": topic.get("qid"),
            "qid_candidates": get_qid_candidates(topic),
            "qid_resolution_status": topic.get(
                "qid_resolution_status"
            ),

            "evidence_status": "unchecked",
            "review_status": "proposed",
        }

        relations.append(relation)

    return relations


def build_topic_relations(content_id, topics, wikidata_data):
    """
    기존 11 v1의 Wikidata 관계를 재활용한다.

    단, Wikidata 관계를 검증 완료로 간주하지 않는다.
    """
    if not wikidata_data:
        return []

    topic_lookup = {
        topic["topic_id"]: topic
        for topic in topics
    }

    relations = []

    for old in wikidata_data.get("relations", []):
        source_topic_id = old.get("source_topic_id")

        if source_topic_id not in topic_lookup:
            continue

        source_topic = topic_lookup[source_topic_id]

        source_qid = old.get("source_qid_candidate")
        target_qid = old.get("target_qid")

        relation = {
            "relation_id": make_id(
                "topic_relation",
                source_topic_id,
                source_qid,
                old.get("property_id"),
                target_qid,
                old.get("statement_id"),
            ),

            "source_topic_id": source_topic_id,
            "source_topic_name": source_topic["display_name"],

            "source_qid_candidate": source_qid,

            "target_topic_id": None,
            "target_topic_name": old.get("target_name"),
            "target_qid": target_qid,

            "relation_type": old.get("relation_type"),
            "property_id": old.get("property_id"),

            "evidence_source": "wikidata_statement",
            "evidence_url": old.get("evidence_url"),
            "statement_id": old.get("statement_id"),
            "statement_rank": old.get("statement_rank"),

            "source_qid_confirmed": False,

            "evidence_status": "unchecked",
            "review_status": "proposed",
        }

        relations.append(relation)

    return unique_records(relations)


def build_discovery_candidates(content_id, catalogue):
    """
    기존 Wikipedia 탐색 후보를 보존한다.

    Wikipedia 링크는 관계 증거가 아니므로
    topic_relations에 넣지 않는다.
    """
    candidates = []

    for index, candidate in enumerate(
        catalogue.get("navigation_candidates", [])
    ):
        candidate_id = make_id(
            "discovery",
            content_id,
            index,
            candidate,
        )

        candidates.append({
            "candidate_id": candidate_id,
            "content_id": content_id,
            "candidate": candidate,

            "discovery_source": "wikipedia_link",

            "relation_type": None,
            "relation_claimed": False,

            "verification_role": "candidate_discovery_only",

            "evidence_status": "unchecked",
            "review_status": "proposed",
        })

    return candidates


def build_expansion_queue(topics, content_relations):
    """
    모든 토픽을 확장 대상으로 유지한다.

    QID가 없는 토픽도 제외하지 않는다.
    """
    queue = []

    for topic in topics:
        qid_candidates = get_qid_candidates(topic)

        queue.append({
            "topic_id": topic["topic_id"],
            "topic_name": topic["display_name"],

            "candidate_origins": get_topic_origins(topic),

            "qid": topic.get("qid"),
            "qid_candidates": qid_candidates,

            "expansion_status": "pending",

            "expansion_methods": [
                "wikipedia_context",
                "wikidata_if_qid_resolved",
                "llm_relation_proposal",
            ],
        })

    return queue


def process(content_id):
    catalogue_path = (
        BASE / f"topic_catalogue_{content_id}_v3.json"
    )

    wikidata_path = (
        BASE / f"topic_relations_{content_id}_v1.json"
    )

    output_path = (
        BASE / f"topic_relations_{content_id}_v2.json"
    )

    if output_path.exists():
        raise FileExistsError(
            f"기존 파일을 덮어쓰지 않습니다: {output_path}"
        )

    catalogue = load_json(catalogue_path)
    wikidata_data = load_json(
        wikidata_path,
        required=False,
    )

    topics = catalogue["topics"]

    content_relations = build_content_topic_relations(
        content_id,
        topics,
    )

    topic_relations = build_topic_relations(
        content_id,
        topics,
        wikidata_data,
    )

    discovery_candidates = build_discovery_candidates(
        content_id,
        catalogue,
    )

    expansion_queue = build_expansion_queue(
        topics,
        content_relations,
    )

    origin_counts = Counter()

    for topic in topics:
        for origin in get_topic_origins(topic):
            origin_counts[origin] += 1

    output = {
        "schema_version": "topic_relations_v2",

        "content_id": content_id,

        "source_files": {
            "topic_catalogue": catalogue_path.name,
            "wikidata_relations": (
                wikidata_path.name
                if wikidata_data
                else None
            ),
        },

        "content_topic_relations": content_relations,

        "topic_relations": topic_relations,

        "discovery_candidates": discovery_candidates,

        "expansion_queue": expansion_queue,

        "summary": {
            "total_topics": len(topics),

            "content_topic_relations": len(
                content_relations
            ),

            "wikidata_topic_relations": len(
                topic_relations
            ),

            "discovery_candidates": len(
                discovery_candidates
            ),

            "expansion_queue": len(
                expansion_queue
            ),

            "topics_without_confirmed_qid": sum(
                1 for topic in topics
                if not topic.get("qid")
            ),

            "origin_counts": dict(origin_counts),
        },

        "policy": {
            "primary_entity": "keyword_or_topic",

            "qid_required": False,
            "qid_auto_confirmed": False,

            "original_keywords_preserved": True,

            "wikipedia_links_as_relation_evidence": False,

            "llm_used": False,
            "database_written": False,
        },

        "limitations": [
            "Wikidata relations are imported from v1 and remain unverified.",
            "Wikipedia navigation candidates are not asserted topic relations.",
            "The expansion queue records pending work; it does not execute expansion.",
            "Content-topic links preserve source provenance but are not independently verified.",
        ],
    }

    with output_path.open("x", encoding="utf-8") as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2,
        )

    summary = output["summary"]

    print(f"\n[{content_id}]")
    print(f"전체 토픽: {summary['total_topics']}")
    print(
        "콘텐츠-토픽 관계:",
        summary["content_topic_relations"],
    )
    print(
        "Wikidata 토픽 관계:",
        summary["wikidata_topic_relations"],
    )
    print(
        "Wikipedia 탐색 후보:",
        summary["discovery_candidates"],
    )
    print(
        "확장 대기 토픽:",
        summary["expansion_queue"],
    )
    print(f"저장: {output_path}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    args = parser.parse_args()

    process(args.content_id)


if __name__ == "__main__":
    main()