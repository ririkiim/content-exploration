
import os
from typing import Any

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


# =====================================================
# 1. 환경 설정
# =====================================================

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

VALID_CONTENT_TYPES = {"movie", "tv", "book"}

VALID_PREFERENCE_TYPES = {
    "content_type",
    "genre",
    "topic",
    "keyword",
    "entity_type",
    "exploration_style",
}

VALID_ENTITY_TYPES = {
    "PERSON",
    "EVENT",
    "PLACE",
    "ORGANIZATION",
    "TOPIC",
    "KEYWORD",
    "TIME_PERIOD",
    "METADATA",
}


# =====================================================
# 2. 사용자 입력값 검증
# =====================================================

def validate_preference(preference: dict[str, Any]) -> dict[str, Any]:
    """
    사용자 선호도 입력값을 검증하고 정규화한다.

    필수 입력:
        preference_type
        preference_value

    선택 입력:
        target_content_type
        preference_weight
    """

    if not isinstance(preference, dict):
        raise ValueError("각 선호도는 딕셔너리 형태여야 합니다.")

    preference_type = str(
        preference.get("preference_type", "")
    ).strip().lower()

    preference_value = str(
        preference.get("preference_value", "")
    ).strip()

    target_content_type = preference.get("target_content_type")

    if target_content_type is not None:
        target_content_type = str(
            target_content_type
        ).strip().lower()

        if target_content_type not in VALID_CONTENT_TYPES:
            raise ValueError(
                f"잘못된 콘텐츠 유형입니다: {target_content_type}"
            )

    # 선호도 유형 검증
    if preference_type not in VALID_PREFERENCE_TYPES:
        raise ValueError(
            f"지원하지 않는 선호도 유형입니다: {preference_type}"
        )

    # 선호도 값 검증
    if not preference_value:
        raise ValueError("선호도 값은 비어 있을 수 없습니다.")

    # 콘텐츠 유형 값 검증
    if preference_type == "content_type":
        preference_value = preference_value.lower()

        if preference_value not in VALID_CONTENT_TYPES:
            raise ValueError(
                f"지원하지 않는 콘텐츠 유형입니다: {preference_value}"
            )

        if target_content_type is not None:
            raise ValueError(
                "content_type 선호도에는 target_content_type을 지정하지 않습니다."
            )

    # 장르는 영화 또는 드라마에만 적용
    if preference_type == "genre":
        if target_content_type not in {"movie", "tv"}:
            raise ValueError(
                "genre 선호도에는 movie 또는 tv를 지정해야 합니다."
            )

    # 개체 유형 검증
    if preference_type == "entity_type":
        preference_value = preference_value.upper()

        if preference_value not in VALID_ENTITY_TYPES:
            raise ValueError(
                f"지원하지 않는 개체 유형입니다: {preference_value}"
            )

        if target_content_type is not None:
            raise ValueError(
                "entity_type 선호도는 공통 선호도로 저장합니다."
            )

    # 탐색 방식 검증
    if preference_type == "exploration_style":
        preference_value = preference_value.lower()

        if target_content_type is not None:
            raise ValueError(
                "exploration_style은 공통 선호도로 저장합니다."
            )

        allowed_exploration_styles = {
            "creator",
            "performer",
            "context",
            "theme",
        }

        if preference_value not in allowed_exploration_styles:
            raise ValueError(
                "지원하지 않는 탐색 방식입니다. "
                f"허용 값: {', '.join(sorted(allowed_exploration_styles))}"
            )

    # 선호도 강도 검증
    weight = preference.get("preference_weight", 1.0)

    if isinstance(weight, bool):
        raise ValueError("preference_weight는 숫자여야 합니다.")

    try:
        weight = float(weight)
    except (TypeError, ValueError):
        raise ValueError(
            "preference_weight는 숫자로 입력해야 합니다."
        )

    if not 0 <= weight <= 1:
        raise ValueError(
            "preference_weight는 0 이상 1 이하여야 합니다."
        )

    return {
        "preference_type": preference_type,
        "preference_value": preference_value,
        "target_content_type": target_content_type,
        "preference_weight": weight,
    }

# =====================================================
# 3. 전체 선호도 입력값 검증
# =====================================================

