-- =========================================================
-- 03_movie_catalogue_load.sql | Content Catalogue v1
-- 최초 영화 적재 전용 (기존 영화 자동 삭제/교체 없음)
-- 01, 02와 동일한 PostgreSQL 세션에서 실행
-- RAW 보존 / 기존 TV, 도서 데이터 보존
-- =========================================================
BEGIN;

-- 0. 필수 임시 테이블 및 기존 영화 데이터 검사
DO $$
BEGIN
    IF to_regclass('pg_temp.tmp_kmdb_dedup') IS NULL
       OR to_regclass('pg_temp.tmp_tmdb_match') IS NULL
       OR to_regclass('pg_temp.tmp_confirmed_movie_match') IS NULL
       OR to_regclass('pg_temp.tmp_movie_match_candidates') IS NULL
       OR to_regclass('pg_temp.tmp_pending_tmdb_match') IS NULL
       OR to_regclass('pg_temp.tmp_tmdb_latest') IS NULL
    THEN
        RAISE EXCEPTION '01/02 전처리·매칭 임시 테이블이 없습니다. 동일 세션에서 실행하세요.';
    END IF;

    -- TV/도서가 있어도 허용. 기존 영화가 있으면 안전하게 중단.
    IF EXISTS (SELECT 1 FROM public.content WHERE content_type = 'movie')
       OR EXISTS (
           SELECT 1 FROM public.content_source_map
           WHERE source_name IN ('kmdb_movie', 'tmdb_movie')
       )
    THEN
        RAISE EXCEPTION '기존 영화 카탈로그가 있습니다. 03은 최초 영화 적재 전용이며 자동 교체하지 않습니다.';
    END IF;

    IF EXISTS (SELECT 1 FROM tmp_kmdb_dedup WHERE dedup_key LIKE 'raw:%')
    THEN
        RAISE EXCEPTION '영구 식별자가 없는 KMDb 작품이 있습니다. 적재를 중단합니다.';
    END IF;
END $$;

-- 1. KMDb 적재 대상
DROP TABLE IF EXISTS pg_temp.tmp_kmdb_load;
CREATE TEMP TABLE tmp_kmdb_load AS
SELECT
    k.raw_kmdb_movie_id,
    k.dedup_key AS kmdb_source_id,
    NULLIF(TRIM(r.raw_payload->>'영화명'), '') AS title,
    NULLIF(TRIM(r.raw_payload->>'원제명'), '') AS original_title,
    k.year AS release_year,
    k.title AS normalized_title
FROM tmp_kmdb_dedup k
JOIN public.raw_kmdb_movie r
  ON r.raw_kmdb_movie_id = k.raw_kmdb_movie_id;
CREATE UNIQUE INDEX idx_tmp_kmdb_load_source ON tmp_kmdb_load (kmdb_source_id);

-- 2. KMDb content_id 발급 및 적재
ALTER TABLE tmp_kmdb_load ADD COLUMN content_id BIGINT;
UPDATE tmp_kmdb_load
SET content_id = nextval(pg_get_serial_sequence('public.content', 'content_id'));

INSERT INTO public.content
    (content_id, content_type, title, original_title, release_year, normalized_title)
SELECT content_id, 'movie', title, original_title, release_year, normalized_title
FROM tmp_kmdb_load;

-- 3. KMDb source map
INSERT INTO public.content_source_map
    (content_id, source_name, source_id, match_method, match_confidence)
SELECT content_id, 'kmdb_movie', kmdb_source_id, 'source_import', 1.000
FROM tmp_kmdb_load;

-- 4. 확정된 TMDb 매칭 연결
INSERT INTO public.content_source_map
    (content_id, source_name, source_id, match_method, match_confidence)
SELECT k.content_id, 'tmdb_movie', m.tmdb_id::TEXT, m.match_method, NULL
FROM tmp_confirmed_movie_match m
JOIN tmp_kmdb_load k ON k.raw_kmdb_movie_id = m.raw_kmdb_movie_id;

