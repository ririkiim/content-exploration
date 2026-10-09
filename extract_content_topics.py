
import json
import os
import re

import psycopg
import requests
from dotenv import load_dotenv


# =========================================================
# 1. 환경 설정
# =========================================================

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]
SOMSOM_API_KEY = os.environ["SOMSOM_API_KEY"]

API_URL = (
    "https://factchat-cloud.mindlogic.ai/v1/gateway/"
    "chatbots/54857/chat/completions"
)

# 프롬프트를 크게 변경하면 버전을 바꿔 재처리할 수 있음
PROMPT_VERSION = "topic-v1"

# .env에 SOMSOM_MODEL_NAME을 설정하면 모델명을 기록
MODEL_NAME = os.getenv("SOMSOM_MODEL_NAME")

HEADERS = {
    "x-api-key": SOMSOM_API_KEY,
    "Content-Type": "application/json",
}


# =========================================================
# 2. 콘텐츠 조회 SQL
# =========================================================

SELECT_CONTENTS_SQL = """
SELECT
    c.content_id,
    c.content_type,
    c.title,
    c.description,

    CASE
        WHEN c.content_type = 'movie' THEN m.genre
        WHEN c.content_type = 'tv' THEN t.genre
        ELSE NULL
    END AS genre,

    CASE
        WHEN c.content_type = 'movie' THEN m.keywords
        WHEN c.content_type = 'tv' THEN t.keywords
        ELSE NULL
    END AS keywords,

    b.goods_sort_nm,
    b.bookintroduction

FROM content AS c

LEFT JOIN movie AS m
    ON c.content_id = m.content_id

LEFT JOIN tv AS t
    ON c.content_id = t.content_id

LEFT JOIN book AS b
    ON c.content_id = b.content_id

LEFT JOIN content_topic_extraction_status AS s
    ON c.content_id = s.content_id

WHERE c.content_type IN ('movie', 'tv', 'book')
  AND (
      s.content_id IS NULL
      OR s.status <> 'success'
      OR s.prompt_version IS DISTINCT FROM %s
  )

ORDER BY c.content_id;
"""


# =========================================================
# 3. 처리 상태 SQL
# =========================================================

START_PROCESSING_SQL = """
INSERT INTO content_topic_extraction_status (
    content_id,
    status,
    attempt_count,
    topic_count,
    last_error,
    prompt_version,
    model_name,
    started_at,
    completed_at,
    updated_at
)
VALUES (
    %s,
    'processing',
    1,
    0,
    NULL,
    %s,
    %s,
    NOW(),
    NULL,
    NOW()
)
ON CONFLICT (content_id)
DO UPDATE SET
    status = 'processing',
    attempt_count =
        content_topic_extraction_status.attempt_count + 1,
    last_error = NULL,
    prompt_version = EXCLUDED.prompt_version,
    model_name = EXCLUDED.model_name,
    started_at = NOW(),
    completed_at = NULL,
    updated_at = NOW();
"""


FAIL_PROCESSING_SQL = """
UPDATE content_topic_extraction_status
SET
    status = 'failed',
    last_error = %s,
    completed_at = NOW(),
    updated_at = NOW()
WHERE content_id = %s;
"""


SUCCESS_PROCESSING_SQL = """
UPDATE content_topic_extraction_status
SET
    status = 'success',
    topic_count = %s,
    last_error = NULL,
    model_name = %s,
    completed_at = NOW(),
    updated_at = NOW()
WHERE content_id = %s;
"""


# =========================================================
# 4. 주제 저장 SQL
# =========================================================

INSERT_TOPIC_SQL = """
INSERT INTO topic (
    topic_name
)
VALUES (%s)
ON CONFLICT (topic_name) DO NOTHING;
"""


SELECT_TOPIC_ID_SQL = """
SELECT topic_id
FROM topic
WHERE topic_name = %s;
"""


INSERT_CONTENT_TOPIC_SQL = """
INSERT INTO content_topic (
    content_id,
    topic_id
)
VALUES (%s, %s)
ON CONFLICT (content_id, topic_id) DO NOTHING;
"""


