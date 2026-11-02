import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


BASE = Path("data/enrichment/normalization")

WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# 방향을 보존해야 하므로 관계 유형을 임의로 related_to로 바꾸지 않음
PROPERTIES = {
    "P361": "part_of",
    "P527": "has_part",
    "P921": "main_subject",
    "P1269": "facet_of",
    "P155": "follows",
    "P156": "followed_by",
    "P279": "subclass_of",
    "P131": "located_in_administrative_entity",
}


def normalize(value):
    import unicodedata

    return " ".join(
        unicodedata.normalize("NFKC", str(value))
        .casefold()
        .split()
    )


def fetch_json(params):
    query = urllib.parse.urlencode(params)
    url = f"{WIKIDATA_API}?{query}"

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "ContentExplorationCapstone/0.1 "
                "(academic research)"
            )
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def select_query_qid(topic):
    """
    관계 조회용 QID 후보만 선별한다.
    최종 QID 승인과는 별개다.
    """
    name = normalize(topic["display_name"])
    matches = set()

    for candidate in topic.get("qid_candidates", []):
        qid = candidate.get("qid")

        if not qid:
            continue

        labels = candidate.get("labels") or []

        if any(normalize(label) == name for label in labels):
            matches.add(qid)

    if len(matches) == 1:
        return next(iter(matches)), "unique_exact_label_candidate"

    if len(matches) > 1:
        return None, "ambiguous_exact_label_candidates"

    return None, "no_unique_exact_label_candidate"


def fetch_entities(qids):
    if not qids:
        return {}

    result = {}

    for start in range(0, len(qids), 40):
        batch = qids[start:start + 40]

        data = fetch_json({
            "action": "wbgetentities",
            "ids": "|".join(batch),
            "props": "labels|descriptions|claims",
            "languages": "ko|en",
            "format": "json",
        })

        result.update(data.get("entities", {}))
        time.sleep(0.2)

    return result


def get_label(entity):
    labels = entity.get("labels", {})

    for lang in ("ko", "en"):
        value = labels.get(lang, {}).get("value")

        if value:
            return value

    return entity.get("id")


def extract_target_qid(statement):
    mainsnak = statement.get("mainsnak", {})

    if mainsnak.get("snaktype") != "value":
        return None

    value = mainsnak.get("datavalue", {}).get("value", {})

    if not isinstance(value, dict):
        return None

    if value.get("entity-type") != "item":
        return None

    return value.get("id")


def process(content_id):
    source = BASE / f"topic_catalogue_{content_id}_v3.json"
    target = BASE / f"topic_relations_{content_id}_v1.json"

    if not source.exists():
        raise FileNotFoundError(source)

    if target.exists():
        raise FileExistsError(
            f"기존 결과를 덮어쓰지 않습니다: {target}"
        )

    with source.open(encoding="utf-8") as f:
        catalogue = json.load(f)

    topics = catalogue["topics"]

    query_topics = []
    skipped_topics = []

    for topic in topics:
        qid, reason = select_query_qid(topic)

        record = {
            "topic_id": topic["topic_id"],
            "display_name": topic["display_name"],
            "qid": qid,
            "reason": reason,
        }

        if qid:
            query_topics.append(record)
        else:
            skipped_topics.append(record)

    entities = fetch_entities(
        sorted({t["qid"] for t in query_topics})
    )

    raw_relations = []
    target_qids = set()

    for topic in query_topics:
        source_qid = topic["qid"]
        entity = entities.get(source_qid, {})

        if entity.get("missing"):
            continue

        claims = entity.get("claims", {})

        for property_id, relation_type in PROPERTIES.items():
            for statement in claims.get(property_id, []):
                target_qid = extract_target_qid(statement)

                if not target_qid:
                    continue

                # deprecated Wikidata statement 제외
                rank = statement.get("rank", "normal")

                if rank == "deprecated":
                    continue

                raw_relations.append({
                    "source_topic_id": topic["topic_id"],
                    "source_topic_name": topic["display_name"],
                    "source_qid_candidate": source_qid,
                    "target_qid": target_qid,
                    "relation_type": relation_type,
                    "property_id": property_id,
                    "statement_id": statement.get("id"),
                    "statement_rank": rank,
                    "evidence_source": "wikidata_statement",
                    "evidence_url": (
                        f"https://www.wikidata.org/wiki/"
                        f"{source_qid}#{property_id}"
                    ),
                    "evidence_status": "unchecked",
                    "review_status": "proposed",
                    "source_qid_confirmed": False,
                })

                target_qids.add(target_qid)

    targets = fetch_entities(sorted(target_qids))

    relations = []

    for relation in raw_relations:
        target_entity = targets.get(relation["target_qid"], {})

        relations.append({
            **relation,
            "target_name": (
                get_label(target_entity)
                if target_entity
                else relation["target_qid"]
            ),
        })

    output = {
        "schema_version": "topic_relations_v1",
        "content_id": content_id,
        "source_file": source.name,
        "query_topics": query_topics,
        "skipped_topics": skipped_topics,
        "relations": relations,
        "summary": {
            "total_topics": len(topics),
            "queryable_topics": len(query_topics),
            "skipped_topics": len(skipped_topics),
            "relations_generated": len(relations),
            "unique_target_qids": len(target_qids),
            "verified_relations": 0,
        },
        "policy": {
            "database_written": False,
            "llm_used": False,
            "qid_auto_confirmed": False,
            "relationship_auto_verified": False,
            "wikipedia_links_used_as_evidence": False,
        },
    }

    with target.open("x", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[{content_id}]")
    print(f"전체 토픽: {len(topics)}")
    print(f"관계 조회 대상: {len(query_topics)}")
    print(f"보류 토픽: {len(skipped_topics)}")
    print(f"생성된 관계: {len(relations)}")
    print(f"저장: {target}")


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