-- 5. TMDb-only 후보 및 정보 부족 사유 분리
-- KMDb와 매칭되거나 매칭 보류 중인 TMDb는 이 필터 대상이 아님.
DROP TABLE IF EXISTS pg_temp.tmp_tmdb_only_filter_flags;
CREATE TEMP TABLE tmp_tmdb_only_filter_flags AS
WITH eligible AS (
    SELECT
        t.tmdb_id,
        NULLIF(TRIM(r.raw_payload->>'title'), '') AS title,
        NULLIF(TRIM(r.raw_payload->>'original_title'), '') AS original_title,
        t.year AS release_year,
        t.title AS normalized_title,
        r.raw_payload
    FROM tmp_tmdb_match t
    JOIN tmp_tmdb_latest r ON r.tmdb_id = t.tmdb_id
    WHERE NOT EXISTS (
        SELECT 1 FROM tmp_confirmed_movie_match m WHERE m.tmdb_id = t.tmdb_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM tmp_movie_match_candidates c WHERE c.tmdb_id = t.tmdb_id
    )
),
features AS (
    SELECT
        e.*,
        CASE WHEN jsonb_typeof(e.raw_payload->'genres') = 'array'
             THEN jsonb_array_length(e.raw_payload->'genres') > 0
             ELSE FALSE END AS has_genres,
        NULLIF(TRIM(e.raw_payload->>'overview'), '') IS NOT NULL AS has_overview,
        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE WHEN jsonb_typeof(e.raw_payload->'credits'->'crew') = 'array'
                     THEN e.raw_payload->'credits'->'crew'
                     ELSE '[]'::JSONB END
            ) AS person
            WHERE person->>'job' = 'Director'
        ) AS has_director
    FROM eligible e
)
SELECT
    tmdb_id, title, original_title, release_year, normalized_title,
    has_genres, has_overview, has_director,
    (NOT has_genres AND NOT has_overview) AS excluded_no_genre_overview,
    (NOT has_overview AND NOT has_director) AS excluded_no_overview_director
FROM features;
CREATE UNIQUE INDEX idx_tmp_tmdb_only_flags_id
    ON tmp_tmdb_only_filter_flags (tmdb_id);
ANALYZE tmp_tmdb_only_filter_flags;

-- 6. TMDb-only 실제 적재 대상
DROP TABLE IF EXISTS pg_temp.tmp_tmdb_only_load;
CREATE TEMP TABLE tmp_tmdb_only_load AS
SELECT tmdb_id, title, original_title, release_year, normalized_title
FROM tmp_tmdb_only_filter_flags
WHERE NOT excluded_no_genre_overview
  AND NOT excluded_no_overview_director;
CREATE UNIQUE INDEX idx_tmp_tmdb_only_id ON tmp_tmdb_only_load (tmdb_id);

-- 7. TMDb-only content_id 발급 및 적재
ALTER TABLE tmp_tmdb_only_load ADD COLUMN content_id BIGINT;
UPDATE tmp_tmdb_only_load
SET content_id = nextval(pg_get_serial_sequence('public.content', 'content_id'));

INSERT INTO public.content
    (content_id, content_type, title, original_title, release_year, normalized_title)
SELECT content_id, 'movie', title, original_title, release_year, normalized_title
FROM tmp_tmdb_only_load;

-- 8. TMDb-only source map
INSERT INTO public.content_source_map
    (content_id, source_name, source_id, match_method, match_confidence)
SELECT content_id, 'tmdb_movie', tmdb_id::TEXT, 'source_import', 1.000
FROM tmp_tmdb_only_load;

-- 9. 무결성 검증: TMDb-only 제외 건수도 분할에 포함
DO $$
DECLARE
    v_total_movies BIGINT;
    v_kmdb_links BIGINT;
    v_tmdb_links BIGINT;
    v_matched BIGINT;
    v_tmdb_only BIGINT;
    v_pending BIGINT;
    v_filtered BIGINT;
    v_dual_links BIGINT;
