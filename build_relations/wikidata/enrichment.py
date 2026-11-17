from datetime import datetime, timezone

from .client import (
    claim_qids,
    get_entities,
    label,
    request_json,
)
from .config import API_URL


# 수집 대상 속성
PROPERTIES = {
    "P31": "instance_of",
    "P136": "genre",
    "P921": "main_subject",
    "P180": "depicts",
    "P840": "narrative_location",
    "P793": "significant_event",
    "P144": "based_on",
    "P57": "director",
    "P58": "screenwriter",
    "P170": "creator",
    "P50": "author",
    "P161": "cast_member",
}


def description(entity):
    descriptions = entity.get("descriptions", {})
    return (
        descriptions.get("ko", {}).get("value")
        or descriptions.get("en", {}).get("value")
    )


def fetch_work(qid):
    """작품 정보와 Wikipedia 연결 정보를 함께 조회한다."""
    data = request_json(
        API_URL,
        {
            "action": "wbgetentities",
            "format": "json",
            "ids": qid,
            "props": "labels|descriptions|claims|sitelinks",
            "languages": "ko|en",
        },
    )

    entity = data.get("entities", {}).get(qid)

    if not entity or "missing" in entity:
        raise ValueError(f"Wikidata 작품이 없습니다: {qid}")

    return entity


def collect_wikidata(qid):
    # 1. 작품 원본 조회
    work = fetch_work(qid)

    # 2. 선택한 속성에서 직접 연결된 QID 수집
    targets_by_property = {
        prop: claim_qids(work, prop)
        for prop in PROPERTIES
    }

    target_qids = {
        target
        for targets in targets_by_property.values()
        for target in targets
    }

    # 3. 직접 연결된 개체만 조회
    target_entities = get_entities(target_qids)

    # 4. 다음 단계에서 사용하기 편한 형태로 정리
    claims = []

    for prop, targets in targets_by_property.items():
        for target_qid in targets:
            target = target_entities.get(target_qid, {})

            claims.append({
                "property_id": prop,
                "property_name": PROPERTIES[prop],
                "target_qid": target_qid,
                "target_label": label(target) or target_qid,
                "target_description": description(target),
                "target_type_qids": claim_qids(target, "P31"),
                "target_available": bool(target),
                "source_url": f"https://www.wikidata.org/wiki/{qid}",
            })

    return {
        "qid": qid,
        "label": label(work),
        "description": description(work),
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "kowiki_title": (
            work.get("sitelinks", {})
            .get("kowiki", {})
            .get("title")
        ),
        "claims": claims,
        # 추후 재가공을 위해 API 원본도 보존
        "raw_work": work,
        "raw_targets": target_entities,
    }