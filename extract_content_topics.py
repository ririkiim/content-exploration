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

# 프롬프트 변경 시 버전을 바꿔 재처리할 수 있음
PROMPT_VERSION = "topic-v2"

# .env에 SOMSOM_MODEL_NAME을 설정하면 모델명을 기록
MODEL_NAME = os.getenv("SOMSOM_MODEL_NAME")

HEADERS = {
    "x-api-key": SOMSOM_API_KEY,
    "Content-Type": "application/json",
}

VALID_TOPIC_TYPES = {
    "subject",
    "theme",
    "social_issue",
    "human_experience",
}

VALID_EVIDENCE_LEVELS = {"explicit", "inferred"}
VALID_CONFIDENCE_LEVELS = {"high", "medium", "low"}


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
    last_error,
    prompt_version,
    updated_at
)
VALUES (
    %s,
    'processing',
    1,
    NULL,
    %s,
    NOW()
)
ON CONFLICT (content_id)
DO UPDATE SET
    status = 'processing',
    attempt_count =
        content_topic_extraction_status.attempt_count + 1,
    last_error = NULL,
    prompt_version = EXCLUDED.prompt_version,
    updated_at = NOW();
"""

FAIL_PROCESSING_SQL = """
UPDATE content_topic_extraction_status
SET
    status = 'failed',
    last_error = %s,
    updated_at = NOW()
WHERE content_id = %s;
"""

SUCCESS_PROCESSING_SQL = """
UPDATE content_topic_extraction_status
SET
    status = 'success',
    last_error = NULL,
    updated_at = NOW()
WHERE content_id = %s;
"""


# =========================================================
# 4. 토픽 저장 SQL
# =========================================================


INSERT_TOPIC_SQL = """
INSERT INTO topic (
    topic_name,
    topic_description
)
VALUES (%s, %s)
ON CONFLICT (topic_name)
DO UPDATE SET
    topic_description = EXCLUDED.topic_description
WHERE
    topic.topic_description IS NULL
    OR BTRIM(topic.topic_description) = '';
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
    콘텐츠 정보를 바탕으로 개선된 토픽 추출 프롬프트를 구성한다.
    """

    content_json = json.dumps(
        content,
        ensure_ascii=False,
        indent=2,
    )

    prompt_template = """
# 1. 역할과 목적

너는 영화·드라마·도서의 핵심 주제를 추출하는 콘텐츠 의미 분석 전문가다.

주어진 콘텐츠 정보를 바탕으로 핵심 소재, 주제, 사회적 쟁점,
인간 경험을 나타내는 토픽을 추출하라.

추출된 토픽은 관계 기반 다중 콘텐츠 탐색 시스템에서
서로 다른 콘텐츠를 연결하고, 후속 LLM이 관계 확장 키워드를
생성하는 데 사용된다.

토픽은 콘텐츠의 핵심 의미를 정확하게 반영하고,
다른 콘텐츠에도 적용할 수 있는 재사용성을 가져야 한다.
지나치게 포괄적이거나 지나치게 세부적인 표현을 피하되,
콘텐츠 고유의 역사적·사회적 맥락과 독창적인 주제는 보존한다.
의미가 중복되는 토픽을 불필요하게 여러 개 생성하지 않는다.
토픽 수나 연결성을 높이기 위해 근거 없는 개념을 추가하지 않는다.

# 2. 사용할 수 있는 정보

다음 입력 필드만 근거로 사용하라.
- content_id
- content_type
- title
- description
- genre
- keywords
- goods_sort_nm
- bookintroduction

필드가 없거나 비어 있으면 해당 정보를 사용하지 않는다.
제목만으로 내용을 임의 추측하지 말고, 제공된 정보에서 확인할 수
있는 범위 안에서 판단하라. 외부 지식이나 일반적인 작품 해석을
임의로 보충하지 않는다.

