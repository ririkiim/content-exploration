
"""사용자 상호작용을 개인화 신호로 변환한다.

역할:
1. 명시적 좋아요/싫어요의 최신 상태 계산
2. 암묵적 선택/조회 행동의 관심도 계산
3. Entity를 통한 Content 탐색 맥락 집계
4. 추후 개인화 재랭킹에서 사용할 신호 프로필 생성

이 파일은 DB 데이터를 수정하지 않는다.
추천 후보 생성 및 재랭킹도 수행하지 않는다.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row

from personalization.user_interaction import (
    _database_url,
    _require_user_id,
)


# =========================================================
# 1. 신호 계산 설정
# =========================================================

IMPLICIT_WEIGHTS = {
    "impression": 0.0,
    "select": 0.3,
    "view": 0.1,
}

EXPLICIT_WEIGHTS = {
    "like": 1.0,
    "dislike": -1.0,
}

# 같은 대상에 대한 반복 행동의 영향 제한
MAX_IMPLICIT_SCORE = 0.6

# 암묵적 관심의 시간 감쇠 반감기
IMPLICIT_HALF_LIFE_DAYS = 30.0

# 동일 세션에서 동일 대상·행동은 한 번만 집계
DEDUPLICATE_WITHIN_SESSION = True


# =========================================================
# 2. 공통 함수
# =========================================================

def _target_key(event: dict[str, Any]) -> tuple[str, int]:
    """이벤트가 가리키는 대상을 식별한다."""

    target_type = event["target_type"]

    if target_type == "entity":
        return ("entity", event["entity_id"])

    return ("content", event["content_id"])


def _to_utc(value: datetime) -> datetime:
    """datetime을 UTC 기준으로 변환한다."""

    if value.tzinfo is None:
        raise ValueError(
            "created_at에는 시간대 정보가 있어야 합니다."
        )

    return value.astimezone(timezone.utc)


def calculate_interaction_decay(
    created_at: datetime,
    *,
    now: datetime | None = None,
    half_life_days: float = IMPLICIT_HALF_LIFE_DAYS,
) -> float:
    """암묵적 행동의 시간 감쇠 계수를 계산한다."""

    if half_life_days <= 0:
        raise ValueError(
            "half_life_days는 0보다 커야 합니다."
        )

    current_time = (
        _to_utc(now)
        if now is not None
        else datetime.now(timezone.utc)
    )

    event_time = _to_utc(created_at)

    elapsed_days = max(
        0.0,
        (current_time - event_time).total_seconds() / 86400,
    )

    return 0.5 ** (elapsed_days / half_life_days)


# =========================================================
# 3. 사용자 상호작용 전체 조회
# =========================================================

def load_interaction_events(
    external_user_key: str,
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """사용자의 상호작용 이력을 시간순으로 조회한다."""

    with psycopg.connect(
        _database_url(database_url),
        row_factory=dict_row,
    ) as conn:

        user_id = _require_user_id(
            conn,
            external_user_key,
        )

        rows = conn.execute(
            """
            SELECT
                event_id,
                user_id,
                session_id,
                parent_event_id,
                event_type,
                target_type,
                entity_id,
                content_id,
                source_content_id,
                selected_entity_id,
                created_at
            FROM user_interaction
            WHERE user_id = %s
            ORDER BY created_at ASC, event_id ASC
            """,
            (user_id,),
        ).fetchall()

    return [dict(row) for row in rows]


# =========================================================
# 4. 대상별 명시적 피드백 계산
# =========================================================

def calculate_explicit_signals(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """대상별 최신 좋아요/싫어요만 반영한다.

    예:
    like -> dislike -> like

    최종 상태는 like(+1.0)이다.
    """

    latest = {}

    for event in events:
        event_type = event["event_type"]

        if event_type not in EXPLICIT_WEIGHTS:
            continue

        key = _target_key(event)

        previous = latest.get(key)

        if previous is None or (
            event["created_at"],
            event["event_id"],
        ) > (
            previous["created_at"],
            previous["event_id"],
        ):
            latest[key] = event

    result = []

    for (target_type, target_id), event in latest.items():

        result.append({
            "target_type": target_type,
            "target_id": target_id,
            "feedback": event["event_type"],
            "score": EXPLICIT_WEIGHTS[event["event_type"]],
            "event_id": event["event_id"],
            "created_at": event["created_at"],
        })

    return sorted(
        result,
        key=lambda item: (
            item["target_type"],
            item["target_id"],
        ),
    )


# =========================================================
# 5. 대상별 암묵적 관심도 계산
# =========================================================

def calculate_implicit_signals(
    events: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    half_life_days: float = IMPLICIT_HALF_LIFE_DAYS,
) -> list[dict[str, Any]]:
    """선택·조회 행동으로 암묵적 관심도를 계산한다.

    동일 세션의 같은 대상·행동은 중복 집계하지 않는다.
    시간 감쇠와 최대 점수 제한을 적용한다.
    """

    current_time = (
        _to_utc(now)
        if now is not None
        else datetime.now(timezone.utc)
    )

    # 동일 세션에서 반복된 행동은 최신 이벤트를 사용
    selected_events = {}

    for event in events:
        event_type = event["event_type"]

        if event_type not in IMPLICIT_WEIGHTS:
            continue

        if IMPLICIT_WEIGHTS[event_type] == 0:
            continue

        target_type, target_id = _target_key(event)

        if DEDUPLICATE_WITHIN_SESSION:
            key = (
                event["session_id"],
                event["source_content_id"],
                event["selected_entity_id"],
                target_type,
                target_id,
                event_type,
            )
        else:
            key = (event["event_id"],)

        previous = selected_events.get(key)

        if previous is None or (
            event["created_at"],
            event["event_id"],
        ) > (
            previous["created_at"],
            previous["event_id"],
        ):
            selected_events[key] = event

    scores = defaultdict(float)
    counts = defaultdict(int)

    for event in selected_events.values():

        target = _target_key(event)
        event_type = event["event_type"]

        decay = calculate_interaction_decay(
            event["created_at"],
            now=current_time,
            half_life_days=half_life_days,
        )

        scores[target] += (
            IMPLICIT_WEIGHTS[event_type] * decay
        )

        counts[target] += 1

    result = []

    for (target_type, target_id), score in scores.items():

        result.append({
            "target_type": target_type,
            "target_id": target_id,
            "score": round(
                min(score, MAX_IMPLICIT_SCORE),
                4,
            ),
            "action_count": counts[
                (target_type, target_id)
            ],
        })

    return sorted(
        result,
        key=lambda item: (
            item["target_type"],
            item["target_id"],
        ),
    )


# =========================================================
# 6. 명시적·암묵적 신호 통합
# =========================================================

def combine_target_signals(
    explicit_signals: list[dict[str, Any]],
    implicit_signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """대상별 최종 관심 신호를 구성한다.

    명시적 피드백이 있으면 해당 점수를 우선한다.
    암묵적 점수는 별도 보존한다.

    여기서 계산한 score는 추천 점수가 아니라
    사용자 관심 신호이다.
    """

    combined = {}

    for item in implicit_signals:

        key = (
            item["target_type"],
            item["target_id"],
        )

        combined[key] = {
            "target_type": item["target_type"],
            "target_id": item["target_id"],
            "explicit_feedback": None,
            "explicit_score": None,
            "implicit_score": item["score"],
            "signal_score": item["score"],
            "signal_source": "implicit",
        }

    for item in explicit_signals:

        key = (
            item["target_type"],
            item["target_id"],
        )

        if key not in combined:
            combined[key] = {
                "target_type": item["target_type"],
                "target_id": item["target_id"],
                "explicit_feedback": None,
                "explicit_score": None,
                "implicit_score": 0.0,
                "signal_score": 0.0,
                "signal_source": "implicit",
            }

        combined[key]["explicit_feedback"] = item["feedback"]
        combined[key]["explicit_score"] = item["score"]
        combined[key]["signal_score"] = item["score"]
        combined[key]["signal_source"] = "explicit"

    return sorted(
        combined.values(),
        key=lambda item: (
            item["target_type"],
            item["target_id"],
        ),
    )


# =========================================================
# 7. 관계 탐색 맥락 집계
# =========================================================


def calculate_exploration_context(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Entity를 매개로 한 Content 탐색 맥락을 집계한다.

    - 같은 세션/경로/행동의 중복 기록은 1회 인정
    - 다른 세션이나 다른 경로는 별도로 인정
    - 좋아요/싫어요는 경로별 최신 평가만 표시
    - DB 원본 이벤트는 변경하지 않음
    """

    contexts = {}
    seen_actions = set()

    ordered_events = sorted(
        events,
        key=lambda event: (
            event["created_at"],
            event["event_id"],
        ),
    )

    for event in ordered_events:

        if event["target_type"] != "content":
            continue

        source_id = event["source_content_id"]
        entity_id = event["selected_entity_id"]
        content_id = event["content_id"]

        if (
            source_id is None
            or entity_id is None
            or content_id is None
        ):
            continue

        path_key = (
            source_id,
            entity_id,
            content_id,
        )

        if path_key not in contexts:
            contexts[path_key] = {
                "source_content_id": source_id,
                "selected_entity_id": entity_id,
                "recommended_content_id": content_id,
                "impression_count": 0,
                "select_count": 0,
                "view_count": 0,
                "latest_feedback": None,
                "session_ids": set(),
            }

        context = contexts[path_key]
        event_type = event["event_type"]
        session_id = event["session_id"]

        context["session_ids"].add(str(session_id))

        if event_type in ("impression", "select", "view"):

            action_key = (
                session_id,
                source_id,
                entity_id,
                content_id,
                event_type,
            )

            if action_key in seen_actions:
                continue

            seen_actions.add(action_key)

            count_field = f"{event_type}_count"
            context[count_field] += 1

        elif event_type in ("like", "dislike"):
            # 시간순으로 처리하므로 마지막 평가가 최신
            context["latest_feedback"] = event_type

    result = []

    for context in contexts.values():

        context["session_count"] = len(
            context["session_ids"]
        )

        del context["session_ids"]

        result.append(context)

    return sorted(
        result,
        key=lambda item: (
            item["source_content_id"],
            item["selected_entity_id"],
            item["recommended_content_id"],
        ),
    )

