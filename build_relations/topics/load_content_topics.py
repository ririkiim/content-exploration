import argparse
import hashlib
import os
import time
import traceback
import uuid

import psycopg

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from topic_prompt import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    prepare_content,
)

from extract_content_topics import (
    MODEL_NAME,
    call_llm,
    parse_response,
    validate_topics,
)


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

MIN_DESCRIPTION_LENGTH = 30

MIN_DESCRIPTION_LENGTH = 30

TEST_CONTENTS = [
    # 영화 10개
    ("movie", "기생충"),
    ("movie", "살인의 추억"),
    ("movie", "명량"),
    ("movie", "1987"),
    ("movie", "변호인"),
    ("movie", "동주"),
    ("movie", "아가씨"),
    ("movie", "신과함께-죄와 벌"),
    ("movie", "부산행"),
    ("movie", "국제시장"),

    # 드라마 10개
    ("tv", "미스터 션샤인"),
    ("tv", "킹덤"),
    ("tv", "옷소매 붉은 끝동"),
    ("tv", "시그널"),
    ("tv", "미생"),
    ("tv", "D.P."),
    ("tv", "이상한 변호사 우영우"),
    ("tv", "무빙"),
    ("tv", "정년이"),
    ("tv", "악의 꽃"),

    # 도서 10개
    ("book", "칼의 노래"),
    ("book", "소년이 온다"),
    ("book", "난장이가 쏘아올린 작은 공"),
    ("book", "지구 끝의 온실"),
    ("book", "토지"),
    ("book", "구운몽"),
    ("book", "살인자의 기억법"),
    ("book", "아몬드"),
    ("book", "완득이"),
    ("book", "82년생 김지영"),
]

# =========================================================
# 1. 콘텐츠 조회
# =========================================================

SELECT_CONTENTS_SQL = """
WITH content_data AS (
    SELECT
        c.content_id,
        c.content_type,
        c.title,
        c.original_title,
        c.release_year,

        CASE
            WHEN c.content_type = 'movie'
                THEN m.overview
            WHEN c.content_type = 'tv'
                THEN t.overview
            WHEN c.content_type = 'book'
                THEN b.description
        END AS description,

        CASE
            WHEN c.content_type = 'movie'
                THEN m.genres
            WHEN c.content_type = 'tv'
                THEN t.genres
        END AS genres,

        CASE
            WHEN c.content_type = 'movie'
                THEN m.keywords
            WHEN c.content_type = 'tv'
                THEN t.keywords
        END AS keywords

    FROM content c

    LEFT JOIN movie_detail m
        ON c.content_id = m.content_id
       AND c.content_type = 'movie'

    LEFT JOIN tv_detail t
        ON c.content_id = t.content_id
       AND c.content_type = 'tv'

    LEFT JOIN LATERAL (
        SELECT be.description
        FROM book_edition be
        WHERE be.content_id = c.content_id
        ORDER BY
            be.is_representative DESC NULLS LAST,
            be.book_edition_id
        LIMIT 1
    ) b ON c.content_type = 'book'
)

SELECT
    cd.*,
    s.status AS extraction_status,
    s.attempt_count,
    s.prompt_version AS previous_prompt_version,
    s.model_name AS previous_model_name

FROM content_data cd

LEFT JOIN content_topic_extraction_status s
    ON cd.content_id = s.content_id

WHERE
    (
        %(content_id)s::BIGINT IS NULL
        OR cd.content_id = %(content_id)s::BIGINT
    )

    AND (
        NOT %(test_only)s::BOOLEAN
        OR EXISTS (
            SELECT 1
            FROM jsonb_to_recordset(
                %(test_contents)s::JSONB
            ) AS tc(
                content_type TEXT,
                title TEXT
            )
            WHERE tc.content_type = cd.content_type
              AND tc.title = cd.title
        )
    )

    AND LENGTH(
        TRIM(COALESCE(cd.description, ''))
    ) >= %(min_length)s

    AND (
        %(force)s::BOOLEAN
        OR %(record_only)s::BOOLEAN
        OR s.content_id IS NULL

        OR (
            s.status = 'failed'
            AND %(retry_failed)s::BOOLEAN
            AND COALESCE(s.attempt_count, 0) < 3
        )

        OR (
            s.status IS DISTINCT FROM 'failed'
            AND (
                s.status IS DISTINCT FROM 'success'
                OR s.prompt_version IS DISTINCT FROM
                    %(prompt_version)s
                OR s.model_name IS DISTINCT FROM
                    %(model_name)s
            )
        )
    )

ORDER BY cd.content_id

LIMIT %(limit)s;
"""