서로 다른 필드의 정보를 종합할 수 있지만, 정보가 상충하거나
근거가 불충분하면 확정적인 토픽을 생성하지 않는다.

# 3. 토픽 유형

각 토픽은 다음 네 가지 유형 중 하나로 분류한다.

1. subject: 핵심 소재, 대상 또는 탐구 분야
   예: 범죄 수사, 감염병, 여성 국극, 노동과 빈곤

2. theme: 핵심 가치, 갈등, 관점 또는 의미
   예: 정체성 탐색, 희생과 책임, 자유와 억압

3. social_issue: 사회적·역사적·제도적 쟁점
   예: 사회적 불평등, 군대 내 가혹행위, 식민 지배, 여성 차별

4. human_experience: 인간관계, 감정, 변화 또는 삶의 경험
   예: 가족의 상실, 청소년의 성장, 신뢰의 붕괴, 진로 선택과 갈등

하나의 토픽에는 가장 적합한 유형 하나만 부여한다.
동일한 개념을 유형만 바꾸어 중복 생성하지 않는다.

# 4. 토픽 선정 원칙

## 4-1. 핵심 의미와 탐색 가치

콘텐츠의 주요 소재, 중심 갈등, 핵심 주제, 중요한 사회적 맥락
또는 의미 있는 인간 경험을 추출한다.

다음 기준을 고려하라.
- 콘텐츠의 핵심 의미를 설명하는가?
- 다른 콘텐츠에서도 의미 있게 활용할 수 있는가?
- 후속 관계 확장 키워드 생성에 유용한 출발점인가?
- 입력 정보에서 해당 토픽의 근거를 확인할 수 있는가?

모든 조건을 기계적으로 충족해야 하는 것은 아니지만,
근거와 핵심 의미를 우선한다.

## 4-2. 적절한 추상화 수준

토픽은 재사용 가능한 개념으로 표현하되, 콘텐츠의 고유한 의미를
잃을 정도로 일반화하지 않는다.

- 지나치게 구체적인 사건이나 장면은 더 넓고 재사용 가능한
  개념으로 추상화할 수 있는지 검토한다.
- 특정 인물의 행동이나 일회성 상황을 그대로 토픽으로 만들지 않는다.
- 특정 역사적 사건, 시대적 맥락, 독특한 사회문제처럼 구체성이
  핵심 의미인 경우에는 이를 보존한다.
- 서로 다른 의미를 단순히 비슷하다는 이유로 하나의 넓은
  토픽에 합치지 않는다.

예시:
- '열차 안의 폐쇄 공간'은 공간 자체가 핵심 주제가 아니라면
  '재난 속 생존' 등 더 의미 있는 개념을 검토한다.
- '부유층 가정으로의 침투'가 구체적인 줄거리 묘사에 그친다면
  '계층 격차', '계급 관계' 등으로 추상화할 수 있는지 검토한다.
- '1970년대 한국 사회'는 시대적 맥락 자체가 중요하다면 유지한다.
- '광주의 역사적 상처'는 역사적 사건과 집단적 기억의 의미가
  중요하므로 지나치게 일반적인 표현으로 대체하지 않는다.

위 예시는 판단 방향을 설명하기 위한 것이며,
예시의 토픽을 실제 결과에 무조건 복사하지 않는다.

## 4-3. 의미 중복 제거

표현이 다르더라도 핵심 의미가 사실상 같고 탐색상 구별할
실익이 없다면 하나의 대표 토픽으로 통합한다.

예시:
- '정체성 탐색'과 '자아 찾기'는 의미가 동일하다면 하나로 통합한다.
- '가족을 위한 희생'과 '가족에 대한 헌신'은 해당 콘텐츠에서
  의미가 동일하다면 통합한다.
- '정체성 탐색'과 '정체성 혼란'은 관련은 있지만 의미가 다르므로
  별도 근거가 있다면 유지한다.
