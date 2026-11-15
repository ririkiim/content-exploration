# [영화] TMDB 작품명으로 KMDb에서 검색
# 제목 / 연도 기준으로 검색

import argparse
import json
import os
import time
from paths import KMDB_RAW_DIR

import psycopg
import requests
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
KMDB_API_KEY = os.environ["KMDB_API_KEY"]

KMDB_API_URL = (
    "https://api.koreafilm.or.kr/"
    "openapi-data2/wisenut/search_api/search_json2.jsp"
)

# 지정된 TMDB ID 중 한국 영화만 조회 
# 한국어 작품 / 제작국에 한국(KR)이 포함된 영화
SELECT_KOREAN_MOVIES_BY_IDS_SQL = """
SELECT
    tmdb_id,
    title,
    payload ->> 'release_date' AS release_date
FROM raw_tmdb_content
WHERE media_type = 'movie'
  AND tmdb_id = ANY(%(tmdb_ids)s)
  AND (
      payload ->> 'original_language' = 'ko'
      OR EXISTS (
          SELECT 1
          FROM jsonb_array_elements(
              COALESCE(
                  payload -> 'production_countries',
                  '[]'::jsonb
              )
          ) AS country
          WHERE country ->> 'iso_3166_1' = 'KR'
      )
  )
ORDER BY tmdb_id;
"""

# TMDB 개봉일에서 KMDb 검색에 사용할 연도만 추출
def get_release_year(release_date: str | None) -> int | None:
    if not release_date:
        return None

    try:
        return int(release_date[:4])
    except ValueError:
        return None

# 작품 제목과 연도 범위로 KMDb 후보 검색
# 제작연도와 개봉연도 차이 고려해서 ±1년으로 검색 
def fetch_kmdb_movie(
    title: str,
    release_year: int | None = None,
) -> dict:
    params = {
        "ServiceKey": KMDB_API_KEY,
        "collection": "kmdb_new2",
        "detail": "Y",
        "title": title,
        "listCount": 20,
    }

    if release_year:
        params["releaseDts"] = f"{release_year - 1}0101"
        params["releaseDte"] = f"{release_year + 1}1231"

    response = requests.get(
        KMDB_API_URL,
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()

    return result

# 검색한 작품 정보와 KMDb 검색 결과를 함께 저장
def fetch_kmdb_raw(tmdb_ids: list[int]) -> None:
    KMDB_RAW_DIR.mkdir(parents=True, exist_ok=True)

    fetched_count = 0
    failed_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                SELECT_KOREAN_MOVIES_BY_IDS_SQL,
                {"tmdb_ids": tmdb_ids},
            )
            movies = cursor.fetchall()

    selected_ids = {tmdb_id for tmdb_id, _, _ in movies}
    skipped_ids = set(tmdb_ids) - selected_ids

    print(f"요청한 TMDB 영화: {len(tmdb_ids)}개")
    print(f"KMDb 수집 대상: {len(movies)}개")

    if skipped_ids:
        print(
            "적재되지 않았거나 한국 콘텐츠 조건을 "
            f"충족하지 않은 ID: {sorted(skipped_ids)}"
        )

    print()

    for tmdb_id, title, release_date in movies:
        release_year = get_release_year(release_date)

        try:
            kmdb_payload = fetch_kmdb_movie(
                title=title,
                release_year=release_year,
            )

            # request: KMDB에 무엇을 검색했는지 기록
            # payload: KMDb가 검색 결과로 무엇을 보여줬는지 기록
            raw_data = {
                "request": {
                    "tmdb_id": tmdb_id,
                    "title": title,
                    "release_year": release_year,
                },
                "payload": kmdb_payload,
            }

            file_path = KMDB_RAW_DIR / f"tmdb_movie_{tmdb_id}.json"

            with file_path.open("w", encoding="utf-8") as file:
                json.dump(
                    raw_data,
                    file,
                    ensure_ascii=False,
                    indent=2,
                )

            fetched_count += 1

        except (
            requests.RequestException,
            ValueError,
        ) as error:
            failed_count += 1
            print(f"수집 실패: {tmdb_id} / {title} / {error}")

        time.sleep(0.2)

    print()
    print(f"수집 완료: {fetched_count}개")
    print(f"수집 실패: {failed_count}개")
    print(f"저장 위치: {KMDB_RAW_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ids",
        type=int,
        nargs="+",
        required=True,
    )
    args = parser.parse_args()

    fetch_kmdb_raw(args.ids)


if __name__ == "__main__":
    main()