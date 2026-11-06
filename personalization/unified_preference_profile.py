
"""
통합 사용자 개인화 프로필 생성

역할:
1. user_preference 기반 선호 프로필 조회
2. user_interaction 기반 관심 신호 조회
3. Entity 및 Content 관심 신호 분리
4. 관계 탐색 경로와 피드백 이력 보존
5. 최종 재랭킹에 전달할 공통 데이터 구조 생성

주의:
- DB를 수정하지 않는다.
- 후보 생성, 정제, 임베딩, 점수화는 수행하지 않는다.
- 서로 다른 출처의 점수를 임의로 합산하지 않는다.
- preference_feature_mapping.py는 사용하지 않는다.
"""

from datetime import datetime, timezone
from typing import Any

from .preference_profile import build_preference_profile
from .interaction_to_preference import build_interaction_profile


# =========================================================
# 1. 기본 설정
# =========================================================

DEFAULT_HALF_LIFE_DAYS = 30.0

SUPPORTED_PREFERENCE_TYPES = (
    "content_type",
    "genre",
    "topic",
    "keyword",
    "entity_type",
    "exploration_style",
)


# =========================================================
# 2. 초기 선호 프로필 정리
# =========================================================

def normalize_stored_preferences(
    preference_profile: dict[str, Any],
) -> dict[str, Any]:
    """
    기존 build_preference_profile()의 결과를
    재랭킹에서 활용하기 쉬운 형태로 정리한다.

    explicit/implicit을 구분하여 보존한다.
    """

    result = {
        preference_type: {
            "explicit": [],
            "implicit": [],
        }
        for preference_type in SUPPORTED_PREFERENCE_TYPES
    }

    raw_profile = preference_profile.get("profile", {})

    for preference_type in SUPPORTED_PREFERENCE_TYPES:

        category = raw_profile.get(
            preference_type,
            {},
        )

        for source in ("explicit", "implicit"):

            for item in category.get(source, []):

                result[preference_type][source].append({
                    "preference_id": item.get("preference_id"),
                    "preference_value": item["preference_value"],
                    "target_content_type": item.get(
                        "target_content_type"
                    ),
                    "base_weight": float(
                        item["base_weight"]
                    ),
                    "decay_factor": float(
                        item["decay_factor"]
                    ),
                    "effective_weight": float(
                        item["effective_weight"]
                    ),
                    "updated_at": item.get("updated_at"),
                })

    return result


# =========================================================
# 3. 행동 기반 대상별 관심 신호 정리
# =========================================================

