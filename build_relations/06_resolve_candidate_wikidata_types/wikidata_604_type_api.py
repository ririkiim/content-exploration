import re

from wikidata_601_type_config import (
    BATCH_SIZE,
    WIKIDATA_API,
)
from wikidata_602_type_utils import (
    batched,
    cache_path,
    load_json_cache,
    request_json,
    save_json_cache,
)


def get_entity_cache(qid):
    return load_json_cache(cache_path(qid))


def fetch_wikidata_entities(qids):
    """
    Wikidata 엔티티를 조회한다.
    캐시가 있는 QID는 API를 다시 호출하지 않는다.
    """
    qids = list(dict.fromkeys(
        qid
        for qid in qids
        if qid and re.fullmatch(r"Q\d+", qid)
    ))

    entities = {}
    missing_qids = []

    for qid in qids:
        cached = get_entity_cache(qid)

        if cached and cached.get("id") == qid:
            entities[qid] = cached
        else:
            missing_qids.append(qid)

    for batch in batched(missing_qids, BATCH_SIZE):
        data = request_json(
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(batch),
                "props": "labels|descriptions|claims",
                "languages": "ko|en",
            },
        )

        for qid, entity in data.get("entities", {}).items():
            if entity.get("missing"):
                continue

            entities[qid] = entity
            save_json_cache(cache_path(qid), entity)

    return entities


def claim_qids(entity, property_id):
    """특정 Wikidata 속성에서 엔티티 QID 목록을 추출한다."""
    result = set()

    claims = entity.get("claims", {}).get(property_id, [])

    for claim in claims:
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})

        if datavalue.get("type") != "wikibase-entityid":
            continue

        qid = datavalue.get("value", {}).get("id")

        if qid and re.fullmatch(r"Q\d+", qid):
            result.add(qid)

    return result


def labels_for_qids(qids, entities):
    """타입 QID에 대응하는 한국어·영어 라벨을 반환한다."""
    labels = {}

    for qid in qids:
        label_data = entities.get(qid, {}).get("labels", {})

        labels[qid] = (
            label_data.get("ko", {}).get("value")
            or label_data.get("en", {}).get("value")
            or qid
        )

    return labels


def fetch_type_hierarchy(instance_qids, entities, max_depth=2):
    """
    P31(instance of)에서 시작해 P279(subclass of) 상위 타입을
    max_depth 단계까지 조회한다.
    """
    all_type_qids = set(instance_qids)
    frontier = set(instance_qids)

    for _ in range(max_depth):
        missing = [
            qid for qid in frontier
            if qid not in entities
        ]

        if missing:
            entities.update(fetch_wikidata_entities(missing))

        next_frontier = set()

        for qid in frontier:
            entity = entities.get(qid, {})

            for parent_qid in claim_qids(entity, "P279"):
                if parent_qid not in all_type_qids:
                    next_frontier.add(parent_qid)

        all_type_qids.update(next_frontier)
        frontier = next_frontier

        if not frontier:
            break

    missing = [
        qid for qid in all_type_qids
        if qid not in entities
    ]

    if missing:
        entities.update(fetch_wikidata_entities(missing))

    return all_type_qids