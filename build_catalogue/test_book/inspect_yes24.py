"""YES24 필드 확인 및 대표 판본의 상품 ID 연결."""

import argparse
import json
import os
import re

import psycopg
from psycopg.rows import dict_row

from build_catalogue.yes24_client import yes24_get


def normalize_isbn(value):
    return re.sub(r"[^0-9Xx]", "", str(value or "")).upper()


def load_book(content_id):
    with psycopg.connect(
        os.environ["DATABASE_URL"],
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.title,
                    be.book_edition_id,
                    be.isbn13,
                    be.yes24_product_id
                FROM content c
                JOIN book_edition be
                  ON be.content_id = c.content_id
                WHERE c.content_id = %s
                  AND c.content_type = 'book'
                  AND be.is_representative = TRUE
                """,
                (content_id,),
            )
            book = cur.fetchone()

    if book is None:
        raise ValueError("대표 판본이 있는 도서를 찾지 못했습니다.")

    if not normalize_isbn(book["isbn13"]):
        raise ValueError("대표 판본에 ISBN이 없습니다.")

    return book


def preview_fields(value, path=""):
    """필드 경로·길이·미리보기만 출력한다."""
    if isinstance(value, dict):
        if not value:
            print(f"{path}: 빈 객체")
        for key, child in value.items():
            preview_fields(child, f"{path}.{key}" if path else key)

    elif isinstance(value, list):
        print(f"{path}: 목록 {len(value)}개")
        # 목록은 첫 항목의 구조만 확인
        if value:
            preview_fields(value[0], f"{path}[0]")

    else:
        text = str(value) if value is not None else ""
        preview = " ".join(text.split())
        print(f"\n{path} | {len(text)}자")
        print(preview or "(비어 있음)")


def save_link(book, item_id):
    """소개·목차 없이 상품 ID만 기존 판본에 연결한다."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE book_edition
                SET yes24_product_id = %s,
                    updated_at = NOW()
                WHERE book_edition_id = %s
                  AND isbn13 = %s
                  AND is_representative = TRUE
                  AND (
                      yes24_product_id IS NULL
                      OR yes24_product_id = %s
                  )
                RETURNING book_edition_id
                """,
                (
                    str(item_id),
                    book["book_edition_id"],
                    book["isbn13"],
                    str(item_id),
                ),
            )

            if cur.fetchone() is None:
                raise ValueError(
                    "판본 정보가 변경됐거나 다른 YES24 ID가 연결돼 있습니다."
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content-id", type=int, required=True)
    parser.add_argument(
        "--save-link",
        action="store_true",
        help="ISBN이 일치하는 상품 ID만 DB에 저장",
    )
    args = parser.parse_args()

    book = load_book(args.content_id)
    print(f"대상: {book['title']} / ISBN {book['isbn13']}")

    result = yes24_get(params={
        "query": book["title"],
        "page": 1,
        "pageSize": 10,
    })

    items = result["data"]["items"]

    print("\n[검색 후보]")
    for item in items:
        print(json.dumps(
            {
                key: item.get(key)
                for key in ("itemId", "title", "author", "isbn13")
            },
            ensure_ascii=False,
        ))

    matches = [
        item
        for item in items
        if normalize_isbn(item.get("isbn13"))
        == normalize_isbn(book["isbn13"])
    ]

    if len(matches) != 1:
        raise ValueError(
            f"첫 페이지에서 ISBN 일치 상품이 {len(matches)}개입니다. "
            "자동 선택·저장하지 않습니다."
        )

    item = matches[0]

    print("\n[ISBN 일치 상품의 필드]")
    preview_fields(item)

    if args.save_link:
        save_link(book, item["itemId"])
        print(f"\nDB 연결 완료: YES24 ID {item['itemId']}")
    else:
        print("\n확인만 완료했습니다. 저장한 데이터는 없습니다.")


if __name__ == "__main__":
    main()