def fetch_contents(
    connection,
    content_id,
    limit,
    force,
    record_only,
    retry_failed,
    model_name,
    test_only=False,
):
    test_contents = [
        {
            "content_type": content_type,
            "title": title,
        }
        for content_type, title in TEST_CONTENTS
    ]

    with connection.cursor() as cursor:
        cursor.execute(
            SELECT_CONTENTS_SQL,
            {
                "content_id": content_id,
                "min_length": MIN_DESCRIPTION_LENGTH,
                "force": force,
                "record_only": record_only,
                "retry_failed": retry_failed,
                "prompt_version": PROMPT_VERSION,
                "model_name": model_name,
                "limit": limit,
                "test_only": test_only,
                "test_contents": Jsonb(test_contents),
            },
        )

        return cursor.fetchall()
    
    with connection.cursor() as cursor:
        cursor.execute(
            SELECT_CONTENTS_SQL,
            {
                "content_id": content_id,
                "min_length": MIN_DESCRIPTION_LENGTH,
                "force": force,
                "record_only": record_only,
                "retry_failed": retry_failed,
                "prompt_version": PROMPT_VERSION,
                "model_name": model_name,
                "test_only": test_only,
                "limit": limit,
            },
        )

        return cursor.fetchall()


# =========================================================
# 2. 실행 이력 SQL
# =========================================================

INSERT_RUN_SQL = """
INSERT INTO content_topic_extraction_run (
    run_id,
    content_id,
    prompt_version,
    model_name,
    status,
    input_snapshot
)
VALUES (
    %s, %s, %s, %s, 'processing', %s
);
"""


UPDATE_RAW_RESPONSE_SQL = """
UPDATE content_topic_extraction_run
SET raw_response = %s
WHERE run_id = %s;
"""


UPDATE_RUN_SUCCESS_SQL = """
UPDATE content_topic_extraction_run
SET
    status = %s,
    topic_count = %s,
    accepted_count = %s,
    elapsed_seconds = %s,
    error_message = NULL,
    completed_at = NOW()
WHERE run_id = %s;
"""


UPDATE_RUN_FAILED_SQL = """
UPDATE content_topic_extraction_run
SET
    status = 'failed',
    topic_count = %s,
    accepted_count = %s,
    elapsed_seconds = %s,
    error_message = %s,
    completed_at = NOW()
WHERE run_id = %s;
"""


# =========================================================
# 3. 실행 이력 저장
# =========================================================

def create_run(
    connection,
    run_id,
    content,
    model_name,
):
    prompt_hash = hashlib.sha256(
        SYSTEM_PROMPT.encode("utf-8")
    ).hexdigest()

    snapshot = {
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": prompt_hash,
        "user_prompt": build_prompt(content),
        "content": prepare_content(content),
    }

    with connection.cursor() as cursor:
        cursor.execute(
            INSERT_RUN_SQL,
            (
                run_id,
                content["content_id"],
                PROMPT_VERSION,
                model_name,
                Jsonb(snapshot),
            ),
        )

    connection.commit()


def save_raw_response(
    connection,
    run_id,
    raw_response,
):
    with connection.cursor() as cursor:
        cursor.execute(
            UPDATE_RAW_RESPONSE_SQL,
            (
                raw_response,
                run_id,
            ),
        )

    connection.commit()


def mark_run_failed(
    connection,
    run_id,
    error_message,
    elapsed_seconds,
    topic_count=None,
    accepted_count=0,
):
    with connection.cursor() as cursor:
        cursor.execute(
            UPDATE_RUN_FAILED_SQL,
            (
                topic_count,
                accepted_count,
                elapsed_seconds,
                error_message[:4000],
                run_id,
            ),
        )

    connection.commit()


