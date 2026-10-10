import os
import csv
import time
import html
import requests

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

API_KEY = (
    os.getenv("DATA4LIBRARY_API_KEY")
    or os.getenv("DATA4LIBRARY_AUTH_KEY")
)

if not API_KEY:
    raise RuntimeError(
        ".env에 DATA4LIBRARY_API_KEY를 설정해 주세요."
    )

BASE_URL = "https://data4library.kr/api/srchDtlList"

BOOKS = {
    "칼의 노래": [
        "9788954623360",
        "9788954617246",
    ],
    "소년이 온다": [
        "9788936434410",
        "9788936434120",
    ],
    "난장이가 쏘아올린 작은 공": [
        "9788995151228",
    ],
    "지구 끝의 온실": [
        "9791191824001",
    ],
    "토지": [
        "9791130642888",
        "9791130642871",
    ],
    "구운몽": [
        "9788928521524",
    ],
    "살인자의 기억법": [
        "9791197021688",
        "9788954622035",
    ],
    "아몬드": [
        "9791198363510",
        "9791198363503",
        "9788936438753",
    ],
    "완득이": [
        "9788936456085",
        "9788936433635",
    ],
    "82년생 김지영": [
        "9788937437878",
        "9788937439209",
        "9788937438660",
    ],
}


def fetch_book(isbn):
    response = requests.get(
        BASE_URL,
        params={
            "authKey": API_KEY,
            "isbn13": isbn,
            "format": "json",
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()
    details = data.get("response", {}).get("detail", [])

    if not details:
        return None

    return details[0].get("book")


def main():
    rows = []

    for title, isbns in BOOKS.items():
        print(f"\n{'=' * 60}")
        print(f"작품: {title}")
        print("=" * 60)

        for isbn in isbns:
            try:
                book = fetch_book(isbn)

                if not book:
                    print(f"\nISBN: {isbn}")
                    print("조회 결과 없음")

                    rows.append({
                        "title": title,
                        "isbn13": isbn,
                        "found": False,
                        "description_length": 0,
                        "description": "",
                    })
                    continue

                description = html.unescape(
                    book.get("description") or ""
                ).strip()

                print(f"\nISBN: {isbn}")
                print(f"조회 제목: {book.get('bookname')}")
                print(f"저자: {book.get('authors')}")
                print(f"출판사: {book.get('publisher')}")
                print(f"설명 길이: {len(description)}자")
                print(f"설명:\n{description or '(없음)'}")

                rows.append({
                    "title": title,
                    "isbn13": isbn,
                    "found": True,
                    "description_length": len(description),
                    "description": description,
                })

            except (requests.RequestException, ValueError) as error:
                print(f"\nISBN: {isbn}")
                print(f"API 오류: {error}")

                rows.append({
                    "title": title,
                    "isbn13": isbn,
                    "found": False,
                    "description_length": 0,
                    "description": "",
                })

            time.sleep(0.3)

    output_dir = Path("data/search")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / "data4library_description_test.csv"

    with output_path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "title",
                "isbn13",
                "found",
                "description_length",
                "description",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    # 작품별 설명 확보율
    print("\n\n===== 작품별 확보 현황 =====")

    available_titles = set()

    for row in rows:
        if row["description_length"] > 0:
            available_titles.add(row["title"])

    for title in BOOKS:
        status = "O" if title in available_titles else "X"
        print(f"{title}: {status}")

    print(
        f"\n설명 확보 작품: "
        f"{len(available_titles)}/{len(BOOKS)}권"
    )

    print(f"CSV 저장 완료: {output_path}")


if __name__ == "__main__":
    main()