# KMDb 검색원본 파일 적재
import json
import os
from paths import KMDB_RAW_DIR

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

# 테이블 생성
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS raw_kmdb_search (
    raw_id BIGSERIAL PRIMARY KEY,

    requested_tmdb_id BIGINT NOT NULL
        CHECK (requested_tmdb_id > 0),

    query_title TEXT NOT NULL,

    query_year INTEGER,

    payload JSONB NOT NULL,

    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (requested_tmdb_id)
);
"""

# 최신 검색 결과 갱신
UPSERT_SQL = """
INSERT INTO raw_kmdb_search (
    requested_tmdb_id,
    query_title,
    query_year,
    payload
)
VALUES (
    %(requested_tmdb_id)s,
    %(query_title)s,
    %(query_year)s,
    %(payload)s
)
ON CONFLICT (requested_tmdb_id)
DO UPDATE SET
    query_title = EXCLUDED.query_title,
    query_year = EXCLUDED.query_year,
    payload = EXCLUDED.payload,
    fetched_at = NOW();
"""

# 파일을 하나씩 읽어서 검색에 사용한 TMDB ID / 제목 / 연도 추출
# KMDb 후보 원본 JSON 추출 후 적재
def load_kmdb_raw():
    json_files = sorted(
        KMDB_RAW_DIR.glob("tmdb_movie_*.json")
    )

    if not json_files:
        raise FileNotFoundError(
            f"KMDb JSON 파일이 없습니다: "
            f"{KMDB_RAW_DIR}"
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

                    request_data = data.get("request", {})
                    kmdb_payload = data.get("payload")

                    tmdb_id = request_data.get("tmdb_id")
                    title = request_data.get("title")
                    release_year = request_data.get(
                        "release_year"
                    )

                    if release_year:
                        release_year = int(release_year)

                    if (
                        tmdb_id is None
                        or not title
                        or kmdb_payload is None
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
                            "requested_tmdb_id": tmdb_id,
                            "query_title": title,
                            "query_year": release_year,
                            "payload": Jsonb(kmdb_payload),
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
    load_kmdb_raw()