
"""05 단계에서 저장한 위키 후보를 Wikidata QID/P31 기준으로 타입화한다.

현재 DB 구조:
- relationship: 후보 관계
- entity: 관계 대상 엔티티
- entity.qid: Wikidata QID
- relationship.evidence: 후보 근거 및 타입화 결과

QID를 확인할 수 없는 후보는 unresolved로 남긴다.
기존 관계의 fact_status, visibility, relationship_type은 변경하지 않는다.

실행:
    python build_relations/06_resolve_candidate_wikidata_types.py
"""

import json
import os
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "enrichment" / "candidate_wikidata"

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

REQUEST_DELAY_SECONDS = 1.2
BATCH_SIZE = 40

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

WIKIMEDIA_CONTACT = os.getenv(
    "WIKIMEDIA_CONTACT",
    "mailto:content-exploration@example.invalid",
)
USER_AGENT = (
    f"content-exploration-capstone/1.0 ({WIKIMEDIA_CONTACT})"
)

# 05 단계에서 저장하는 정제 후보 관계 유형
REFINED_RELATION_TYPES = (
    "wikipedia_context_candidate",
    "wikipedia_original_candidate",
    "wikipedia_series_candidate",
    "wikipedia_related_work_candidate",
    "wikipedia_creator_candidate",
    "wikipedia_mention_candidate",
)

# Wikidata 상위 개념 QID.
# 특정 작품의 정답 목록이 아니라 일반적인 타입 분류 규칙이다.
HUMAN_QID = "Q5"

WORK_INSTANCE_QIDS = {
    "Q11424",       # film
    "Q5398426",      # television series
    "Q571",          # book
    "Q7725634",      # literary work
    "Q47461344",     # written work
    "Q11060274",     # webtoon
    "Q7889",         # video game
}

EVENT_INSTANCE_QIDS = {
    "Q1190554",      # occurrence
    "Q1656682",      # event
    "Q178561",       # battle
    "Q198",          # war
    "Q40231",        # election
}

ORGANIZATION_INSTANCE_QIDS = {
    "Q43229",        # organization
    "Q4830453",      # business
    "Q2088357",      # publisher
}

PLACE_INSTANCE_QIDS = {
    "Q17334923",     # location
    "Q2221906",      # geographic location
    "Q515",          # city
    "Q6256",         # country
    "Q82794",        # geographic region
}

NEXT_HOP_CATEGORIES = {"event", "person", "work"}


def chunks(items, size=BATCH_SIZE):
    """목록을 API 요청 단위로 나눈다."""
    for index in range(0, len(items), size):
        yield items[index:index + size]


def request_json(base_url, params):
    """Wikimedia API를 호출하고 JSON 응답을 반환한다."""
    request = Request(
        f"{base_url}?{urlencode(params)}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)

    except HTTPError as error:
        if error.code == 429:
            raise RuntimeError(
                "Wikimedia 요청 제한(429)입니다. "
                "잠시 후 다시 실행하세요. "
                "완료된 Wikidata 엔티티는 캐시에서 재사용합니다."
            ) from error
        raise


def ensure_columns(conn):
    """현재 entity 테이블에 타입화 결과를 저장할 컬럼을 추가한다."""
    conn.execute(
        """
        ALTER TABLE entity
        ADD COLUMN IF NOT EXISTS wikidata_instance_of JSONB
        """
    )

    conn.execute(
        """
        ALTER TABLE entity
        ADD COLUMN IF NOT EXISTS wikidata_category TEXT
        """
    )

    conn.execute(
        """
        ALTER TABLE entity
        ADD COLUMN IF NOT EXISTS wikidata_resolved_at TIMESTAMPTZ
        """
    )


def load_relations(conn):
    """05 단계에서 저장한 표시 가능한 미확정 후보를 조회한다."""
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
        (list(REFINED_RELATION_TYPES),),
    ).fetchall()


def cache_path(qid):
    return CACHE_DIR / f"{qid}.json"


