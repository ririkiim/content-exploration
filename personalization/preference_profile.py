
from datetime import datetime, timezone
from collections import defaultdict
from typing import Any

from .user_preference import get_user_preferences


# =====================================================
# 1. 기본 설정
# =====================================================

# 선호도 가중치가 절반으로 감소하는 기간
DEFAULT_HALF_LIFE_DAYS = 30

# 지원하는 선호도 유형
PREFERENCE_CATEGORIES = {
    "content_type",
    "genre",
    "topic",
    "keyword",
    "entity_type",
    "exploration_style",
}


# =====================================================
# 2. 시간 감쇠 계산
# =====================================================

def calculate_time_decay(
    updated_at: datetime,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    reference_time: datetime | None = None,
) -> float:
    """
    선호도 갱신 시각을 기준으로 시간 감쇠 계수를 계산한다.

    반감기 방식:
        decay = 0.5 ** (경과 일수 / 반감기)

    Returns:
        0~1 사이의 시간 감쇠 계수
    """

    if half_life_days <= 0:
        raise ValueError("half_life_days는 0보다 커야 합니다.")

    if updated_at.tzinfo is None:
        raise ValueError(
            "updated_at은 시간대 정보가 포함된 datetime이어야 합니다."
        )

    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    elif reference_time.tzinfo is None:
        raise ValueError(
            "reference_time은 시간대 정보가 포함된 datetime이어야 합니다."
        )

    elapsed_seconds = (
        reference_time - updated_at
    ).total_seconds()

    # 미래 시각으로 인한 가중치 증가 방지
    elapsed_seconds = max(0, elapsed_seconds)

    elapsed_days = elapsed_seconds / 86400

    decay = 0.5 ** (elapsed_days / half_life_days)

    return decay


# =====================================================
# 3. 개별 선호도 가중치 계산
# =====================================================

def calculate_effective_weight(
    preference: dict[str, Any],
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    개별 선호도의 시간 감쇠를 적용한다.

    기본 가중치 × 시간 감쇠 계수
    """

    base_weight = float(
        preference["preference_weight"]
    )

    decay = calculate_time_decay(
        updated_at=preference["updated_at"],
        half_life_days=half_life_days,
        reference_time=reference_time,
    )

    effective_weight = base_weight * decay

    result = dict(preference)

    result["base_weight"] = base_weight
    result["decay_factor"] = decay
    result["effective_weight"] = effective_weight

    return result


# =====================================================
# 4. 사용자 선호도 프로필 생성
# =====================================================

def build_preference_profile(
    external_user_key: str,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    database_url: str | None = None,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    사용자의 누적 선호도를 조회하고
    시간 감쇠를 적용한 개인화 프로필을 생성한다.

    explicit와 implicit은 별도로 구분한다.
    """

    if half_life_days <= 0:
        raise ValueError("half_life_days는 0보다 커야 합니다.")

    # DB에서 사용자 선호도 조회
    preferences = get_user_preferences(
        external_user_key=external_user_key,
        database_url=database_url,
    )

    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    # 선호도 유형별 분류
    profile = {
        category: {
            "explicit": [],
            "implicit": [],
        }
        for category in PREFERENCE_CATEGORIES
    }

    # 각 선호도에 시간 감쇠 적용
    for preference in preferences:

        preference_type = preference["preference_type"]
        preference_source = preference["preference_source"]

        if preference_type not in PREFERENCE_CATEGORIES:
            continue

        if preference_source not in {"explicit", "implicit"}:
            continue

        weighted_preference = calculate_effective_weight(
            preference=preference,
            half_life_days=half_life_days,
            reference_time=reference_time,
        )

        profile[preference_type][preference_source].append(
            weighted_preference
        )

    # 유효 가중치가 높은 순으로 정렬
    for category in profile.values():
        for source in category.values():
            source.sort(
                key=lambda item: item["effective_weight"],
                reverse=True,
            )

    return {
        "external_user_key": external_user_key,
        "generated_at": reference_time,
        "half_life_days": half_life_days,
        "total_preference_count": len(preferences),
        "profile": profile,
    }


# =====================================================
# 5. 프로필 출력
# =====================================================

def print_preference_profile(
    preference_profile: dict[str, Any],
) -> None:
    """
    생성된 사용자 프로필을 읽기 쉬운 형태로 출력한다.
    """

    print("=" * 60)
    print("사용자 개인화 프로필")
    print("=" * 60)

    print(
        f"사용자: {preference_profile['external_user_key']}"
    )

    print(
        f"총 선호도 수: "
        f"{preference_profile['total_preference_count']}"
    )

    print(
        f"시간 감쇠 반감기: "
        f"{preference_profile['half_life_days']}일"
    )

    profile = preference_profile["profile"]

    for category_name, sources in profile.items():

        print(f"\n[{category_name}]")

        for source_name, preferences in sources.items():

            if not preferences:
                continue

            print(f"  - {source_name}")

            for item in preferences:
                print(
                    f"      값: {item['preference_value']} | "
                    f"기본 가중치: {item['base_weight']:.3f} | "
                    f"감쇠 계수: {item['decay_factor']:.3f} | "
                    f"최종 가중치: {item['effective_weight']:.3f}"
                )

    print("\n" + "=" * 60)
