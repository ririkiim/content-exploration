import argparse
import csv
import json
import re
import time

from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from extract_content_topics import (
    DATABASE_URL,
    MODEL_NAME,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_prompt,
    client,
    parse_response,
    validate_evidence_quote,
)


# =========================================================
# 1. 평가 대상
# =========================================================

TEST_CONTENTS = [
    # 영화 10개
    ("movie", "기생충"),
    ("movie", "살인의 추억"),
    ("movie", "명량"),
    ("movie", "1987"),
    ("movie", "변호인"),
    ("movie", "동주"),
    ("movie", "아가씨"),
    ("movie", "신과함께- 죄와 벌"),
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
# 2. 콘텐츠 조회
# =========================================================

SELECT_SQL = """
SELECT
    c.content_id,
    c.content_type,
    c.title,
    c.original_title,
    c.release_year,

    CASE
        WHEN c.content_type = 'movie' THEN m.overview
        WHEN c.content_type = 'tv' THEN t.overview
        WHEN c.content_type = 'book' THEN b.description
    END AS description,

    CASE
        WHEN c.content_type = 'movie' THEN m.genres
        WHEN c.content_type = 'tv' THEN t.genres
        ELSE NULL::jsonb
    END AS genres,

    CASE
        WHEN c.content_type = 'movie' THEN m.keywords
        WHEN c.content_type = 'tv' THEN t.keywords
        ELSE NULL::jsonb
    END AS keywords,

    b.publisher,
    b.pub_year,
    b.contributors,
    b.description_source

FROM content c

LEFT JOIN movie_detail m
    ON c.content_id = m.content_id
    AND c.content_type = 'movie'

LEFT JOIN tv_detail t
    ON c.content_id = t.content_id
    AND c.content_type = 'tv'

LEFT JOIN LATERAL (
    SELECT
        be.description,
        be.publisher,
        be.pub_year,
        be.contributors,
        be.description_source
    FROM book_edition be
    WHERE be.content_id = c.content_id
      AND be.is_representative = TRUE
    ORDER BY be.book_edition_id
    LIMIT 1
) b ON c.content_type = 'book'

WHERE c.content_type = %s
  AND (
      c.title = %s
      OR c.original_title = %s
      OR c.normalized_title = %s
  )

ORDER BY c.content_id;
"""


SELECT_BY_ID_SQL = """
SELECT
    c.content_id,
    c.content_type,
    c.title,
    c.original_title,
    c.release_year,

    COALESCE(
        m.overview,
        t.overview
    ) AS description,

    COALESCE(
        m.genres,
        t.genres
    ) AS genres,

    COALESCE(
        m.keywords,
        t.keywords
    ) AS keywords,

    NULL::TEXT AS publisher,
    NULL::INTEGER AS pub_year,
    NULL::JSONB AS contributors,
    NULL::TEXT AS description_source

FROM content c

LEFT JOIN movie_detail m
    ON c.content_id = m.content_id
    AND c.content_type = 'movie'

LEFT JOIN tv_detail t
    ON c.content_id = t.content_id
    AND c.content_type = 'tv'

WHERE c.content_id = %s;
"""


def normalize_title(title):
    return re.sub(
        r"\s+",
        "",
        title or "",
    ).casefold()


def load_test_contents():

    selected = []
    issues = []

    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    ) as conn:

        with conn.cursor() as cursor:

            for content_type, title in TEST_CONTENTS:

                cursor.execute(
                    SELECT_SQL,
                    (
                        content_type,
                        title,
                        title,
                        title,
                    ),
                )

                rows = cursor.fetchall()

                # 정확한 제목이 없으면
                # 공백 차이를 허용한 후보 검색
                if not rows:

                    cursor.execute(
                        """
                        SELECT
                            content_id,
                            title,
                            original_title,
                            normalized_title
                        FROM content
                        WHERE content_type = %s;
                        """,
                        (content_type,),
                    )

                    target = normalize_title(title)

                    candidates = [
                        row
                        for row in cursor.fetchall()
                        if target in {
                            normalize_title(
                                row["title"]
                            ),
                            normalize_title(
                                row["original_title"]
                            ),
                            normalize_title(
                                row["normalized_title"]
                            ),
                        }
                    ]

                    if len(candidates) == 1:

                        cursor.execute(
                            SELECT_BY_ID_SQL,
                            (
                                candidates[0]["content_id"],
                            ),
                        )

                        rows = cursor.fetchall()

                    elif len(candidates) > 1:

                        issues.append({
                            "title": title,
                            "content_type": content_type,
                            "reason": "multiple_matches",
                            "candidates": [
                                dict(row)
                                for row in candidates
                            ],
                        })

                        continue

                if not rows:

                    issues.append({
                        "title": title,
                        "content_type": content_type,
                        "reason": "not_found",
                    })

                    continue

                if len(rows) > 1:

                    issues.append({
                        "title": title,
                        "content_type": content_type,
                        "reason": "multiple_matches",
                        "candidates": [
                            {
                                "content_id": row["content_id"],
                                "title": row["title"],
                                "release_year": row["release_year"],
                            }
                            for row in rows
                        ],
                    })

                    continue

                content = dict(rows[0])

                description = (
                    content["description"] or ""
                ).strip()

                if len(description) < 30:

                    issues.append({
                        "title": title,
                        "content_type": content_type,
                        "content_id": content["content_id"],
                        "reason": "insufficient_description",
                        "description_length": len(
                            description
                        ),
                    })

                    continue

                selected.append(content)

    return selected, issues