- '사회적 불평등'과 '여성 차별'은 각각 중요한 의미를 가지면 유지한다.
- '재난 속 생존'과 '감염병의 확산과 대응'은 생존과 재난 대응이라는
  독립적인 초점이 있다면 유지한다.

중복 여부는 단어의 유사성이 아니라 의미, 초점,
탐색상 구별 필요성을 기준으로 판단한다.
유사한 개념이라고 무조건 통합하지 않는다.

## 4-4. 토픽 이름 작성

토픽 이름은 한국어 명사 또는 간결한 명사구로 작성한다.

- 가능하면 공백을 포함해 2~15자 내외로 작성한다.
- 일반적으로 20자를 초과하지 않는다.
- 길이보다 의미의 정확성이 중요하므로 필요한 경우 예외를 허용한다.
- 여러 개념을 불필요하게 'A와 B', 'A 및 B',
  'A와 B의 갈등' 형태로 결합하지 않는다.
- 두 개념의 관계 자체가 핵심 주제라면 관계를 표현하는
  명사구를 허용한다.
- 구별에 필요하지 않은 수식어는 제거한다.
- 의미가 불분명한 추상어, 과도한 문학적 표현, 단순한 감상평은 피한다.
- 같은 개념에는 가능한 한 일관된 대표 표현을 사용한다.

예를 들어 '과학수사와 직관적 수사의 대립'은 핵심이 수사 방식의
충돌이라면 '수사 방식의 갈등'으로 표현할 수 있다.
단, 과학수사 자체가 중요한 독립적 소재라면 '과학수사'를
별도로 추출할 수 있다.

## 4-5. 장르와 토픽 구분

장르 자체는 원칙적으로 토픽으로 추출하지 않는다.
예: 액션, 로맨스, 스릴러, 드라마, 판타지, 역사극, 성장물.

장르와 관련된 소재나 주제는 추출할 수 있다.
- '좀비'는 핵심 소재라면 subject로 추출할 수 있다.
- '좀비 역병'은 감염과 사회적 재난이 핵심이면 추출할 수 있다.
- '사랑과 국가 의무의 갈등'은 실제 주제이므로 추출할 수 있다.

genre 필드는 참고 자료로 활용하되 장르명을 그대로 토픽 목록에
복사하지 않는다.

## 4-6. 인물, 창작자, 작품명 및 줄거리 제한

다음 항목은 원칙적으로 토픽으로 생성하지 않는다.
- 등장인물의 이름
- 배우, 감독, 작가 등 창작자의 이름
- 콘텐츠 제목 또는 제목의 단순 변형
- 특정 인물이 특정 장소에서 특정 행동을 하는 장면의 단순 묘사
- 사건을 시간순으로 나열한 줄거리 요약
- 콘텐츠의 품질이나 재미에 대한 평가
- 입력 정보에서 확인할 수 없는 설정, 사건, 주제

인물이나 사건이 드러내는 일반화 가능한 주제는 추출할 수 있다.
예를 들어 특정 인물의 이름 대신 '군사적 리더십',
특정 장면 대신 '권력 남용', 사건의 단순 묘사 대신
'진실 은폐'로 표현할 수 있다.
단, 해당 개념이 입력 정보로 뒷받침되어야 한다.

## 4-7. 과도한 일반화와 세분화 방지

다음 두 가지 오류를 모두 피한다.

- 과도한 일반화: '인간', '사회', '삶', '갈등', '감정', '관계'처럼
  연결 범위가 지나치게 넓어 탐색 가치가 낮은 토픽
- 과도한 세분화: 특정 콘텐츠의 한 상황에만 적용되고
  다른 콘텐츠와 연결할 의미가 거의 없는 장황한 토픽

단, '가족의 상실', '사회적 낙인', '전쟁과 분단의 여파'처럼
구체적인 의미가 있는 개념은 유지할 수 있다.

## 4-8. 근거와 불확실성

각 토픽은 입력 정보에 근거해야 한다.

