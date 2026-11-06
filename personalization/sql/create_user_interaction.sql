
-- =========================================================
-- user_interaction: 사용자 탐색 및 평가 행동 기록
-- =========================================================
-- 주요 기능
-- 1. Entity / Content 노출, 선택, 조회, 평가 기록
-- 2. 세션별 사용자 탐색 이력 관리
-- 3. 부모 이벤트를 통한 관계 탐색 경로 추적
--
-- 사전 생성 필요 테이블:
-- user_profile
-- =========================================================

BEGIN;

CREATE TABLE IF NOT EXISTS public.user_interaction (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    user_id BIGINT NOT NULL,
    session_id UUID NOT NULL,
    parent_event_id BIGINT,

    event_type VARCHAR(20) NOT NULL,
    target_type VARCHAR(10) NOT NULL,

    entity_id BIGINT,
    content_id BIGINT,

    source_content_id BIGINT,
    selected_entity_id BIGINT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 사용자 및 콘텐츠·Entity 참조
    CONSTRAINT fk_ui_user
        FOREIGN KEY (user_id)
        REFERENCES public.user_profile(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_ui_entity
        FOREIGN KEY (entity_id)
        REFERENCES public.entity(entity_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_ui_content
        FOREIGN KEY (content_id)
        REFERENCES public.content(content_id)
        ON DELETE RESTRICT,

    CONSTRAINT fk_ui_source_content
        FOREIGN KEY (source_content_id)
        REFERENCES public.content(content_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_ui_selected_entity
        FOREIGN KEY (selected_entity_id)
        REFERENCES public.entity(entity_id)
        ON DELETE SET NULL,

    -- 부모 이벤트는 동일 사용자·세션에 속해야 함
    CONSTRAINT uq_ui_event_user_session
        UNIQUE (event_id, user_id, session_id),

    CONSTRAINT fk_ui_parent_same_session
        FOREIGN KEY (
            parent_event_id,
            user_id,
            session_id
        )
        REFERENCES public.user_interaction (
            event_id,
            user_id,
            session_id
        )
        ON DELETE NO ACTION
        DEFERRABLE INITIALLY IMMEDIATE,

    -- 허용 이벤트 및 대상 유형
    CONSTRAINT chk_ui_event_type
        CHECK (
            event_type IN (
                'impression',
                'select',
                'view',
                'like',
                'dislike'
            )
        ),

    CONSTRAINT chk_ui_target_type
        CHECK (
            target_type IN ('entity', 'content')
        ),

    -- Entity 또는 Content 중 하나만 지정
    CONSTRAINT chk_ui_target
        CHECK (
            (
                target_type = 'entity'
                AND entity_id IS NOT NULL
                AND content_id IS NULL
            )
            OR
            (
                target_type = 'content'
                AND content_id IS NOT NULL
                AND entity_id IS NULL
            )
        ),

    -- 자기 자신을 부모 이벤트로 지정하는 것 방지
    CONSTRAINT chk_ui_parent_not_self
        CHECK (
            parent_event_id IS NULL
            OR parent_event_id <> event_id
        )
);


-- =========================================================
-- 조회 성능을 위한 인덱스
-- =========================================================

-- 사용자별 최신 이벤트
CREATE INDEX IF NOT EXISTS idx_ui_user_created
    ON public.user_interaction (user_id, created_at DESC);

-- 사용자·세션별 이벤트
CREATE INDEX IF NOT EXISTS idx_ui_session
    ON public.user_interaction (user_id, session_id, created_at);

-- 부모 이벤트 기반 탐색 경로
CREATE INDEX IF NOT EXISTS idx_ui_parent
    ON public.user_interaction (parent_event_id)
    WHERE parent_event_id IS NOT NULL;

-- 사용자별 Entity 상호작용
CREATE INDEX IF NOT EXISTS idx_ui_entity
    ON public.user_interaction (user_id, entity_id, created_at DESC)
    WHERE entity_id IS NOT NULL;

-- 사용자별 Content 상호작용
CREATE INDEX IF NOT EXISTS idx_ui_content
    ON public.user_interaction (user_id, content_id, created_at DESC)
    WHERE content_id IS NOT NULL;

COMMIT;
