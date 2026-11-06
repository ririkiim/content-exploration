
"""사용자-콘텐츠/엔티티 상호작용 이벤트 저장 및 조회.

기존 user_profile, user_interaction 테이블을 사용하며
새로운 테이블을 생성하지 않습니다.
"""

import os
from typing import Any
from uuid import UUID, uuid4

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

VALID_EVENT_TYPES = frozenset({
    "impression",
    "select",
    "view",
    "like",
    "dislike",
})

VALID_TARGET_TYPES = frozenset({"entity", "content"})
FEEDBACK_TYPES = frozenset({"like", "dislike"})


# =========================================================
# 1. 공통 검증 함수
# =========================================================

def _database_url(database_url: str | None) -> str:
    url = database_url or DATABASE_URL

    if not url:
        raise ValueError(
            "DATABASE_URL 환경변수가 없거나 "
            "database_url이 지정되지 않았습니다."
        )

    return url


def _session_uuid(value: UUID | str) -> UUID:
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(
            "session_id는 유효한 UUID여야 합니다."
        ) from exc


def _positive_id(
    value: Any,
    name: str,
    *,
    optional: bool = False,
) -> int | None:

    if value is None and optional:
        return None

    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
    ):
        raise ValueError(
            f"{name}는 양의 정수여야 합니다."
        )

    return value


def _validate_event(event: dict[str, Any]) -> dict[str, Any]:

    if not isinstance(event, dict):
        raise ValueError("각 이벤트는 dict여야 합니다.")

    event_type = event.get("event_type")
    target_type = event.get("target_type")

    if event_type not in VALID_EVENT_TYPES:
        raise ValueError(
            f"event_type은 {sorted(VALID_EVENT_TYPES)} "
            "중 하나여야 합니다."
        )

    if target_type not in VALID_TARGET_TYPES:
        raise ValueError(
            "target_type은 'entity' 또는 'content'여야 합니다."
        )

    entity_id = _positive_id(
        event.get("entity_id"),
        "entity_id",
        optional=True,
    )

    content_id = _positive_id(
        event.get("content_id"),
        "content_id",
        optional=True,
    )

    if target_type == "entity":
        if entity_id is None or content_id is not None:
            raise ValueError(
                "entity 이벤트는 entity_id만 지정해야 합니다."
            )

    if target_type == "content":
        if content_id is None or entity_id is not None:
            raise ValueError(
                "content 이벤트는 content_id만 지정해야 합니다."
            )

    source_content_id = _positive_id(
        event.get("source_content_id"),
        "source_content_id",
        optional=True,
    )

    selected_entity_id = _positive_id(
        event.get("selected_entity_id"),
        "selected_entity_id",
        optional=True,
    )

    parent_event_id = _positive_id(
        event.get("parent_event_id"),
        "parent_event_id",
        optional=True,
    )

    if (
        target_type == "entity"
        and selected_entity_id is not None
    ):
        raise ValueError(
            "entity 이벤트에는 "
            "selected_entity_id를 지정하지 않습니다."
        )

    if (
        selected_entity_id is not None
        and source_content_id is None
    ):
        raise ValueError(
            "selected_entity_id를 지정할 때는 "
            "source_content_id도 지정해야 합니다."
        )

    return {
        "event_type": event_type,
        "target_type": target_type,
        "entity_id": entity_id,
        "content_id": content_id,
        "source_content_id": source_content_id,
        "selected_entity_id": selected_entity_id,
        "parent_event_id": parent_event_id,
    }


# =========================================================
# 2. 세션 및 사용자 조회
# =========================================================

def create_session_id() -> UUID:
    """새로운 탐색 세션 UUID를 생성한다."""
    return uuid4()


def _find_user_id(
    conn: psycopg.Connection,
    external_user_key: str,
) -> int | None:

    if (
        not isinstance(external_user_key, str)
        or not external_user_key.strip()
    ):
        raise ValueError(
            "external_user_key는 비어 있지 않은 문자열이어야 합니다."
        )

    row = conn.execute(
        """
        SELECT user_id
        FROM user_profile
        WHERE external_user_key = %s
        """,
        (external_user_key.strip(),),
    ).fetchone()

    return row["user_id"] if row else None


