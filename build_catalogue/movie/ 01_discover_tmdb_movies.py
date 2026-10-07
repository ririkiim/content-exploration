import json
import time
from datetime import date
from pathlib import Path

import requests

from tmdb_client import tmdb_get


START_YEAR = 1910
END_YEAR = date.today().year

OUTPUT_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "selections"
    / "korean_movie_tmdb_ids.jsonl"
)

REQUEST_DELAY_SECONDS = 0.25


def discover_movies_by_year(
    year,
    criteria_name,
    criteria_params,
):
    movies = {}
    page = 1

    while True:
        params = {
            "language": "ko-KR",
            "include_adult": "false",
            "include_video": "false",
            "primary_release_year": year,
            "sort_by": "primary_release_date.asc",
            "page": page,
            **criteria_params,
        }

        result = tmdb_get("/discover/movie", params)

        for movie in result.get("results", []):
            tmdb_id = movie.get("id")

            if not tmdb_id:
                continue

            movies[tmdb_id] = {
                "tmdb_id": tmdb_id,
                "title": movie.get("title"),
                "original_title": movie.get(
                    "original_title"
                ),
                "release_date": movie.get(
                    "release_date"
                ),
                "overview": movie.get("overview", ""),
                "genre_ids": movie.get("genre_ids", []),
                "criteria": [criteria_name],
            }

        total_pages = result.get("total_pages", 1)

        print(
            f"{year} / {criteria_name} / "
            f"{page}/{total_pages} / "
            f"누적 {len(movies)}개"
        )

        if page >= total_pages:
            break

        page += 1
        time.sleep(REQUEST_DELAY_SECONDS)

    return movies


def merge_movies(all_movies, new_movies):
    for tmdb_id, movie in new_movies.items():
        if tmdb_id not in all_movies:
            all_movies[tmdb_id] = movie
            continue

        existing = all_movies[tmdb_id]

        for criteria in movie["criteria"]:
            if criteria not in existing["criteria"]:
                existing["criteria"].append(criteria)

        if not existing.get("overview") and movie.get("overview"):
            existing["overview"] = movie["overview"]

        existing["genre_ids"] = sorted(
            set(existing.get("genre_ids", []))
            | set(movie.get("genre_ids", []))
        )

def discover_korean_movies():
    all_movies = {}

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
                movies = discover_movies_by_year(
                    year=year,
                    criteria_name=criteria_name,
                    criteria_params=criteria_params,
                )

                merge_movies(
                    all_movies=all_movies,
                    new_movies=movies,
                )

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

    movies = sorted(
        all_movies.values(),
        key=lambda movie: (
            movie.get("release_date") or "",
            movie["tmdb_id"],
        ),
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
    ) as output_file:
        for movie in movies:
            json.dump(
                movie,
                output_file,
                ensure_ascii=False,
            )
            output_file.write("\n")

    print()
    print(f"한국 영화 TMDb ID 수집 완료: {len(movies)}개")
    print(f"저장 위치: {OUTPUT_PATH}")


if __name__ == "__main__":
    discover_korean_movies()