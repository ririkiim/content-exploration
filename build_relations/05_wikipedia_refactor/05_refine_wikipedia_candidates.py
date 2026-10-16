"""04의 Wikipedia 매칭 결과를 원문으로 재검증하고 탐색 후보를 DB에 저장한다.

실행 전제:
- 이 파일과 wikipedia_candidate_config.py, wikipedia_candidate_parser.py,
  wikipedia_candidate_db.py, common.py, candidate_scoring.py를 같은 디렉터리에 둔다.
- 04의 책 캐시는 data/enrichment/wikipedia/book_<YES24 source_id>.json에 있어야 한다.
- 영화/드라마 선택 파일은 data/selections/selected_movie_drama_contexts.jsonl이다.
"""
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import psycopg
from dotenv import load_dotenv

from wikipedia_501_candidate_config import (
    PAGE_OVERRIDES, REFINED_CACHE_DIR, REQUEST_DELAY_SECONDS,
    USER_AGENT, WIKIPEDIA_API, WIKIPEDIA_CACHE_DIR, MAX_CANDIDATES_PER_CONTENT,
)
from wikipedia_502_candidate_parser import (
    extract_infobox_signals, extract_relevant_sections, extract_section_candidates,
    normalize, strip_wikitext,
)
from wikipedia_503_candidate_db import (
    clear_previous_refined_candidates, get_content_id, get_verified_entity_names,
    upsert_candidate_relation,
)

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
DATABASE_URL = os.environ["DATABASE_URL"]


def request_json(params):
    url = WIKIPEDIA_API + "?" + urlencode(params)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def page_url(page_title):
    return "https://ko.wikipedia.org/wiki/" + quote(str(page_title).replace(" ", "_"), safe="()_-")


def load_json(path):
    try:
        with path.open(encoding="utf-8") as file:
            value = json.load(file)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        print(f"[경고] JSON 캐시를 읽을 수 없음: {path}")
        return None


def normalize_item(raw, *, default_type=None, cache_path=None):
    """04/선택 파일의 서로 다른 형식을 05 내부 표준 형식으로 맞춘다."""
    content_type = raw.get("content_type") or default_type
    if content_type not in {"movie", "drama", "book"}:
        return None

    if content_type == "book":
        source_name = raw.get("source_name") or "yes24"
        source_id = raw.get("source_id")
        if source_id is None and cache_path:
            source_id = cache_path.stem.removeprefix("book_")
        title = raw.get("requested_title") or raw.get("title")
    else:
        source_name = raw["source_name"]
        source_ids = raw.get("source_ids") or {}
        source_id = raw.get("source_id") or source_ids.get("tmdb")
        title = raw.get("title") or raw.get("requested_title")

    if source_id is None or not title:
        return None

    return {
        "content_type": content_type,
        "source_name": str(source_name),
        "source_id": str(source_id),
        "title": str(title),
        "year": raw.get("year"),
        "page_title": raw.get("page_title"),
        "source_url": raw.get("source_url"),
        "match_status": raw.get("match_status"),
        "requested_title": raw.get("requested_title") or str(title),
    }


