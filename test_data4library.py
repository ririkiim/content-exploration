import os
import time
import csv
import requests

from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

API_KEY = os.environ["DATA4LIBRARY_API_KEY"]
URL = "https://data4library.kr/api/srchBooks"

TOPICS = {
    "변호인": ["민주화 운동", "국가폭력", "인권 탄압"],
    "기생충": ["빈부격차", "계층 갈등", "경제적 불평등"],
    "살인의 추억": ["연쇄살인", "미제 사건", "범죄 수사"],
}

PAGE_SIZE = 20
MAX_PAGES = 3


def search_books(keyword):
    collected = []
    total = 0

    for page in range(1, MAX_PAGES + 1):
        response = requests.get(
            URL,
            params={
                "authKey": API_KEY,
                "keyword": keyword,
                "pageNo": page,
                "pageSize": PAGE_SIZE,
                "format": "json",
            },
            timeout=20,
        )

        response.raise_for_status()
        payload = response.json()
        response_data = payload.get("response", {})

        if "error" in response_data:
            raise RuntimeError(
                f"{keyword}: {response_data['error']}"
            )

        if page == 1:
            total = int(response_data.get("numFound") or 0)

        docs = response_data.get("docs") or []

        for entry in docs:
            book = entry.get("doc", entry)

            collected.append({
                "title": book.get("bookname", ""),
                "isbn13": str(book.get("isbn13") or ""),
                "authors": book.get("authors", ""),
                "publisher": book.get("publisher", ""),
                "description": book.get("description", ""),
            })

        if not docs or len(collected) >= total:
            break

        if len(docs) < PAGE_SIZE:
            break

        time.sleep(0.2)

    return total, collected


all_rows = []

for content_title, keywords in TOPICS.items():
    print(f"\n{'=' * 55}")
    print(f"출발 작품: {content_title}")
    print("=" * 55)

    merged = {}
    matches = defaultdict(set)

    for keyword in keywords:
        try:
            total, books = search_books(keyword)
        except Exception as exc:
            print(f"{keyword}: 오류 - {exc}")
            continue

        for book in books:
            isbn = book["isbn13"].strip()
            title = book["title"].strip()

            key = (
                f"isbn:{isbn}"
                if isbn
                else f"title:{title}|{book['authors']}"
            )

            merged[key] = book
            matches[key].add(keyword)

        print(
            f"{keyword}: 전체 {total}건 / "
            f"수집 {len(books)}건"
        )

        time.sleep(0.3)

    print(f"\nOR 통합 후보: {len(merged)}권")

    for count in (1, 2, 3):
        n = sum(
            len(keywords_found) == count
            for keywords_found in matches.values()
        )
        print(f"검색어 {count}개에서 발견: {n}")

    print("\n후보 예시:")

    for key, book in list(merged.items())[:10]:
        print(
            f"- {book['title']} "
            f"| ISBN: {book['isbn13']} "
            f"| 검색어: {', '.join(sorted(matches[key]))}"
        )

    for key, book in merged.items():
        all_rows.append({
            "source_content": content_title,
            "title": book["title"],
            "isbn13": book["isbn13"],
            "authors": book["authors"],
            "publisher": book["publisher"],
            "matched_keywords": "|".join(sorted(matches[key])),
            "description": book["description"],
        })


output_path = Path(__file__).resolve().parent / "data4library_candidates.csv"

with output_path.open("w", newline="", encoding="utf-8-sig") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=[
            "source_content",
            "title",
            "isbn13",
            "authors",
            "publisher",
            "matched_keywords",
            "description",
        ],
    )

    writer.writeheader()
    writer.writerows(all_rows)

print(f"\nCSV 저장 완료: {output_path}")