BEGIN
    SELECT COUNT(*) INTO v_total_movies FROM public.content WHERE content_type = 'movie';
    SELECT COUNT(*) INTO v_kmdb_links FROM public.content_source_map WHERE source_name = 'kmdb_movie';
    SELECT COUNT(*) INTO v_tmdb_links FROM public.content_source_map WHERE source_name = 'tmdb_movie';
    SELECT COUNT(*) INTO v_matched FROM tmp_confirmed_movie_match;
    SELECT COUNT(*) INTO v_tmdb_only FROM tmp_tmdb_only_load;
    SELECT COUNT(*) INTO v_pending FROM tmp_pending_tmdb_match;
    SELECT COUNT(*) INTO v_filtered
    FROM tmp_tmdb_only_filter_flags
    WHERE excluded_no_genre_overview OR excluded_no_overview_director;
    SELECT COUNT(*) INTO v_dual_links FROM (
        SELECT content_id
        FROM public.content_source_map
        WHERE source_name IN ('kmdb_movie', 'tmdb_movie')
        GROUP BY content_id
        HAVING COUNT(DISTINCT source_name) = 2
    ) s;

    IF v_total_movies <> v_kmdb_links + v_tmdb_only THEN
        RAISE EXCEPTION '전체 영화 수 검증 실패';
    END IF;
    IF v_tmdb_links <> v_matched + v_tmdb_only THEN
        RAISE EXCEPTION 'TMDb 연결 수 검증 실패';
    END IF;
    IF v_dual_links <> v_matched THEN
        RAISE EXCEPTION 'KMDb-TMDb 연결 수 검증 실패';
    END IF;
    IF EXISTS (
        SELECT 1 FROM tmp_pending_tmdb_match p
        JOIN public.content_source_map s
          ON s.source_name = 'tmdb_movie' AND s.source_id = p.tmdb_id::TEXT
    ) THEN
        RAISE EXCEPTION '미확정 TMDb 작품이 적재되었습니다.';
    END IF;
    IF (SELECT COUNT(*) FROM tmp_tmdb_match)
        <> v_matched + v_tmdb_only + v_pending + v_filtered THEN
        RAISE EXCEPTION 'TMDb 대상 분할 검증 실패';
    END IF;

    RAISE NOTICE '전체 영화: %', v_total_movies;
    RAISE NOTICE 'KMDb 연결: %', v_kmdb_links;
    RAISE NOTICE 'TMDb 연결: %', v_tmdb_links;
    RAISE NOTICE '확정 매칭: %', v_matched;
    RAISE NOTICE 'TMDb 단독: %', v_tmdb_only;
    RAISE NOTICE 'TMDb 보류: %', v_pending;
    RAISE NOTICE 'TMDb-only 정보 부족 제외: %', v_filtered;
    RAISE NOTICE '적재 검증 통과';
END $$;

-- 10. 최종 결과 및 필터링 사유별 통계
SELECT
    (SELECT COUNT(*) FROM public.content WHERE content_type = 'movie') AS total_movies,
    (SELECT COUNT(*) FROM public.content_source_map WHERE source_name = 'kmdb_movie') AS kmdb_links,
    (SELECT COUNT(*) FROM public.content_source_map WHERE source_name = 'tmdb_movie') AS tmdb_links,
    (SELECT COUNT(*) FROM tmp_confirmed_movie_match) AS matched_movies,
    (SELECT COUNT(*) FROM tmp_tmdb_only_load) AS tmdb_only_movies,
    (SELECT COUNT(*) FROM tmp_pending_tmdb_match) AS pending_tmdb_movies,
    (SELECT COUNT(*) FROM tmp_tmdb_only_filter_flags
     WHERE excluded_no_genre_overview OR excluded_no_overview_director) AS tmdb_only_filtered;

SELECT
    COUNT(*) AS tmdb_only_candidates,
    COUNT(*) FILTER (WHERE excluded_no_genre_overview) AS no_genre_and_overview,
    COUNT(*) FILTER (WHERE excluded_no_overview_director) AS no_overview_and_director,
    COUNT(*) FILTER (WHERE excluded_no_genre_overview OR excluded_no_overview_director) AS total_filtered,
    COUNT(*) FILTER (WHERE NOT excluded_no_genre_overview AND NOT excluded_no_overview_director) AS total_loaded
FROM tmp_tmdb_only_filter_flags;

COMMIT;
