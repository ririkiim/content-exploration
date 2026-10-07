import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv
from common import require_normalized_name


ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data/selections/selected_movie_drama_contexts.jsonl"
CACHE_DIR = ROOT / "data/enrichment/wikidata"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

TMDB_MOVIE_ID = "P4947"
TMDB_TV_ID = "P4983"

# Wikidata 작품 claim → 우리 관계 타입 / 엔티티 타입
CLAIM_RULES = {
    "P57": ("directed_by", "person"),        # 감독
    "P58": ("written_by", "person"),         # 각본가·드라마 작가
    "P170": ("created_by", "person"),        # 창작자
    "P161": ("acted_by", "person"),          # 출연자
    "P144": ("adapted_from", "source_work"), # 원작
    "P180": ("depicts", "concept"),          # 묘사 대상
    "P921": ("about_concept", "concept"),    # 주요 주제
}

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]
WIKIMEDIA_CONTACT = os.getenv(
    "WIKIMEDIA_CONTACT",
    "mailto:unknown@example.com",
)

USER_AGENT = (
    "content-exploration-capstone/1.0 "
    f"({WIKIMEDIA_CONTACT})"
)


def chunks(items, size=50):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def request_json(url, params):
    request = Request(
        f"{url}?{urlencode(params)}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },

    )

    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)

    except HTTPError as error:
        if error.code == 429:
            raise RuntimeError(
                "Wikidata 서버 요청 제한(429)에 걸렸습니다. "
                "1~2분 기다린 뒤 다시 실행하세요."
            ) from error

        raise


def ensure_enrichment_table(cur):
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS content_external_enrichment (
            content_id BIGINT NOT NULL
                REFERENCES content_catalogue(content_id)
                ON DELETE CASCADE,

            source VARCHAR(30) NOT NULL,
            external_id TEXT NOT NULL,
            source_url TEXT,
            match_status VARCHAR(20) NOT NULL,
            raw_json JSONB,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            PRIMARY KEY (content_id, source)
        )
        """
    )


def get_content_id(cur, content_type, tmdb_id):
    cur.execute(
        """
        SELECT catalogue.content_id
        FROM content_catalogue AS catalogue
        JOIN content_source_map AS source_map
          ON source_map.content_id = catalogue.content_id
        WHERE catalogue.content_type = %s
          AND source_map.source = 'tmdb'
          AND source_map.source_id = %s
        LIMIT 1
        """,
        (content_type, str(tmdb_id)),
    )

    row = cur.fetchone()
    return row[0] if row else None


def lookup_all_wikidata_qids(items):
    """
    영화·드라마를 한 번의 SPARQL 요청으로 Wikidata Q-ID와 연결한다.

    반환 예:
    {
      ('movie', 496243): 'Q123456',
      ('drama', 64840): 'Q987654'
    }
    """
    values = []

    for item in items:
        tmdb_id = item["source_ids"]["tmdb"]

        property_id = (
            TMDB_MOVIE_ID
            if item["content_type"] == "movie"
            else TMDB_TV_ID
        )

        values.append(
            f'("{tmdb_id}" wdt:{property_id} "{item["content_type"]}")'
        )

    sparql = f"""
    SELECT ?item ?tmdb_id ?content_type WHERE {{
      VALUES (?tmdb_id ?tmdb_property ?content_type) {{
        {" ".join(values)}
      }}

      ?item ?tmdb_property ?tmdb_id .
    }}
    """

    data = request_json(
        WIKIDATA_SPARQL,
        {
            "query": sparql,
            "format": "json",
        },
    )

    result = {}

    for binding in data["results"]["bindings"]:
        content_type = binding["content_type"]["value"]
        tmdb_id = int(binding["tmdb_id"]["value"])
        qid = binding["item"]["value"].rsplit("/", 1)[-1]

        result[(content_type, tmdb_id)] = qid

    return result


def get_entities(qids):
    """Wikidata 엔티티를 최대 50개씩 조회한다."""
    qids = list(dict.fromkeys(qids))
    result = {}

    for qid_chunk in chunks(qids):
        data = request_json(
            WIKIDATA_API,
            {
                "action": "wbgetentities",
                "ids": "|".join(qid_chunk),
                "props": "labels|descriptions|claims",
                "languages": "ko|en",
                "format": "json",
            },
        )

        result.update(data.get("entities", {}))
        time.sleep(1)

    return result


def get_label(entity):
    labels = entity.get("labels", {})

    return (
        labels.get("ko", {}).get("value")
        or labels.get("en", {}).get("value")
        or entity.get("id")
    )


def get_description(entity):
    descriptions = entity.get("descriptions", {})

    return (
        descriptions.get("ko", {}).get("value")
        or descriptions.get("en", {}).get("value")
    )


def extract_target_qids(entity, property_id):
    """Wikidata claim에서 관계 대상 Q-ID만 추출한다."""
    qids = []

    for claim in entity.get("claims", {}).get(property_id, []):
        mainsnak = claim.get("mainsnak", {})

        if mainsnak.get("snaktype") != "value":
            continue

        datavalue = mainsnak.get("datavalue", {})
        value = datavalue.get("value", {})

        if datavalue.get("type") == "wikibase-entityid":
            qid = value.get("id")

            if qid:
                qids.append(qid)

    return list(dict.fromkeys(qids))


def get_or_create_entity(cur, entity_type, name):
    normalized_name = require_normalized_name(name)

    cur.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND normalized_name = %s
        ORDER BY entity_id
        LIMIT 1
        """,
        (entity_type, normalized_name),
    )

    row = cur.fetchone()

    if row:
        return row[0]

    cur.execute(
        """
        INSERT INTO entity (
            entity_type,
            name,
            normalized_name
        )
        VALUES (%s, %s, %s)
        RETURNING entity_id
        """,
        (entity_type, name, normalized_name),
    )

    return cur.fetchone()[0]


