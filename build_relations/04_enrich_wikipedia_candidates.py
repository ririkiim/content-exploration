
import json
import os
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv


# =========================================================
# 1. 경로 및 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

SELECTION_PATH = (
    ROOT / "data" / "selections"
    / "selected_movie_drama_contexts.jsonl"
)

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


# 동음이의어 또는 연도 제목 때문에 작품 문서를
# 명시해야 하는 경우에만 지정한다.
PAGE_OVERRIDES = {
    ("movie", 496243): "기생충 (영화)",
    ("movie", 282631): "명량 (영화)",
    ("movie", 437103): "1987 (2017년 영화)",

    ("drama", 70593): "킹덤 (2019년 드라마)",
    ("drama", 64840): "시그널 (드라마)",
    ("drama", 61678): "미생 (드라마)",
    ("drama", 110534): "D.P.",
    ("drama", 126485): "무빙 (드라마)",
    ("drama", 214528): "정년이 (드라마)",
    ("drama", 99494): "악의 꽃 (2020년 드라마)",
}


# =========================================================
# 2. 텍스트 및 파일 캐시
# =========================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value).strip()
    return value or None


def normalize(value):
    value = clean_text(value)

    if not value:
        return None

    return " ".join(value.split()).casefold()


def unique(values):
    seen = set()
    result = []

    for value in values:
        value = clean_text(value)

        if not value:
            continue

        key = normalize(value)

        if not key or key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


def cache_path(item):
    content_type = item["content_type"]
    tmdb_id = item["source_ids"]["tmdb"]

    return CACHE_DIR / f"{content_type}_{tmdb_id}.json"


def load_file_cache(item):
    path = cache_path(item)

    if not path.exists():
        return None

    try:
        with path.open(encoding="utf-8") as file:
            result = json.load(file)

        # 필요한 필드가 있는 캐시만 재사용한다.
        required_fields = {
            "match_status",
            "requested_title",
            "page_title",
            "summary",
            "links",
            "source_url",
        }

        if required_fields.issubset(result):
            return result

    except (OSError, json.JSONDecodeError):
        pass

    return None


def save_file_cache(item, result):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    path = cache_path(item)

    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# =========================================================
# 3. Wikipedia API 요청
# =========================================================

def request_json(params):
    query = urlencode(params)

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
    encoded_title = quote(page_title.replace(" ", "_"), safe="()")

    return f"https://ko.wikipedia.org/wiki/{encoded_title}"


# =========================================================
# 4. DB에서 작품 확인
# =========================================================

def get_content_id(conn, item):
    tmdb_id = item.get("source_ids", {}).get("tmdb")

    if not tmdb_id:
        return None

    # JSONL에서는 drama, DB에서는 tv로 저장한다.
    content_type = (
        "tv" if item["content_type"] == "drama"
        else item["content_type"]
    )

    row = conn.execute(
        """
        SELECT c.content_id
        FROM content AS c
        JOIN content_source_map AS sm
          ON sm.content_id = c.content_id
        WHERE sm.source_name = 'tmdb'
          AND sm.source_id = %s
          AND c.content_type = %s
        ORDER BY c.content_id
        LIMIT 1
        """,
        (str(tmdb_id), content_type),
    ).fetchone()

    return row["content_id"] if row else None


# =========================================================
# 5. 작품 Wikipedia 문서 조회
# =========================================================

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

    actual_title = page.get("title", requested_title)
    source_url = page_url(actual_title)

    # 동음이의어 문서는 후보로 사용하지 않는다.
    if "disambiguation" in page.get("pageprops", {}):
        return {
            "match_status": "ambiguous",
            "requested_title": requested_title,
            "page_title": actual_title,
            "summary": None,
            "links": [],
            "source_url": source_url,
        }

    links = unique(
        link.get("title")
        for link in page.get("links", [])
        if link.get("title")
    )

    # 작품 자신을 가리키는 링크는 제거한다.
    self_titles = {
        normalize(item.get("title")),
        normalize(actual_title),
        normalize(requested_title),
    }

    links = [
        link
        for link in links
        if normalize(link) not in self_titles
    ][:MAX_LINKS]

    return {
        "match_status": "matched",
        "requested_title": requested_title,
        "page_title": actual_title,
        "summary": clean_text(page.get("extract")),
        "links": links,
        "source_url": source_url,
    }


# =========================================================
# 6. 메인 실행
# =========================================================

def main():
    with SELECTION_PATH.open(encoding="utf-8") as file:
        items = [
            json.loads(line)
            for line in file
            if line.strip()
        ]

    matched = 0
    ambiguous = 0
    unmatched = 0
    link_count = 0
    cache_hits = 0
    missing_contents = []

    with psycopg.connect(
        DATABASE_URL,
        row_factory=psycopg.rows.dict_row,
    ) as conn:

        for index, item in enumerate(items, start=1):
            title = item.get("title", "(제목 없음)")

            content_id = get_content_id(conn, item)

            if content_id is None:
                missing_contents.append(title)
                print(f"[{index}] DB 작품 매칭 실패: {title}")
                continue

            # 기존 캐시가 있으면 API 요청을 생략한다.
            result = load_file_cache(item)

            if result is not None:
                cache_hits += 1
                print(f"[{index}] 캐시 재사용: {title}")

            else:
                try:
                    result = fetch_wikipedia_page(item)

                except Exception as error:
                    print(f"[{index}] 요청 실패: {title} / {error}")
                    continue

                if result is None:
                    result = {
                        "match_status": "unmatched",
                        "requested_title": PAGE_OVERRIDES.get(
                            (
                                item["content_type"],
                                item["source_ids"]["tmdb"],
                            ),
                            title,
                        ),
                        "page_title": None,
                        "summary": None,
                        "links": [],
                        "source_url": None,
                    }

                save_file_cache(item, result)
                time.sleep(REQUEST_DELAY_SECONDS)

            # 매칭 상태별 집계
            if result["match_status"] == "ambiguous":
                ambiguous += 1
                print(f"[{index}] 동음이의어 확인 필요: {title}")
                continue

            if result["match_status"] != "matched":
                unmatched += 1
                print(f"[{index}] 매칭 실패: {title}")
                continue

            matched += 1
            link_count += len(result.get("links", []))

            print(
                f"[{index}] 완료: {title} "
                f"→ {result['page_title']} "
                f"(링크 후보 {len(result.get('links', []))}개)"
            )

    print("\n=== Wikipedia 원시 후보 수집 완료 ===")
    print(f"처리 작품 수: {len(items)}")
    print(f"Wikipedia 문서 매칭: {matched}")
    print(f"동음이의어: {ambiguous}")
    print(f"매칭 실패: {unmatched}")
    print(f"DB 작품 매칭 실패: {len(missing_contents)}")
    print(f"캐시 재사용: {cache_hits}")
    print(f"저장한 링크 후보: {link_count}")

    if missing_contents:
        print("\nDB에서 찾지 못한 작품:")
        for title in missing_contents:
            print(f"- {title}")


if __name__ == "__main__":
    main()