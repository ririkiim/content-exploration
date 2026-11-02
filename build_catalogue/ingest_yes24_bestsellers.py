
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from build_catalogue.book_repository import (
    get_connection,
    ingest_book,
    normalize,
)
from build_catalogue.data4library_client import Data4LibraryClient


DATA_DIR = Path("data/raw/yes24/bestsellers")


def valid_isbn13(value):
    isbn = re.sub(r"[^0-9]", "", str(value or ""))

    if len(isbn) != 13:
        return False

    total = sum(
        int(digit) * (1 if i % 2 == 0 else 3)
        for i, digit in enumerate(isbn[:12])
    )
    return (10 - total % 10) % 10 == int(isbn[-1])


def check_existing(title, isbn):
    """읽기 전용 중복 검사."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT be.content_id, c.title
                FROM book_edition be
                JOIN content c ON c.content_id = be.content_id
                WHERE be.isbn13 = %s
                """,
                (isbn,),
            )
            existing = cur.fetchone()

            if existing:
                if normalize(existing[1]) == normalize(title):
                    return "reused"
                return "review"

            cur.execute(
                """
                SELECT content_id
                FROM content
                WHERE content_type = 'book'
                  AND normalized_title = %s
                LIMIT 1
                """,
                (normalize(title),),
            )

            if cur.fetchone():
                return "review"

    return "new"


def get_yes24_description(item):
    detail = item.get("contentDetail") or {}
    return str(detail.get("bookIntroduction") or "").strip()


def prepare_book(item, client):
    title = str(item.get("title") or "").strip()
    author = str(item.get("author") or "").strip()
    isbn = re.sub(r"[^0-9]", "", str(item.get("isbn13") or ""))

    if not title or not author or not valid_isbn13(isbn):
        return None, "invalid"

    if item.get("goodsType") not in (None, "", "도서"):
        return None, "excluded"

    # 기존 대표 판본 선정 규칙 중 명백한 제외 대상 적용
    excluded = [
        "문제집", "수험서", "필사", "큰글자",
        "큰글씨", "대활자", "전집", "세트",
    ]
    if any(word in title for word in excluded):
        return None, "excluded"

    status = check_existing(title, isbn)
    if status != "new":
        return None, status

    try:
        detail = client.get_book_detail(isbn) or {}
    except Exception as exc:
        print(f"  정보나루 오류: {exc}")
        detail = {}

    description = str(detail.get("description") or "").strip()
    description_source = "data4library"

    if len(re.sub(r"\s+", "", description)) < 50:
        description = get_yes24_description(item)
        description_source = "yes24"

    if len(re.sub(r"\s+", "", description)) < 50:
        return None, "no_description"

    selected = {
        "isbn13": isbn,
        "publisher": detail.get("publisher") or item.get("publisher"),
        "publish_date": item.get("publishDate"),
        "description": description,
        "description_source": description_source,
    }

    return {
        "title": title,
        "author": author,
        "selected": selected,
    }, "ready"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit은 1 이상이어야 합니다.")

    source = args.file
    if source is None:
        files = sorted(
            DATA_DIR.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
        )
        if not files:
            raise FileNotFoundError("베스트셀러 JSON 파일이 없습니다.")
        source = files[-1]

    data = json.loads(source.read_text(encoding="utf-8"))
    items = data.get("items") or []

    print("입력:", source)
    print("모드:", "실제 DB 적재" if args.apply else "DRY RUN")
    print("처리 권수:", min(args.limit, len(items)))

    client = Data4LibraryClient()
    counts = Counter()
    seen_isbns = set()

    for index, item in enumerate(items[:args.limit], 1):
        title = item.get("title") or "(제목 없음)"
        isbn = re.sub(
            r"[^0-9]", "", str(item.get("isbn13") or "")
        )

        print(f"\n[{index}] {title}")

        if isbn in seen_isbns:
            print("  SKIP: 입력 목록 내 ISBN 중복")
            counts["duplicate_input"] += 1
            continue

        seen_isbns.add(isbn)

        try:
            prepared, status = prepare_book(item, client)

            if status != "ready":
                print(f"  SKIP: {status}")
                counts[status] += 1
                continue

            selected = prepared["selected"]
            print("  ISBN:", selected["isbn13"])
            print("  설명 출처:", selected["description_source"])
            print("  설명 길이:", len(selected["description"]))

            if not args.apply:
                print("  READY: 실제 INSERT 없음")
                counts["ready"] += 1
                continue

            result = ingest_book(
                title=prepared["title"],
                author=prepared["author"],
                selected=selected,
            )

            counts[result["status"]] += 1

        except Exception as exc:
            print(f"  ERROR: {exc}")
            counts["error"] += 1

    print("\n========== 결과 ==========")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")

    if not args.apply:
        print("\nDRY RUN 완료: DB에 변경 사항 없음")


if __name__ == "__main__":
    main()