def save_entity_wikidata_id(cur, entity_id, qid):
    cur.execute(
        """
        INSERT INTO entity_source_map (
            entity_id,
            source,
            source_id,
            source_url
        )
        VALUES (
            %s,
            'wikidata',
            %s,
            %s
        )
        ON CONFLICT (source, source_id)
        DO UPDATE SET
            entity_id = EXCLUDED.entity_id,
            source_url = EXCLUDED.source_url
        """,
        (
            entity_id,
            qid,
            f"https://www.wikidata.org/wiki/{qid}",
        ),
    )


def upsert_content_entity_relation(
    cur,
    content_id,
    entity_id,
    relation_type,
):
    cur.execute(
        """
        INSERT INTO content_entity_relation (
            content_id,
            entity_id,
            relation_type,
            fact_status,
            review_status
        )
        VALUES (%s, %s, %s, 'verified', 'auto_checked')
        ON CONFLICT (content_id, entity_id, relation_type)
        DO UPDATE SET
            fact_status = EXCLUDED.fact_status,
            updated_at = NOW()
        RETURNING content_entity_relation_id
        """,
        (content_id, entity_id, relation_type),
    )

    return cur.fetchone()[0]


def add_wikidata_evidence(
    cur,
    relation_id,
    work_qid,
    property_id,
    target_name,
):
    source_url = f"https://www.wikidata.org/wiki/{work_qid}"

    cur.execute(
        """
        INSERT INTO relation_evidence (
            content_entity_relation_id,
            source_name,
            source_url,
            excerpt,
            source_quality
        )
        SELECT
            %s,
            'wikidata',
            %s,
            %s,
            'curated_community'
        WHERE NOT EXISTS (
            SELECT 1
            FROM relation_evidence
            WHERE content_entity_relation_id = %s
              AND source_name = 'wikidata'
        )
        """,
        (
            relation_id,
            source_url,
            f"Wikidata {property_id}: {target_name}",
            relation_id,
        ),
    )


