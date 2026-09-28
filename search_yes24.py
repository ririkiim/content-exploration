# 제목 검색으로 YES24 책 후보와 ID 추출

import argparse
import json

from paths import DATA_DIR
from yes24_client import yes24_get


OUTPUT_DIR = DATA_DIR / "search"
OUTPUT_PATH = OUTPUT_DIR / "yes24_candidates.json"

# title 정규화 : 공백과 특수문자를 제거하고 소문자로 변환
def normalize_title(value: str) -> str:
    return "".join(
        character.lower()
        for character in str(value)
        if character.isalnum()
    )

# 검색어와 검색 결과 제목이 같은지 확인
def is_same_work_title(query: str, result_title: str) -> bool:
    return normalize_title(query) == normalize_title(result_title)


# YES24에서 제목을 검색하고 책 후보 추출
def search_yes24_by_title(query: str) -> list[dict]:
    result = yes24_get(
        params={
            "query": query,
            "page": 1,
            "pageSize": 10,
        },
    )

    return result["data"]["items"]


# 검색 결과에서 필요한 정보만 정리
def build_candidates(
    query: str,
    limit: int,
) -> list[dict]:
    candidates = []

    for result in search_yes24_by_title(query)[:limit]:
        candidates.append(
            {
                "query": query,
                "yes24_item_id": result["itemId"],
                "title": result["title"],
                "author": result["author"],
                "isbn13": result.get("isbn13"), #없을수도있음
                "publish_date": result.get("publishDate"),
                "title_match": (
                    "exact" 
                    if is_same_work_title(query, result["title"]) 
                    else "related"  #실제로 관계있단뜻은 XX
                ),
                "selection_status": "pending",
            }
        )

    return candidates


# 검색 후보 출력
def print_candidates(candidates: list[dict]) -> None:
    for candidate in candidates:
        match_label = (
            "제목 일치"
            if candidate["title_match"] == "exact"
            else "유사 제목 후보"
        )

        print(
            f"매칭여부: {match_label} / "
            f"YES24 ID: {candidate['yes24_item_id']} / "
            f"ISBN13: {candidate['isbn13'] or 'ISBN13 없음'} / "
            f"제목: {candidate['title']} / "
            f"저자: {candidate['author']} / "
            f"출판일: {candidate['publish_date'] or '날짜 없음'}"
            
        )


# 검색 후보를 JSON 파일로 저장
def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--queries",
        nargs="+",
        required=True,
        help="책 제목 검색어 목록",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="검색어당 표시할 최대 후보 수"
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
            print("책 후보를 찾지 못했습니다.")
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
    