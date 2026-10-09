import json
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv

# =========================================================
# 기본 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

INPUT_PATH = ROOT / "data" / "selections" / "selected_movie_drama_contexts.jsonl"
CACHE_DIR = ROOT / "data" / "enrichment" / "wikidata"

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

TMDB_MOVIE_ID = "P4947"
TMDB_TV_ID = "P4983"


# =========================================================
# Wikidata 작품 claim
# → 우리 DB 관계 타입 / 엔티티 타입
# =========================================================

CLAIM_RULES = {
    "P57": ("directed_by", "person"),        # 감독
    "P58": ("written_by", "person"),         # 각본가·드라마 작가
    "P170": ("created_by", "person"),        # 창작자
    "P161": ("acted_by", "person"),          # 출연자
    "P144": ("adapted_from", "source_work"), # 원작
    "P180": ("depicts", "concept"),          # 묘사 대상
    "P921": ("about_concept", "concept"),     # 주요 주제
}


# =========================================================
# 환경변수
# =========================================================

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


# =========================================================
# 공통 유틸
# =========================================================

def chunks(items, size=50):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def request_json(url, params):
    """
    Wikidata API / SPARQL 요청.
    """

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


# =========================================================
# content 조회
# =========================================================

def get_content_id(cur, content_type, tmdb_id):
    """
    JSONL의 content_type과 TMDb ID를 이용해
    현재 DB의 content_id를 찾는다.

    JSONL:
        movie → DB movie
        drama → DB tv
    """

    db_content_type = {
        "movie": "movie",
        "drama": "tv",
        "tv": "tv",
    }.get(content_type)

    if db_content_type is None:
        return None

    cur.execute(
        """
        SELECT c.content_id
        FROM content AS c
        JOIN content_source_map AS source_map
          ON source_map.content_id = c.content_id
        WHERE c.content_type = %s
          AND source_map.source_name = 'tmdb'
          AND source_map.source_id = %s
        LIMIT 1
        """,
        (
            db_content_type,
            str(tmdb_id),
        ),
    )

    row = cur.fetchone()

    return row[0] if row else None


# =========================================================
# 작품 QID 일괄 조회
# =========================================================

