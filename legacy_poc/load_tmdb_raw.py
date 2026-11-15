# TMDB 원본 JSON 파일을 JSONB로 적재함
import json
import os
from paths import TMDB_RAW_DIR

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

# 테이블 생성 SQL
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_tmdb_content (
    raw_id BIGSERIAL PRIMARY KEY,

    media_type VARCHAR(10) NOT NULL
        CHECK (media_type IN ('movie', 'tv')),

    tmdb_id BIGINT NOT NULL
        CHECK (tmdb_id > 0),

    title TEXT NOT NULL,

    payload JSONB NOT NULL,

    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (media_type, tmdb_id)
);
"""


# 데이터 적재 / 갱신 SQL
UPSERT_SQL = """
INSERT INTO raw_tmdb_content (
    media_type,
    tmdb_id,
    title,
    payload
)
VALUES (
    %(media_type)s,
    %(tmdb_id)s,
    %(title)s,
    %(payload)s
)
ON CONFLICT (media_type, tmdb_id)
DO UPDATE SET
    title = EXCLUDED.title,
    payload = EXCLUDED.payload,
    fetched_at = NOW();
"""


# 파일 제목으로 영화 / 드라마 구분
def get_media_type(file_path):
    if file_path.name.startswith("movie_"):
        return "movie"

    if file_path.name.startswith("tv_"):
        return "tv"

    return None


# 영화는 title, 드라마는 name 필드에서 대표 제목 추출
def get_title(data, media_type):
    if media_type == "movie":
        return data.get("title")

    return data.get("name")


# JSON 파일을 PostgreSQL에 적재
def load_tmdb_raw():
    json_files = sorted(TMDB_RAW_DIR.glob("*.json"))

    if not json_files:
        raise FileNotFoundError(
            f"JSON 파일이 없습니다: {TMDB_RAW_DIR.resolve()}"
        )

    loaded_count = 0
    skipped_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)

            for file_path in json_files:
                media_type = get_media_type(file_path)

                if media_type is None:
                    print(f"건너뜀: 파일명 형식 오류 - {file_path.name}")
                    skipped_count += 1
                    continue

                with file_path.open(
                    "r",
                    encoding="utf-8",
                ) as file:
                    data = json.load(file)

                tmdb_id = data.get("id")
                title = get_title(data, media_type)

                if tmdb_id is None or not title:
                    print(f"건너뜀: 필수값 누락 - {file_path.name}")
                    skipped_count += 1
                    continue

                cursor.execute(
                    UPSERT_SQL,
                    {
                        "media_type": media_type,
                        "tmdb_id": tmdb_id,
                        "title": title,
                        "payload": Jsonb(data),
                    },
                )

                loaded_count += 1

        connection.commit()

    print()
    print(f"적재 또는 갱신: {loaded_count}개")
    print(f"건너뜀: {skipped_count}개")


if __name__ == "__main__":
    load_tmdb_raw()