# =========================================================
# 8. 개인화 신호 프로필 생성
# =========================================================

def build_interaction_profile(
    external_user_key: str,
    *,
    database_url: str | None = None,
    now: datetime | None = None,
    half_life_days: float = IMPLICIT_HALF_LIFE_DAYS,
) -> dict[str, Any]:
    """상호작용 기반 개인화 신호 프로필을 생성한다.

    반환 항목:
    - explicit_signals
    - implicit_signals
    - target_signals
    - exploration_contexts

    DB를 수정하지 않는다.
    """

    current_time = (
        _to_utc(now)
        if now is not None
        else datetime.now(timezone.utc)
    )

    events = load_interaction_events(
        external_user_key,
        database_url=database_url,
    )

    explicit_signals = calculate_explicit_signals(
        events
    )

    implicit_signals = calculate_implicit_signals(
        events,
        now=current_time,
        half_life_days=half_life_days,
    )

    target_signals = combine_target_signals(
        explicit_signals,
        implicit_signals,
    )

    exploration_contexts = calculate_exploration_context(
        events
    )

    return {
        "external_user_key": external_user_key,
        "calculated_at": current_time,
        "total_events": len(events),
        "explicit_signals": explicit_signals,
        "implicit_signals": implicit_signals,
        "target_signals": target_signals,
        "exploration_contexts": exploration_contexts,
    }


# =========================================================
# 9. 프로필 출력
# =========================================================

def print_interaction_profile(
    profile: dict[str, Any],
) -> None:
    """계산 결과를 터미널에 출력한다."""

    print("\n========== 상호작용 기반 개인화 프로필 ==========")

    print("사용자:", profile["external_user_key"])
    print("총 이벤트:", profile["total_events"])

    print("\n[1. 명시적 평가]")
    for item in profile["explicit_signals"]:
        print(item)

    print("\n[2. 암묵적 관심]")
    for item in profile["implicit_signals"]:
        print(item)

    print("\n[3. 대상별 통합 신호]")
    for item in profile["target_signals"]:
        print(item)

    print("\n[4. 관계 탐색 맥락]")
    for item in profile["exploration_contexts"]:
        print(item)
