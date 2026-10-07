import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv
from common import require_normalized_name


ROOT = Path(__file__).resolve().parents[1]

SELECTION_PATH = ROOT / "data" / "selections" / "selected_movie_drama_contexts.jsonl"
CACHE_DIR = ROOT / "data" / "enrichment" / "wikipedia"

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"

load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]

WIKIMEDIA_CONTACT = os.getenv(
    "WIKIMEDIA_CONTACT",
    "mailto:content-exploration@example.com",
)

USER_AGENT = (
    "content-exploration-capstone/1.0 "
    f"({WIKIMEDIA_CONTACT})"
)

MAX_LINKS = 100
REQUEST_DELAY_SECONDS = 1.0

# 동음이의어 또는 연도 제목 때문에 작품 문서를 명시해야 하는 경우만 적는다.
PAGE_OVERRIDES = {
    ("movie", 496243): "기생충 (영화)",
    ("movie", 282631): "명량 (영화)",
    ("movie", 437103): "1987 (2017년 영화)",

    ("drama", 70593): "킹덤 (2019년 드라마)",
    ("drama", 64840): "시그널 (드라마)",
    ("drama", 61678): "미생 (드라마)",
    ("drama", 110534): "D.P. (드라마)",
    ("drama", 126485): "무빙 (드라마)",
    ("drama", 214528): "정년이 (드라마)",
    ("drama", 99494): "악의 꽃 (드라마)",
}

def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def normalize(value):
    return require_normalized_name(clean_text(value))


def unique(values):
    seen = set()
    result = []

    for value in values:
        value = clean_text(value)
        key = normalize(value)

        if not value or not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def request_json(params):
    query = "&".join(
        f"{quote(str(key))}={quote(str(value))}"
        for key, value in params.items()
    )

    request = Request(
        f"{WIKIPEDIA_API}?{query}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def page_url(page_title):
    return (
        "https://ko.wikipedia.org/wiki/"
        + quote(page_title.replace(" ", "_"))
    )


def get_content_id(conn, item):
    row = conn.execute(
        """
        SELECT catalogue.content_id
        FROM content_catalogue AS catalogue
        JOIN content_source_map AS source_map
          ON source_map.content_id = catalogue.content_id
        WHERE source_map.source = 'tmdb'
          AND source_map.source_id = %s
          AND catalogue.content_type = %s
        ORDER BY catalogue.content_id
        LIMIT 1
        """,
        (
            str(item["source_ids"]["tmdb"]),
            item["content_type"],
        ),
    ).fetchone()

    return row["content_id"] if row else None


def ensure_external_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS content_external_enrichment (
            content_id BIGINT NOT NULL
                REFERENCES content_catalogue(content_id),
            source VARCHAR(30) NOT NULL,
            external_id TEXT,
            source_url TEXT,
            match_status VARCHAR(30) NOT NULL,
            raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (content_id, source)
        )
        """
    )


def get_or_create_entity(conn, entity_type, name):
    normalized_name = require_normalized_name(name)

    row = conn.execute(
        """
        SELECT entity_id
        FROM entity
        WHERE entity_type = %s
          AND normalized_name = %s
        ORDER BY entity_id
        LIMIT 1
        """,
        (entity_type, normalized_name),
    ).fetchone()

    if row:
        return row["entity_id"]

    row = conn.execute(
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
    ).fetchone()

    return row["entity_id"]


def add_wikipedia_link_relation(conn, content_id, link, source_url):
    entity_id = get_or_create_entity(conn, "concept", link)

    relation = conn.execute(
        """
        INSERT INTO content_entity_relation (
            content_id,
            entity_id,
            relation_type,
            fact_status,
            review_status
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (content_id, entity_id, relation_type)
        DO UPDATE SET
            fact_status = EXCLUDED.fact_status,
            review_status = EXCLUDED.review_status
        RETURNING content_entity_relation_id
        """,
        (
            content_id,
            entity_id,
            "wikipedia_link",
            "proposed",
            "not_reviewed",
        ),
    ).fetchone()

    relation_id = relation["content_entity_relation_id"]

    conn.execute(
        """
        DELETE FROM relation_evidence
        WHERE content_entity_relation_id = %s
        """,
        (relation_id,),
    )

    conn.execute(
        """
        INSERT INTO relation_evidence (
            content_entity_relation_id,
            source_name,
            source_url,
            excerpt,
            source_quality
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            relation_id,
            "wikipedia",
            source_url,
            f"한국어 위키백과 내부 링크 후보: {link}",
            "curated_community",
        ),
    )


def fetch_wikipedia_page(item):
    tmdb_id = item["source_ids"]["tmdb"]
    requested_title = PAGE_OVERRIDES.get(
        (item["content_type"], tmdb_id),
        item["title"],
    )

    data = request_json(
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "redirects": "1",
            "titles": requested_title,
            "prop": "extracts|links|pageprops",
            "exintro": "1",
            "explaintext": "1",
            "exchars": "500",
            "plnamespace": "0",
            "pllimit": str(MAX_LINKS),
            "maxlag": "5",
        }
    )

    pages = data.get("query", {}).get("pages", [])
    if not pages:
        return None

    page = pages[0]

    if page.get("missing"):
        return None

    if "disambiguation" in page.get("pageprops", {}):
        return {
            "match_status": "ambiguous",
            "requested_title": requested_title,
            "page_title": page.get("title"),
            "summary": None,
            "links": [],
            "source_url": page_url(page.get("title", requested_title)),
        }

    links = unique(
        link.get("title")
        for link in page.get("links", [])
        if link.get("title")
    )

    # 작품 자기 자신 링크 제거
    links = [
        link
        for link in links
        if normalize(link) != normalize(item["title"])
    ][:MAX_LINKS]

    return {
        "match_status": "matched",
        "requested_title": requested_title,
        "page_title": page.get("title"),
        "summary": clean_text(page.get("extract")),
        "links": links,
        "source_url": page_url(page.get("title", requested_title)),
    }


