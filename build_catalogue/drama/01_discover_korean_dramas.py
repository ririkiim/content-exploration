import json
import os
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv


load_dotenv()

TMDB_TOKEN = os.environ["TMDB_ACCESS_TOKEN"]

TMDB_BASE_URL = "https://api.themoviedb.org/3"

TMDB_HEADERS = {
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "accept": "application/json",
}

START_YEAR = 1950
END_YEAR = date.today().year

PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_PATH = (
    PROJECT_ROOT
    / "data"
    / "selections"
    / "korean_drama_tmdb_ids.jsonl"
)

REQUEST_DELAY_SECONDS = 0.25


def tmdb_get(endpoint, params):
    response = requests.get(
        f"{TMDB_BASE_URL}{endpoint}",
        headers=TMDB_HEADERS,
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    return response.json()


def discover_dramas_by_year(
    year,
    criteria_name,
    criteria_params,
):
    dramas = {}
    page = 1

    while True:
        params = {
            "language": "ko-KR",
            "include_adult": "false",
            "first_air_date_year": year,
            "with_genres": "18",
            "sort_by": "first_air_date.asc",
            "page": page,
            **criteria_params,
        }

        result = tmdb_get("/discover/tv", params)

        for drama in result.get("results", []):
            tmdb_id = drama.get("id")

            if not tmdb_id:
                continue

            dramas[tmdb_id] = {
                "tmdb_id": tmdb_id,
                "title": drama.get("name"),
                "original_title": drama.get(
                    "original_name"
                ),
                "first_air_date": drama.get(
                    "first_air_date"
                ),
                "overview": drama.get("overview", ""),
                "genre_ids": drama.get("genre_ids", []),
                "criteria": [criteria_name],
            }

        total_pages = result.get("total_pages", 1)

        print(
            f"{year} / {criteria_name} / "
            f"{page}/{total_pages} / "
            f"연도별 {len(dramas)}개"
        )

        if page >= total_pages:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return dramas


def merge_dramas(all_dramas, new_dramas):
    for tmdb_id, drama in new_dramas.items():
        if tmdb_id not in all_dramas:
            all_dramas[tmdb_id] = drama
            continue

        existing = all_dramas[tmdb_id]

        for criteria in drama["criteria"]:
            if criteria not in existing["criteria"]:
                existing["criteria"].append(criteria)

        if not existing.get("overview") and drama.get(
            "overview"
        ):
            existing["overview"] = drama["overview"]

        existing["genre_ids"] = sorted(
            set(existing.get("genre_ids", []))
            | set(drama.get("genre_ids", []))
        )


def discover_korean_dramas():
    all_dramas = {}

    filters = [
        (
            "origin_country_kr",
            {
                "with_origin_country": "KR",
            },
        ),
        (
            "original_language_ko",
            {
                "with_original_language": "ko",
            },
        ),
    ]

    for year in range(START_YEAR, END_YEAR + 1):
        for criteria_name, criteria_params in filters:
            try:
                dramas = discover_dramas_by_year(
                    year,
                    criteria_name,
                    criteria_params,
                )

                merge_dramas(all_dramas, dramas)

            except requests.RequestException as error:
                print(
                    f"수집 실패: {year} / "
                    f"{criteria_name} / {error}"
                )

            time.sleep(REQUEST_DELAY_SECONDS)

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dramas = sorted(
        all_dramas.values(),
        key=lambda drama: (
            drama.get("first_air_date") or "",
            drama["tmdb_id"],
        ),
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        for drama in dramas:
            json.dump(
                drama,
                output_file,
                ensure_ascii=False,
            )
            output_file.write("\n")

    print()
    print(
        f"한국 드라마 TMDb 수집 완료: "
        f"{len(dramas)}개"
    )
    print(f"저장 위치: {OUTPUT_PATH}")


if __name__ == "__main__":
    discover_korean_dramas()