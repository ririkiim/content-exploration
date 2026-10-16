
import json
import os
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


# =========================================================
# 1. 경로 및 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]

SELECTION_PATH = (
    ROOT
    / "data"
    / "selections"
    / "selected_movie_drama_contexts.jsonl"
)

CACHE_DIR = ROOT / "data" / "enrichment" / "wikipedia"

WIKIPEDIA_API = "https://ko.wikipedia.org/w/api.php"

load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL이 .env에 설정되어 있지 않습니다.")

WIKIMEDIA_CONTACT = os.getenv(
    "WIKIMEDIA_CONTACT",
    "content-exploration-capstone",
)

USER_AGENT = (
    "content-exploration-capstone/1.0 "
    f"({WIKIMEDIA_CONTACT})"
)

# 프로토타입에서는 보수적으로 .... 기존값(100,1.0)
MAX_LINKS = 50 
REQUEST_DELAY_SECONDS = 5.0


# 동음이의어 또는 연도 제목 때문에 작품 문서를
# 명시해야 하는 경우에만 지정한다.
#
# 영화·드라마: TMDb ID
# 도서: YES24 상품 ID
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

    ("book", 119458386): "토지 (소설)",
    ("book", 2870072): "완득이 (소설)",
    ("book", 119697570): "아몬드 (소설)",
    ("book", 91901136): "살인자의 기억법 (소설)",
    # 도서 문서에 대한 예외가 필요한 경우 아래에 추가한다.
    # 예: ("book", YES24_상품_ID): "Wikipedia 문서 제목",
}


# =========================================================
# 2. 텍스트 및 데이터 식별 유틸리티
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


def get_source_info(item):
    """
    콘텐츠 유형에 따라 외부 출처와 ID를 반환한다.

    movie / drama:
        source_name = tmdb
        source_id = TMDb ID

    book:
        source_name = yes24
        source_id = YES24 상품 ID
    """
    content_type = clean_text(item.get("content_type"))
    source_ids = item.get("source_ids") or {}

    if content_type in {"movie", "drama"}:
        source_name = "tmdb"
        source_id = source_ids.get("tmdb")

    elif content_type == "book":
        source_name = "yes24"
        source_id = source_ids.get("yes24")

    else:
        return None, None

    if source_id is None or not clean_text(source_id):
        return None, None

    return source_name, str(source_id)


def get_page_override(item):
    """외부 ID의 자료형이 문자열 또는 정수여도 예외 설정을 찾는다."""
    source_name, source_id = get_source_info(item)

    if source_id is None:
        return None

    content_type = item.get("content_type")

    possible_ids = [source_id]

    if source_id.isdigit():
        possible_ids.append(int(source_id))

    for candidate_id in possible_ids:
        override = PAGE_OVERRIDES.get(
            (content_type, candidate_id)
        )

        if override:
            return override

    return None


def get_requested_title(item):
    """Wikipedia 검색에 사용할 제목을 결정한다."""
    override = get_page_override(item)

    if override:
        return override

    return clean_text(item.get("title"))


# =========================================================
# 3. 파일 캐시
# =========================================================

def cache_path(item):
    """
    기존 영화·드라마 캐시 파일명은 유지한다.

    예:
        movie_282631.json
        drama_64840.json
        book_333133.json
    """
    content_type = item["content_type"]
    source_name, source_id = get_source_info(item)

    if source_id is None:
        raise ValueError(
            f"외부 출처 ID가 없습니다: {item.get('title')}"
        )

    return CACHE_DIR / f"{content_type}_{source_id}.json"


def load_file_cache(item):
    path = cache_path(item)

    if not path.exists():
        return None

    try:
        with path.open(encoding="utf-8") as file:
            result = json.load(file)

        required_fields = {
            "match_status",
            "requested_title",
            "page_title",
            "summary",
            "links",
            "source_url",
        }

        if not isinstance(result, dict):
            return None

        if not required_fields.issubset(result):
            return None

        # 예외 제목이 바뀌었거나 조회 제목이 달라졌다면
        # 기존 캐시를 그대로 사용하지 않는다.
        if normalize(result["requested_title"]) != normalize(
            get_requested_title(item)
        ):
            return None

        # 이전 버전 캐시에도 식별 정보를 보완한다.
        source_name, source_id = get_source_info(item)

        result.setdefault("content_type", item["content_type"])
        result.setdefault("source_name", source_name)
        result.setdefault("source_id", source_id)

        return result

    except (OSError, json.JSONDecodeError):
        return None


def save_file_cache(item, result):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    path = cache_path(item)

    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# =========================================================