evidence_level:
- explicit: 입력 정보에 토픽의 핵심 내용이 명시적으로 나타남
- inferred: 직접 명시되어 있지는 않지만 제공된 설명에서
  합리적으로 도출할 수 있음

confidence:
- high: 입력 정보가 토픽을 명확하고 직접적으로 뒷받침함
- medium: 합리적으로 추론할 수 있지만 해석의 여지가 있음
- low: 근거가 약하거나 여러 해석이 가능함

low 수준의 토픽은 원칙적으로 출력하지 않는다.
단, 콘텐츠의 핵심 의미일 가능성이 높고 제공된 정보로 제한적인
판단이 가능한 경우에만 예외적으로 포함할 수 있다.
근거가 부족한 토픽을 개수 충족을 위해 추가하지 않는다.

# 5. 토픽 개수

일반적으로 콘텐츠당 3~8개의 토픽을 추출한다.
이는 목표 범위이지 반드시 채워야 하는 할당량이 아니다.

- 핵심 의미가 풍부하면 8개까지 추출할 수 있다.
- 구별되는 의미가 충분하지 않으면 3개 미만이어도 된다.
- 중복 토픽을 개수에 맞추어 추가하지 않는다.
- 입력 정보가 부족하면 빈 배열을 반환한다.
- 하나의 개념을 여러 표현으로 나누어 개수를 늘리지 않는다.

# 6. 근거(evidence) 작성

각 토픽을 뒷받침하는 입력 정보의 근거를 작성한다.

- 가능하면 10~50자 내외의 짧은 구절로 작성한다.
- 입력 필드에 실제로 포함된 표현을 우선 사용한다.
- 여러 필드의 정보를 종합한 경우 간결하게 요약할 수 있다.
- 입력에 없는 문장이나 사건을 직접 인용한 것처럼 작성하지 않는다.
- 토픽 이름을 반복하는 것만으로 근거를 대신하지 않는다.
- 추론된 토픽은 어떤 입력 정보에서 도출했는지 드러나게 작성한다.
- 근거는 해당 토픽을 뒷받침해야 한다.

# 7. 출력 형식

반드시 유효한 JSON 객체 하나만 출력한다.
마크다운 코드 블록, 설명, 주석, 인사말 등 JSON 외부의 텍스트를
출력하지 않는다.

출력 형식:
{
  "content_id": 123,
  "topics": [
    {
      "name": "사회적 불평등",
      "type": "social_issue",
      "evidence": "빈곤과 계층 격차를 중심으로 전개되는 내용",
      "evidence_level": "explicit",
      "confidence": "high"
    }
  ]
}

위 예시는 형식 설명용이며 실제 결과에 복사하지 않는다.

필드 규칙:
- content_id: 입력받은 콘텐츠 ID를 변경 없이 반환한다.
- topics: 토픽 객체의 배열이다.
- name: 간결한 대표 토픽명이다.
- type: subject, theme, social_issue, human_experience 중 하나다.
- evidence: 토픽을 뒷받침하는 입력 정보다.
- evidence_level: explicit 또는 inferred 중 하나다.
- confidence: high, medium, low 중 하나다.

각 필드는 지정된 이름과 자료형을 사용한다.
문자열은 큰따옴표로 감싸고 JSON 문법을 정확히 지킨다.
후행 쉼표를 사용하지 않는다.
토픽이 없는 경우 topics는 null이 아닌 빈 배열 []로 반환한다.

content_id가 입력에 존재하지 않으면 다음 객체만 반환한다.
{"error": "missing_content_id"}

# 8. 최종 검토

출력 전에 다음 사항을 내부적으로 점검한다.
점검 과정은 출력하지 않는다.

1. 모든 토픽이 입력 정보로 뒷받침되는가?
2. 장르, 인물명, 창작자명, 작품명, 단순 줄거리를
   토픽으로 잘못 추출하지 않았는가?