# =========================================================
# 3. 모델 호출 및 인용 검증
# =========================================================

def evaluate_one(content, model_name):
    """
    LLM 호출 후 토픽 추출 및 인용 검증.

    DB INSERT/UPDATE/DELETE 없음.
    """

    prompt = build_prompt(content)

    start = time.perf_counter()

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": prompt,
            },
        ],
        stream=False,
    )

    elapsed = time.perf_counter() - start

    answer = response.choices[0].message.content

    if not answer:
        raise ValueError(
            "LLM 응답이 비어 있습니다."
        )

    topics = parse_response(
        answer,
        expected_content_id=content["content_id"],
    )

    # 인용 검증
    for topic in topics:

        validation = validate_evidence_quote(
            topic,
            content,
        )

        topic.update(validation)

    # 검증 통계
    topic_count = len(topics)

    valid_quote_count = sum(
        topic["quote_valid"]
        for topic in topics
    )

    invalid_quote_count = (
        topic_count - valid_quote_count
    )

    quote_validation_rate = (
        round(
            valid_quote_count / topic_count,
            4,
        )
        if topic_count > 0
        else None
    )

    validation_warnings = {}

    for topic in topics:

        warning = topic.get(
            "validation_warning"
        )

        if warning:

            validation_warnings[warning] = (
                validation_warnings.get(
                    warning,
                    0,
                ) + 1
            )

    usage = response.usage

    return {
        "content_id": content["content_id"],
        "content_type": content["content_type"],
        "title": content["title"],

        "model_requested": model_name,
        "model_returned": response.model,

        "prompt_version": PROMPT_VERSION,

        "elapsed_seconds": round(
            elapsed,
            3,
        ),

        "input_tokens": (
            usage.prompt_tokens
            if usage
            else None
        ),

        "output_tokens": (
            usage.completion_tokens
            if usage
            else None
        ),

        "total_tokens": (
            usage.total_tokens
            if usage
            else None
        ),

        "topic_count": topic_count,

        "valid_quote_count": valid_quote_count,

        "invalid_quote_count": invalid_quote_count,

        "quote_validation_rate": (
            quote_validation_rate
        ),

        "validation_warnings": (
            validation_warnings
        ),

        "topics": topics,

        "raw_response": answer,

        "status": "success",

        "error": None,
    }


# =========================================================
# 4. 결과 저장
# =========================================================

def save_json(path, data):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
            default=str,
        )