def validate_preferences(
    preferences: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """
    여러 선호도를 검증한다.
    중복된 선호도는 한 번만 저장하도록 처리한다.
    """

    if not isinstance(preferences, list):
        raise ValueError("선호도 목록은 리스트 형태여야 합니다.")

    validated = []
    seen = set()

    for preference in preferences:
        item = validate_preference(preference)

        unique_key = (
            item["preference_type"],
            item["preference_value"],
            item["target_content_type"],
        )

        if unique_key in seen:
            continue

        seen.add(unique_key)
        validated.append(item)

    return validated


# =====================================================
# 4. 사용자 등록 또는 조회
# =====================================================

def get_or_create_user(
    conn: psycopg.Connection,
    external_user_key: str,
) -> int:
    """
    외부 사용자 식별값으로 사용자를 조회한다.
    존재하지 않으면 새 사용자를 생성한다.
    """

    external_user_key = external_user_key.strip()

    if not external_user_key:
        raise ValueError("external_user_key는 비어 있을 수 없습니다.")

    if len(external_user_key) > 100:
        raise ValueError(
            "external_user_key는 100자 이하여야 합니다."
        )

    row = conn.execute(
        """
        INSERT INTO user_profile (external_user_key)
        VALUES (%s)
        ON CONFLICT (external_user_key)
        DO UPDATE SET updated_at = NOW()
        RETURNING user_id;
        """,
        (external_user_key,),
    ).fetchone()

    return row["user_id"]


# =====================================================
# 5. 초기 선호도 저장
# =====================================================

def save_initial_preferences(
    external_user_key: str,
    preferences: list[dict[str, Any]],
    database_url: str | None = None,
) -> dict[str, Any]:
    """
    
        사용자의 선호도를 검증하고 DB에 누적 저장한다.

        - 기존 선호도는 삭제하지 않는다.
        - 동일한 선호도가 있으면 가중치와 갱신 시각을 업데이트한다.
        - 새로운 선호도는 새 행으로 추가한다.
        - 초기 설문과 이후 명시적 피드백을 모두 저장할 수 있다.


    Args:
        external_user_key:
            사용자를 구분하는 외부 식별값

        preferences:
            사용자 선호도 목록

        database_url:
            DB 접속 주소. 생략하면 환경변수 DATABASE_URL 사용

    Returns:
        user_id와 저장된 선호도 개수
    """

    db_url = database_url or DATABASE_URL

    if not db_url:
        raise RuntimeError(
            "DATABASE_URL 환경변수가 설정되지 않았습니다."
        )

    # DB에 접근하기 전에 모든 입력값 검증
    validated_preferences = validate_preferences(preferences)

    with psycopg.connect(
        db_url,
        row_factory=dict_row,
    ) as conn:

        # 사용자 등록 또는 조회
        user_id = get_or_create_user(
            conn,
            external_user_key,
        )


        # 새로운 초기 선호도 저장
        for preference in validated_preferences:
            conn.execute(
                """
                INSERT INTO user_preference (
                    user_id,
                    preference_type,
                    preference_value,
                    target_content_type,
                    preference_weight,
                    preference_source
                )
                VALUES (%s, %s, %s, %s, %s, 'explicit')

                ON CONFLICT (
                    user_id,
                    preference_type,
                    preference_value,
                    (COALESCE(target_content_type, '__all__')),
                    preference_source
                )
                DO UPDATE SET
                    preference_weight = EXCLUDED.preference_weight,
                    updated_at = NOW();
                """,
                (
                    user_id,
                    preference["preference_type"],
                    preference["preference_value"],
                    preference["target_content_type"],
                    preference["preference_weight"],
                ),
            )

    return {
        "user_id": user_id,
        "saved_count": len(validated_preferences),
        "message": "초기 선호도가 정상적으로 저장되었습니다.",
    }


# =====================================================
# 6. 저장 결과 조회
# =====================================================

def get_user_preferences(
    external_user_key: str,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    사용자의 전체 선호도를 조회한다.
    """

    db_url = database_url or DATABASE_URL

    if not db_url:
        raise RuntimeError(
            "DATABASE_URL 환경변수가 설정되지 않았습니다."
        )

    with psycopg.connect(
        db_url,
        row_factory=dict_row,
    ) as conn:

        user = conn.execute(
            """
            SELECT user_id
            FROM user_profile
            WHERE external_user_key = %s;
            """,
            (external_user_key.strip(),),
        ).fetchone()

        if user is None:
            return []

        rows = conn.execute(
            """
            SELECT
                preference_id,
                preference_type,
                preference_value,
                target_content_type,
                preference_weight,
                preference_source,
                created_at,
                updated_at
            FROM user_preference
            WHERE user_id = %s
            ORDER BY preference_type, preference_value;
            """,
            (user["user_id"],),
        ).fetchall()

    return [dict(row) for row in rows]


def save_implicit_preferences(
    external_user_key: str,
    preferences: list[dict[str, Any]],
    database_url: str | None = None,
) -> dict[str, Any]:
    """
    행동 데이터로 추론한 암묵적 선호도를 저장한다.

    기존 선호도를 삭제하지 않고,
    동일한 선호도가 있으면 가중치와 updated_at을 갱신한다.
    """

    db_url = database_url or DATABASE_URL

    if not db_url:
        raise ValueError("DATABASE_URL이 설정되지 않았습니다.")

    normalized_preferences = validate_preferences(preferences)

    if not normalized_preferences:
        return {
            "user_id": None,
            "saved_count": 0,
            "message": "저장할 암묵적 선호도가 없습니다.",
        }

    saved_count = 0

    with psycopg.connect(
        db_url,
        row_factory=dict_row,
    ) as conn:

        user_id = get_or_create_user(
            conn,
            external_user_key,
        )

        for preference in normalized_preferences:

            conn.execute(
                """
                INSERT INTO user_preference (
                    user_id,
                    preference_type,
                    preference_value,
                    target_content_type,
                    preference_weight,
                    preference_source
                )
                VALUES (%s, %s, %s, %s, %s, %s)

                ON CONFLICT (
                    user_id,
                    preference_type,
                    preference_value,
                    (COALESCE(target_content_type, '__all__')),
                    preference_source
                )

                DO UPDATE SET
                    preference_weight = EXCLUDED.preference_weight,
                    updated_at = NOW()
                """,
                (
                    user_id,
                    preference["preference_type"],
                    preference["preference_value"],
                    preference["target_content_type"],
                    preference["preference_weight"],
                    "implicit",
                ),
            )

            saved_count += 1

    return {
        "user_id": user_id,
        "saved_count": saved_count,
        "message": f"{saved_count}개의 암묵적 선호도를 저장했습니다.",
    }