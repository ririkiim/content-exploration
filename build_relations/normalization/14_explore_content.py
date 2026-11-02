import os
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


# =========================================================
# 1. 환경 설정
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL이 .env에 없습니다.")


# =========================================================
# 2. DB 연결
# =========================================================

def connect_db():
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        autocommit=True,
    )

    # 실수로라도 DB를 수정하지 않도록 읽기 전용 설정
    conn.execute("SET default_transaction_read_only = on")

    return conn


# =========================================================
# 3. 콘텐츠 기본 정보
# =========================================================

def get_content(conn, content_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                content_id,
                title,
                content_type
            FROM content
            WHERE content_id = %s
            """,
            (content_id,),
        )

        return cur.fetchone()


# =========================================================
# 4. 적재된 토픽 조회
# =========================================================

def get_stored_topics(conn, content_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT
                t.topic_name
            FROM content_topic ct
            JOIN topic t
                ON ct.topic_id = t.topic_id
            WHERE ct.content_id = %s
            ORDER BY t.topic_name
            """,
            (content_id,),
        )

        return [
            row["topic_name"]
            for row in cur.fetchall()
        ]


# =========================================================
# 5. 원본 키워드 추출
# =========================================================

def parse_keywords(raw_keywords):
    if raw_keywords is None:
        return []

    if isinstance(raw_keywords, str):
        try:
            raw_keywords = json.loads(raw_keywords)
        except json.JSONDecodeError:
            return []

    if not isinstance(raw_keywords, list):
        return []

    keywords = []

    for item in raw_keywords:
        if isinstance(item, str):
            name = item.strip()

        elif isinstance(item, dict):
            name = (
                item.get("name")
                or item.get("keyword")
                or ""
            ).strip()

        else:
            continue

        if name:
            keywords.append(name)

    return list(dict.fromkeys(keywords))