# =========================================================
# 5. LLM 입력 구성
# =========================================================

def build_prompt(content):
    """
    콘텐츠 정보를 솜솜AI 챗봇에 전달할 프롬프트로 구성한다.
    """

    return f"""
다음 콘텐츠의 주제를 추출해 주세요.

입력 데이터:
{json.dumps(content, ensure_ascii=False, indent=2)}

주제 추출 기준:
1. 콘텐츠의 장르와 작품의 주제를 구분하세요.
2. 제목, 설명, 장르, 키워드, 도서 소개글에 근거해 판단하세요.
3. 근거가 부족한 주제를 추측해서 만들지 마세요.
4. 인물 이름, 창작자 이름, 작품 제목 자체를 주제로 만들지 마세요.
5. 의미가 지나치게 일반적인 주제는 피하세요.
6. 근거가 충분하면 3~8개를 추출하고,
   부족하면 더 적게 추출하거나 빈 배열을 반환하세요.
7. 입력에 없는 사실을 만들어내지 마세요.
8. content_id는 입력값을 그대로 사용하세요.

응답은 JSON 객체만 반환하세요.
마크다운 코드 블록이나 부가 설명은 넣지 마세요.

응답 형식:
{{
  "content_id": {content["content_id"]},
  "topics": [
    {{
      "name": "주제 이름",
      "type": "subject",
      "evidence": "주제를 판단한 근거",
      "evidence_level": "explicit",
      "confidence": "high"
    }}
  ]
}}

type은 다음 중 하나를 사용하세요.
- subject
- theme
- social_issue
- human_experience

evidence_level은 explicit 또는 inferred,
confidence는 high, medium, low 중 하나를 사용하세요.

근거가 부족하면 topics를 빈 배열로 반환하세요.
"""


# =========================================================
# 6. 응답 JSON 처리
# =========================================================

def parse_response(answer, expected_content_id):
    """
    LLM 응답을 JSON으로 변환하고 기본 구조를 검증한다.
    """

    answer = answer.strip()

    # 혹시 응답이 마크다운 코드 블록으로 감싸진 경우 처리
    answer = re.sub(
        r"^```(?:json)?\s*",
        "",
        answer,
        flags=re.IGNORECASE,
    )
    answer = re.sub(r"\s*```$", "", answer)

    result = json.loads(answer)

    if not isinstance(result, dict):
        raise ValueError("응답이 JSON 객체가 아닙니다.")

    if result.get("content_id") != expected_content_id:
        raise ValueError(
            f"content_id 불일치: "
            f"기대값={expected_content_id}, "
            f"응답값={result.get('content_id')}"
        )

    topics = result.get("topics")

    if not isinstance(topics, list):
        raise ValueError("topics가 리스트가 아닙니다.")

    validated_topics = []
    seen_names = set()

    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError("topics 안에 객체가 아닌 항목이 있습니다.")

        name = topic.get("name")

        if not isinstance(name, str) or not name.strip():
            raise ValueError("주제 이름이 비어 있거나 문자열이 아닙니다.")

        name = name.strip()

        if name not in seen_names:
            validated_topics.append(topic)
            seen_names.add(name)

    return validated_topics


# =========================================================
# 7. 솜솜AI API 호출
# =========================================================

def extract_topics_from_api(content):
    """
    콘텐츠 한 건을 솜솜AI에 전달하고 주제 목록을 반환한다.
    """

    prompt = build_prompt(content)

    response = requests.post(
        API_URL,
        headers=HEADERS,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            "stream": False,
        },
        timeout=120,
    )

    if not response.ok:
        raise RuntimeError(
            f"HTTP {response.status_code}: "
            f"{response.text[:1000]}"
        )

    result = response.json()

    answer = result["choices"][0]["message"]["content"]

    topics = parse_response(
        answer,
        expected_content_id=content["content_id"],
    )

    actual_model = result.get("model") or MODEL_NAME

    return topics, actual_model


# =========================================================
# 8. 주제 및 연결 관계 저장
# =========================================================

