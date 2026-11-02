
"""정규화된 토픽을 PostgreSQL에 적재한다.

기본 실행: 미리보기 (DB 변경 없음)
--commit: 실제 적재
--content-id: 하나 이상의 콘텐츠 ID
--llm-result: 단일 콘텐츠에 사용할 LLM result.json 명시
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import load_dotenv


NORMALIZATION_DIR = Path("data/enrichment/normalization_v2")
SEMANTIC_DIR = Path("data/enrichment/semantic_topics_v2")


def read_json(path):
    if not path.is_file():
        raise FileNotFoundError(f"파일 없음: {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_semantic_topics(content_id, explicit_path=None):
    if explicit_path:
        path = Path(explicit_path)
    else:
        root = SEMANTIC_DIR / str(content_id)
        paths = sorted(root.glob("*/result.json"))

        if not paths:
            return {}

        if len(paths) > 1:
            raise ValueError(
                f"{content_id}: LLM 실행 결과가 여러 개입니다. "
                "--llm-result로 사용할 파일을 지정하세요."
            )
        path = paths[0]

    data = read_json(path)

    if data.get("status") != "success":
        raise ValueError(f"LLM 실행 실패 또는 미완료: {path}")

    if str(data.get("content_id")) != str(content_id):
        raise ValueError(f"LLM content_id 불일치: {path}")

    topics = {}
    for item in data.get("topics", []):
        name = item.get("name", "").strip()
        if not name:
            continue
        if name in topics:
            raise ValueError(f"LLM 토픽 이름 중복: {name}")
        topics[name] = item

    return topics


def prepare_rows(content_id, llm_result=None):
    path = (
        NORMALIZATION_DIR
        / f"topic_catalogue_{content_id}_v2.json"
    )
    catalogue = read_json(path)

    if str(catalogue.get("content_id")) != str(content_id):
        raise ValueError(f"Catalogue content_id 불일치: {path}")

    topics = catalogue.get("topics", [])
    if not isinstance(topics, list):
        raise ValueError("topics는 리스트여야 합니다.")

    requires_llm = any(
        "llm_semantic_expansion" in t.get("candidate_origins", [])
        for t in topics
    )

    llm_topics = (
        load_semantic_topics(content_id, llm_result)
        if requires_llm
        else {}
    )

    rows = []
    seen_names = set()

    for topic in topics:
        name = topic.get("display_name", "").strip()
        origins = topic.get("candidate_origins", [])

        if not name:
            continue
        if name in seen_names:
            raise ValueError(f"정규화 결과에 중복 토픽: {name}")
        seen_names.add(name)

        if "llm_semantic_expansion" in origins:
            kind = "llm_semantic_expansion"
            detail = llm_topics.get(name)

            if detail is None:
                raise ValueError(
                    f"LLM 원본에서 토픽을 찾을 수 없음: {name}"
                )

            quote = detail.get("evidence_quote")
            if not quote:
                raise ValueError(f"LLM 근거 문장 누락: {name}")

            if detail.get("quote_valid") is not True:
                raise ValueError(f"근거 인용 검증 미통과: {name}")

            evidence = detail.get("evidence") or quote
            evidence_source = detail.get("evidence_source")
            evidence_level = detail.get("evidence_level") or "inferred"
            confidence = detail.get("confidence") or "low"
            prompt_version = "semantic-expansion-v5.2"

        elif "source_keyword" in origins:
            kind = "source_keyword"
            evidence = "Original content keyword"
            quote = None
            evidence_source = "source_keyword"
            evidence_level = "explicit"
            confidence = "medium"
            prompt_version = "keyword_normalization_v2"

        else:
            kind = "supplementary"
            evidence = "Supplementary topic candidate"
            quote = None
            evidence_source = origins
            evidence_level = "inferred"
            confidence = "low"
            prompt_version = "topic_catalogue_v2"

        def as_text(value):
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return json.dumps(value, ensure_ascii=False)

        rows.append({
            "content_id": content_id,
            "topic_name": name,
            "kind": kind,
            "topic_type": "subject",
            "evidence": as_text(evidence),
            "evidence_quote": as_text(quote),
            "evidence_source": as_text(evidence_source),
            "evidence_level": as_text(evidence_level),
            "confidence": as_text(confidence),
            "prompt_version": prompt_version,
        })

    if not rows:
        raise ValueError(f"{content_id}: 적재할 토픽이 없습니다.")

    return rows


def insert_rows(rows):
    load_dotenv(".env")
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL 환경변수가 없습니다.")

    inserted = 0
    skipped = 0

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
                    result = cur.fetchone()
                    if result is None:
                        raise RuntimeError(
                            f"topic_id 조회 실패: {row['topic_name']}"
                        )
                    topic_id = result[0]

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
                            evidence_quote,
                            evidence_source
                        )
                        VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s
                        )
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
                            row["evidence_quote"],
                            row["evidence_source"],
                        ),
                    )

                    if cur.rowcount:
                        inserted += 1
                    else:
                        skipped += 1

    return inserted, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--content-id", type=int, nargs="+", required=True
    )
    parser.add_argument("--llm-result")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    if args.llm_result and len(args.content_id) != 1:
        parser.error(
            "--llm-result는 콘텐츠 하나를 지정할 때만 사용합니다."
        )

    rows = []

    for content_id in args.content_id:
        current = prepare_rows(content_id, args.llm_result)
        rows.extend(current)

        counts = Counter(row["kind"] for row in current)

        print(f"\n콘텐츠 ID: {content_id}")
        print(f"전체 토픽: {len(current)}개")
        print(f"원본 키워드: {counts['source_keyword']}개")
        print(f"LLM 의미 토픽: {counts['llm_semantic_expansion']}개")
        print(f"기타: {counts['supplementary']}개")

    if not args.commit:
        print("\n미리보기 완료. DB 변경 없음.")
        return

    inserted, skipped = insert_rows(rows)

    print("\nDB 적재 완료")
    print(f"신규 관계: {inserted}개")
    print(f"기존 관계로 건너뜀: {skipped}개")


if __name__ == "__main__":
    main()
