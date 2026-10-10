import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

BATCH_SIZE = 10_000


COUNT_REMAINING_SQL = """
SELECT COUNT(*)
FROM raw_national_bibliography_book AS book
WHERE NOT EXISTS (
    SELECT 1
    FROM content_source_map AS source_map
    WHERE source_map.source = 'national_biblio'
      AND source_map.source_content_type = 'book'
      AND source_map.source_id = book.national_bibliography_id
);
"""

SELECT_BATCH_SQL = """
SELECT
    book.national_bibliography_id,
    book.title,
    book.description,
    book.keywords,
    book.author,
    book.publisher
FROM raw_national_bibliography_book AS book
WHERE NOT EXISTS (
    SELECT 1
    FROM content_source_map AS source_map
    WHERE source_map.source = 'national_biblio'
      AND source_map.source_content_type = 'book'
      AND source_map.source_id = book.national_bibliography_id
)
ORDER BY book.national_bibliography_id
LIMIT %(batch_size)s;
"""

NEXT_CONTENT_IDS_SQL = """
SELECT nextval(
    pg_get_serial_sequence(
        'content_catalogue',
        'content_id'
    )
)
FROM generate_series(1, %(count)s);
"""

INSERT_CONTENT_SQL = """
INSERT INTO content_catalogue (
    content_id,
    content_type,
    title,
    overview,
    keywords
)
VALUES (
    %(content_id)s,
    'book',
    %(title)s,
    %(overview)s,
    %(keywords)s
);
"""

INSERT_SOURCE_MAP_SQL = """
INSERT INTO content_source_map (
    content_id,
    source,
    source_content_type,
    source_id
)
VALUES (
    %(content_id)s,
    'national_biblio',
    'book',
    %(national_bibliography_id)s
);
"""


def unique_texts(values):
    result = []
    seen = set()

    for value in values:
        if not value:
            continue

        text = str(value).strip()

        if not text:
            continue

        if text.lower() in seen:
            continue

        seen.add(text.lower())
        result.append(text)

    return result


def make_keywords(
    existing_keywords,
    title,
    author,
    publisher,
):
    if not isinstance(existing_keywords, list):
        existing_keywords = []

    return unique_texts(
        [
            *existing_keywords,
            title,
            author,
            publisher,
        ]
    )


def add_books_to_catalogue():
    created_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(COUNT_REMAINING_SQL)
            remaining_count = cursor.fetchone()[0]

            print(
                f"새 content_id 생성 대상: "
                f"{remaining_count:,}개"
            )

            while True:
                cursor.execute(
                    SELECT_BATCH_SQL,
                    {"batch_size": BATCH_SIZE},
                )
                books = cursor.fetchall()

                if not books:
                    break

                cursor.execute(
                    NEXT_CONTENT_IDS_SQL,
                    {"count": len(books)},
                )
                content_ids = [
                    row[0]
                    for row in cursor.fetchall()
                ]

                content_rows = []
                source_map_rows = []

                for content_id, book in zip(
                    content_ids,
                    books,
                ):
                    (
                        national_bibliography_id,
                        title,
                        description,
                        keywords,
                        author,
                        publisher,
                    ) = book

                    content_rows.append(
                        {
                            "content_id": content_id,
                            "title": title,
                            "overview": (
                                description.strip()
                                if description
                                and description.strip()
                                else None
                            ),
                            "keywords": Jsonb(
                                make_keywords(
                                    keywords,
                                    title,
                                    author,
                                    publisher,
                                )
                            ),
                        }
                    )

                    source_map_rows.append(
                        {
                            "content_id": content_id,
                            "national_bibliography_id": (
                                national_bibliography_id
                            ),
                        }
                    )

                cursor.executemany(
                    INSERT_CONTENT_SQL,
                    content_rows,
                )

                cursor.executemany(
                    INSERT_SOURCE_MAP_SQL,
                    source_map_rows,
                )

                connection.commit()

                created_count += len(books)

                print(
                    f"책 카탈로그 적재: "
                    f"{created_count:,}개"
                )

    print()
    print(
        f"책 카탈로그 추가 완료: "
        f"{created_count:,}개"
    )


if __name__ == "__main__":
    add_books_to_catalogue()