def save_comparison_csv(path, results):
    """
    콘텐츠 × 모델 단위 비교 CSV.
    """

    fields = [
        "content_id",
        "content_type",
        "title",

        "model_requested",
        "model_returned",

        "status",

        "topic_count",

        "valid_quote_count",
        "invalid_quote_count",
        "quote_validation_rate",

        "elapsed_seconds",

        "input_tokens",
        "output_tokens",
        "total_tokens",

        "topic_names",

        "validation_warnings",

        "error",
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for result in results:

            row = {
                key: result.get(key)
                for key in fields
            }

            row["topic_names"] = " | ".join(
                topic["name"]
                for topic in result.get(
                    "topics",
                    [],
                )
            )

            row["validation_warnings"] = (
                json.dumps(
                    result.get(
                        "validation_warnings",
                        {},
                    ),
                    ensure_ascii=False,
                )
            )

            writer.writerow(row)


def save_topics_csv(path, results):
    """
    토픽 단위 상세 비교 CSV.

    인용문 및 검증 결과 포함.
    """

    fields = [
        "content_id",
        "content_type",
        "title",

        "model",

        "topic_name",
        "topic_type",

        "evidence_quote",
        "evidence",

        "evidence_level",
        "confidence",

        "quote_valid",

        "evidence_source",
        "evidence_start",
        "evidence_end",

        "validation_warning",
    ]

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for result in results:

            if result["status"] != "success":
                continue

            for topic in result["topics"]:

                location = (
                    topic.get(
                        "evidence_location"
                    )
                    or {}
                )

                writer.writerow({
                    "content_id": (
                        result["content_id"]
                    ),

                    "content_type": (
                        result["content_type"]
                    ),

                    "title": result["title"],

                    "model": (
                        result["model_requested"]
                    ),

                    "topic_name": (
                        topic["name"]
                    ),

                    "topic_type": (
                        topic["type"]
                    ),

                    "evidence_quote": (
                        topic["evidence_quote"]
                    ),

                    "evidence": (
                        topic["evidence"]
                    ),

                    "evidence_level": (
                        topic["evidence_level"]
                    ),

                    "confidence": (
                        topic["confidence"]
                    ),

                    "quote_valid": (
                        topic["quote_valid"]
                    ),

                    "evidence_source": (
                        topic.get(
                            "evidence_source"
                        )
                    ),

                    "evidence_start": (
                        location.get("start")
                    ),

                    "evidence_end": (
                        location.get("end")
                    ),

                    "validation_warning": (
                        topic.get(
                            "validation_warning"
                        )
                    ),
                })


# =========================================================
# 5. 평가 실행
# =========================================================

def main():

    parser = argparse.ArgumentParser(
        description=(
            "LLM 모델별 토픽 추출 및 "
            "근거 인용 검증"
        )
    )

    parser.add_argument(
        "--models",
        nargs="+",
        default=[MODEL_NAME],
        help="비교할 모델 ID 목록",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="평가할 최대 콘텐츠 수",
    )

    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="API 호출 간격(초)",
    )

    parser.add_argument(
        "--new-only",
        action="store_true",
        help="기존 평가 작품 5개를 제외하고 신규 작품만 평가",
    ),
    

    args = parser.parse_args()

    if args.limit < 1:
        parser.error(
            "--limit은 1 이상이어야 합니다."
        )

    if args.delay < 0:
        parser.error(
            "--delay는 0 이상이어야 합니다."
        )

    contents, issues = load_test_contents()

    if args.new_only:
        previous_contents = {
            ("movie", "기생충"),
            ("movie", "살인의 추억"),
            ("movie", "명량"),
            ("movie", "1987"),
            ("tv", "미스터 션샤인"),
        }

        contents = [
            content
            for content in contents
            if (
                content["content_type"],
                content["title"],
            ) not in previous_contents
        ]

    contents = contents[:args.limit]

    run_id = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    output_dir = (
        Path(__file__).resolve().parent
        / "evaluation"
        / run_id
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    save_json(
        output_dir / "inputs.json",
        contents,
    )

    save_json(
        output_dir / "matching_issues.json",
        issues,
    )

    print("=" * 55)
    print("LLM Topic Extraction Evaluation")
    print(f"모델: {', '.join(args.models)}")
    print(f"콘텐츠: {len(contents)}개")
    print(f"프롬프트: {PROMPT_VERSION}")
    print(f"결과 폴더: {output_dir}")
    print("=" * 55)

    if issues:

        print(
            f"\n매칭 제외/확인 필요: "
            f"{len(issues)}개"
        )

        for issue in issues:

            print(
                f"  - {issue['title']}: "
                f"{issue['reason']}"
            )

    results = []

    total_calls = (
        len(contents) * len(args.models)
    )

    call_index = 0

    for content in contents:

        for model_name in args.models:

            call_index += 1

            print(
                f"\n[{call_index}/{total_calls}] "
                f"{content['title']} | "
                f"{model_name}"
            )

            try:

                result = evaluate_one(
                    content,
                    model_name,
                )

                print(
                    f"  성공 | "
                    f"토픽 {result['topic_count']}개 | "
                    f"{result['elapsed_seconds']}초"
                )

                valid_count = result[
                    "valid_quote_count"
                ]

                total_count = result[
                    "topic_count"
                ]

                print(
                    f"  인용 검증: "
                    f"{valid_count}/{total_count}"
                )

                for topic in result["topics"]:

                    status = (
                        "✓"
                        if topic["quote_valid"]
                        else "✗"
                    )

                    print(
                        f"    {status} "
                        f"{topic['name']} "
                        f"[{topic['type']}]"
                    )

                    if not topic["quote_valid"]:

                        print(
                            "      인용 오류: "
                            f"{topic['validation_warning']}"
                        )

                        print(
                            "      인용문: "
                            f"{topic['evidence_quote']}"
                        )

            except Exception as error:

                result = {
                    "content_id": (
                        content["content_id"]
                    ),

                    "content_type": (
                        content["content_type"]
                    ),

                    "title": content["title"],

                    "model_requested": model_name,
                    "model_returned": None,

                    "prompt_version": (
                        PROMPT_VERSION
                    ),

                    "elapsed_seconds": None,

                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,

                    "topic_count": 0,

                    "valid_quote_count": 0,
                    "invalid_quote_count": 0,

                    "quote_validation_rate": None,

                    "validation_warnings": {},

                    "topics": [],

                    "raw_response": None,

                    "status": "failed",

                    "error": repr(error),
                }

                print(
                    f"  실패 | {repr(error)}"
                )

            results.append(result)

            # 호출마다 결과 저장
            save_json(
                output_dir / "results.json",
                results,
            )

            save_comparison_csv(
                output_dir / "comparison.csv",
                results,
            )

            save_topics_csv(
                output_dir / "topics_detail.csv",
                results,
            )

            if (
                call_index < total_calls
                and args.delay > 0
            ):
                time.sleep(args.delay)

    # =====================================================
    # 최종 결과
    # =====================================================

    success_results = [
        result
        for result in results
        if result["status"] == "success"
    ]

    failed_results = [
        result
        for result in results
        if result["status"] == "failed"
    ]

    total_topics = sum(
        result["topic_count"]
        for result in success_results
    )

    total_valid_quotes = sum(
        result["valid_quote_count"]
        for result in success_results
    )

    total_invalid_quotes = sum(
        result["invalid_quote_count"]
        for result in success_results
    )

    overall_validation_rate = (
        total_valid_quotes / total_topics
        if total_topics > 0
        else None
    )

    print("\n" + "=" * 55)
    print("모델 비교 실험 완료")

    print(f"전체 호출: {len(results)}")
    print(f"성공: {len(success_results)}")
    print(f"실패: {len(failed_results)}")

    print(f"전체 토픽: {total_topics}")
    print(f"인용 검증 성공: {total_valid_quotes}")
    print(f"인용 검증 실패: {total_invalid_quotes}")

    if overall_validation_rate is not None:

        print(
            "전체 인용 검증률: "
            f"{overall_validation_rate:.1%}"
        )

    else:

        print(
            "전체 인용 검증률: N/A "
            "(생성된 토픽 없음)"
        )

    print(f"결과: {output_dir}")
    print("=" * 55)


if __name__ == "__main__":
    main()