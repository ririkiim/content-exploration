
-- =========================================================
-- user_profile: 사용자 식별 정보
-- =========================================================

CREATE TABLE IF NOT EXISTS public.user_profile (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_user_key VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);



-- =========================================================
-- 2. 사용자 등록 (사용 예시)
-- =========================================================

-- external_user_key만 입력하면 나머지 컬럼은 자동 생성
-- 실제 서비스에서는 Python 코드에서 사용자 등록 처리

-- INSERT INTO public.user_profile (external_user_key)
-- VALUES ('test_user_01')
-- RETURNING user_id;


-- =========================================================
-- 3. 사용자 식별 키 수정 (사용 예시)
-- =========================================================

-- UPDATE public.user_profile
-- SET
--     external_user_key = 'new_user_key',
--     updated_at = NOW()
-- WHERE external_user_key = 'test_user_01';


-- =========================================================
-- 4. 사용자 조회 (사용 예시)
-- =========================================================

-- SELECT *
-- FROM public.user_profile
-- WHERE external_user_key = 'test_user_01';