def resolve_titles_to_qids(titles):
    """한국어 위키 문서 제목을 Wikidata QID로 변환한다."""
    result = {title: None for title in titles}
    unique_titles = list(dict.fromkeys(
        title for title in titles if title
    ))

    for title_batch in chunks(unique_titles):
        data = request_json(
            WIKIPEDIA_API,
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
                "titles": "|".join(title_batch),
                "prop": "pageprops",
                "ppprop": "wikibase_item",
            },
        )

        query = data.get("query", {})
        aliases = {}

        for item in (
            query.get("normalized", [])
            + query.get("redirects", [])
        ):
            source = item.get("from")
            target = item.get("to")
            if source and target:
                aliases[source] = target

        pages = {
            page.get("title"): page
            for page in query.get("pages", [])
            if not page.get("missing") and page.get("title")
        }

        for requested_title in title_batch:
            resolved_title = aliases.get(
                requested_title,
                requested_title,
            )
            page = pages.get(resolved_title)

            if page:
                result[requested_title] = (
                    page.get("pageprops", {}).get("wikibase_item")
                )

        time.sleep(REQUEST_DELAY_SECONDS)

    return result


def get_entity_cache(qid):
    """캐시된 Wikidata 엔티티를 읽는다."""
    path = cache_path(qid)

    if not path.exists():
        return None

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def fetch_wikidata_entities(qids):
    """Wikidata 엔티티를 가져온다. 캐시된 QID는 재요청하지 않는다."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    result = {}
    missing_qids = []

    for qid in dict.fromkeys(qids):
        if not qid:
            continue

        cached = get_entity_cache(qid)

        if cached is not None:
            result[qid] = cached
        else:
            missing_qids.append(qid)

    for qid_batch in chunks(missing_qids):
        data = request_json(
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "format": "json",
                "ids": "|".join(qid_batch),
                "props": "labels|descriptions|claims",
                "languages": "ko|en",
            },
        )

        for qid, entity in data.get("entities", {}).items():
            result[qid] = entity

            cache_path(qid).write_text(
                json.dumps(
                    entity,
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        time.sleep(REQUEST_DELAY_SECONDS)

    return result


def claim_qids(entity, property_id):
    """Wikidata의 특정 속성에서 QID 목록을 추출한다."""
    qids = []

    for claim in entity.get("claims", {}).get(property_id, []):
        snak = claim.get("mainsnak", {})
        value = snak.get("datavalue", {}).get("value", {})

        if (
            snak.get("snaktype") == "value"
            and value.get("id")
        ):
            qids.append(value["id"])

    return list(dict.fromkeys(qids))


def labels_for_qids(instance_qids, entities):
    """QID 목록을 한국어 또는 영어 라벨로 변환한다."""
    labels = []

    for qid in instance_qids:
        entity = entities.get(qid, {})

        label = (
            entity.get("labels", {}).get("ko", {}).get("value")
            or entity.get("labels", {}).get("en", {}).get("value")
            or qid
        )
        labels.append(label)

    return labels


def fetch_type_hierarchy(instance_qids, entities, max_depth=2):
    """P31 타입에서 시작해 P279 상위 타입을 최대 두 단계 수집한다."""
    all_qids = set(instance_qids)
    frontier = set(instance_qids)

    for _ in range(max_depth):
        parent_qids = set()

        for qid in frontier:
            parent_qids.update(
                claim_qids(entities.get(qid, {}), "P279")
            )

        new_qids = parent_qids - all_qids

        if not new_qids:
            break

        entities.update(
            fetch_wikidata_entities(sorted(new_qids))
        )

        all_qids.update(new_qids)
        frontier = new_qids

    return list(all_qids)


def category_from_instance_of(instance_qids, instance_labels):
    """P31 및 제한된 상위 타입 정보를 보수적인 탐색 유형으로 변환한다."""
    qid_set = set(instance_qids)
    text = " ".join(instance_labels).casefold()

    # 동음이의어 문서는 실제 엔티티로 취급하지 않는다.
    if re.search(r"동음이의어|disambiguation", text):
        return "unresolved"

    # 수상·장르 등은 사건이나 작품으로 잘못 분류하지 않도록 먼저 제외한다.
    if re.search(
        r"\b(award|prize)\b|수상|영화상|시상",
        text,
    ):
        return "award"

    if re.search(
        r"\b(genre|style|emotion)\b|장르|양식|감정",
        text,
    ):
        return "genre"

    if HUMAN_QID in qid_set:
        return "person"

    if qid_set & WORK_INSTANCE_QIDS:
        return "work"

    # 장소와 조직 타입이 함께 있으면 장소를 우선한다.
    if qid_set & PLACE_INSTANCE_QIDS:
        return "place"

    if qid_set & ORGANIZATION_INSTANCE_QIDS:
        return "organization"

    if qid_set & EVENT_INSTANCE_QIDS:
        return "event"

    # 구체적인 P31 타입의 라벨을 이용한 보수적 보조 분류
    if re.search(
        r"\b(event|battle|war|revolution|uprising)\b"
        r"|사건|전쟁|전투|항쟁|혁명|해전",
        text,
    ):
        return "event"

    if re.search(
        r"\b(film|television series|book|novel|webtoon|literary work)\b"
        r"|영화|드라마|책|소설|웹툰|문학 작품",
        text,
    ):
        return "work"

    if re.search(
        r"\b(city|country|village|geographic|location)\b"
        r"|도시|국가|지역|지리",
        text,
    ):
        return "place"

    if re.search(
        r"\b(organization|company|newspaper|broadcasting)\b"
        r"|조직|기업|신문사|방송사",
        text,
    ):
        return "organization"

    return "unresolved"


def is_next_hop_eligible(row, category, evidence):
    """타입뿐 아니라 후보 관계의 문맥까지 확인해 다음 홉 여부를 결정한다."""
    if category not in NEXT_HOP_CATEGORIES:
        return False

    if row["relationship_type"] == "wikipedia_mention_candidate":
        return False

    if evidence.get("mention_reason"):
        return False

    if evidence.get("relation_hint") == "comparison":
        return False

    return True


def update_entity_type(
    conn,
    entity_id,
    qid,
    instance_qids,
    category,
):
    """엔티티의 Wikidata 타입 정보를 저장한다.

    이미 다른 엔티티가 같은 QID를 사용하고 있으면 qid 충돌을 피한다.
    타입 정보는 해당 엔티티에 저장하되, 기존의 다른 QID를 덮어쓰지 않는다.
    """
    if qid:
        conn.execute(
            """
            UPDATE entity AS target
            SET qid = %s
            WHERE target.entity_id = %s
              AND target.qid IS NULL
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
            json.dumps(instance_qids, ensure_ascii=False),
            category,
            entity_id,
        ),
    )