# =========================================================
# 4. 기존 최신 상태 SQL
# =========================================================

START_PROCESSING_SQL = """
INSERT INTO content_topic_extraction_status (
    content_id,
    status,
    attempt_count,
    prompt_version,
    model_name,
    last_error,
    updated_at
)
VALUES (
    %s, 'processing', 1, %s, %s, NULL, NOW()
)
ON CONFLICT (content_id)
DO UPDATE SET
    status = 'processing',
    attempt_count =
        content_topic_extraction_status.attempt_count + 1,
    prompt_version = EXCLUDED.prompt_version,
    model_name = EXCLUDED.model_name,
    last_error = NULL,
    updated_at = NOW();
"""


SUCCESS_PROCESSING_SQL = """
INSERT INTO content_topic_extraction_status (
    content_id,
    status,
    attempt_count,
    prompt_version,
    model_name,
    last_error,
    updated_at
)
VALUES (
    %s, 'success', 1, %s, %s, NULL, NOW()
)
ON CONFLICT (content_id)
DO UPDATE SET
    status = 'success',
    prompt_version = EXCLUDED.prompt_version,
    model_name = EXCLUDED.model_name,
    last_error = NULL,
    updated_at = NOW();
"""


FAIL_PROCESSING_SQL = """
INSERT INTO content_topic_extraction_status (
    content_id,
    status,
    attempt_count,
    prompt_version,
    model_name,
    last_error,
    updated_at
)
VALUES (
    %s, 'failed', 1, %s, %s, %s, NOW()
)
ON CONFLICT (content_id)
DO UPDATE SET
    status = 'failed',
    prompt_version = EXCLUDED.prompt_version,
    model_name = EXCLUDED.model_name,
    last_error = EXCLUDED.last_error,
    updated_at = NOW();
"""


def start_processing(
    connection,
    content_id,
    model_name,
):
    with connection.cursor() as cursor:
        cursor.execute(
            START_PROCESSING_SQL,
            (
                content_id,
                PROMPT_VERSION,
                model_name,
            ),
        )

    connection.commit()


def mark_processing_failed(
    connection,
    content_id,
    model_name,
    error_message,
):
    with connection.cursor() as cursor:
        cursor.execute(
            FAIL_PROCESSING_SQL,
            (
                content_id,
                PROMPT_VERSION,
                model_name,
                error_message[:4000],
            ),
        )

    connection.commit()


# =========================================================
# 5. 토픽 저장 SQL
# =========================================================

INSERT_TOPIC_SQL = """
INSERT INTO topic (topic_name)
VALUES (%s)
ON CONFLICT (topic_name) DO NOTHING;
"""


SELECT_TOPIC_SQL = """
SELECT topic_id
FROM topic
WHERE topic_name = %s;
"""


INSERT_CONTENT_TOPIC_SQL = """
INSERT INTO content_topic (
    content_id,
    topic_id,
    topic_type,
    evidence,
    evidence_level,
    confidence,
    prompt_version,
    evidence_quote,
    evidence_source,
    evidence_location,
    quote_valid,
    quote_fragments_valid,
    review_flags
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s
)
ON CONFLICT (content_id, topic_id)
DO UPDATE SET
    topic_type = EXCLUDED.topic_type,
    evidence = EXCLUDED.evidence,
    evidence_level = EXCLUDED.evidence_level,
    confidence = EXCLUDED.confidence,
    prompt_version = EXCLUDED.prompt_version,
    evidence_quote = EXCLUDED.evidence_quote,
    evidence_source = EXCLUDED.evidence_source,
    evidence_location = EXCLUDED.evidence_location,
    quote_valid = EXCLUDED.quote_valid,
    quote_fragments_valid = EXCLUDED.quote_fragments_valid,
    review_flags = EXCLUDED.review_flags;
"""


INSERT_RESULT_SQL = """
INSERT INTO content_topic_extraction_result (
    run_id,
    content_id,
    prompt_version,
    model_name,
    topic_name,
    topic_type,
    evidence_quote,
    evidence,
    evidence_level,
    confidence,
    quote_valid,
    quote_fragments_valid,
    evidence_source,
    evidence_location,
    review_flags,
    accepted
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, FALSE
)
RETURNING extraction_id;
"""


