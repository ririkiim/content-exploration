
-- =====================================================
-- 사용자 개인화 선호도 테이블
-- Module: personalization
-- =====================================================

-- 1. 사용자 기본 정보
CREATE TABLE IF NOT EXISTS user_profile (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 로그인 기능이 없을 때도 사용자를 구분하기 위한 식별값
    external_user_key VARCHAR(100) NOT NULL UNIQUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- 2. 사용자 선호도
CREATE TABLE IF NOT EXISTS user_preference (
    preference_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    user_id BIGINT NOT NULL
        REFERENCES user_profile(user_id)
        ON DELETE CASCADE,

    -- 선호도 유형
    preference_type VARCHAR(30) NOT NULL
        CHECK (
            preference_type IN (
                'content_type',
                'genre',
                'topic',
                'keyword',
                'entity_type',
                'exploration_style'
            )
        ),

    -- 선호도 실제 값
    -- 예: movie, 역사, 민주화 운동, PERSON, EVENT_CENTERED
    preference_value TEXT NOT NULL,

    -- 특정 콘텐츠 유형에만 적용되는 선호도인지 구분
    -- NULL이면 콘텐츠 유형에 관계없이 공통 적용
    target_content_type VARCHAR(20)
        CHECK (
            target_content_type IN ('movie', 'tv', 'book')
            OR target_content_type IS NULL
        ),

    -- 선호도 강도
    preference_weight NUMERIC(5, 4) NOT NULL DEFAULT 1.0
        CHECK (
            preference_weight >= 0
            AND preference_weight <= 1
        ),

    -- 선호도 형성 출처
    preference_source VARCHAR(20) NOT NULL DEFAULT 'explicit'
        CHECK (
            preference_source IN ('explicit', 'behavior')
        ),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 장르 선호도는 영화 또는 드라마에만 적용
    CONSTRAINT chk_genre_target
        CHECK (
            preference_type <> 'genre'
            OR target_content_type IN ('movie', 'tv')
        )
);


-- 3. 중복 선호도 방지
-- target_content_type이 NULL인 공통 선호도도 중복 저장되지 않도록 처리
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_preference_value
ON user_preference (
    user_id,
    preference_type,
    preference_value,
    COALESCE(target_content_type, '__all__'),
    preference_source
);


-- 4. 사용자별 선호도 조회 성능을 위한 인덱스
CREATE INDEX IF NOT EXISTS idx_user_preference_user
ON user_preference(user_id);


CREATE INDEX IF NOT EXISTS idx_user_preference_type
ON user_preference(user_id, preference_type);


-- =====================================================
-- 생성 확인
-- =====================================================
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN (
      'user_profile',
      'user_preference'
  )
ORDER BY table_name;