# 4. Wikipedia API 요청
# =========================================================


def request_json(params, max_retries=5):
    query = urlencode(params)

    request = Request(
        f"{WIKIPEDIA_API}?{query}",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    for attempt in range(max_retries + 1):
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )

        except HTTPError as error:
            # 요청 제한 또는 서버 일시 오류만 재시도
            if error.code not in (429, 503):
                raise

            # 최대 재시도 횟수를 초과하면 오류를 상위로 전달
            if attempt == max_retries:
                raise

            # 서버가 지정한 대기 시간이 있으면 우선 사용
            retry_after = error.headers.get("Retry-After")

            try:
                wait_seconds = float(retry_after)
            except (TypeError, ValueError):
                # 서버의 대기 시간이 없으면 지수형으로 대기
                wait_seconds = min(60, 5 * (2 ** attempt))

            print(
                f"[재시도] HTTP {error.code}: "
                f"{wait_seconds:.0f}초 대기 후 재요청 "
                f"({attempt + 1}/{max_retries})"
            )

            time.sleep(wait_seconds)


def page_url(page_title):
    encoded_title = quote(
        page_title.replace(" ", "_"),
        safe="()",
    )

    return f"https://ko.wikipedia.org/wiki/{encoded_title}"


# =========================================================
# 5. Wikipedia 문서 조회
# =========================================================

def fetch_wikipedia_page(item):
    requested_title = get_requested_title(item)

    if not requested_title:
        return None

    source_name, source_id = get_source_info(item)

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
            "content_type": item["content_type"],
            "source_name": source_name,
            "source_id": source_id,
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
        "content_type": item["content_type"],
        "source_name": source_name,
        "source_id": source_id,
        "requested_title": requested_title,
        "page_title": actual_title,
        "summary": clean_text(page.get("extract")),
        "links": links,
        "source_url": source_url,
    }


def make_unmatched_result(item):
    source_name, source_id = get_source_info(item)

    return {
        "match_status": "unmatched",
        "content_type": item["content_type"],
        "source_name": source_name,
        "source_id": source_id,
        "requested_title": get_requested_title(item),
        "page_title": None,
        "summary": None,
        "links": [],
        "source_url": None,
    }


# =========================================================
# 6. DB에서 영화·드라마 확인
# =========================================================

def get_content_id(conn, item):
    """
    JSONL에 content_id가 있으면 우선 사용한다.
    그렇지 않으면 기존 방식대로 TMDb 매핑을 조회한다.
    """
    if item.get("content_id") is not None:
        return item["content_id"]

    source_name, source_id = get_source_info(item)

    if source_name != "tmdb":
        return None

    content_type = item["content_type"]

    # JSONL에서는 drama, DB에서는 tv로 저장한다.
    db_content_type = (
        "tv" if content_type == "drama"
        else content_type
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
        (source_id, db_content_type),
    ).fetchone()

    return row["content_id"] if row else None


# =========================================================
# 7. DB에서 도서 목록 조회
# =========================================================

def load_book_items(conn):
    """
    content와 content_source_map에 매핑된 도서를 조회한다.

    raw_yes24_item은 원본 payload를 보유하지만,
    Wikipedia 조회에는 콘텐츠 제목과 YES24 ID만 사용한다.
    """
    rows = conn.execute(
        """
        SELECT
            c.content_id,
            c.title AS content_title,
            sm.source_id AS yes24_id,
            r.title AS raw_title
        FROM content AS c
        JOIN content_source_map AS sm
          ON sm.content_id = c.content_id
         AND sm.source_name = 'yes24'
        JOIN raw_yes24_item AS r
          ON r.yes24_item_id::text = sm.source_id
        WHERE c.content_type = 'book'
        ORDER BY c.content_id
        """
    ).fetchall()

    books = []

    for row in rows:
        title = clean_text(row["content_title"])
        if not title:
            title = clean_text(row["raw_title"])

        if not title:
            print(
                "[SKIP] 도서 제목 없음:",
                row["yes24_id"],
            )
            continue

        books.append(
            {
                "content_id": row["content_id"],
                "content_type": "book",
                "title": title,
                "source_ids": {
                    "yes24": str(row["yes24_id"]),
                },
            }
        )

    return books


# =========================================================
# 8. 처리 대상 구성
# =========================================================

def load_movie_drama_items():
    if not SELECTION_PATH.exists():
        raise FileNotFoundError(
            f"선택 JSONL 파일을 찾을 수 없습니다: {SELECTION_PATH}"
        )

    with SELECTION_PATH.open(encoding="utf-8") as file:
        items = [
            json.loads(line)
            for line in file
            if line.strip()
        ]

    return items