def _require_user_id(
    conn: psycopg.Connection,
    external_user_key: str,
) -> int:

    user_id = _find_user_id(conn, external_user_key)

    if user_id is None:
        raise ValueError(
            "등록되지 않은 사용자입니다. "
            "먼저 user_preference.py에서 사용자를 생성하세요."
        )

    return user_id


# =========================================================
# 3. 부모 이벤트 검증
# =========================================================

def _check_parent(
    conn: psycopg.Connection,
    *,
    parent_event_id: int | None,
    user_id: int,
    session_id: UUID,
) -> None:

    if parent_event_id is None:
        return

    row = conn.execute(
        """
        SELECT event_id, user_id, session_id
        FROM user_interaction
        WHERE event_id = %s
        FOR KEY SHARE
        """,
        (parent_event_id,),
    ).fetchone()

    if row is None:
        raise ValueError(
            f"부모 이벤트 {parent_event_id}가 존재하지 않습니다."
        )

    if (
        row["user_id"] != user_id
        or row["session_id"] != session_id
    ):
        raise ValueError(
            "부모 이벤트는 동일 사용자·동일 세션에 속해야 합니다."
        )


# =========================================================
# 4. 이벤트 저장 내부 함수
# =========================================================

def _insert_interaction(
    conn: psycopg.Connection,
    *,
    user_id: int,
    session_id: UUID,
    event: dict[str, Any],
) -> dict[str, Any]:

    validated = _validate_event(event)

    _check_parent(
        conn,
        parent_event_id=validated["parent_event_id"],
        user_id=user_id,
        session_id=session_id,
    )

    row = conn.execute(
        """
        INSERT INTO user_interaction (
            user_id,
            session_id,
            parent_event_id,
            event_type,
            target_type,
            entity_id,
            content_id,
            source_content_id,
            selected_entity_id
        )
        VALUES (
            %(user_id)s,
            %(session_id)s,
            %(parent_event_id)s,
            %(event_type)s,
            %(target_type)s,
            %(entity_id)s,
            %(content_id)s,
            %(source_content_id)s,
            %(selected_entity_id)s
        )
        RETURNING *
        """,
        {
            "user_id": user_id,
            "session_id": session_id,
            **validated,
        },
    ).fetchone()

    return dict(row)


# =========================================================
# 5. 단일 상호작용 저장
# =========================================================

def record_interaction(
    external_user_key: str,
    session_id: UUID | str,
    event_type: str,
    target_type: str,
    *,
    entity_id: int | None = None,
    content_id: int | None = None,
    source_content_id: int | None = None,
    selected_entity_id: int | None = None,
    parent_event_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Entity 또는 Content 상호작용 이벤트 한 건을 저장한다."""

    session_uuid = _session_uuid(session_id)

    event = {
        "event_type": event_type,
        "target_type": target_type,
        "entity_id": entity_id,
        "content_id": content_id,
        "source_content_id": source_content_id,
        "selected_entity_id": selected_entity_id,
        "parent_event_id": parent_event_id,
    }

    validated = _validate_event(event)

    with psycopg.connect(
        _database_url(database_url),
        row_factory=dict_row,
    ) as conn:

        user_id = _require_user_id(
            conn,
            external_user_key,
        )

        return _insert_interaction(
            conn,
            user_id=user_id,
            session_id=session_uuid,
            event=validated,
        )


# =========================================================
# 6. 여러 상호작용 일괄 저장
# =========================================================

def record_interactions(
    external_user_key: str,
    session_id: UUID | str,
    events: list[dict[str, Any]],
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """여러 이벤트를 하나의 트랜잭션으로 저장한다.

    중간에 오류가 발생하면 해당 호출에서 저장한
    모든 이벤트가 롤백된다.
    """

    if not isinstance(events, list):
        raise ValueError("events는 list여야 합니다.")

    if not events:
        return []

    session_uuid = _session_uuid(session_id)

    validated_events = [
        _validate_event(event)
        for event in events
    ]

    saved_events = []

    with psycopg.connect(
        _database_url(database_url),
        row_factory=dict_row,
    ) as conn:

        user_id = _require_user_id(
            conn,
            external_user_key,
        )

        for event in validated_events:

            saved = _insert_interaction(
                conn,
                user_id=user_id,
                session_id=session_uuid,
                event=event,
            )

            saved_events.append(saved)

    return saved_events


# =========================================================
# 7. 사용자 전체 상호작용 이력 조회
# =========================================================

def get_user_interactions(
    external_user_key: str,
    *,
    event_type: str | None = None,
    target_type: str | None = None,
    limit: int = 100,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """사용자 상호작용을 최신순으로 조회한다."""

    if event_type is not None and event_type not in VALID_EVENT_TYPES:
        raise ValueError("유효하지 않은 event_type입니다.")

    if target_type is not None and target_type not in VALID_TARGET_TYPES:
        raise ValueError("유효하지 않은 target_type입니다.")

    limit = _positive_id(limit, "limit")

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
            SELECT *
            FROM user_interaction
            WHERE user_id = %(user_id)s
              AND (
                  %(event_type)s IS NULL
                  OR event_type = %(event_type)s
              )
              AND (
                  %(target_type)s IS NULL
                  OR target_type = %(target_type)s
              )
            ORDER BY created_at DESC, event_id DESC
            LIMIT %(limit)s
            """,
            {
                "user_id": user_id,
                "event_type": event_type,
                "target_type": target_type,
                "limit": limit,
            },
        ).fetchall()

        return [dict(row) for row in rows]