def save_external_cache(conn, content_id, result):
    # 위키 문서를 못 찾은 경우에도 캐시 행은 남긴다.
    # 다음 실행 때 같은 작품을 다시 불필요하게 조회하지 않기 위함.
    external_id = (
        result.get("page_title")
        or f"unmatched:{result.get('requested_title', content_id)}"
    )

    conn.execute(
        """
        INSERT INTO content_external_enrichment (
            content_id,
            source,
            external_id,
            source_url,
            match_status,
            raw_json,
            fetched_at
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, NOW())
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
            "wikipedia",
            external_id,
            result.get("source_url"),
            result["match_status"],
            json.dumps(result, ensure_ascii=False),
        ),
    )


def save_file_cache(item, result):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    path = (
        CACHE_DIR
        / f"{item['content_type']}_{item['source_ids']['tmdb']}.json"
    )

    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    with SELECTION_PATH.open(encoding="utf-8") as file:
        items = [json.loads(line) for line in file if line.strip()]

    matched = 0
    ambiguous = 0
    unmatched = 0
    link_count = 0

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        ensure_external_table(conn)

        for index, item in enumerate(items, start=1):
            content_id = get_content_id(conn, item)

            if not content_id:
                print(f"[{index}] content_id 없음: {item['title']}")
                continue

            try:
                result = fetch_wikipedia_page(item)
            except Exception as error:
                print(f"[{index}] 요청 실패: {item['title']} / {error}")
                continue

            if result is None:
                unmatched += 1
                result = {
                    "match_status": "unmatched",
                    "requested_title": item["title"],
                    "page_title": None,
                    "summary": None,
                    "links": [],
                    "source_url": None,
                }

                save_external_cache(conn, content_id, result)
                save_file_cache(item, result)
                print(f"[{index}] 매칭 실패: {item['title']}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            save_external_cache(conn, content_id, result)
            save_file_cache(item, result)

            if result["match_status"] == "ambiguous":
                ambiguous += 1
                print(f"[{index}] 동음이의어 확인 필요: {item['title']}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            matched += 1

            # 04는 넓은 원시 링크 캐시 단계다. 관계 테이블에는 쓰지 않는다.
            # 실제 후보 관계는 05의 정제 규칙을 통과한 뒤에만 생성한다.
            link_count += len(result["links"])

            print(
                f"[{index}] 완료: {item['title']} "
                f"→ {result['page_title']} "
                f"(링크 후보 {len(result['links'])}개)"
            )

            time.sleep(REQUEST_DELAY_SECONDS)

        conn.commit()

    print("\n--- 결과 ---")
    print(f"위키 문서 매칭: {matched}")
    print(f"동음이의어: {ambiguous}")
    print(f"매칭 실패: {unmatched}")
    print(f"저장한 링크 후보: {link_count}")


if __name__ == "__main__":
    main()