def combine_items(movie_drama_items, book_items):
    """
    영화·드라마 선택 목록과 DB의 도서 목록을 합친다.
    동일 외부 출처 ID는 한 번만 처리한다.
    """
    combined = []
    seen = set()

    for item in movie_drama_items + book_items:
        source_name, source_id = get_source_info(item)

        if source_name is None or source_id is None:
            print(
                "[SKIP] 외부 출처 ID 없음:",
                item.get("title"),
            )
            continue

        key = (
            item.get("content_type"),
            source_name,
            source_id,
        )

        if key in seen:
            continue

        seen.add(key)
        combined.append(item)

    return combined


# =========================================================
# 9. 메인 실행
# =========================================================

def main():
    movie_drama_items = load_movie_drama_items()

    stats = {
        "total_items": 0,
        "processed_contents": 0,
        "missing_contents": 0,
        "matched": 0,
        "ambiguous": 0,
        "unmatched": 0,
        "request_errors": 0,
        "cache_hits": 0,
        "link_count": 0,
        "movie_drama_items": len(movie_drama_items),
        "book_items": 0,
    }

    missing_contents = []

    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    ) as conn:

        # DB에 매핑된 도서를 추가한다.
        book_items = load_book_items(conn)
        stats["book_items"] = len(book_items)

        items = combine_items(
            movie_drama_items,
            book_items,
        )

        stats["total_items"] = len(items)

        for index, item in enumerate(items, start=1):
            title = item.get("title", "(제목 없음)")
            content_type = item.get("content_type")

            content_id = get_content_id(conn, item)

            if content_id is None:
                stats["missing_contents"] += 1
                missing_contents.append(
                    f"{content_type}: {title}"
                )

                print(
                    f"[{index}] DB 콘텐츠 매칭 실패: "
                    f"[{content_type}] {title}"
                )
                continue

            stats["processed_contents"] += 1

            # 기존 캐시가 있으면 API 요청을 생략한다.
            result = load_file_cache(item)

            if result is not None:
                stats["cache_hits"] += 1

                print(
                    f"[{index}] 캐시 재사용: "
                    f"[{content_type}] {title}"
                )

            else:
                try:
                    result = fetch_wikipedia_page(item)

                except Exception as error:
                    stats["request_errors"] += 1

                    print(
                        f"[{index}] 요청 실패: "
                        f"[{content_type}] {title} / {error}"
                    )

                    # 일시적인 네트워크 오류를
                    # unmatched 결과로 저장하지 않는다.
                    time.sleep(REQUEST_DELAY_SECONDS)
                    continue

                if result is None:
                    result = make_unmatched_result(item)

                save_file_cache(item, result)

                time.sleep(REQUEST_DELAY_SECONDS)

            # 매칭 상태별 집계
            match_status = result.get("match_status")

            if match_status == "ambiguous":
                stats["ambiguous"] += 1

                print(
                    f"[{index}] 동음이의어 확인 필요: "
                    f"[{content_type}] {title}"
                )
                continue

            if match_status != "matched":
                stats["unmatched"] += 1

                print(
                    f"[{index}] Wikipedia 매칭 실패: "
                    f"[{content_type}] {title}"
                )
                continue

            stats["matched"] += 1
            link_count = len(result.get("links", []))
            stats["link_count"] += link_count

            print(
                f"[{index}] 완료: [{content_type}] {title} "
                f"→ {result['page_title']} "
                f"(링크 후보 {link_count}개)"
            )

    print("\n=== Wikipedia 원시 후보 수집 완료 ===")
    print(f"영화·드라마 선택 목록: {stats['movie_drama_items']}")
    print(f"DB에서 조회한 도서: {stats['book_items']}")
    print(f"전체 처리 대상: {stats['total_items']}")
    print(f"DB 콘텐츠 매칭 성공: {stats['processed_contents']}")
    print(f"DB 콘텐츠 매칭 실패: {stats['missing_contents']}")
    print(f"Wikipedia 문서 매칭: {stats['matched']}")
    print(f"동음이의어: {stats['ambiguous']}")
    print(f"Wikipedia 매칭 실패: {stats['unmatched']}")
    print(f"요청 오류: {stats['request_errors']}")
    print(f"캐시 재사용: {stats['cache_hits']}")
    print(f"저장한 링크 후보 합계: {stats['link_count']}")

    if missing_contents:
        print("\nDB에서 찾지 못한 콘텐츠:")

        for title in missing_contents:
            print(f"- {title}")


if __name__ == "__main__":
    main()