# =========================================================
# 8. 특정 세션 상호작용 이력 조회
# =========================================================

def get_session_interactions(
    external_user_key: str,
    session_id: UUID | str,
    *,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """특정 탐색 세션의 이벤트를 발생 순서대로 조회한다."""

    session_uuid = _session_uuid(session_id)

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
            SELECT *
            FROM user_interaction
            WHERE user_id = %s
              AND session_id = %s
            ORDER BY created_at ASC, event_id ASC
            """,
            (user_id, session_uuid),
        ).fetchall()

        return [dict(row) for row in rows]


# =========================================================
# 9. 특정 대상의 최신 좋아요/싫어요 조회
# =========================================================

def get_latest_feedback(
    external_user_key: str,
    target_type: str,
    *,
    entity_id: int | None = None,
    content_id: int | None = None,
    database_url: str | None = None,
) -> dict[str, Any] | None:
    """특정 Entity 또는 Content의 최신 명시적 평가를 조회한다."""

    validated = _validate_event({
        "event_type": "like",
        "target_type": target_type,
        "entity_id": entity_id,
        "content_id": content_id,
    })

    with psycopg.connect(
        _database_url(database_url),
        row_factory=dict_row,
    ) as conn:

        user_id = _require_user_id(
            conn,
            external_user_key,
        )

        row = conn.execute(
            """
            SELECT *
            FROM user_interaction
            WHERE user_id = %(user_id)s
              AND target_type = %(target_type)s
              AND entity_id IS NOT DISTINCT FROM %(entity_id)s
              AND content_id IS NOT DISTINCT FROM %(content_id)s
              AND event_type IN ('like', 'dislike')
            ORDER BY created_at DESC, event_id DESC
            LIMIT 1
            """,
            {
                "user_id": user_id,
                "target_type": validated["target_type"],
                "entity_id": validated["entity_id"],
                "content_id": validated["content_id"],
            },
        ).fetchone()

        return dict(row) if row else None


# =========================================================
# 10. 사용자별 최신 평가 목록 조회
# =========================================================

def get_user_feedback(
    external_user_key: str,
    *,
    target_type: str | None = None,
    limit: int = 100,
    database_url: str | None = None,
) -> list[dict[str, Any]]:
    """대상별 가장 최근 좋아요/싫어요를 조회한다.

    같은 대상에 여러 번 평가한 경우
    최신 평가 한 건만 반환한다.
    """

    if target_type is not None and target_type not in VALID_TARGET_TYPES:
        raise ValueError("유효하지 않은 target_type입니다.")

    limit = _positive_id(limit, "limit")

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
            SELECT *
            FROM (
                SELECT DISTINCT ON (
                    target_type,
                    entity_id,
                    content_id
                )
                    *
                FROM user_interaction
                WHERE user_id = %(user_id)s
                  AND event_type IN ('like', 'dislike')
                  AND (
                      %(target_type)s IS NULL
                      OR target_type = %(target_type)s
                  )
                ORDER BY
                    target_type,
                    entity_id,
                    content_id,
                    created_at DESC,
                    event_id DESC
            ) AS latest
            ORDER BY created_at DESC, event_id DESC
            LIMIT %(limit)s
            """,
            {
                "user_id": user_id,
                "target_type": target_type,
                "limit": limit,
            },
        ).fetchall()

        return [dict(row) for row in rows]
