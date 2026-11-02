import argparse
import json
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv()


CONTENT_IDS = [42844, 79246, 84900]
DATA_DIR = Path("data/enrichment/normalization")


def load_rows():
    rows = []

    for content_id in CONTENT_IDS:
        path = DATA_DIR / f"topic_relations_{content_id}_v2.json"

        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        for item in data["content_topic_relations"]:
            name = item["target_topic_name"].strip()
            origins = item.get("candidate_origins", [])

            if not name:
                continue

            is_source = "source_keyword" in origins

            rows.append({
                "content_id": content_id,
                "topic_name": name,
                "topic_type": "subject",
                "evidence": (
                    "Original content keyword"
                    if is_source
                    else "Supplementary topic candidate"
                ),
                "evidence_level": (
                    "explicit" if is_source else "inferred"
                ),
                "confidence": (
                    "medium" if is_source else "low"
                ),
                "prompt_version": "keyword_normalization_v3",
                "evidence_source": json.dumps(
                    origins, ensure_ascii=False
                ),
            })

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--commit",
        action="store_true",
        help="실제 DB에 적재",
    )
    args = parser.parse_args()

    rows = load_rows()

    print(f"적재 대상 콘텐츠–토픽 관계: {len(rows)}개")

    for cid in CONTENT_IDS:
        count = sum(r["content_id"] == cid for r in rows)
        print(f"  {cid}: {count}개")

    if not args.commit:
        print("\n미리보기 완료. DB는 변경하지 않았습니다.")
        return

    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL 환경변수가 필요합니다.")

    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        """
                        INSERT INTO topic (topic_name)
                        VALUES (%s)
                        ON CONFLICT (topic_name) DO NOTHING
                        """,
                        (row["topic_name"],),
                    )

                    cur.execute(
                        """
                        SELECT topic_id
                        FROM topic
                        WHERE topic_name = %s
                        """,
                        (row["topic_name"],),
                    )

                    topic_id = cur.fetchone()[0]

                    cur.execute(
                        """
                        INSERT INTO content_topic (
                            content_id,
                            topic_id,
                            topic_type,
                            evidence,
                            evidence_level,
                            confidence,
                            prompt_version,
                            evidence_source
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (content_id, topic_id)
                        DO NOTHING
                        """,
                        (
                            row["content_id"],
                            topic_id,
                            row["topic_type"],
                            row["evidence"],
                            row["evidence_level"],
                            row["confidence"],
                            row["prompt_version"],
                            row["evidence_source"],
                        ),
                    )

    print("\nDB 적재 완료")


if __name__ == "__main__":
    main()