3. 의미가 같은 토픽이 다른 이름으로 중복되어 있지 않은가?
4. 관련은 있지만 구별되는 개념을 잘못 통합하지 않았는가?
5. 긴 줄거리형 표현을 재사용 가능한 개념으로 간결하게 만들 수 있는가?
6. 지나치게 일반화하여 고유한 의미를 잃은 토픽은 없는가?
7. 관계 탐색에 활용할 수 있는 의미 있는 개념인가?
8. 근거가 약하거나 개수 충족을 위해 추가한 토픽은 없는가?
9. type, evidence_level, confidence가 허용된 값인가?
10. 유효한 JSON 객체 하나만 출력하는가?

위 기준을 모두 고려하여 최종 토픽을 출력하라.

# 입력 데이터

__INPUT_JSON__
"""

    return prompt_template.replace("__INPUT_JSON__", content_json)


# =========================================================
# 6. 응답 JSON 처리 및 검증
# =========================================================

def parse_response(answer, expected_content_id):
    """
    LLM 응답을 JSON으로 변환하고 구조와 필수 필드를 검증한다.
    """

    answer = answer.strip()

    # 마크다운 코드 블록으로 감싼 응답 처리
    answer = re.sub(
        r"^\s*```(?:json)?\s*",
        "",
        answer,
        flags=re.IGNORECASE,
    )
    answer = re.sub(r"\s*```\s*$", "", answer)

    result = json.loads(answer)

    if not isinstance(result, dict):
        raise ValueError("응답이 JSON 객체가 아닙니다.")

    if result.get("error") == "missing_content_id":
        raise ValueError("LLM 응답에 content_id가 없습니다.")

    if result.get("content_id") != expected_content_id:
        raise ValueError(
            f"content_id 불일치: 기대값={expected_content_id}, "
            f"응답값={result.get('content_id')}"
        )

    topics = result.get("topics")
    if not isinstance(topics, list):
        raise ValueError("topics가 리스트가 아닙니다.")

    validated_topics = []
    seen_names = set()

    required_fields = {
        "name",
        "type",
        "evidence",
        "evidence_level",
        "confidence",
    }

    for index, topic in enumerate(topics, start=1):
        if not isinstance(topic, dict):
            raise ValueError(
                f"topics의 {index}번째 항목이 객체가 아닙니다."
            )

        missing_fields = required_fields - topic.keys()
        if missing_fields:
            raise ValueError(
                f"topics의 {index}번째 항목에 필수 필드가 없습니다: "
                f"{sorted(missing_fields)}"
            )

        name = topic["name"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"topics의 {index}번째 토픽 이름이 비어 있습니다."
            )

        # 앞뒤 공백 제거 및 연속 공백 정리
        name = re.sub(r"\s+", " ", name.strip())

        # 표면형이 동일한 중복 토픽만 제거
        if name in seen_names:
            continue

        topic_type = topic["type"]
        if (
            not isinstance(topic_type, str)
            or topic_type not in VALID_TOPIC_TYPES
        ):
            raise ValueError(
                f"허용되지 않은 topic type: {topic_type}"
            )

        evidence = topic["evidence"]
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(
                f"'{name}' 토픽의 evidence가 비어 있거나 "
                "문자열이 아닙니다."
            )
        evidence = evidence.strip()

        evidence_level = topic["evidence_level"]
        if (
            not isinstance(evidence_level, str)
            or evidence_level not in VALID_EVIDENCE_LEVELS
        ):
            raise ValueError(
                f"허용되지 않은 evidence_level: {evidence_level}"
            )

        confidence = topic["confidence"]
        if (
            not isinstance(confidence, str)
            or confidence not in VALID_CONFIDENCE_LEVELS
        ):
            raise ValueError(
                f"허용되지 않은 confidence: {confidence}"
            )

        validated_topics.append(
            {
                "name": name,
                "type": topic_type,
                "evidence": evidence,
                "evidence_level": evidence_level,
                "confidence": confidence,
            }
        )
        seen_names.add(name)

    return validated_topics


# =========================================================
# 7. 솜솜AI API 호출
# =========================================================

def extract_topics_from_api(content):
    """
    콘텐츠 한 건을 솜솜AI에 전달하고 토픽 목록을 반환한다.
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
            f"HTTP {response.status_code}: {response.text[:1000]}"
        )

    result = response.json()

    try:
        answer = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(
            f"API 응답에서 모델 답변을 찾을 수 없습니다: {error}"
        ) from error

    topics = parse_response(
        answer,
        expected_content_id=content["content_id"],
    )

    actual_model = result.get("model") or MODEL_NAME
    return topics, actual_model