def update_relationship_evidence(
    conn,
    row,
    qid,
    instance_qids,
    classification_qids,
    instance_labels,
    category,
    next_hop,
):
    """관계별 근거 JSON에 타입화 결과를 추가한다."""
    evidence = row["evidence"] or {}

    if not isinstance(evidence, dict):
        evidence = {"previous_evidence": evidence}

    evidence.update(
        {
            "wikidata_qid": qid,
            "wikidata_instance_of": instance_qids,
            "wikidata_type_classification_qids": classification_qids,
            "wikidata_instance_labels": instance_labels,
            "wikidata_category": category,
            "type_source": "wikidata_p31",
            "next_hop_eligible": next_hop,
            "type_score_version": "v3-wikidata-p31",
        }
    )

    conn.execute(
        """
        UPDATE relationship
        SET evidence = %s::jsonb,
            next_hop_eligible = %s
        WHERE relationship_id = %s
        """,
        (
            json.dumps(evidence, ensure_ascii=False),
            next_hop,
            row["relationship_id"],
        ),
    )


def main():
    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    ) as conn:
        ensure_columns(conn)
        rows = load_relations(conn)

        if not rows:
            print("타입화 대상 후보가 없습니다.")
            return

        # 05 단계의 점수 저장 상태 확인
        scored_count = sum(
            row["candidate_score"] is not None
            for row in rows
        )
        versioned_count = sum(
            row["score_version"] is not None
            for row in rows
        )
        timestamped_count = sum(
            row["scored_at"] is not None
            for row in rows
        )

        print("=== 05 단계 점수 상태 ===")
        print(f"타입화 대상 후보: {len(rows)}")
        print(f"점수 저장 후보: {scored_count}")
        print(f"점수 버전 저장 후보: {versioned_count}")
        print(f"점수 산정 시각 저장 후보: {timestamped_count}")



        # 기존 entity.qid가 있으면 우선 사용한다.
        titles_without_qid = [
            row["entity_name"]
            for row in rows
            if not row["qid"] and row["entity_name"]
        ]

        title_to_qid = resolve_titles_to_qids(
            titles_without_qid
        )

        row_qids = {}
        for row in rows:
            row_qids[row["relationship_id"]] = (
                row["qid"]
                or title_to_qid.get(row["entity_name"])
            )

        qids = [
            qid for qid in row_qids.values()
            if qid
        ]
        entities = fetch_wikidata_entities(qids)

        # P31에 연결된 타입 엔티티를 먼저 가져온다.
        instance_qids = []

        for qid in qids:
            entity = entities.get(qid, {})
            if not entity.get("missing"):
                instance_qids.extend(
                    claim_qids(entity, "P31")
                )

        instance_qids = list(dict.fromkeys(instance_qids))
        entities.update(
            fetch_wikidata_entities(instance_qids)
        )

        stats = defaultdict(int)

        for row in rows:
            relationship_id = row["relationship_id"]
            entity_id = row["entity_id"]
            qid = row_qids[relationship_id]

            wikidata_entity = entities.get(qid, {}) if qid else {}

            if (
                not qid
                or not wikidata_entity
                or wikidata_entity.get("missing")
            ):
                category = "unresolved"
                p31_qids = []
                classification_qids = []
                labels = []
                next_hop = False

                update_entity_type(
                    conn,
                    entity_id,
                    None,
                    p31_qids,
                    category,
                )

                update_relationship_evidence(
                    conn,
                    row,
                    None,
                    p31_qids,
                    classification_qids,
                    labels,
                    category,
                    next_hop,
                )

                stats["unmatched"] += 1
                stats[category] += 1
                continue

            p31_qids = claim_qids(
                wikidata_entity,
                "P31",
            )

            classification_qids = fetch_type_hierarchy(
                p31_qids,
                entities,
                max_depth=2,
            )

            labels = labels_for_qids(
                classification_qids,
                entities,
            )

            category = category_from_instance_of(
                classification_qids,
                labels,
            )

            evidence = row["evidence"] or {}
            if not isinstance(evidence, dict):
                evidence = {}

            next_hop = is_next_hop_eligible(
                row,
                category,
                evidence,
            )

            update_entity_type(
                conn,
                entity_id,
                qid,
                p31_qids,
                category,
            )

            update_relationship_evidence(
                conn,
                row,
                qid,
                p31_qids,
                classification_qids,
                labels,
                category,
                next_hop,
            )

            stats[category] += 1

        # psycopg 연결 컨텍스트가 정상 종료 시 트랜잭션을 커밋한다.

    print("=== Wikidata 후보 타입화 완료 ===")
    print(f"대상 관계: {len(rows)}")
    print(f"QID 미매칭: {stats['unmatched']}")

    for category in (
        "person",
        "event",
        "work",
        "place",
        "organization",
        "genre",
        "award",
        "unresolved",
    ):
        print(f"{category}: {stats[category]}")

    print(
        "다음 홉은 person·event·work 중에서도 "
        "언급 후보 및 비교 문맥을 제외한 관계만 허용합니다."
    )


if __name__ == "__main__":
    main()