"""위키 후보를 Wikidata QID/P31로 타입화한다.

05 단계의 후보 수집 결과를 대상으로 한다. 제목·인물명·주제명 목록을 코드에
넣지 않고, 한국어 위키의 ``wikibase_item``과 Wikidata의 P31(instance of)만
사용한다. 매칭되지 않은 후보는 ``unresolved``로 남기며 다음 홉 seed로 쓰지
않는다.

실행:
    uv run python build_relations/06_resolve_candidate_wikidata_types.py
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
from dotenv import load_dotenv

from common import normalize_name


ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "enrichment" / "candidate_wikidata"
WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
REQUEST_DELAY_SECONDS = 1.2
BATCH_SIZE = 40

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]
WIKIMEDIA_CONTACT = os.getenv(
    "WIKIMEDIA_CONTACT", "mailto:content-exploration@example.invalid"
)
USER_AGENT = f"content-exploration-capstone/1.0 ({WIKIMEDIA_CONTACT})"

REFINED_RELATION_TYPES = (
    "wikipedia_context_candidate",
    "wikipedia_original_candidate",
    "wikipedia_series_candidate",
    "wikipedia_related_work_candidate",
    "wikipedia_creator_candidate",
    "wikipedia_mention_candidate",
)

# Wikidata의 상위 개념 QID다. 작품별 정답 목록이 아니라 P31을 서비스의
# 6개 탐색 타입으로 번역하기 위한 일반 온톨로지다.
HUMAN_QID = "Q5"
WORK_INSTANCE_QIDS = {
    "Q11424",      # film
    "Q5398426",    # television series
    "Q571",        # book
    "Q7725634",    # literary work
    "Q47461344",   # written work
    "Q11060274",   # webtoon
    "Q7889",       # video game (다른 매체 확장을 막지 않기 위해 work로 보관)
}
EVENT_INSTANCE_QIDS = {
    "Q1190554",    # occurrence
    "Q1656682",    # event
    "Q178561",     # battle
    "Q198",        # war
    "Q40231",      # election (사건성 문서)
}
ORGANIZATION_INSTANCE_QIDS = {
    "Q43229",      # organization
    "Q4830453",    # business
    "Q2088357",    # publisher
}
PLACE_INSTANCE_QIDS = {
    "Q17334923",   # location
    "Q2221906",    # geographic location
    "Q515",        # city
    "Q6256",       # country
    "Q82794",      # geographic region
}


def chunks(items, size=BATCH_SIZE):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def request_json(base_url, params):
    request = Request(
        f"{base_url}?{urlencode(params)}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=45) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 429:
            raise RuntimeError(
                "Wikimedia 요청 제한(429)입니다. 1~2분 뒤 다시 실행하세요. "
                "이 스크립트는 완료된 QID 캐시를 재사용하므로 재실행해도 됩니다."
            ) from error
        raise


def ensure_columns(conn):
    for column_sql in (
        "ADD COLUMN IF NOT EXISTS wikidata_qid TEXT",
        "ADD COLUMN IF NOT EXISTS wikidata_instance_of JSONB",
        "ADD COLUMN IF NOT EXISTS wikidata_category TEXT",
        "ADD COLUMN IF NOT EXISTS wikidata_resolved_at TIMESTAMPTZ",
    ):
        conn.execute(f"ALTER TABLE content_entity_relation {column_sql}")

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_content_entity_relation_wikidata_qid
        ON content_entity_relation (wikidata_qid)
        """
    )


def load_relations(conn):
    return conn.execute(
        """
        SELECT
            relation.content_entity_relation_id,
            relation.relation_type,
            relation.source_score,
            relation.score_features,
            relation.exploration_branch,
            entity.name,
            entity.normalized_name
        FROM content_entity_relation AS relation
        JOIN entity ON entity.entity_id = relation.entity_id
        WHERE relation.fact_status = 'proposed'
          AND relation.candidate_is_stale = FALSE
          AND relation.relation_type = ANY(%s)
        ORDER BY relation.content_entity_relation_id
        """,
        # psycopg에서 tuple은 PostgreSQL record로 직렬화된다. ANY(%s)는
        # PostgreSQL text[]가 필요하므로 list로 넘긴다.
        (list(REFINED_RELATION_TYPES),),
    ).fetchall()


def cache_path(qid):
    return CACHE_DIR / f"{qid}.json"


def resolve_titles_to_qids(titles):
    """한국어 위키 문서 제목을 QID로 변환한다. 미매칭은 None으로 보존한다."""
    result = {title: None for title in titles}
    unique_titles = list(dict.fromkeys(titles))

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
        for item in query.get("normalized", []) + query.get("redirects", []):
            source, target = item.get("from"), item.get("to")
            if source and target:
                aliases[source] = target

        pages = {
            page.get("title"): page
            for page in query.get("pages", [])
            if not page.get("missing") and page.get("title")
        }
        for requested_title in title_batch:
            page = pages.get(aliases.get(requested_title, requested_title))
            if page:
                result[requested_title] = page.get("pageprops", {}).get("wikibase_item")
        time.sleep(REQUEST_DELAY_SECONDS)
    return result