# =========================================================
# 8. 토픽 및 연결 관계 저장
# =========================================================


def save_topics(cursor, content_id, topics):
    """
    토픽을 생성하거나 재사용하고,
    토픽의 근거를 topic_description에 저장한 뒤
    콘텐츠와 토픽을 연결한다.
    """

    saved_count = 0

    for topic in topics:
        topic_name = topic["name"].strip()

        topic_description = topic.get("evidence")
        if isinstance(topic_description, str):
            topic_description = topic_description.strip() or None
        else:
            topic_description = None

        # 토픽 생성 또는 기존 토픽 재사용
        # 기존 설명이 비어 있을 때만 evidence로 채움
        cursor.execute(
            INSERT_TOPIC_SQL,
            (topic_name, topic_description),
        )

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

        # 콘텐츠와 토픽 연결
        cursor.execute(
            INSERT_CONTENT_TOPIC_SQL,
            (content_id, topic_id),
        )

        saved_count += 1

    return saved_count



# =========================================================
# 9. 전체 콘텐츠 토픽 추출
# =========================================================

def extract_content_topics():
    success_count = 0
    failed_count = 0
    total_topics = 0
    total_count = 0

    with psycopg.connect(DATABASE_URL) as connection:

        # 대상 콘텐츠 조회
        with connection.cursor() as cursor:
            cursor.execute(SELECT_CONTENTS_SQL, (PROMPT_VERSION,))
            contents = cursor.fetchall()

        total_count = len(contents)

        print(f"처리 대상 콘텐츠: {total_count}개")
        print(f"프롬프트 버전: {PROMPT_VERSION}")

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
                f"\n[{success_count + failed_count + 1}/{total_count}] "
                f"{content_type} | {title}"
            )

            try:
                # 처리 시작 상태 기록
                with connection.cursor() as cursor:
                    cursor.execute(
                        START_PROCESSING_SQL,
                        (content_id, PROMPT_VERSION),
                    )

                # 처리 시작 상태를 별도로 커밋
                connection.commit()

                # 솜솜AI API 호출
                topics, actual_model = extract_topics_from_api(content)
                
                # 새로운 주제 연결 및 성공 상태를 저장
                with connection.cursor() as cursor:

                    # 1. 이번에 추출한 토픽 연결
                    saved_count = save_topics(
                        cursor,
                        content_id,
                        topics,
                    )

                    # 2. 추출 성공 상태 기록
                    cursor.execute(
                        SUCCESS_PROCESSING_SQL,
                        (content_id,),
                    )

                # 저장·성공 상태를 한 번에 확정
                connection.commit()


                success_count += 1
                total_topics += saved_count

                print(f"  성공: 토픽 {saved_count}개 저장")

            except Exception as error:
                connection.rollback()
                error_message = str(error)[:4000]

                # 실패 상태 기록 후 다음 콘텐츠 진행
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            FAIL_PROCESSING_SQL,
                            (error_message, content_id),
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
    print("토픽 추출 작업 완료")
    print(f"처리 대상: {total_count}개")
    print(f"성공: {success_count}개")
    print(f"실패: {failed_count}개")
    print(f"저장한 토픽 연결 수: {total_topics}개")
    print("=" * 45)


if __name__ == "__main__":
    extract_content_topics()