def save_work_cache(cur, content_id, qid, work_entity):
    cur.execute(
        """
        INSERT INTO content_external_enrichment (
            content_id,
            source,
            external_id,
            source_url,
            match_status,
            raw_json
        )
        VALUES (
            %s,
            'wikidata',
            %s,
            %s,
            'matched',
            %s::jsonb
        )
        ON CONFLICT (content_id, source)
        DO UPDATE SET
            external_id = EXCLUDED.external_id,
            source_url = EXCLUDED.source_url,
            match_status = EXCLUDED.match_status,
            raw_json = EXCLUDED.raw_json,
            fetched_at = NOW()
        """,
        (
            content_id,
            qid,
            f"https://www.wikidata.org/wiki/{qid}",
            json.dumps(work_entity, ensure_ascii=False),
        ),
    )


def main():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    items = [
        json.loads(line)
        for line in INPUT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    print("1/4 Wikidata 작품 Q-ID 매칭 중...")
    work_qids = lookup_all_wikidata_qids(items)

    # SPARQL → MediaWiki API 호출 간 짧은 간격
    time.sleep(2)

    print("2/4 작품 상세 claim 조회 중...")
    work_entities = get_entities(list(work_qids.values()))

    target_qids = set()

    for work_entity in work_entities.values():
        for property_id in CLAIM_RULES:
            target_qids.update(
                extract_target_qids(work_entity, property_id)
            )

    print("3/4 관계 대상 엔티티 조회 중...")
    target_entities = get_entities(sorted(target_qids))

    stats = {
        "matched_works": 0,
        "unmatched_works": 0,
        "processed_relations": 0,
    }

    print("4/4 관계·근거 저장 중...")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            ensure_enrichment_table(cur)

            for item in items:
                content_type = item["content_type"]
                title = item["title"]
                tmdb_id = item["source_ids"]["tmdb"]

                content_id = get_content_id(cur, content_type, tmdb_id)

                work_qid = work_qids.get(
                    (content_type, tmdb_id)
                )

                if content_id is None or work_qid is None:
                    stats["unmatched_works"] += 1
                    print(
                        f"[미매칭] {content_type} | "
                        f"{title} | TMDb {tmdb_id}"
                    )
                    continue

                work_entity = work_entities.get(work_qid)

                if not work_entity or work_entity.get("missing"):
                    stats["unmatched_works"] += 1
                    continue

                save_work_cache(
                    cur,
                    content_id,
                    work_qid,
                    work_entity,
                )

                cache_path = CACHE_DIR / f"{content_type}_{tmdb_id}.json"

                cache_path.write_text(
                    json.dumps(
                        work_entity,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                stats["matched_works"] += 1

                for property_id, rule in CLAIM_RULES.items():
                    relation_type, entity_type = rule

                    for target_qid in extract_target_qids(
                        work_entity,
                        property_id,
                    ):
                        target = target_entities.get(target_qid)

                        if not target or target.get("missing"):
                            continue

                        target_name = get_label(target)
                        target_description = get_description(target)

                        entity_id = get_or_create_entity(
                            cur,
                            entity_type,
                            target_name,
                        )

                        if target_description:
                            cur.execute(
                                """
                                UPDATE entity
                                SET description = COALESCE(
                                    description,
                                    %s
                                ),
                                updated_at = NOW()
                                WHERE entity_id = %s
                                """,
                                (
                                    target_description,
                                    entity_id,
                                ),
                            )

                        save_entity_wikidata_id(
                            cur,
                            entity_id,
                            target_qid,
                        )

                        relation_id = upsert_content_entity_relation(
                            cur,
                            content_id,
                            entity_id,
                            relation_type,
                        )

                        add_wikidata_evidence(
                            cur,
                            relation_id,
                            work_qid,
                            property_id,
                            target_name,
                        )

                        stats["processed_relations"] += 1

        conn.commit()

    print("\n=== Wikidata 자동 관계 보강 완료 ===")
    print(f"작품 매칭 성공: {stats['matched_works']}개")
    print(f"작품 매칭 실패: {stats['unmatched_works']}개")
    print(f"관계 처리: {stats['processed_relations']}개")


if __name__ == "__main__":
    main()
