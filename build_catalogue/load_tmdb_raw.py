import json
import os
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

RAW_DIR = Path("data/raw/tmdb")

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://root:root@localhost:5433/content_exploration",
)


def main():
    if not RAW_DIR.exists():
        raise FileNotFoundError(f"폴더가 없습니다: {RAW_DIR}")

    files = sorted(RAW_DIR.glob("*.json"))

    if not files:
        raise RuntimeError("적재할 TMDb JSON 파일이 없습니다.")

    counts = {"movie": 0, "tv": 0}
    skipped = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            # 기존 TMDb ID 확인: 재실행 시 중복 적재 방지
            existing = {}

            for media_type in ("movie", "tv"):
                table = f"raw_tmdb_{media_type}"

                cur.execute(f"SELECT tmdb_id FROM {table}")
                existing[media_type] = {
                    row[0] for row in cur.fetchall()
                }

            for path in files:
                if path.name.startswith("movie_"):
                    media_type = "movie"
                elif path.name.startswith("tv_"):
                    media_type = "tv"
                else:
                    continue

                with path.open(encoding="utf-8") as f:
                    payload = json.load(f)

                tmdb_id = payload.get("id")

                if tmdb_id is None:
                    raise ValueError(f"TMDb ID가 없습니다: {path}")

                tmdb_id = int(tmdb_id)

                if tmdb_id in existing[media_type]:
                    skipped += 1
                    continue

                table = f"raw_tmdb_{media_type}"

                cur.execute(
                    f"""
                    INSERT INTO {table} (tmdb_id, raw_payload)
                    VALUES (%s, %s)
                    """,
                    (tmdb_id, Jsonb(payload)),
                )

                existing[media_type].add(tmdb_id)
                counts[media_type] += 1

        conn.commit()

    print("TMDb RAW 적재 완료")
    print(f"영화: {counts['movie']:,}건")
    print(f"TV: {counts['tv']:,}건")
    print(f"중복 건너뜀: {skipped:,}건")


if __name__ == "__main__":
    main()