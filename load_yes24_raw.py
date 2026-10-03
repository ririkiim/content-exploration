# YES24 책 원본 파일 적재

import json
import os

from paths import YES24_RAW_DIR

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


# 테이블 생성
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_yes24_item (
    raw_id BIGSERIAL PRIMARY KEY,

    yes24_item_id BIGINT NOT NULL
        CHECK (yes24_item_id > 0),

    title TEXT NOT NULL,

    payload JSONB NOT NULL,

    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (yes24_item_id)
);
"""


# 최신 원본 데이터 갱신
UPSERT_SQL = """
INSERT INTO raw_yes24_item (
    yes24_item_id,
    title,
    payload
)
VALUES (
    %(yes24_item_id)s,
    %(title)s,
    %(payload)s
)
ON CONFLICT (yes24_item_id)
DO UPDATE SET
    title = EXCLUDED.title,
    payload = EXCLUDED.payload,
    fetched_at = NOW();
"""


# YES24 원본 JSON을 PostgreSQL에 적재
def load_yes24_raw():
    json_files = sorted(
        YES24_RAW_DIR.glob("*.json")
    )

    if not json_files:
        raise FileNotFoundError(
            f"YES24 JSON 파일이 없습니다: "
            f"{YES24_RAW_DIR}"
        )

    loaded_count = 0
    skipped_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)

            for file_path in json_files:
                try:
                    with file_path.open(
                        "r",
                        encoding="utf-8",
                    ) as file:
                        data = json.load(file)

                    yes24_item_id = data.get("itemId")
                    title = data.get("title")

                    if (
                        yes24_item_id is None
                        or not title
                    ):
                        skipped_count += 1
                        print(
                            f"건너뜀: 필수 정보 없음 "
                            f"/ {file_path.name}"
                        )
                        continue

                    cursor.execute(
                        UPSERT_SQL,
                        {
                            "yes24_item_id": yes24_item_id,
                            "title": title,
                            "payload": Jsonb(data),
                        },
                    )

                    loaded_count += 1

                except (
                    json.JSONDecodeError,
                    TypeError,
                    ValueError,
                ) as error:
                    skipped_count += 1
                    print(
                        f"건너뜀: "
                        f"{file_path.name} / {error}"
                    )

        connection.commit()

    print()
    print(f"적재 또는 갱신: {loaded_count}개")
    print(f"건너뜀: {skipped_count}개")


if __name__ == "__main__":
    load_yes24_raw()