import os
import csv
import time
import requests

from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.environ["DATA4LIBRARY_API_KEY"]

DETAIL_URL = "https://data4library.kr/api/srchDtlList"

D4L_CSV = BASE_DIR / "data4library_candidates.csv"
YES24_CSV = BASE_DIR / "yes24_parasite_candidates.csv"

OUTPUT_CSV = BASE_DIR / "data4library_unique_test.csv"

TARGET_CONTENT = "기생충"
SAMPLE_SIZE = 20


# 1. CSV 읽기
def read_csv(path):
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        return list(csv.DictReader(f))


d4l_rows = read_csv(D4L_CSV)
yes24_rows = read_csv(YES24_CSV)


# 2. YES24 ISBN 집합
yes24_isbns = {
    row["isbn13"].strip()
    for row in yes24_rows
    if row.get("isbn13", "").strip()
}


# 3. 정보나루에서만 발견된 후보
unique_books = {}

for row in d4l_rows:

    if row["source_content"] != TARGET_CONTENT:
        continue

    isbn = row.get("isbn13", "").strip()

    if not isbn:
        continue

    if isbn in yes24_isbns:
        continue

    if isbn not in unique_books:
        unique_books[isbn] = row


sample = list(unique_books.values())[:SAMPLE_SIZE]

print("=" * 60)
print("정보나루 고유 후보 검증")
print("=" * 60)

print(f"정보나루 고유 후보: {len(unique_books)}권")
print(f"테스트 대상: {len(sample)}권")


# 4. ISBN으로 상세정보 조회
def get_book_detail(isbn):

    response = requests.get(
        DETAIL_URL,
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

    first = details[0]

    return first.get("book", first)


# 5. 상세정보 수집
results = []

for index, book in enumerate(sample, start=1):

    isbn = book["isbn13"].strip()

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

    except Exception as e:

        print(f"[API 오류] ISBN={isbn}: {e}")

        result = {
            "title": book["title"],
            "isbn13": isbn,
            "matched_keywords": book.get(
                "matched_keywords", ""
            ),
            "description": "",
            "description_length": 0,
            "status": "error",
        }

    results.append(result)

    print(
        f"[{index}/{len(sample)}] "
        f"{result['title']} | "
        f"소개 {result['description_length']}자"
    )

    time.sleep(0.3)


# 6. CSV 저장
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


# 7. 결과 요약
found = sum(
    row["status"] == "found"
    for row in results
)

with_description = sum(
    row["description_length"] > 0
    for row in results
)

print("\n" + "=" * 60)
print("검증 결과")
print("=" * 60)

print(f"테스트 대상: {len(results)}권")
print(f"상세정보 조회 성공: {found}권")
print(f"책 소개 확보: {with_description}권")
print(f"CSV 저장: {OUTPUT_CSV}")