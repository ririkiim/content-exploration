# 선택된 TMDb ID의 상세 원본을 JSON 파일로 저장
import requests
import argparse
import json
import time

from pathlib import Path

from build_catalogue.tmdb_client import tmdb_get

TMDB_RAW_DIR = Path("data/raw/tmdb")


SELECTION_FILES = {
    "movie": Path("data/selections/korean_movie_tmdb_ids.jsonl"),
    "tv": Path("data/selections/korean_tv_tmdb_ids.jsonl"),
}


# TMDb 상세 원본 수집
def fetch_tmdb_content(tmdb_id: int, media_type: str) -> bool:
    TMDB_RAW_DIR.mkdir(parents=True, exist_ok=True)

    file_path = TMDB_RAW_DIR / f"{media_type}_{tmdb_id}.json"

    # 이미 수집한 파일은 건너뛰기
    if file_path.exists():
        return False

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

    # 저장 도중 중단되어 불완전한 파일이 남지 않도록 임시 파일 사용
    temp_path = file_path.with_suffix(".json.tmp")

    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    temp_path.replace(file_path)

    return True


# JSONL에서 TMDb ID 목록 읽기
def load_tmdb_ids(media_type: str) -> list[int]:
    file_path = SELECTION_FILES[media_type]

    ids = []

    with file_path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            row = json.loads(line)
            ids.append(int(row["tmdb_id"]))

    # 중복 ID 제거
    return list(dict.fromkeys(ids))


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
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="JSONL에 있는 전체 TMDb ID 수집",
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="처리할 ID 개수 제한",
    )

    args = parser.parse_args()

    if args.all:
        ids = load_tmdb_ids(args.media_type)
    elif args.ids:
        ids = args.ids
    else:
        parser.error("--ids 또는 --all 중 하나를 지정해야 합니다.")

    if args.limit is not None:
        ids = ids[:args.limit]

    total = len(ids)
    saved = 0
    skipped = 0
    failed = []

    print(f"\n수집 대상: {args.media_type} / {total:,}건\n")

    for index, tmdb_id in enumerate(ids, start=1):
        try:
            result = fetch_tmdb_content(
                tmdb_id=tmdb_id,
                media_type=args.media_type,
            )

            if result:
                saved += 1
                status = "저장"
                time.sleep(0.3)
            else:
                skipped += 1
                status = "건너뜀"

            if index % 100 == 0 or index == total:
                print(
                    f"[{index:,}/{total:,}] "
                    f"신규 {saved:,} / "
                    f"기존 {skipped:,} / "
                    f"실패 {len(failed):,}"
                )

        except (requests.RequestException, ValueError, KeyError) as error:
            failed.append(tmdb_id)
            print(f"[실패] {tmdb_id}: {error}")
            time.sleep(2)

    # 실패한 ID 별도 저장
    if failed:
        failure_path = TMDB_RAW_DIR / f"failed_{args.media_type}_ids.txt"

        with failure_path.open("w", encoding="utf-8") as file:
            for tmdb_id in failed:
                file.write(f"{tmdb_id}\n")

        print(f"\n실패 목록 저장: {failure_path}")

    print("\n수집 종료")
    print(f"신규 저장: {saved:,}건")
    print(f"기존 파일: {skipped:,}건")
    print(f"실패: {len(failed):,}건")


if __name__ == "__main__":
    main()