def get_original_keywords(conn, content_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT keywords
            FROM movie_detail
            WHERE content_id = %s

            UNION ALL

            SELECT keywords
            FROM tv_detail
            WHERE content_id = %s
            """,
            (content_id, content_id),
        )

        rows = cur.fetchall()

    keywords = []

    for row in rows:
        keywords.extend(
            parse_keywords(row["keywords"])
        )

    return list(dict.fromkeys(keywords))


# =========================================================
# 6. 탐색 가능한 토픽
# =========================================================

def get_exploration_topics(conn, content_id):
    stored = get_stored_topics(conn, content_id)
    original = get_original_keywords(conn, content_id)

    # 정규화된 토픽을 우선하고,
    # 적재되지 않은 콘텐츠는 원본 키워드 사용
    if stored:
        return stored, "content_topic"

    return original, "original_keywords"


# =========================================================
# 7. 토픽으로 관련 콘텐츠 검색
# =========================================================

SEARCH_SQL = """
WITH content_text AS (
    SELECT
        c.content_id,
        c.title,
        c.content_type,
        m.overview AS description,
        m.keywords::text AS keyword_text
    FROM content c
    JOIN movie_detail m
        ON c.content_id = m.content_id

    UNION ALL

    SELECT
        c.content_id,
        c.title,
        c.content_type,
        tv.overview AS description,
        tv.keywords::text AS keyword_text
    FROM content c
    JOIN tv_detail tv
        ON c.content_id = tv.content_id

    UNION ALL

    SELECT
        c.content_id,
        c.title,
        c.content_type,
        b.description,
        ''::text AS keyword_text
    FROM content c
    JOIN book_edition b
        ON c.content_id = b.content_id
),

matched AS (
    SELECT
        c.content_id,
        c.title,
        c.content_type,

        BOOL_OR(
            COALESCE(c.keyword_text, '')
            ILIKE '%%' || %s || '%%'
        ) AS keyword_match,

        BOOL_OR(
            COALESCE(c.description, '')
            ILIKE '%%' || %s || '%%'
        ) AS description_match,

        BOOL_OR(
            COALESCE(c.title, '')
            ILIKE '%%' || %s || '%%'
        ) AS title_match,

        MAX(
            (
                LENGTH(LOWER(COALESCE(c.description, '')))
                -
                LENGTH(
                    REPLACE(
                        LOWER(COALESCE(c.description, '')),
                        LOWER(%s),
                        ''
                    )
                )
            ) / NULLIF(LENGTH(%s), 0)
        ) AS description_count

    FROM content_text c

    WHERE c.content_id <> ALL(%s::bigint[])
      AND (
          COALESCE(c.keyword_text, '')
              ILIKE '%%' || %s || '%%'
          OR COALESCE(c.description, '')
              ILIKE '%%' || %s || '%%'
          OR COALESCE(c.title, '')
              ILIKE '%%' || %s || '%%'
      )

    GROUP BY
        c.content_id,
        c.title,
        c.content_type
)

SELECT
    content_id,
    title,
    content_type,
    keyword_match,
    description_match,
    title_match,
    description_count,

    (
        CASE WHEN keyword_match THEN 5 ELSE 0 END
        +
        CASE
            WHEN description_count >= 2 THEN 3
            WHEN description_match THEN 2
            ELSE 0
        END
        +
        CASE WHEN title_match THEN 2 ELSE 0 END
        +
        CASE
            WHEN keyword_match AND description_match THEN 2
            ELSE 0
        END
    ) AS match_score

FROM matched

ORDER BY
    match_score DESC,
    title,
    content_id

LIMIT %s;
"""



def search_contents(
    conn,
    topic,
    visited_ids,
    limit=10,
):
    with conn.cursor() as cur:
        cur.execute(
            SEARCH_SQL,
            (
                topic,
                topic,
                list(visited_ids),
                topic,
                topic,
                limit,
            ),
        )

        return cur.fetchall()


# =========================================================
# 8. 출력
# =========================================================

def show_topics(topics, source):
    print(f"\n[탐색 가능한 토픽: {len(topics)}개]")
    print(f"출처: {source}\n")

    for index, topic in enumerate(topics, start=1):
        print(f"{index:2}. {topic}")


def show_candidates(candidates):
    print(f"\n[검색된 콘텐츠 후보: {len(candidates)}개]\n")

    for index, row in enumerate(candidates, start=1):

        evidence = []

        if row["keyword_match"]:
            evidence.append("원본 키워드")

        if row["description_match"]:
            evidence.append("줄거리/설명")

        print(
            f"{index:2}. "
            f"{row['title']} "
            f"({row['content_type']}, "
            f"ID: {row['content_id']}) "
            f"| {row['match_score']}점 "
            f"| {', '.join(evidence)}"
        )


# =========================================================
# 9. 사용자 선택
# =========================================================

def choose_index(max_index, message):
    while True:
        value = input(message).strip()

        if value.lower() in {"q", "quit", "exit"}:
            return None

        if not value.isdigit():
            print("번호를 입력해 주세요. 종료하려면 q")
            continue

        index = int(value)

        if 1 <= index <= max_index:
            return index - 1

        print(f"1~{max_index} 사이의 번호를 입력해 주세요.")


# =========================================================
# 10. 반복 탐색
# =========================================================

def explore(conn, start_content_id=79246):

    current_id = start_content_id
    visited_ids = {start_content_id}

    path = []

    while True:

        content = get_content(conn, current_id)

        if not content:
            print(f"콘텐츠 {current_id}를 찾을 수 없습니다.")
            break

        print("\n" + "=" * 65)
        print(f"현재 콘텐츠: {content['title']}")
        print(f"ID: {content['content_id']}")
        print("=" * 65)

        # 현재 콘텐츠의 토픽 조회
        topics, source = get_exploration_topics(
            conn,
            current_id,
        )

        if not topics:
            print("탐색 가능한 토픽이 없습니다.")
            break

        show_topics(topics, source)

        topic_index = choose_index(
            len(topics),
            "\n탐색할 토픽 번호 (q: 종료): ",
        )

        if topic_index is None:
            break

        selected_topic = topics[topic_index]

        print(f"\n선택한 토픽: {selected_topic}")
        print("관련 콘텐츠 검색 중...")

        candidates = search_contents(
            conn,
            selected_topic,
            visited_ids,
            limit=10,
        )

        if not candidates:
            print("검색된 콘텐츠가 없습니다.")
            continue

        show_candidates(candidates)

        candidate_index = choose_index(
            len(candidates),
            "\n이동할 콘텐츠 번호 (q: 종료): ",
        )

        if candidate_index is None:
            break

        selected = candidates[candidate_index]

        # 탐색 경로 저장 (메모리에서만 관리)
        path.append({
            "from_content_id": current_id,
            "topic": selected_topic,
            "to_content_id": selected["content_id"],
            "to_title": selected["title"],
            "score": selected["match_score"],
        })

        current_id = selected["content_id"]
        visited_ids.add(current_id)

        print("\n[현재 탐색 경로]")

        for step in path:
            print(
                f"  {step['from_content_id']}"
                f" → {step['topic']}"
                f" → {step['to_title']}"
                f" ({step['to_content_id']})"
            )

    print("\n탐색 종료")


# =========================================================
# 11. 실행
# =========================================================

if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--content-id",
        type=int,
        default=79246,
        help="시작 콘텐츠 ID",
    )

    args = parser.parse_args()

    with connect_db() as conn:
        explore(
            conn,
            start_content_id=args.content_id,
        )