def save_topics(cursor, content_id, topics):
    """
    주제가 이미 있으면 재사용하고,
    없으면 생성한 뒤 콘텐츠와 연결한다.
    """

    saved_count = 0

    for topic in topics:
        topic_name = topic["name"].strip()

        # 기존 주제가 없다면 신규 생성
        cursor.execute(
            INSERT_TOPIC_SQL,
            (topic_name,),
        )

        # 기존 또는 신규 주제의 topic_id 조회
        cursor.execute(
            SELECT_TOPIC_ID_SQL,
            (topic_name,),
        )

        row = cursor.fetchone()

        if row is None:
            raise RuntimeError(
                f"topic_id 조회 실패: {topic_name}"
            )

        topic_id = row[0]

        # 콘텐츠와 주제 연결
        cursor.execute(
            INSERT_CONTENT_TOPIC_SQL,
            (content_id, topic_id),
        )

        saved_count += 1

    return saved_count


# =========================================================
# 9. 전체 콘텐츠 주제 추출
# =========================================================

def extract_content_topics():
    success_count = 0
    failed_count = 0
    total_topics = 0

    with psycopg.connect(DATABASE_URL) as connection:

        # 대상 콘텐츠 조회
        with connection.cursor() as cursor:
            cursor.execute(
                SELECT_CONTENTS_SQL,
                (PROMPT_VERSION,),
            )
            contents = cursor.fetchall()

        total_count = len(contents)

        print(f"처리 대상 콘텐츠: {total_count}개")

        for row in contents:
            (
                content_id,
                content_type,
                title,
                description,
                genre,
                keywords,
                goods_sort_nm,
                bookintroduction,
            ) = row

            content = {
                "content_id": content_id,
                "content_type": content_type,
                "title": title,
                "description": description,
                "genre": genre,
                "keywords": keywords,
                "goods_sort_nm": goods_sort_nm,
                "bookintroduction": bookintroduction,
            }

            print(
                f"\n[{success_count + failed_count + 1}"
                f"/{total_count}] "
                f"{content_type} | {title}"
            )

            try:
                # 처리 시작 상태 기록
                with connection.cursor() as cursor:
                    cursor.execute(
                        START_PROCESSING_SQL,
                        (
                            content_id,
                            PROMPT_VERSION,
                            MODEL_NAME,
                        ),
                    )

                # API 호출 전에 상태를 별도로 커밋
                connection.commit()

                # 솜솜AI API 호출
                topics, actual_model = extract_topics_from_api(
                    content
                )

                # 주제와 연결 관계, 성공 상태를 함께 저장
                with connection.cursor() as cursor:
                    saved_count = save_topics(
                        cursor,
                        content_id,
                        topics,
                    )

                    cursor.execute(
                        SUCCESS_PROCESSING_SQL,
                        (
                            len(topics),
                            actual_model,
                            content_id,
                        ),
                    )

                # 콘텐츠 한 건의 DB 작업 확정
                connection.commit()

                success_count += 1
                total_topics += saved_count

                print(
                    f"  성공: 주제 {saved_count}개 저장"
                )

            except Exception as error:
                # 해당 콘텐츠의 주제 저장 작업 롤백
                connection.rollback()

                error_message = str(error)[:4000]

                # 실패 상태를 기록하고 다음 콘텐츠 진행
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            FAIL_PROCESSING_SQL,
                            (
                                error_message,
                                content_id,
                            ),
                        )

                    connection.commit()

                except Exception as status_error:
                    connection.rollback()

                    print(
                        "  경고: 실패 상태 저장도 실패했습니다. "
                        f"{status_error}"
                    )

                failed_count += 1

                print(f"  실패: {error_message}")

    print("\n" + "=" * 45)
    print("주제 추출 작업 완료")
    print(f"처리 대상: {total_count}개")
    print(f"성공: {success_count}개")
    print(f"실패: {failed_count}개")
    print(f"저장한 주제 연결 수: {total_topics}개")
    print("=" * 45)


if __name__ == "__main__":
    extract_content_topics()