UPDATE_RESULT_ACCEPTED_SQL = """
UPDATE content_topic_extraction_result
SET accepted = TRUE
WHERE extraction_id = %s;
"""


# =========================================================
# 6. 결과 이력 저장
# =========================================================

def save_extraction_results(
    cursor,
    run_id,
    content_id,
    model_name,
    topics,
):
    saved_results = []

    for topic in topics:
        location = topic.get(
            "evidence_location"
        )

        cursor.execute(
            INSERT_RESULT_SQL,
            (
                run_id,
                content_id,
                PROMPT_VERSION,
                model_name,
                topic["name"],
                topic["type"],
                topic["evidence_quote"],
                topic["evidence"],
                topic["evidence_level"],
                topic["confidence"],
                topic["quote_valid"],
                topic["quote_fragments_valid"],
                topic["evidence_source"],

                # JSON null이 아닌 SQL NULL
                Jsonb(location)
                if location is not None
                else None,

                topic["review_flags"],
            ),
        )

        extraction_id = cursor.fetchone()[
            "extraction_id"
        ]

        saved_results.append(
            (extraction_id, topic)
        )

    return saved_results


# =========================================================
# 7. 서비스 반영 대상 선별
# =========================================================

def select_approved_results(saved_results):
    approved = []

    seen_names = set()

    for extraction_id, topic in saved_results:
        if not topic["quote_valid"]:
            continue

        if topic["confidence"] == "low":
            continue

        if "dropped_duplicate" in topic["review_flags"]:
            continue

        name_key = topic["name"].casefold()

        if name_key in seen_names:
            continue

        seen_names.add(name_key)

        approved.append(
            (extraction_id, topic)
        )

    return approved


# =========================================================
# 8. 서비스용 content_topic 저장
# =========================================================

def save_approved_topics(
    cursor,
    content_id,
    saved_results,
):
    approved = select_approved_results(
        saved_results
    )

    if not approved:
        return 0

    # 유효한 새 결과가 있을 때만 교체
    cursor.execute(
        """
        DELETE FROM content_topic
        WHERE content_id = %s;
        """,
        (content_id,),
    )

    saved_count = 0

    for extraction_id, topic in approved:
        cursor.execute(
            INSERT_TOPIC_SQL,
            (topic["name"],),
        )

        cursor.execute(
            SELECT_TOPIC_SQL,
            (topic["name"],),
        )

        row = cursor.fetchone()

        if row is None:
            raise RuntimeError(
                f"topic_id 조회 실패: {topic['name']}"
            )

        location = topic.get(
            "evidence_location"
        )

        cursor.execute(
            INSERT_CONTENT_TOPIC_SQL,
            (
                content_id,
                row["topic_id"],
                topic["type"],
                topic["evidence"],
                topic["evidence_level"],
                topic["confidence"],
                PROMPT_VERSION,
                topic["evidence_quote"],
                topic["evidence_source"],

                Jsonb(location)
                if location is not None
                else None,

                topic["quote_valid"],
                topic["quote_fragments_valid"],
                topic["review_flags"],
            ),
        )

        cursor.execute(
            UPDATE_RESULT_ACCEPTED_SQL,
            (extraction_id,),
        )

        saved_count += 1

    return saved_count


# =========================================================
# 9. 결과 확정
# =========================================================

