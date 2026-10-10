# 선택된 TMDB ID의 상세 원본을 JSON 파일로 저장

import requests
import argparse
import json

from paths import TMDB_RAW_DIR
from build_catalogue.tmdb_client import tmdb_get

# TMDB 에서 사용할 부가 정보를 함께 요청해 JSON 파일로 저장
def fetch_tmdb_content(tmdb_id: int, media_type: str) -> None:
    data = tmdb_get(
        f"/{media_type}/{tmdb_id}",
        {
            "language": "ko-KR",
            "append_to_response": ",".join(
                [
                    "credits",
                    "keywords",
                    "external_ids",
                    "alternative_titles",
                    "watch/providers",
                    "recommendations",
                ]
            ),
        },
    )

    TMDB_RAW_DIR.mkdir(parents=True, exist_ok=True)

    file_path = TMDB_RAW_DIR / f"{media_type}_{tmdb_id}.json"

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print(f"저장 완료: {file_path}")

# 하나의 작품에서 요청 실패해도 나머지 수집을 계속 진행함
def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--media-type",
        choices=["movie", "tv"],
        required=True,
    )
    parser.add_argument(
        "--ids",
        type=int,
        nargs="+",
        required=True,
    )

    args = parser.parse_args()

    for tmdb_id in args.ids:
        try:
            fetch_tmdb_content(
                tmdb_id=tmdb_id,
                media_type=args.media_type,
            )
        except requests.RequestException as error:
            print(f"수집 실패: {tmdb_id} / {error}")


if __name__ == "__main__":
    main()