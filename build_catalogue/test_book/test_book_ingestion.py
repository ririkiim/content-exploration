import argparse

from book_selector import select_representative
from book_repository import ingest_book, get_connection


TEST_BOOKS = [
    ("소년이 온다", "한강"),
    ("구운몽", "김만중"),
    ("토지", "박경리"),
]

ALL_BOOKS = [
    ("칼의 노래", "김훈"),
    ("소년이 온다", "한강"),
    ("난장이가 쏘아올린 작은 공", "조세희"),
    ("지구 끝의 온실", "김초엽"),
    ("토지", "박경리"),
    ("구운몽", "김만중"),
    ("살인자의 기억법", "김영하"),
    ("아몬드", "손원평"),
    ("완득이", "김려령"),
    ("82년생 김지영", "조남주"),
]


def print_db_status():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM content
                WHERE content_type = 'book'
                """
            )
            content_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM book_edition
                """
            )
            edition_count = cur.fetchone()[0]

            cur.execute(
                """
                SELECT COUNT(*)
                FROM book_edition
                WHERE is_representative = TRUE
                """
            )
            representative_count = cur.fetchone()[0]

    print("\n" + "=" * 60)
    print("PostgreSQL 적재 현황")
    print("=" * 60)
    print(f"도서 작품: {content_count}")
    print(f"도서 판본: {edition_count}")
    print(f"대표 판본: {representative_count}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--all",
        action="store_true",
        help="10개 작품 전체 테스트",
    )

    parser.add_argument(
        "--commit",
        action="store_true",
        help="실제 DB 적재 실행",
    )

    args = parser.parse_args()

    books = ALL_BOOKS if args.all else TEST_BOOKS

    print_db_status()

    print("\n" + "=" * 60)
    print("도서 적재 테스트 시작")
    print("=" * 60)

    if not args.commit:
        print("DRY RUN: 실제 DB에는 적재하지 않음")

    results = []

    for title, author in books:
        try:
            selected = select_representative(title, author)

            if not selected:
                print(f"[SKIP] {title}: 대표 판본 선정 실패")
                continue

            if args.commit:
                result = ingest_book(title, author, selected)
            else:
                result = {
                    "status": "dry_run",
                    "isbn13": selected["isbn13"],
                }

                print(
                    f"  [DRY RUN] {title} "
                    f"/ {selected['isbn13']}"
                )

            results.append({
                "title": title,
                **result,
            })

        except Exception as exc:
            print(f"[ERROR] {title}: {exc}")

    print("\n" + "=" * 60)
    print("테스트 결과")
    print("=" * 60)

    for result in results:
        print(
            f"{result['title']:<20} "
            f"{result['status']:<10} "
            f"{result['isbn13']}"
        )

    print_db_status()


if __name__ == "__main__":
    main()