def get_entity_cache(qid):
    path = cache_path(qid)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def fetch_wikidata_entities(qids):
    """P31만 받아 캐시한다. 캐시에 있는 QID는 외부 요청하지 않는다."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    result = {}
    missing_qids = []
    for qid in dict.fromkeys(qids):
        cached = get_entity_cache(qid)
        if cached:
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
                json.dumps(entity, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        time.sleep(REQUEST_DELAY_SECONDS)
    return result


def claim_qids(entity, property_id):
    qids = []
    for claim in entity.get("claims", {}).get(property_id, []):
        snak = claim.get("mainsnak", {})
        value = snak.get("datavalue", {}).get("value", {})
        if snak.get("snaktype") == "value" and value.get("id"):
            qids.append(value["id"])
    return list(dict.fromkeys(qids))


def labels_for_qids(instance_qids, entities):
    labels = []
    for qid in instance_qids:
        entity = entities.get(qid, {})
        labels.append(
            entity.get("labels", {}).get("ko", {}).get("value")
            or entity.get("labels", {}).get("en", {}).get("value")
            or qid
        )
    return labels


def category_from_instance_of(instance_qids, instance_labels):
    """P31을 보수적인 탐색 타입으로 변환한다. 알 수 없으면 unresolved다."""
    qid_set = set(instance_qids)
    text = " ".join(instance_labels).casefold()

    # P279는 '수상 → 행사', '장르 → 추상 개념'처럼 지나치게 넓은 상위
    # 개념까지 이어질 수 있다. 이들은 콘텐츠 탐색 seed가 아니므로 event보다
    # 먼저 차단한다. 작품별 이름이 아닌 Wikidata 타입 라벨 기반의 일반 규칙이다.
    if re.search(r"동음이의어|disambiguation", text):
        return "unresolved"
    if re.search(r"\b(award|prize)\b|수상|영화상|시상", text):
        return "award"
    if re.search(r"\b(genre|style|emotion)\b|장르|양식|감정", text):
        return "genre"
    if HUMAN_QID in qid_set:
        return "person"
    if qid_set & WORK_INSTANCE_QIDS:
        return "work"
    # 한성부처럼 city와 행정기관 계열 타입을 함께 가진 경우 장소가 우선이다.
    if qid_set & PLACE_INSTANCE_QIDS:
        return "place"
    if qid_set & ORGANIZATION_INSTANCE_QIDS:
        return "organization"
    if qid_set & EVENT_INSTANCE_QIDS:
        return "event"

    # P31의 사람이 아닌 하위 타입도 최대한 보수적으로만 분류한다.
    if re.search(r"\b(event|battle|war|revolution|uprising)\b|사건|전쟁|전투|항쟁|혁명|해전", text):
        return "event"
    if re.search(r"\b(film|television series|book|novel|webtoon|literary work)\b|영화|드라마|책|소설|웹툰|문학 작품", text):
        return "work"
    if re.search(r"\b(city|country|village|geographic|location)\b|도시|국가|지역|지리", text):
        return "place"
    if re.search(r"\b(organization|company|newspaper|broadcasting)\b|조직|기업|신문사|방송사", text):
        return "organization"
    return "unresolved"


def type_ancestors(instance_qids, entities, max_depth=2):
    """P31의 P279 상위 타입을 최대 두 단계까지만 따라간다.

    특정 전투·특정 드라마처럼 세부 타입만 P31에 적힌 경우에도 상위의
    event/work에 도달하도록 하되, 분류용 탐색이 끝없이 퍼지지는 않게 한다.
    """
    seen = set(instance_qids)
    frontier = set(instance_qids)
    for _ in range(max_depth):
        parents = set()
        for qid in frontier:
            parents.update(claim_qids(entities.get(qid, {}), "P279"))
        frontier = parents - seen
        if not frontier:
            break
        seen.update(frontier)
    return list(seen)


def score_update(row, category, instance_qids, instance_labels):
    features = row["score_features"] or {}
    source = row["source_score"] if row["source_score"] is not None else features.get("source_score", 0)
    cross_bonus = features.get("cross_bonus", 0)
    link_count = features.get("link_count", 0)
    type_bonus = {
        "event": 3, "person": 2, "work": 2,
        "place": -1, "organization": -4, "award": -4, "genre": 0,
        "unresolved": 0,
    }.get(category, 0)
    frequency_bonus = (
        2 if link_count >= 6 else 1 if link_count >= 3 else 0
    ) if category in {"event", "person", "work"} else 0
    final_score = max(0, source + type_bonus + cross_bonus + frequency_bonus)

    is_cast = row["exploration_branch"] == "cast"
    if row["relation_type"] == "wikipedia_creator_candidate":
        branch = "creator"
    elif is_cast and category == "person":
        branch = "cast"
    elif category == "person":
        # P31=human만으로는 역사 인물/현대 인물을 단정하지 않는다.
        # 다만 주제·장르와 섞지 않고 별도 인물 가지에서 제시한다.
        branch = "person_context"
    elif category == "work":
        branch = "source_or_related_work"
    elif category == "event":
        branch = "history_context"
    elif category in {"place", "organization"}:
        branch = "place_or_organization"
    elif category == "genre":
        branch = "genre_mood"
    elif category == "award":
        branch = "hidden"
    else:
        branch = "theme_context"

    next_hop = category in {"event", "person", "work"}
    features.update(
        {
            "entity_category": category,
            "type_source": "wikidata_p31",
            "wikidata_instance_of": instance_qids,
            "wikidata_instance_labels": instance_labels,
            "type_bonus": type_bonus,
            "frequency_bonus": frequency_bonus,
            "next_hop_eligible": next_hop,
            "score_version": "v3-wikidata-p31",
        }
    )
    return final_score, type_bonus + cross_bonus + frequency_bonus, features, branch, next_hop


def main():
    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        ensure_columns(conn)
        rows = load_relations(conn)
        titles = list(dict.fromkeys(row["name"] for row in rows))
        title_to_qid = resolve_titles_to_qids(titles)
        qids = [qid for qid in title_to_qid.values() if qid]
        entities = fetch_wikidata_entities(qids)

        # P31 라벨과 P279 상위 타입을 같은 캐시/배치 경로로 가져온다.
        instance_qids = []
        for entity in entities.values():
            instance_qids.extend(claim_qids(entity, "P31"))
        all_entities = {**entities, **fetch_wikidata_entities(instance_qids)}
        frontier = set(instance_qids)
        for _ in range(2):
            parent_qids = set()
            for qid in frontier:
                parent_qids.update(
                    claim_qids(all_entities.get(qid, {}), "P279")
                )
            frontier = parent_qids - set(all_entities)
            if not frontier:
                break
            all_entities.update(fetch_wikidata_entities(sorted(frontier)))

        stats = defaultdict(int)
        for row in rows:
            qid = title_to_qid.get(row["name"])
            if not qid or qid not in entities or entities[qid].get("missing"):
                features = row["score_features"] or {}
                features.update(
                    {
                        "entity_category": "unresolved",
                        "type_source": "wikidata_no_qid",
                        "next_hop_eligible": False,
                        "score_version": "v3-wikidata-p31",
                    }
                )
                conn.execute(
                    """
                    UPDATE content_entity_relation
                    SET wikidata_qid = NULL,
                        wikidata_instance_of = '[]'::jsonb,
                        wikidata_category = 'unresolved',
                        wikidata_resolved_at = NOW(),
                        score_features = %s::jsonb,
                        score_version = 'v3-wikidata-p31',
                        next_hop_eligible = FALSE,
                        updated_at = NOW()
                    WHERE content_entity_relation_id = %s
                    """,
                    (json.dumps(features, ensure_ascii=False), row["content_entity_relation_id"]),
                )
                stats["unmatched"] += 1
                continue
            instance_of = claim_qids(entities[qid], "P31")
            classification_qids = type_ancestors(instance_of, all_entities)
            labels = labels_for_qids(classification_qids, all_entities)
            category = category_from_instance_of(classification_qids, labels)
            score, rule_score, features, branch, next_hop = score_update(
                row, category, instance_of, labels
            )
            features["wikidata_type_classification_qids"] = classification_qids
            conn.execute(
                """
                UPDATE content_entity_relation
                SET wikidata_qid = %s,
                    wikidata_instance_of = %s::jsonb,
                    wikidata_category = %s,
                    wikidata_resolved_at = NOW(),
                    candidate_score = %s,
                    rule_score = %s,
                    score_features = %s::jsonb,
                    score_version = 'v3-wikidata-p31',
                    exploration_branch = %s,
                    next_hop_eligible = %s,
                    updated_at = NOW()
                WHERE content_entity_relation_id = %s
                """,
                (
                    qid, json.dumps(instance_of), category, score, rule_score,
                    json.dumps(features, ensure_ascii=False), branch, next_hop,
                    row["content_entity_relation_id"],
                ),
            )
            stats[category] += 1

        conn.commit()

    print("=== Wikidata 후보 타입화 완료 ===")
    print(f"대상 관계: {len(rows)}")
    print(f"QID 미매칭: {stats['unmatched']}")
    for category in ("person", "event", "work", "place", "organization", "genre", "award", "unresolved"):
        print(f"{category}: {stats[category]}")
    print("QID/P31이 확인된 person·event·work만 next_hop_eligible=TRUE입니다.")


if __name__ == "__main__":
    main()