def lookup_all_wikidata_qids(items):
    """
    영화·드라마를 한 번의 SPARQL 요청으로
    Wikidata Q-ID와 연결한다.

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

        tmdb_id = int(
            binding["tmdb_id"]["value"]
        )

        qid = binding["item"]["value"].rsplit(
            "/",
            1,
        )[-1]

        result[(content_type, tmdb_id)] = qid

    return result


# =========================================================
# Wikidata Entity 일괄 조회
# =========================================================

def get_entities(qids):
    """
    Wikidata entity를 최대 50개씩 조회한다.
    """

    qids = list(dict.fromkeys(qids))

    result = {}

    for qid_chunk in chunks(qids):
        if not qid_chunk:
            continue

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

        result.update(
            data.get("entities", {})
        )

        time.sleep(1)

    return result


# =========================================================
# Wikidata label / description
# =========================================================

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


# =========================================================
# Claim에서 대상 QID 추출
# =========================================================

def extract_target_qids(entity, property_id):
    """
    Wikidata claim에서 관계 대상 Q-ID만 추출한다.
    """

    qids = []

    for claim in entity.get("claims", {}).get(
        property_id,
        [],
    ):
        mainsnak = claim.get(
            "mainsnak",
            {},
        )

        if mainsnak.get("snaktype") != "value":
            continue

        datavalue = mainsnak.get(
            "datavalue",
            {},
        )

        value = datavalue.get(
            "value",
            {},
        )

        if datavalue.get("type") == "wikibase-entityid":
            qid = value.get("id")

            if qid:
                qids.append(qid)

    return list(dict.fromkeys(qids))


# =========================================================
# Entity 조회 / 생성
# =========================================================

def get_or_create_entity(
    cur,
    entity_type,
    name,
    qid=None,
    description=None,
):
    """
    현재 entity 스키마에 맞춰 entity를 가져오거나 생성한다.

    우선순위:
    1. QID
    2. entity_type + entity_name
    3. 신규 생성
    """

    if not name:
        return None

    name = name.strip()

    # -----------------------------------------------------
    # 1. QID로 먼저 확인
    # -----------------------------------------------------

    if qid:
        cur.execute(
            """
            SELECT entity_id
            FROM entity
            WHERE qid = %s
            LIMIT 1
            """,
            (qid,),
        )

        row = cur.fetchone()

        if row:
            entity_id = row[0]

            if description:
                cur.execute(
                    """
                    UPDATE entity
                    SET entity_description =
                        COALESCE(
                            entity_description,
                            %s
                        )
                    WHERE entity_id = %s
                    """,
                    (
                        description,
                        entity_id,
                    ),
                )

            return entity_id

    # -----------------------------------------------------
    # 2. entity_type + entity_name으로 확인
    # -----------------------------------------------------

    cur.execute(
        """
        SELECT
            entity_id,
            qid
        FROM entity
        WHERE entity_type = %s
          AND entity_name = %s
        ORDER BY entity_id
        LIMIT 1
        """,
        (
            entity_type,
            name,
        ),
    )

    row = cur.fetchone()

    if row:
        entity_id = row[0]
        existing_qid = row[1]

        # 기존 entity에 QID가 없다면 연결
        if qid and existing_qid is None:
            cur.execute(
                """
                UPDATE entity
                SET qid = %s,
                    entity_description =
                        COALESCE(
                            entity_description,
                            %s
                        )
                WHERE entity_id = %s
                """,
                (
                    qid,
                    description,
                    entity_id,
                ),
            )

        elif description:
            cur.execute(
                """
                UPDATE entity
                SET entity_description =
                    COALESCE(
                        entity_description,
                        %s
                    )
                WHERE entity_id = %s
                """,
                (
                    description,
                    entity_id,
                ),
            )

        return entity_id

    # -----------------------------------------------------
    # 3. 신규 entity 생성
    # -----------------------------------------------------

    cur.execute(
        """
        INSERT INTO entity (
            entity_type,
            entity_name,
            qid,
            entity_description
        )
        VALUES (%s, %s, %s, %s)
        RETURNING entity_id
        """,
        (
            entity_type,
            name,
            qid,
            description,
        ),
    )

    return cur.fetchone()[0]


# =========================================================
# Relationship 적재
# =========================================================

def upsert_content_entity_relationship(
    cur,
    content_id,
    entity_id,
    relation_type,
    work_qid,
    property_id,
    target_qid,
    target_name,
):
    """
    현재 relationship 스키마에 맞춰
    content → entity 관계를 저장한다.
    """

    evidence = {
        "source": "wikidata",
        "source_url": (
            f"https://www.wikidata.org/wiki/{work_qid}"
        ),
        "work_qid": work_qid,
        "property": property_id,
        "target_qid": target_qid,
        "target_name": target_name,
    }

    cur.execute(
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
            next_hop_eligible
        )
        VALUES (
            'content',
            %s,
            %s,
            'entity',
            %s,
            'wikidata',
            'verified',
            %s::jsonb,
            'shown',
            TRUE
        )
        ON CONFLICT (
            source_type,
            source_id,
            relationship_type,
            target_type,
            target_id
        )
        DO UPDATE SET
            source_name = EXCLUDED.source_name,
            fact_status = 'verified',
            evidence = EXCLUDED.evidence,
            visibility = EXCLUDED.visibility,
            next_hop_eligible = TRUE
        """,
        (
            content_id,
            relation_type,
            entity_id,
            json.dumps(
                evidence,
                ensure_ascii=False,
            ),
        ),
    )


# =========================================================
# 작품 Wikidata 캐시 저장
# =========================================================