def load_matched_items():
    """
    04가 저장한 Wikipedia 매칭 캐시에서 영화·드라마·책을 공통으로 읽는다.

    처리 조건:
    - match_status == "matched"
    - 콘텐츠 유형은 movie, drama, book 중 하나
    - source_name과 source_id가 존재
    - page_title이 존재

    반환값은 기존 Wikipedia 후보 정제 파이프라인에서 사용하는
    공통 item 구조다.
    """
    items_by_source = {}

    if not WIKIPEDIA_CACHE_DIR.exists():
        raise FileNotFoundError(
            f"Wikipedia 매칭 캐시 디렉터리가 없습니다: "
            f"{WIKIPEDIA_CACHE_DIR}"
        )

    for cache_path in sorted(WIKIPEDIA_CACHE_DIR.glob("*.json")):
        try:
            with cache_path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[건너뜀] 캐시 파일을 읽을 수 없습니다: {cache_path} ({exc})")
            continue

        if not isinstance(raw, dict):
            print(f"[건너뜀] JSON 객체가 아닙니다: {cache_path}")
            continue

        # 매칭 완료된 항목만 후보 정제 대상으로 사용한다.
        if raw.get("match_status") != "matched":
            continue

        # 파일명은 movie_<id>.json, drama_<id>.json, book_<id>.json 형식이다.
        prefix = cache_path.stem.split("_", 1)[0].lower()

        if prefix not in {"movie", "drama", "book"}:
            continue

        item = normalize_item(
            raw,
            default_type=prefix,
            cache_path=cache_path,
        )

        if not item:
            print(f"[건너뜀] 항목 정규화 실패: {cache_path}")
            continue

        item["match_status"] = raw.get("match_status")
        item["page_title"] = raw.get("page_title")
        item["source_url"] = raw.get("source_url")
        item["summary"] = raw.get("summary", "")
        item["links"] = raw.get("links", [])

        # 매칭 캐시의 출처 ID와 콘텐츠 유형을 확인한다.
        if item.get("content_type") not in {"movie", "drama", "book"}:
            print(f"[건너뜀] 알 수 없는 콘텐츠 유형: {cache_path}")
            continue

        if not item.get("source_name") or not item.get("source_id"):
            print(f"[건너뜀] 출처 정보 누락: {cache_path}")
            continue

        if not item.get("page_title"):
            print(f"[건너뜀] Wikipedia 페이지 제목 누락: {cache_path}")
            continue

        # 동일 출처 ID가 여러 파일에서 발견되면 최초 항목만 사용한다.
        key = (
            item["source_name"],
            str(item["source_id"]),
        )

        if key in items_by_source:
            print(f"[중복 건너뜀] {key}: {cache_path}")
            continue

        items_by_source[key] = item

    items = list(items_by_source.values())

    print(
        "Wikipedia 매칭 완료 항목: "
        f"{len(items)}건 "
        f"(영화·드라마·책 공통 캐시 로더)"
    )

    return items



def fetch_wikitext(item):
    override_key = (
        item["content_type"],
        str(item["source_id"]),
    )
    override = PAGE_OVERRIDES.get(override_key)
    requested_title = override or item.get("page_title") or item["title"]

    data = request_json({
        "action": "parse",
        "format": "json",
        "formatversion": "2",
        "page": requested_title,
        "prop": "wikitext",
        "redirects": "1",
        "maxlag": "5",
    })

    parsed = data.get("parse")
    if not parsed:
        return None

    wikitext = parsed.get("wikitext")
    if isinstance(wikitext, dict):
        wikitext = wikitext.get("*")
    if not isinstance(wikitext, str) or not wikitext.strip():
        return None

    page_title = parsed.get("title", requested_title)
    return {
        "requested_title": requested_title,
        "page_title": page_title,
        "source_url": page_url(page_title),
        "wikitext": wikitext,
    }


def validate_matched_page(item, result):
    """콘텐츠 유형별 최소 단서를 확인한다. 연도 불일치는 경고로 남긴다."""
    text = strip_wikitext(result["wikitext"][:12_000])
    page_title = result.get("page_title", "")
    expected_year = str(item.get("year") or "")

    if item["content_type"] == "movie":
        type_ok = bool(re.search(r"영화|감독|개봉일|영화 작품", text[:6_000]))
    elif item["content_type"] == "drama":
        type_ok = bool(re.search(r"드라마|방송|방영|연출|텔레비전", text[:6_000]))
    
    else:
        # 책·소설 문서인지 확인할 수 있는 본문 단서
        strong_book_signals = re.search(
            r"소설|저자|작가|ISBN|출판|장편|문학 작품|문학작품|"
            r"고전 문학|책의 저자|지은이|박경리",
            text[:8_000],
        )

        # 제목 자체가 특정 문학 작품을 가리키는지 확인
        title_is_specific = bool(
            re.search(
                r"\((?:소설|책|문학|작품)\)$",
                page_title.strip(),
            )
        )

        type_ok = bool(strong_book_signals or title_is_specific)


    year_ok = not expected_year or expected_year in text
    if type_ok:
        warning = None if year_ok else f"연도 {expected_year} 문서 본문에서 확인 안 됨"
        return True, warning
    return False, f"{item['content_type']} 유형 단서 없음 (문서: {page_title})"


