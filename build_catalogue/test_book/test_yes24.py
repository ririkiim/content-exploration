import os
import time
import csv
import requests

from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv


# ============================================================
# 1. 환경변수 및 API 설정
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

URL = "https://apis.yes24.com/v1/goods/itemList"

HEADERS = {
    "X-Api-Key": os.environ["YES24_API_KEY"]
}


# ============================================================
# 2. 검색할 작품 및 확장 키워드
# ============================================================

TOPICS = {
    "변호인": [
        "민주화 운동",
        "국가폭력",
        "인권 탄압",
    ],
    "기생충": [
        "빈부격차",
        "계층 갈등",
        "경제적 불평등",
    ],
    "살인의 추억": [
        "연쇄살인",
        "미제 사건",
        "범죄 수사",
    ],
}

PAGE_SIZE = 20
MAX_PAGES = 3  # 검색어당 최대 60건


# ============================================================
# 3. YES24 도서 검색
# ============================================================

def search_books(keyword):
    books = []
    total_count = 0

    for page in range(1, MAX_PAGES + 1):

        response = requests.get(
            URL,
            headers=HEADERS,
            params={
                "query": keyword,
                "category": "BOOK",
                "page": page,
                "pageSize": PAGE_SIZE,
                "detail": "Y",
            },
            timeout=20,
        )

        # API 오류 처리
        if response.status_code != 200:
            print(
                f"[API 오류] 검색어={keyword}, "
                f"페이지={page}, "
                f"상태코드={response.status_code}"
            )
            print(response.text[:300])
            break

        data = response.json().get("data") or {}
        items = data.get("items") or []

        if page == 1:
            total_count = int(data.get("totalCount") or 0)

        books.extend(items)

        # 전체 결과를 모두 수집했으면 종료
        if len(books) >= total_count:
            break

        # 마지막 페이지라면 종료
        if len(items) < PAGE_SIZE:
            break

        time.sleep(0.2)

    return total_count, books


# ============================================================
# 4. ISBN 기준 도서 식별
# ============================================================

def get_book_key(book):

    isbn = str(book.get("isbn13") or "").strip()

    if isbn:
        return f"isbn:{isbn}"

    # ISBN이 없는 세트 상품은 임시 식별
    item_id = book.get("itemId")

    if item_id:
        return f"item:{item_id}"

    return f"title:{book.get('title', '')}"


# ============================================================
# 5. CSV 저장
# ============================================================

def save_parasite_csv(merged, keyword_matches):

    output_path = BASE_DIR / "yes24_parasite_candidates.csv"

    with output_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:

        writer = csv.writer(f)

        writer.writerow([
            "title",
            "isbn13",
            "matched_keywords",
            "book_introduction",
        ])

        for key, book in merged.items():

            detail = book.get("contentDetail") or {}

            writer.writerow([
                book.get("title", ""),
                book.get("isbn13", ""),
                "|".join(sorted(keyword_matches[key])),
                detail.get("bookIntroduction", ""),
            ])

    print(f"\nCSV 저장 완료: {output_path}")
    print(f"저장된 후보 수: {len(merged)}권")


# ============================================================
# 6. 작품별 검색 및 후보 통합
# ============================================================

for content_title, keywords in TOPICS.items():

    print(f"\n{'=' * 60}")
    print(f"출발 작품: {content_title}")
    print(f"{'=' * 60}")

    merged = {}
    keyword_matches = defaultdict(set)

    # 검색어별 API 호출
    for keyword in keywords:

        total_count, books = search_books(keyword)

        unique_keys = set()

        for book in books:

            key = get_book_key(book)

            unique_keys.add(key)

            # 어떤 검색어로 발견했는지 기록
            keyword_matches[key].add(keyword)

            # ISBN 기준 중복 제거
            if key not in merged:
                merged[key] = book

        print(
            f"{keyword}: "
            f"전체 {total_count}건 / "
            f"수집 {len(books)}건 / "
            f"고유 {len(unique_keys)}건"
        )

        time.sleep(0.3)

    # ========================================================
    # 7. OR 통합 결과
    # ========================================================

    print(f"\nOR 통합 후보: {len(merged)}권")

    match_counts = defaultdict(int)

    for key, matched_keywords in keyword_matches.items():
        match_counts[len(matched_keywords)] += 1

    print("검색어 1개에서 발견:", match_counts[1])
    print("검색어 2개에서 발견:", match_counts[2])
    print("검색어 3개에서 발견:", match_counts[3])

    # ========================================================
    # 8. 검색어 중복 도서 확인
    # ========================================================

    print("\n여러 검색어에서 발견된 도서:")

    overlapping = [
        (key, matched)
        for key, matched in keyword_matches.items()
        if len(matched) >= 2
    ]

    overlapping.sort(
        key=lambda x: len(x[1]),
        reverse=True,
    )

    if not overlapping:
        print("없음")

    for key, matched in overlapping[:15]:

        book = merged[key]

        print(
            f"- {book.get('title')} "
            f"| ISBN: {book.get('isbn13')} "
            f"| 검색어: {', '.join(sorted(matched))}"
        )

    # ========================================================
    # 9. 기생충 후보만 CSV 저장
    # ========================================================

    if content_title == "기생충":
        save_parasite_csv(merged, keyword_matches)


print("\n모든 YES24 검색 실험 완료!")