def save_work_cache(
    content_type,
    tmdb_id,
    work_qid,
    work_entity,
):
    """
    DB에 별도 enrichment 테이블을 만들지 않고
    파일 캐시만 저장한다.
    """

    cache_path = (
        CACHE_DIR
        / f"{content_type}_{tmdb_id}.json"
    )

    cache_data = {
        "content_type": content_type,
        "tmdb_id": tmdb_id,
        "wikidata_qid": work_qid,
        "entity": work_entity,
    }

    cache_path.write_text(
        json.dumps(
            cache_data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


# =========================================================
# 메인
# =========================================================

def main():
    CACHE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # -----------------------------------------------------
    # 입력 파일 확인
    # -----------------------------------------------------

    if not INPUT_PATH.exists():
        raise FileNotFoundError(
            f"입력 파일이 없습니다: {INPUT_PATH}"
        )

    items = [
        json.loads(line)
        for line in INPUT_PATH.read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]

    # -----------------------------------------------------
    # 1. 작품 QID 매칭
    # -----------------------------------------------------

    print("1/4 Wikidata 작품 Q-ID 매칭 중...")

    work_qids = lookup_all_wikidata_qids(
        items
    )

    # SPARQL → MediaWiki API 호출 간 간격
    time.sleep(2)

    # -----------------------------------------------------
    # 2. 작품 상세 claim 조회
    # -----------------------------------------------------

    print("2/4 작품 상세 claim 조회 중...")

    work_entities = get_entities(
        list(work_qids.values())
    )

    # -----------------------------------------------------
    # 3. 관계 대상 QID 수집
    # -----------------------------------------------------

    target_qids = set()

    for work_entity in work_entities.values():
        for property_id in CLAIM_RULES:
            target_qids.update(
                extract_target_qids(
                    work_entity,
                    property_id,
                )
            )

    print(
        f"  관계 대상 QID: {len(target_qids)}개"
    )

    # -----------------------------------------------------
    # 관계 대상 Entity 조회
    # -----------------------------------------------------

    print("3/4 관계 대상 엔티티 조회 중...")

    target_entities = get_entities(
        sorted(target_qids)
    )

    # -----------------------------------------------------
    # 통계
    # -----------------------------------------------------

    stats = {
        "matched_works": 0,
        "unmatched_works": 0,
        "processed_relations": 0,
        "created_or_reused_entities": 0,
    }

    print("4/4 관계·근거 저장 중...")

    # -----------------------------------------------------
    # DB 저장
    # -----------------------------------------------------

    with psycopg.connect(
        DATABASE_URL
    ) as conn:

        with conn.cursor() as cur:

            for item in items:

                content_type = item["content_type"]
                title = item["title"]
                tmdb_id = item["source_ids"]["tmdb"]

                # -----------------------------------------
                # 현재 DB의 content_id 확인
                # -----------------------------------------

                content_id = get_content_id(
                    cur,
                    content_type,
                    tmdb_id,
                )

                # -----------------------------------------
                # Wikidata 작품 QID 확인
                # -----------------------------------------

                work_qid = work_qids.get(
                    (
                        content_type,
                        tmdb_id,
                    )
                )

                if (
                    content_id is None
                    or work_qid is None
                ):
                    stats["unmatched_works"] += 1

                    print(
                        f"[미매칭] "
                        f"{content_type} | "
                        f"{title} | "
                        f"TMDb {tmdb_id}"
                    )

                    continue

                # -----------------------------------------
                # 작품 Entity 확인
                # -----------------------------------------

                work_entity = work_entities.get(
                    work_qid
                )

                if (
                    not work_entity
                    or work_entity.get("missing")
                ):
                    stats["unmatched_works"] += 1

                    print(
                        f"[Wikidata Entity 미확인] "
                        f"{content_type} | "
                        f"{title} | "
                        f"{work_qid}"
                    )

                    continue

                stats["matched_works"] += 1

                # -----------------------------------------
                # 작품 Wikidata 캐시 저장
                # -----------------------------------------

                save_work_cache(
                    content_type,
                    tmdb_id,
                    work_qid,
                    work_entity,
                )

                # -----------------------------------------
                # Claim별 관계 처리
                # -----------------------------------------

                for (
                    property_id,
                    rule,
                ) in CLAIM_RULES.items():

                    relation_type, entity_type = rule

                    target_qids_for_property = (
                        extract_target_qids(
                            work_entity,
                            property_id,
                        )
                    )

                    for target_qid in (
                        target_qids_for_property
                    ):

                        target = target_entities.get(
                            target_qid
                        )

                        if (
                            not target
                            or target.get("missing")
                        ):
                            continue

                        target_name = get_label(
                            target
                        )

                        target_description = (
                            get_description(
                                target
                            )
                        )

                        # ---------------------------------
                        # Entity 생성 / 재사용
                        # ---------------------------------

                        entity_id = (
                            get_or_create_entity(
                                cur=cur,
                                entity_type=entity_type,
                                name=target_name,
                                qid=target_qid,
                                description=target_description,
                            )
                        )

                        if entity_id is None:
                            continue

                        stats[
                            "created_or_reused_entities"
                        ] += 1

                        # ---------------------------------
                        # Relationship 저장
                        # ---------------------------------

                        upsert_content_entity_relationship(
                            cur=cur,
                            content_id=content_id,
                            entity_id=entity_id,
                            relation_type=relation_type,
                            work_qid=work_qid,
                            property_id=property_id,
                            target_qid=target_qid,
                            target_name=target_name,
                        )

                        stats[
                            "processed_relations"
                        ] += 1

            conn.commit()

    # =====================================================
    # 결과 출력
    # =====================================================

    print(
        "\n=== Wikidata 자동 관계 보강 완료 ==="
    )

    print(
        f"작품 매칭 성공: "
        f"{stats['matched_works']}개"
    )

    print(
        f"작품 매칭 실패: "
        f"{stats['unmatched_works']}개"
    )

    print(
        f"관계 처리: "
        f"{stats['processed_relations']}개"
    )

    print(
        f"entity 생성/재사용: "
        f"{stats['created_or_reused_entities']}개"
    )


if __name__ == "__main__":
    main()