def save_cache(item, result, candidates, selected_sections, infobox_signals):
    REFINED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = REFINED_CACHE_DIR / f"{item['content_type']}_{item['source_id']}.json"
    payload = {
        "content_type": item["content_type"],
        "source_name": item["source_name"],
        "source_id": item["source_id"],
        "title": item["title"],
        "page_title": result["page_title"],
        "source_url": result["source_url"],
        "wikitext": result["wikitext"],
        "infobox_signals": infobox_signals,
        "candidates": candidates,
        "selected_sections": selected_sections,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main():
    items = load_matched_items()
    if not items:
        print("처리할 영화·드라마·도서 항목이 없습니다.")
        return

    print(f"처리 대상: {len(items)}개")
    total_candidates = 0
    failed_titles = []

    with psycopg.connect(DATABASE_URL, row_factory=psycopg.rows.dict_row) as conn:
        for index, item in enumerate(items, start=1):
            content_id = get_content_id(conn, item)
            if not content_id:
                failed_titles.append(item["title"])
                print(f"[{index}] content_id 없음: {item['title']} ({item['source_name']}:{item['source_id']})")
                continue

            try:
                result = fetch_wikitext(item)
            except Exception as error:
                failed_titles.append(item["title"])
                print(f"[{index}] 위키 요청 실패: {item['title']} / {error}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue

            if result is None:
                failed_titles.append(item["title"])
                print(f"[{index}] 위키 문서 없음: {item['title']}")
                time.sleep(REQUEST_DELAY_SECONDS)
                continue


            valid, validation_reason = validate_matched_page(item, result)

            if not valid:
                failed_titles.append(item["title"])
                print(f"[{index}] 위키 문서 채택 보류: {item['title']} → {result['page_title']} / {validation_reason}")
                
                # 검증 실패한 콘텐츠의 기존 Wikipedia 정제 후보만 숨긴다.
                content_id = get_content_id(conn, item)

                if content_id is not None:
                    clear_previous_refined_candidates(conn, content_id)
                else:
                    print(
                        f"[주의] DB 콘텐츠를 찾지 못해 기존 후보를 숨기지 못함: "
                        f"{item['title']}"
                    )
                # 검증 실패 결과를 빈 캐시로 저장한다.
                save_cache(item, result, [], [], {})
                time.sleep(REQUEST_DELAY_SECONDS)
                continue
            if validation_reason:
                print(f"[{index}] 연도 확인 경고: {item['title']} → {result['page_title']} / {validation_reason}")

            infobox_signals = extract_infobox_signals(result["wikitext"])
            candidates = extract_section_candidates(item, result["wikitext"], infobox_signals)
            verified_names = get_verified_entity_names(conn, content_id)
            candidates = [
                candidate for candidate in candidates
                if not (candidate["entity_type"] == "concept" and normalize(candidate["name"]) in verified_names)
            ][:MAX_CANDIDATES_PER_CONTENT]
            selected_sections = extract_relevant_sections(result["wikitext"], infobox_signals)

            # 이전 Wikipedia 제안 후보 숨김과 신규 후보 upsert는 동일한 DB 트랜잭션에서 처리한다.
            clear_previous_refined_candidates(conn, content_id)
            for candidate in candidates:
                upsert_candidate_relation(conn, content_id, candidate, result["source_url"])
            save_cache(item, result, candidates, selected_sections, infobox_signals)
            total_candidates += len(candidates)
            print(
                f"[{index}] 완료: {item['title']} → {result['page_title']} "
                f"(정제 후보 {len(candidates)}개 / 인포박스 신호 {len(infobox_signals)}개 / "
                f"선택 섹션 {len(selected_sections)}개)"
            )
            time.sleep(REQUEST_DELAY_SECONDS)

        conn.commit()

    print("\n--- 결과 ---")
    print(f"저장한 정제 후보: {total_candidates}")
    print(f"문서 없음/실패: {len(failed_titles)}")
    if failed_titles:
        print(", ".join(failed_titles))


if __name__ == "__main__":
    main()
