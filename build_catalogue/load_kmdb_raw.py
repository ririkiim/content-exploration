import csv
import json
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()

CSV_PATH = "data/raw/kmdb_csv.csv"
BATCH_SIZE = 5000

FIELD_MAP = {
    "영화등록번호ID": 0,
    "영화등록번호NO": 1,
    "영화명": 2,
    "영문제명": 3,
    "원제명": 4,
    "유형": 5,
    "용도": 6,
    "장르": 7,
    "제작국가": 8,
    "제작년도": 9,
    "제작사": 10,
    "감독": 11,
    "출연": 12,
    "각본": 13,
    "영화심의여부": 15,
    "대표영화심의일": 16,
    "대표영화심의번호": 17,
    "대표영화관람등급": 18,
    "대표개봉일": 19,
    "대표상영시간": 20,
    "키워드": 21,
    "줄거리": 22,
    "KMDBURL": 23,
    "최초등록일": 24,
    "최종수정일": 25,
}


def normalize(value):
    """문자열 null과 공백을 실제 None으로 변환"""
    if value is None:
        return None

    value = value.strip()

    if not value or value.lower() == "null":
        return None

    return value


def build_payload(values, headers):
    payload = {
        field: normalize(values[index])
        for field, index in FIELD_MAP.items()
    }

    # CSV 원본의 헤더와 26개 값 모두 보존
    payload["_source_headers"] = headers
    payload["_source_values"] = values

    return payload


def insert_batch(cur, batch):
    cur.executemany(
        """
        INSERT INTO raw_kmdb_movie (
            source_id,
            source_row_number,
            raw_payload
        )
        VALUES (%s, %s, %s::jsonb)
        """,
        batch,
    )


def main():
    total = 0

    with open(CSV_PATH, encoding="cp949", newline="") as f:
        reader = csv.reader(f)
        headers = next(reader)

        if len(headers) != 25:
            raise ValueError(
                f"CSV 헤더 개수가 예상과 다릅니다: {len(headers)}"
            )

        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with conn.cursor() as cur:


                batch = []

                for row_number, values in enumerate(reader, start=1):
                    if not values or not any(values):
                        continue

                    if len(values) != 26:
                        raise ValueError(
                            f"CSV {row_number + 1}행: "
                            f"예상 26개, 실제 {len(values)}개"
                        )

                    payload = build_payload(values, headers)

                    # 원본 등록번호 2개를 조합
                    source_id = (
                        f"{values[0].strip()}:{values[1].strip()}"
                    )

                    batch.append(
                        (
                            source_id,
                            row_number,
                            json.dumps(payload, ensure_ascii=False),
                        )
                    )

                    if len(batch) >= BATCH_SIZE:
                        insert_batch(cur, batch)
                        total += len(batch)
                        print(f"{total:,}건 처리")
                        batch.clear()

                if batch:
                    insert_batch(cur, batch)
                    total += len(batch)

            # 정상 종료 시에만 자동 commit

    print(f"\nKMDb RAW 적재 완료: {total:,}건")


if __name__ == "__main__":
    main()