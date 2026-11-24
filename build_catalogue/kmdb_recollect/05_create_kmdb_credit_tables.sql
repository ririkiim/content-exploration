
-- =========================================================
-- 05_create_kmdb_credit.sql
--
-- 목적:
-- 신규 KMDb API에서 추출한 구조화 인물·원작 관계 저장
--
-- 생성 테이블:
--   public.kmdb_credit
--
-- 저장 대상:
--   director
--   cast
--   screenwriter
--   original_author
--
-- 기존 테이블 변경 없음
-- =========================================================

BEGIN;


-- =========================================================
-- 1. KMDb 크레딧 테이블
-- =========================================================

CREATE TABLE IF NOT EXISTS public.kmdb_credit (

    -- 내부 식별자
    credit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- 영화 정보
    kmdb_id TEXT NOT NULL,
    movie_title TEXT,

    -- 인물 정보
    person_id TEXT,
    person_name TEXT NOT NULL,
    person_name_en TEXT,

    -- 관계 유형
    link_role VARCHAR(30) NOT NULL,

    -- 배우 순서 (cast에만 사용)
    billing_order INTEGER,

    -- 세부 역할
    role_detail TEXT,

    -- 원작 작품 정보
    original_title TEXT,
    original_type TEXT,
    original_parse_status VARCHAR(20),

    -- 원작 관련 원본 텍스트
    raw_staff_etc TEXT,

    -- 원본 추적
    source_field VARCHAR(30) NOT NULL,
    source_index INTEGER NOT NULL,
    source_payload JSONB NOT NULL,

    -- 적재 시각
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- =====================================================
    -- 제약조건
    -- =====================================================

    -- 관계 유형 제한
    CONSTRAINT chk_kmdb_credit_role
        CHECK (
            link_role IN (
                'director',
                'cast',
                'screenwriter',
                'original_author'
            )
        ),

    -- 출처 배열 제한
    CONSTRAINT chk_kmdb_credit_source
        CHECK (
            source_field IN (
                'directors',
                'actors',
                'staffs'
            )
        ),

    -- 원본 배열 위치
    CONSTRAINT chk_kmdb_credit_source_index
        CHECK (source_index >= 0),

    -- 인물명 공백 방지
    CONSTRAINT chk_kmdb_credit_person_name
        CHECK (LENGTH(BTRIM(person_name)) > 0),

    -- 배우 순서는 cast에만 허용
    CONSTRAINT chk_kmdb_credit_billing_order
        CHECK (
            (
                link_role = 'cast'
                AND billing_order IS NOT NULL
                AND billing_order >= 0
            )
            OR
            (
                link_role <> 'cast'
                AND billing_order IS NULL
            )
        ),

    -- 원작 파싱 상태 제한
    CONSTRAINT chk_kmdb_credit_parse_status
        CHECK (
            original_parse_status IS NULL
            OR original_parse_status IN (
                'parsed',
                'ambiguous',
                'missing'
            )
        ),

    -- 원작 관련 필드는 원작자 관계에서만 사용
    CONSTRAINT chk_kmdb_credit_original_fields
        CHECK (
            link_role = 'original_author'
            OR (
                original_title IS NULL
                AND original_type IS NULL
                AND original_parse_status IS NULL
                AND raw_staff_etc IS NULL
            )
        ),

    -- 파싱 성공 시 원작 제목 필수
    CONSTRAINT chk_kmdb_credit_parsed_title
        CHECK (
            original_parse_status IS DISTINCT FROM 'parsed'
            OR (
                original_title IS NOT NULL
                AND LENGTH(BTRIM(original_title)) > 0
            )
        ),

    -- 동일 영화의 동일 원본 위치 중복 방지
    CONSTRAINT uq_kmdb_credit_source_position
        UNIQUE (
            kmdb_id,
            source_field,
            source_index
        )
);


-- =========================================================
-- 2. 인덱스
-- =========================================================

-- 영화 ID 기준 조회
CREATE INDEX IF NOT EXISTS idx_kmdb_credit_movie
    ON public.kmdb_credit (kmdb_id);

-- 영화별 관계 유형 조회
CREATE INDEX IF NOT EXISTS idx_kmdb_credit_movie_role
    ON public.kmdb_credit (kmdb_id, link_role);

-- 인물 ID 기준 참여 작품 조회
CREATE INDEX IF NOT EXISTS idx_kmdb_credit_person
    ON public.kmdb_credit (person_id)
    WHERE person_id IS NOT NULL;

-- 역할별 배우 순서 조회
CREATE INDEX IF NOT EXISTS idx_kmdb_credit_cast_order
    ON public.kmdb_credit (kmdb_id, billing_order)
    WHERE link_role = 'cast';

-- 원작 제목이 있는 관계 조회
CREATE INDEX IF NOT EXISTS idx_kmdb_credit_original_title
    ON public.kmdb_credit (original_title)
    WHERE original_title IS NOT NULL;


COMMIT;
