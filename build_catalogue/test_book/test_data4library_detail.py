import os
import csv
import time
import requests

from pathlib import Path
from dotenv import load_dotenv


# ============================================================
# 1. 기본 설정
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

API_KEY = os.environ["DATA4LIBRARY_API_KEY"]

URL = "https://data4library.kr/api/srchDtlList"

INPUT_CSV = BASE_DIR / "data4library_candidates.csv"
OUTPUT_CSV = BASE_DIR / "data4library_detail_test.csv"

TARGET_CONTENT = "기생충"
SAMPLE_SIZE = 20


# ============================================================
# 2. 기존 CSV에서 테스트 후보 선정
# ============================================================

with INPUT_CSV.open(
    "r",
    encoding="utf-8-sig",
    newline="",
) as f:
    rows = list(csv.DictReader(f))

# 기생충 후보만 선택
candidates = [
    row for row in rows
    if row["source_content"] == TARGET_CONTENT
]

# ISBN 중복 제거
unique_books = {}

for book in candidates:
    isbn = book["isbn13"].strip()

    if isbn and isbn not in unique_books:
        unique_books[isbn] = book

# YES24와 비교하기 쉽도록 앞에서부터 20권 선정
sample = list(unique_books.values())[:SAMPLE_SIZE]

print(f"기생충 전체 후보: {len(unique_books)}권")
print(f"상세정보 테스트: {len(sample)}권")


# ============================================================
# 3. 정보나루 상세정보 조회
# ============================================================

def get_book_detail(isbn):

    response = requests.get(
        URL,
        params={
            "authKey": API_KEY,
            "isbn13": isbn,
            "loaninfoYN": "N",
            "format": "json",
        },
        timeout=20,
    )

    response.raise_for_status()

    data = response.json().get("response") or {}

    details = data.get("detail") or []

    if not details:
        return None

    # detail은 목록, 실제 책 정보는 book 내부
    first = details[0]
    return first.get("book", first)


# ============================================================
# 4. 상세정보 수집 및 비교
# ============================================================

results = []

for index, book in enumerate(sample, start=1):

    isbn = book["isbn13"]

    try:
        detail = get_book_detail(isbn)

        if detail:
            description = detail.get("description") or ""

            result = {
                "title": detail.get("bookname") or book["title"],
                "isbn13": isbn,
                "matched_keywords": book.get(
                    "matched_keywords", ""
                ),
                "description": description,
                "description_length": len(description),
                "status": "found",
            }

        else:
            result = {
                "title": book["title"],
                "isbn13": isbn,
                "matched_keywords": book.get(
                    "matched_keywords", ""
                ),
                "description": "",
                "description_length": 0,
                "status": "not_found",
            }

        results.append(result)

        print(
            f"[{index}/{len(sample)}] "
            f"{result['title']} | "
            f"소개 {result['description_length']}자"
        )

    except Exception as e:
        print(f"[{index}/{len(sample)}] 오류: {isbn} - {e}")

        results.append({
            "title": book["title"],
            "isbn13": isbn,
            "matched_keywords": book.get(
                "matched_keywords", ""
            ),
            "description": "",
            "description_length": 0,
            "status": "error",
        })

    time.sleep(0.3)


# ============================================================
# 5. CSV 저장
# ============================================================

with OUTPUT_CSV.open(
    "w",
    newline="",
    encoding="utf-8-sig",
) as f:

    writer = csv.DictWriter(
        f,
        fieldnames=[
            "title",
            "isbn13",
            "matched_keywords",
            "description",
            "description_length",
            "status",
        ],
    )

    writer.writeheader()
    writer.writerows(results)


# ============================================================
# 6. 결과 요약
# ============================================================

found = sum(
    row["status"] == "found"
    for row in results
)

with_description = sum(
    row["description_length"] > 0
    for row in results
)

errors = sum(
    row["status"] == "error"
    for row in results
)

print("\n" + "=" * 60)
print("정보나루 상세정보 테스트 결과")
print("=" * 60)

print(f"테스트 대상: {len(results)}권")
print(f"상세정보 조회 성공: {found}권")
print(f"책 소개 확보: {with_description}권")
print(f"API 오류: {errors}건")

print(f"\nCSV 저장 완료: {OUTPUT_CSV}")