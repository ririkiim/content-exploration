import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

CSV_PATH = Path("/Users/rim/Downloads/kmdb_csv.csv")

MOVIE_TYPES = {
    "극영화",
    "애니메이션",
    "다큐멘터리",
    "문화영화",
    "실험영화",
    "TV-영화",
    "온라인 영화",
}

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_kmdb_catalogue (
    kmdb_source_id TEXT PRIMARY KEY,

    registration_id TEXT NOT NULL,
    registration_no TEXT NOT NULL,

    title TEXT NOT NULL,
    english_title TEXT,
    original_title TEXT,

    content_kind TEXT,
    purpose TEXT,
    genre TEXT,

    production_country TEXT,
    production_year INTEGER,
    production_company TEXT,
    director TEXT,

    raw_payload JSONB NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_raw_kmdb_catalogue_match
ON raw_kmdb_catalogue (
    title,
    production_year,
    director
);
"""

UPSERT_SQL = """
INSERT INTO raw_kmdb_catalogue (
    kmdb_source_id,
    registration_id,
    registration_no,
    title,
    english_title,
    original_title,
    content_kind,
    purpose,
    genre,
    production_country,
    production_year,
    production_company,
    director,
    raw_payload,
    updated_at
)
VALUES (
    %(kmdb_source_id)s,
    %(registration_id)s,
    %(registration_no)s,
    %(title)s,
    %(english_title)s,
    %(original_title)s,
    %(content_kind)s,
    %(purpose)s,
    %(genre)s,
    %(production_country)s,
    %(production_year)s,
    %(production_company)s,
    %(director)s,
    %(raw_payload)s,
    NOW()
)
ON CONFLICT (kmdb_source_id)
DO UPDATE SET
    title = EXCLUDED.title,
    english_title = EXCLUDED.english_title,
    original_title = EXCLUDED.original_title,
    content_kind = EXCLUDED.content_kind,
    purpose = EXCLUDED.purpose,
    genre = EXCLUDED.genre,
    production_country = EXCLUDED.production_country,
    production_year = EXCLUDED.production_year,
    production_company = EXCLUDED.production_company,
    director = EXCLUDED.director,
    raw_payload = EXCLUDED.raw_payload,
    updated_at = NOW();
"""


def clean(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value or value.lower() == "null":
        return None

    return value


def parse_year(value):
    value = clean(value)

    if not value:
        return None

    try:
        return int(value[:4])
    except ValueError:
        return None


def make_record(headers, values):
    # CSV 앞 12개 위치는 확인된 안정 필드다.
    # 이후 값은 raw_payload에 원형 보관한다.
    registration_id = clean(values[0])
    registration_no = clean(values[1])
    title = clean(values[2])

    if not registration_id or not registration_no or not title:
        return None

    content_kind = clean(values[5])
    genre = clean(values[7])
    production_country = clean(values[8])

    if "대한민국" not in (production_country or ""):
        return None

    if "에로" in (genre or ""):
        return None

    if content_kind not in MOVIE_TYPES:
        return None

    return {
        "kmdb_source_id": (
            f"{registration_id}:{registration_no}"
        ),
        "registration_id": registration_id,
        "registration_no": registration_no,
        "title": title,
        "english_title": clean(values[3]),
        "original_title": clean(values[4]),
        "content_kind": content_kind,
        "purpose": clean(values[6]),
        "genre": genre,
        "production_country": production_country,
        "production_year": parse_year(values[9]),
        "production_company": clean(values[10]),
        "director": clean(values[11]),
        "raw_payload": Jsonb(
            {
                "headers": headers,
                "values": values,
            }
        ),
    }


def load_kmdb_catalogue():
    if not CSV_PATH.exists():
        raise FileNotFoundError(
            f"CSV 파일을 찾을 수 없습니다: {CSV_PATH}"
        )

    total_count = 0
    skipped_country_count = 0
    skipped_erotic_count = 0
    skipped_type_count = 0
    invalid_count = 0
    loaded_count = 0

    with CSV_PATH.open(
        encoding="cp949",
        newline="",
    ) as csv_file:
        reader = csv.reader(csv_file)
        headers = next(reader)

        with psycopg.connect(DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(CREATE_TABLE_SQL)
                cursor.execute(CREATE_INDEX_SQL)

                batch = []

                for values in reader:
                    total_count += 1

                    if len(values) < 12:
                        invalid_count += 1
                        continue

                    country = clean(values[8])
                    genre = clean(values[7])
                    content_kind = clean(values[5])

                    if "대한민국" not in (country or ""):
                        skipped_country_count += 1
                        continue

                    if "에로" in (genre or ""):
                        skipped_erotic_count += 1
                        continue

                    if content_kind not in MOVIE_TYPES:
                        skipped_type_count += 1
                        continue

                    record = make_record(headers, values)

                    if record is None:
                        invalid_count += 1
                        continue

                    batch.append(record)

                    if len(batch) >= 1000:
                        cursor.executemany(
                            UPSERT_SQL,
                            batch,
                        )
                        connection.commit()
                        loaded_count += len(batch)
                        print(f"적재 완료: {loaded_count}개")
                        batch = []

                if batch:
                    cursor.executemany(
                        UPSERT_SQL,
                        batch,
                    )
                    connection.commit()
                    loaded_count += len(batch)

    print()
    print(f"CSV 전체 행: {total_count}개")
    print(f"대한민국 외 제작국 제외: {skipped_country_count}개")
    print(f"에로 장르 제외: {skipped_erotic_count}개")
    print(f"영화 외 유형 제외: {skipped_type_count}개")
    print(f"필수값 누락: {invalid_count}개")
    print(f"raw_kmdb_catalogue 적재 완료: {loaded_count}개")


if __name__ == "__main__":
    load_kmdb_catalogue()