def normalize_behavioral_interests(
    interaction_profile: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """
    상호작용 대상별 통합 신호를
    Entity / Content로 분리한다.

    explicit와 implicit 점수는 각각 보존한다.
    """

    entity_interests = []
    content_interests = []

    for signal in interaction_profile.get(
        "target_signals",
        [],
    ):

        target_type = signal["target_type"]

        item = {
            "target_id": signal["target_id"],
            "signal_score": float(
                signal["signal_score"]
            ),
            "signal_source": signal["signal_source"],
            "explicit_feedback": signal.get(
                "explicit_feedback"
            ),
            "explicit_score": (
                float(signal["explicit_score"])
                if signal.get("explicit_score") is not None
                else None
            ),
            "implicit_score": float(
                signal.get("implicit_score") or 0.0
            ),
        }

        if target_type == "entity":

            entity_interests.append({
                "entity_id": item["target_id"],
                **{
                    key: value
                    for key, value in item.items()
                    if key != "target_id"
                },
            })

        elif target_type == "content":

            content_interests.append({
                "content_id": item["target_id"],
                **{
                    key: value
                    for key, value in item.items()
                    if key != "target_id"
                },
            })

    entity_interests.sort(
        key=lambda item: (
            -item["signal_score"],
            item["entity_id"],
        )
    )

    content_interests.sort(
        key=lambda item: (
            -item["signal_score"],
            item["content_id"],
        )
    )

    return {
        "entity_interests": entity_interests,
        "content_interests": content_interests,
    }


# =========================================================
# 4. 관계 탐색 이력 정리
# =========================================================

def normalize_exploration_history(
    interaction_profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Content -> Entity -> Content 경로의
    사용자 반응을 정리한다.

    동일 경로의 집계 결과는 유지한다.
    """

    result = []

    for context in interaction_profile.get(
        "exploration_contexts",
        [],
    ):

        result.append({
            "source_content_id": context[
                "source_content_id"
            ],
            "selected_entity_id": context[
                "selected_entity_id"
            ],
            "recommended_content_id": context[
                "recommended_content_id"
            ],
            "impression_count": int(
                context.get("impression_count") or 0
            ),
            "select_count": int(
                context.get("select_count") or 0
            ),
            "view_count": int(
                context.get("view_count") or 0
            ),
            "latest_feedback": context.get(
                "latest_feedback"
            ),
            "session_count": int(
                context.get("session_count") or 0
            ),
        })

    result.sort(
        key=lambda item: (
            item["source_content_id"],
            item["selected_entity_id"],
            item["recommended_content_id"],
        )
    )

    return result


# =========================================================
# 5. 통합 프로필 생성
# =========================================================

def build_unified_preference_profile(
    external_user_key: str,
    *,
    database_url: str | None = None,
    reference_time: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> dict[str, Any]:
    """
    초기 선호 + 행동 기반 신호 + 탐색 경로를
    하나의 통합 프로필로 생성한다.

    반환 구조:
        external_user_key
        generated_at
        initial_preferences
        behavioral_preferences
        exploration_history
        metadata
    """

    if not isinstance(external_user_key, str):
        raise TypeError(
            "external_user_key는 문자열이어야 합니다."
        )

    external_user_key = external_user_key.strip()

    if not external_user_key:
        raise ValueError(
            "external_user_key는 비어 있을 수 없습니다."
        )

    if half_life_days <= 0:
        raise ValueError(
            "half_life_days는 0보다 커야 합니다."
        )

    if reference_time is None:
        reference_time = datetime.now(timezone.utc)

    elif reference_time.tzinfo is None:
        raise ValueError(
            "reference_time은 시간대 정보가 포함되어야 합니다."
        )

    # -----------------------------------------------------
    # 5-1. 저장된 사용자 선호 조회
    # -----------------------------------------------------

    stored_profile = build_preference_profile(
        external_user_key=external_user_key,
        half_life_days=half_life_days,
        database_url=database_url,
        reference_time=reference_time,
    )

    initial_preferences = normalize_stored_preferences(
        stored_profile
    )

    # -----------------------------------------------------
    # 5-2. 사용자 행동 기반 신호 조회
    # -----------------------------------------------------

    interaction_profile = build_interaction_profile(
        external_user_key=external_user_key,
        database_url=database_url,
        now=reference_time,
        half_life_days=half_life_days,
    )

    behavioral_preferences = (
        normalize_behavioral_interests(
            interaction_profile
        )
    )

    # -----------------------------------------------------
    # 5-3. 관계 탐색 이력 정리
    # -----------------------------------------------------

    exploration_history = normalize_exploration_history(
        interaction_profile
    )

    # -----------------------------------------------------
    # 5-4. 통합 결과 반환
    # -----------------------------------------------------

    return {
        "external_user_key": external_user_key,
        "generated_at": reference_time,
        "half_life_days": half_life_days,
        "initial_preferences": initial_preferences,
        "behavioral_preferences": behavioral_preferences,
        "exploration_history": exploration_history,
        "metadata": {
            "stored_preference_count": stored_profile.get(
                "total_preference_count",
                0,
            ),
            "interaction_event_count": interaction_profile.get(
                "total_events",
                0,
            ),
            "entity_interest_count": len(
                behavioral_preferences["entity_interests"]
            ),
            "content_interest_count": len(
                behavioral_preferences["content_interests"]
            ),
            "exploration_path_count": len(
                exploration_history
            ),
        },
    }


# =========================================================
# 6. 통합 프로필 출력
# =========================================================

def print_unified_preference_profile(
    profile: dict[str, Any],
) -> None:
    """통합 프로필을 사람이 확인하기 쉬운 형태로 출력한다."""

    print("\n" + "=" * 65)
    print("통합 사용자 개인화 프로필")
    print("=" * 65)

    print("사용자:", profile["external_user_key"])
    print("생성 시각:", profile["generated_at"])

    metadata = profile["metadata"]

    print("\n[프로필 요약]")
    print(
        "저장된 선호:",
        metadata["stored_preference_count"],
    )
    print(
        "상호작용 이벤트:",
        metadata["interaction_event_count"],
    )
    print(
        "Entity 관심 대상:",
        metadata["entity_interest_count"],
    )
    print(
        "Content 관심 대상:",
        metadata["content_interest_count"],
    )
    print(
        "탐색 경로:",
        metadata["exploration_path_count"],
    )

    # -----------------------------------------------------
    # 초기 선호
    # -----------------------------------------------------

    print("\n[1. 초기 및 저장된 선호]")

    found_preference = False

    for preference_type, sources in (
        profile["initial_preferences"].items()
    ):

        for source, items in sources.items():

            for item in items:

                found_preference = True

                target = (
                    item["target_content_type"]
                    or "all"
                )

                print(
                    f"{preference_type:20s} "
                    f"{str(item['preference_value']):25s} "
                    f"source={source:8s} "
                    f"target={target:8s} "
                    f"weight={item['effective_weight']:+.4f}"
                )

    if not found_preference:
        print("저장된 선호가 없습니다.")

    # -----------------------------------------------------
    # Entity 관심
    # -----------------------------------------------------

    print("\n[2. Entity 관심 신호]")

    entity_interests = profile[
        "behavioral_preferences"
    ]["entity_interests"]

    if not entity_interests:
        print("Entity 관심 신호가 없습니다.")

    for item in entity_interests:

        print(
            f"entity_id={item['entity_id']} "
            f"score={item['signal_score']:+.4f} "
            f"source={item['signal_source']} "
            f"feedback={item['explicit_feedback']}"
        )

    # -----------------------------------------------------
    # Content 관심
    # -----------------------------------------------------

    print("\n[3. Content 관심 신호]")

    content_interests = profile[
        "behavioral_preferences"
    ]["content_interests"]

    if not content_interests:
        print("Content 관심 신호가 없습니다.")

    for item in content_interests:

        print(
            f"content_id={item['content_id']} "
            f"score={item['signal_score']:+.4f} "
            f"source={item['signal_source']} "
            f"feedback={item['explicit_feedback']}"
        )

    # -----------------------------------------------------
    # 탐색 경로
    # -----------------------------------------------------

    print("\n[4. 관계 탐색 이력]")

    if not profile["exploration_history"]:
        print("탐색 이력이 없습니다.")

    for path in profile["exploration_history"]:

        print(
            f"{path['source_content_id']} "
            f"-> {path['selected_entity_id']} "
            f"-> {path['recommended_content_id']} "
            f"| impression={path['impression_count']} "
            f"| select={path['select_count']} "
            f"| view={path['view_count']} "
            f"| feedback={path['latest_feedback']}"
        )

    print("\n" + "=" * 65)