def finalize_extraction(
    connection,
    run_id,
    content_id,
    model_name,
    topics,
    elapsed_seconds,
    publish=True,
):
    """
    publish=True:
        result 기록 + content_topic 반영

    publish=False:
        result 기록만 수행
        content_topic 및 최신 status 변경 없음
    """

    with connection.cursor() as cursor:
        saved_results = save_extraction_results(
            cursor,
            run_id,
            content_id,
            model_name,
            topics,
        )

        topic_count = len(topics)

        approved_candidates = (
            select_approved_results(saved_results)
        )

        accepted_count = 0

        if topic_count == 0:
            run_status = "success_empty"

        elif not approved_candidates:
            run_status = "failed"

        else:
            run_status = "success"

        if publish:
            if run_status == "success":
                accepted_count = save_approved_topics(
                    cursor,
                    content_id,
                    saved_results,
                )

            elif run_status == "success_empty":
                # 정상 빈 배열은 기존 관계 제거
                cursor.execute(
                    """
                    DELETE FROM content_topic
                    WHERE content_id = %s;
                    """,
                    (content_id,),
                )

            # failed는 기존 관계 유지

        if run_status == "failed":
            error_message = (
                "추출된 토픽 중 승인 가능한 토픽이 없음"
            )

            cursor.execute(
                UPDATE_RUN_FAILED_SQL,
                (
                    topic_count,
                    accepted_count,
                    elapsed_seconds,
                    error_message,
                    run_id,
                ),
            )

            if publish:
                cursor.execute(
                    FAIL_PROCESSING_SQL,
                    (
                        content_id,
                        PROMPT_VERSION,
                        model_name,
                        error_message,
                    ),
                )

        else:
            cursor.execute(
                UPDATE_RUN_SUCCESS_SQL,
                (
                    run_status,
                    topic_count,
                    accepted_count,
                    elapsed_seconds,
                    run_id,
                ),
            )

            if publish:
                cursor.execute(
                    SUCCESS_PROCESSING_SQL,
                    (
                        content_id,
                        PROMPT_VERSION,
                        model_name,
                    ),
                )

    connection.commit()

    return {
        "status": run_status,
        "topic_count": topic_count,
        "accepted_count": accepted_count,
    }


# =========================================================
# 10. 출력
# =========================================================

def print_topics(topics):
    print(
        f"\n  추출된 토픽: {len(topics)}개"
    )

    for topic in topics:
        valid_mark = (
            "✓"
            if topic["quote_valid"]
            else "✗"
        )

        print(
            f"\n  {valid_mark} {topic['name']}"
        )

        print(
            f"     유형: {topic['type']}"
        )

        print(
            f"     인용: {topic['evidence_quote']}"
        )

        print(
            f"     설명: {topic['evidence']}"
        )

        print(
            f"     근거: {topic['evidence_level']}"
        )

        print(
            f"     신뢰도: {topic['confidence']}"
        )

        print(
            f"     quote_valid: {topic['quote_valid']}"
        )

        if topic["review_flags"]:
            print(
                "     review_flags: "
                + ", ".join(
                    topic["review_flags"]
                )
            )


# =========================================================
# 11. 전체 실행
# =========================================================

