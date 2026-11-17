import os

import ijson
import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

from paths import BASE_DIR, NLK_RAW_DIR


load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
BOOK_JSON_DIR = NLK_RAW_DIR

BATCH_SIZE = 1000


INSERT_SQL = """
INSERT INTO public.raw_nlk_book (
    nlk_id,
    raw_payload
)
VALUES (%s, %s);
"""


def json_files():
    return sorted(
        BOOK_JSON_DIR.glob("book_*.json"),
        key=lambda path: int(path.stem.split("_")[1])
    )


def load_books():
    files = json_files()

    if not files:
        raise FileNotFoundError(
            f"국가서지 JSON 파일이 없습니다: {BOOK_JSON_DIR}"
        )

    total_count = 0
    skipped_count = 0

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:

            # 비어 있는 테이블에만 적재
            cur.execute(
                "SELECT EXISTS (SELECT 1 FROM public.raw_nlk_book)"
            )
            if cur.fetchone()[0]:
                raise RuntimeError(
                    "raw_nlk_book에 기존 데이터가 있습니다. "
                    "중복 적재 방지를 위해 실행을 중단합니다."
                )

            for file_path in files:
                print(f"\n처리 시작: {file_path.name}")
                batch = []

                with file_path.open("rb") as f:
                    records = ijson.items(f, "@graph.item")

                    for record in records:
                        if not isinstance(record, dict):
                            skipped_count += 1
                            continue

                        nlk_id = record.get("@id")

                        batch.append((
                            str(nlk_id) if nlk_id else None,
                            Jsonb(record)
                        ))

                        if len(batch) >= BATCH_SIZE:
                            cur.executemany(INSERT_SQL, batch)
                            conn.commit()

                            total_count += len(batch)
                            batch.clear()

                            if total_count % 10000 == 0:
                                print(f"적재 완료: {total_count:,}건")

                if batch:
                    cur.executemany(INSERT_SQL, batch)
                    conn.commit()
                    total_count += len(batch)

                print(f"파일 완료: {file_path.name}")

    print("\n===== 국가서지 RAW 적재 결과 =====")
    print(f"총 적재: {total_count:,}건")
    print(f"비정상 레코드 제외: {skipped_count:,}건")


if __name__ == "__main__":
    load_books()