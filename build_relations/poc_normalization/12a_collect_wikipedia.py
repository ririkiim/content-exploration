import argparse
import hashlib
import json
import random
import re
import time
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


INPUT_DIR = Path("data/enrichment/normalization")
OUTPUT_DIR = Path("data/enrichment/wikipedia_cache")

API_URLS = {
    "ko": "https://ko.wikipedia.org/w/api.php",
    "en": "https://en.wikipedia.org/w/api.php",
}

USER_AGENT = (
    "ContentExplorationResearch/0.1 "
    "(academic capstone project)"
)


def normalize(value):
    value = unicodedata.normalize(
        "NFKC", str(value or "")
    ).casefold()

    return re.sub(r"\s+", " ", value).strip()


def compact(value):
    return re.sub(r"[\W_]+", "", normalize(value))


def stable_id(*parts):
    raw = "|".join(map(str, parts))

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:16]


def read_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_new_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    # 기존 파일 덮어쓰기 금지
    with path.open("x", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


class WikipediaClient:
    def __init__(self, delay=2.0, retries=5):
        self.delay = delay
        self.retries = retries
        self.last_request = 0.0

    def request(self, lang, params):
        url = API_URLS[lang] + "?" + urlencode({
            "format": "json",
            "formatversion": "2",
            **params,
        })

        for attempt in range(self.retries + 1):
            elapsed = time.monotonic() - self.last_request

            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)

            self.last_request = time.monotonic()

            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )

            try:
                with urlopen(req, timeout=30) as response:
                    return json.load(response)

            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504):
                    raise

                if attempt >= self.retries:
                    raise

                retry_after = exc.headers.get(
                    "Retry-After"
                )

                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = min(120, 5 * (2 ** attempt))

                wait = max(wait, 5.0)
                wait += random.uniform(0, 1)

                print(
                    f"    HTTP {exc.code}: "
                    f"{wait:.1f}초 후 재시도"
                )

                time.sleep(wait)

            except (URLError, TimeoutError):
                if attempt >= self.retries:
                    raise

                wait = min(60, 3 * (2 ** attempt))
                time.sleep(wait)

        raise RuntimeError("Wikipedia request failed")


def search_pages(client, lang, keyword, limit=5):
    data = client.request(lang, {
        "action": "query",
        "list": "search",
        "srsearch": keyword,
        "srnamespace": 0,
        "srlimit": limit,
    })

    return [
        {
            "page_id": row.get("pageid"),
            "title": row.get("title"),
        }
        for row in data.get("query", {}).get(
            "search", []
        )
    ]


def select_page(keyword, results):
    """
    제목이 일치하면 선택.
    그 외 검색 결과는 후보로만 보존한다.
    """
    exact = []

    for row in results:
        title = row["title"]

        if normalize(keyword) == normalize(title):
            exact.append(row)

        elif (
            len(compact(keyword)) >= 5
            and compact(keyword) == compact(title)
        ):
            exact.append(row)

    if len(exact) == 1:
        return {
            "status": "exact_title",
            "selected": exact[0],
        }

    if len(exact) > 1:
        return {
            "status": "ambiguous",
            "selected": None,
        }

    return {
        "status": "needs_review",
        "selected": None,
    }


def fetch_page(client, lang, page_id):
    data = client.request(lang, {
        "action": "query",
        "pageids": str(page_id),
        "prop": "extracts|info|revisions",
        "explaintext": 1,
        "inprop": "url",
        "rvprop": "ids",
        "redirects": 1,
    })

    pages = data.get("query", {}).get("pages", [])

    if not pages:
        return None

    page = pages[0]

    if page.get("missing"):
        return None

    revisions = page.get("revisions", [])

    return {
        "language": lang,
        "page_id": page.get("pageid"),
        "title": page.get("title"),
        "url": page.get("fullurl"),
        "revision_id": (
            revisions[0].get("revid")
            if revisions
            else None
        ),
        "text": page.get("extract", ""),
    }


def choose_languages(keyword):
    """
    한글 키워드: 한국어 우선
    영어 키워드: 영어 우선
    """
    has_korean = bool(
        re.search(r"[가-힣]", keyword)
    )

    return (
        ["ko", "en"]
        if has_korean
        else ["en", "ko"]
    )


def collect_topic(client, topic, search_limit):
    keyword = topic["target_topic_name"]
    topic_id = topic["target_topic_id"]

    result = {
        "topic_id": topic_id,
        "keyword": keyword,
        "candidate_origins": topic.get(
            "candidate_origins", []
        ),
        "qid": topic.get("qid"),
        "qid_candidates": topic.get(
            "qid_candidates", []
        ),
        "status": "pending",
        "search_results": [],
        "selected_page": None,
        "error": None,
    }

    for lang in choose_languages(keyword):
        matches = search_pages(
            client,
            lang,
            keyword,
            limit=search_limit,
        )

        result["search_results"].append({
            "language": lang,
            "results": matches,
        })

        selection = select_page(
            keyword,
            matches,
        )

        if selection["selected"] is None:
            continue

        page = fetch_page(
            client,
            lang,
            selection["selected"]["page_id"],
        )

        if page is None:
            continue

        result["selected_page"] = page
        result["status"] = "matched"

        return result

    result["status"] = "needs_review"

    return result


def collect(content_id, delay, retries, search_limit):
    input_path = (
        INPUT_DIR
        / f"topic_relations_{content_id}_v2.json"
    )

    data = read_json(input_path)

    topics = data.get(
        "content_topic_relations", []
    )

    topics = sorted(
        topics,
        key=lambda topic: (
            "source_keyword" not in topic.get(
                "candidate_origins", []
            ),
            topic.get("target_topic_name", ""),
        ),
    )

    content_dir = OUTPUT_DIR / str(content_id)
    content_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    client = WikipediaClient(
        delay=delay,
        retries=retries,
    )

    print(
        f"[{content_id}] "
        f"Wikipedia 수집 시작: {len(topics)}개"
    )

    for index, topic in enumerate(topics, 1):
        topic_id = topic["target_topic_id"]
        keyword = topic["target_topic_name"]

        filename = (
            f"topic_{stable_id(topic_id)}.json"
        )

        output_path = content_dir / filename

        if output_path.exists():
            print(
                f"[{index}/{len(topics)}] "
                f"{keyword}: 캐시 존재 → 건너뜀"
            )
            continue

        print(
            f"[{index}/{len(topics)}] {keyword}"
        )

        try:
            result = collect_topic(
                client,
                topic,
                search_limit=search_limit,
            )

        except Exception as exc:
            result = {
                "topic_id": topic_id,
                "keyword": keyword,
                "status": "error",
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }

        write_new_json(output_path, result)

        print(
            f"  상태: {result['status']}"
        )

        if result.get("error"):
            print(
                f"  오류: {result['error']}"
            )

    print("\n수집 완료")
    print(f"캐시 위치: {content_dir}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
    )

    parser.add_argument(
        "--retries",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--search-limit",
        type=int,
        default=5,
    )

    args = parser.parse_args()

    collect(
        content_id=args.content_id,
        delay=args.delay,
        retries=args.retries,
        search_limit=args.search_limit,
    )


if __name__ == "__main__":
    main()