def run_pipeline(
    limit=3,
    content_id=None,
    force=False,
    retry_failed=False,
    record_only=False,
    dry_run=False,
    delay=1.0,
    model_name=MODEL_NAME,
    temperature=0.0,
    json_mode=False,
    test_only=False,
):
    success_count = 0
    empty_count = 0
    failed_count = 0
    total_saved = 0

    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    ) as connection:

        contents = fetch_contents(
            connection=connection,
            content_id=content_id,
            limit=limit,
            force=force,
            record_only=record_only,
            retry_failed=retry_failed,
            model_name=model_name,
            test_only=test_only,
        )

        connection.commit()

        print("=" * 60)
        print("Content Topic Extraction")
        print(f"Prompt: {PROMPT_VERSION}")
        print(f"Model: {model_name}")
        print(f"Temperature: {temperature}")
        print(f"Record Only: {record_only}")
        print(f"Dry Run: {dry_run}")
        print(f"Targets: {len(contents)}")
        print("=" * 60)

        for index, content in enumerate(
            contents,
            start=1,
        ):
            current_id = content[
                "content_id"
            ]

            run_id = uuid.uuid4()

            started_at = time.perf_counter()

            parsed_topics = None
            run_created = False

            print(
                f"\n[{index}/{len(contents)}] "
                f"{current_id} | {content['title']}"
            )

            try:
                if not dry_run:
                    create_run(
                        connection,
                        run_id,
                        content,
                        model_name,
                    )

                    run_created = True

                    if not record_only:
                        start_processing(
                            connection,
                            current_id,
                            model_name,
                        )

                raw_response = call_llm(
                    content,
                    model_name=model_name,
                    temperature=temperature,
                    json_mode=json_mode,
                )

                if not dry_run:
                    # JSON 파싱 전에 저장
                    save_raw_response(
                        connection,
                        run_id,
                        raw_response,
                    )

                parsed_topics = parse_response(
                    raw_response,
                    expected_content_id=current_id,
                )

                topics = validate_topics(
                    parsed_topics,
                    content,
                )

                print_topics(topics)

                elapsed = (
                    time.perf_counter()
                    - started_at
                )

                if dry_run:
                    result = {
                        "status": (
                            "success_empty"
                            if not topics
                            else "success"
                        ),
                        "accepted_count": 0,
                    }

                else:
                    result = finalize_extraction(
                        connection=connection,
                        run_id=run_id,
                        content_id=current_id,
                        model_name=model_name,
                        topics=topics,
                        elapsed_seconds=elapsed,
                        publish=not record_only,
                    )

                status = result["status"]

                if status == "success":
                    success_count += 1

                elif status == "success_empty":
                    empty_count += 1

                else:
                    failed_count += 1

                total_saved += result[
                    "accepted_count"
                ]

                print(
                    f"\n  상태: {status}"
                )

                if record_only:
                    print(
                        "  이력만 저장 / 서비스 미반영"
                    )

                elif dry_run:
                    print(
                        "  DB 변경 없음"
                    )

                else:
                    print(
                        "  서비스 적재: "
                        f"{result['accepted_count']}개"
                    )

            except Exception as error:
                connection.rollback()

                failed_count += 1

                elapsed = (
                    time.perf_counter()
                    - started_at
                )

                error_message = str(error)[:4000]

                print(
                    f"\n  오류: {error_message}"
                )

                traceback.print_exc()

                if not dry_run:
                    try:
                        if run_created:
                            mark_run_failed(
                                connection,
                                run_id,
                                error_message,
                                elapsed,
                                topic_count=(
                                    len(parsed_topics)
                                    if parsed_topics is not None
                                    else None
                                ),
                            )

                        if not record_only:
                            mark_processing_failed(
                                connection,
                                current_id,
                                model_name,
                                error_message,
                            )

                    except Exception as save_error:
                        connection.rollback()

                        print(
                            "  실패 기록 저장 오류: "
                            f"{save_error}"
                        )

            finally:
                # dry-run에서도 지연 적용
                if (
                    index < len(contents)
                    and delay > 0
                ):
                    time.sleep(delay)

    print("\n" + "=" * 60)
    print(f"성공: {success_count}")
    print(f"정상 빈 배열: {empty_count}")
    print(f"실패: {failed_count}")
    print(f"서비스 적재: {total_saved}")
    print("=" * 60)


# =========================================================
# 12. CLI
# =========================================================

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--model",
        default=MODEL_NAME,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=3,
    )

    parser.add_argument(
        "--content-id",
        type=int,
        default=None,
    )

    parser.add_argument(
        "--force",
        action="store_true",
    )

    parser.add_argument(
        "--retry-failed",
        action="store_true",
    )

    parser.add_argument(
        "--record-only",
        action="store_true",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
    )

    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
    )

    parser.add_argument(
        "--json-mode",
        action="store_true",
    )

    parser.add_argument(
        "--test-only",
        action="store_true",
    )       

    args = parser.parse_args()

    if args.limit < 1:
        parser.error(
            "--limit은 1 이상이어야 합니다."
        )

    if args.delay < 0:
        parser.error(
            "--delay는 0 이상이어야 합니다."
        )

    if (
        args.record_only
        and args.dry_run
    ):
        parser.error(
            "--record-only와 --dry-run은 "
            "동시에 사용할 수 없습니다."
        )

    run_pipeline(
        limit=args.limit,
        content_id=args.content_id,
        force=args.force,
        retry_failed=args.retry_failed,
        record_only=args.record_only,
        dry_run=args.dry_run,
        delay=args.delay,
        model_name=args.model,
        temperature=args.temperature,
        json_mode=args.json_mode,
        test_only=args.test_only,
    )


if __name__ == "__main__":
    main()