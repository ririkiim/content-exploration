import os
import re
from pathlib import Path

import ijson
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]

BOOK_JSON_DIR = Path(
    "/Users/rim/Downloads/book_json_20260807"
)

# 한국어 도서만 우선 적재
KOREAN_ONLY = True


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_national_bibliography_book (
    national_bibliography_id TEXT PRIMARY KEY,

    title TEXT NOT NULL,
    author TEXT,
    publisher TEXT,
    published_year INTEGER,

    isbn TEXT,
    kdc TEXT,
    language TEXT,

    description TEXT,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,

    source_file TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS
idx_raw_national_bibliography_book_title
ON raw_national_bibliography_book (title);
"""

UPSERT_SQL = """
INSERT INTO raw_national_bibliography_book (
    national_bibliography_id,
    title,
    author,
    publisher,
    published_year,
    isbn,
    kdc,
    language,
    description,
    keywords,
    source_file,
    updated_at
)
VALUES (
    %(national_bibliography_id)s,
    %(title)s,
    %(author)s,
    %(publisher)s,
    %(published_year)s,
    %(isbn)s,
    %(kdc)s,
    %(language)s,
    %(description)s,
    %(keywords)s,
    %(source_file)s,
    NOW()
)
ON CONFLICT (national_bibliography_id)
DO UPDATE SET
    title = EXCLUDED.title,
    author = EXCLUDED.author,
    publisher = EXCLUDED.publisher,
    published_year = EXCLUDED.published_year,
    isbn = EXCLUDED.isbn,
    kdc = EXCLUDED.kdc,
    language = EXCLUDED.language,
    description = EXCLUDED.description,
    keywords = EXCLUDED.keywords,
    source_file = EXCLUDED.source_file,
    updated_at = NOW();
"""


def clean_text(value):
    if value is None:
        return None

    if isinstance(value, dict):
        value = (
            value.get("@value")
            or value.get("label")
            or value.get("@id")
        )

    if isinstance(value, list):
        values = [
            clean_text(item)
            for item in value
        ]
        values = [item for item in values if item]
        return ", ".join(values) or None

    value = str(value).strip()

    if value.lower() in {"", "null", "none"}:
        return None

    return value


def get_first_text(record, keys):
    for key in keys:
        value = clean_text(record.get(key))

        if value:
            return value

    return None


def get_keywords(record):
    value = record.get("keyword") or []

    if not isinstance(value, list):
        value = [value]

    keywords = []
    seen = set()

    for item in value:
        text = clean_text(item)

        if not text:
            continue

        if text.lower() in seen:
            continue

        seen.add(text.lower())
        keywords.append(text)

    return keywords


def parse_year(value):
    value = clean_text(value)

    if not value:
        return None

    match = re.search(r"\d{4}", value)

    return int(match.group()) if match else None


def is_book(record):
    types = record.get("@type", [])

    if not isinstance(types, list):
        types = [types]

    return any(
        str(item).endswith("/Book")
        for item in types
    )


def is_korean_book(language):
    return language and language.endswith("/kor")


def json_files():
    return sorted(
        BOOK_JSON_DIR.glob("book_*.json"),
        key=lambda path: int(
            path.stem.split("_")[1]
        ),
    )


def load_national_bibliography_books():
    files = json_files()

    if not files:
        raise FileNotFoundError(
            f"JSON 파일이 없습니다: {BOOK_JSON_DIR}"
        )

    total_count = 0
    loaded_count = 0
    skipped_not_book = 0
    skipped_not_korean = 0
    skipped_no_title = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            cursor.execute(CREATE_INDEX_SQL)

            for file_path in files:
                print(f"처리 시작: {file_path.name}")

                batch = []

                with file_path.open(
                    "rb"
                ) as json_file:
                    records = ijson.items(
                        json_file,
                        "@graph.item",
                    )

                    for record in records:
                        total_count += 1

                        if not is_book(record):
                            skipped_not_book += 1
                            continue

                        language = get_first_text(
                            record,
                            [
                                "http://id.loc.gov/ontologies/"
                                "bibframe/language",
                                "language",
                            ],
                        )

                        if (
                            KOREAN_ONLY
                            and not is_korean_book(language)
                        ):
                            skipped_not_korean += 1
                            continue

                        source_id = clean_text(
                            record.get("@id")
                        )
                        title = get_first_text(
                            record,
                            ["title", "label"],
                        )

                        if not source_id or not title:
                            skipped_no_title += 1
                            continue

                        batch.append(
                            {
                                "national_bibliography_id": source_id,
                                "title": title,
                                "author": get_first_text(
                                    record,
                                    [
                                        "http://purl.org/dc/"
                                        "elements/1.1/creator",
                                        "creator",
                                    ],
                                ),
                                "publisher": get_first_text(
                                    record,
                                    ["publisher"],
                                ),
                                "published_year": parse_year(
                                    get_first_text(
                                        record,
                                        [
                                            "issuedYear",
                                            "datePublished",
                                        ],
                                    )
                                ),
                                "isbn": get_first_text(
                                    record,
                                    ["isbn"],
                                ),
                                "kdc": get_first_text(
                                    record,
                                    ["kdc"],
                                ),
                                "language": language,
                                "description": get_first_text(
                                    record,
                                    ["description"],
                                ),
                                "keywords": Jsonb(
                                    get_keywords(record)
                                ),
                                "source_file": file_path.name,
                            }
                        )

                        if len(batch) >= 1000:
                            cursor.executemany(
                                UPSERT_SQL,
                                batch,
                            )
                            connection.commit()

                            loaded_count += len(batch)
                            print(
                                f"적재 완료: "
                                f"{loaded_count:,}개"
                            )

                            batch = []

                if batch:
                    cursor.executemany(
                        UPSERT_SQL,
                        batch,
                    )
                    connection.commit()

                    loaded_count += len(batch)

                print(f"처리 완료: {file_path.name}")

    print()
    print(f"전체 레코드: {total_count:,}개")
    print(f"한국어 외 도서 제외: {skipped_not_korean:,}개")
    print(f"도서 유형 외 제외: {skipped_not_book:,}개")
    print(f"제목/식별자 누락 제외: {skipped_no_title:,}개")
    print(f"원본 책 적재 완료: {loaded_count:,}개")


if __name__ == "__main__":
    load_national_bibliography_books()