# 제목 검색으로 TMDB 후보와 ID 추출

import argparse
import json
import re

from paths import DATA_DIR
from build_catalogue.tmdb_client import tmdb_get


OUTPUT_DIR = DATA_DIR / "search"
OUTPUT_PATH = OUTPUT_DIR / "tmdb_candidates.json"

# 특수문자 무시하고 제목 일치 여부 비교
def normalize_title(value: str) -> str:
    return "".join(
        character.lower()
        for character in value
        if character.isalnum()
    )

def get_title(candidate: dict, media_type: str) -> str:
    if media_type == "movie":
        return candidate.get("title", "")

    return candidate.get("name", "")

# 개봉일 / 공개일 추출
def get_release_date(
    candidate: dict,
    media_type: str,
) -> str | None:
    if media_type == "movie":
        return candidate.get("release_date")

    return candidate.get("first_air_date")

# TMDB에서 제목을 검색하고 영화 / 드라마 후보 추출
def search_tmdb_by_title(query: str) -> list[dict]:
    result = tmdb_get(
        "/search/multi",
        {
            "query": query,
            "language": "ko-KR",
            "include_adult": "false",
        },
    )

    return [
        candidate
        for candidate in result.get("results", [])
        if candidate.get("media_type") in {"movie", "tv"}
    ]

# 후보를 title, TMDB ID, media_type, 날짜 형식으로 정리
def build_candidates(
    query: str,
    limit: int,
) -> list[dict]:
    candidates = []

    for result in search_tmdb_by_title(query)[:limit]:
        media_type = result["media_type"]
        title = get_title(result, media_type)

        is_exact_title = (
            normalize_title(query)
            == normalize_title(title)
        )

        candidates.append(
            {
                "query": query,
                "tmdb_id": result["id"],
                "media_type": media_type,
                "title": title,
                "release_date": get_release_date(
                    result,
                    media_type,
                ),
                "original_title": (
                    result.get("original_title")
                    or result.get("original_name")
                ),
                "title_match": (
                    "exact"
                    if is_exact_title
                    else "related"
                ),
                "selection_status": "pending",
            }
        )

    return candidates

# 제목 일치 / 유사 제목 후보로 구분
def print_candidates(candidates: list[dict]) -> None:
    for candidate in candidates:
        match_label = (
            "제목 일치"
            if candidate["title_match"] == "exact"
            else "유사 제목 후보"
        )

        print(
            f"[{match_label}] "
            f"{candidate['media_type']} / "
            f"TMDB ID: {candidate['tmdb_id']} / "
            f"{candidate['title']} / "
            f"{candidate['release_date'] or '날짜 없음'}"
        )

# 검색 후보를 JSON 파일로 저장
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--queries",
        nargs="+",
        required=True,
        help="제목 검색어 목록",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="검색어당 표시할 최대 후보 수",
    )
    args = parser.parse_args()

    all_candidates = []

    for query in args.queries:
        print()
        print(f"검색어: {query}")

        candidates = build_candidates(
            query=query,
            limit=args.limit,
        )

        if not candidates:
            print("영화·드라마 후보를 찾지 못했습니다.")
            continue

        print_candidates(candidates)
        all_candidates.extend(candidates)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(
            all_candidates,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print()
    print(f"후보 저장 완료: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()