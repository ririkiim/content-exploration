-- =========================================================
-- 01_movie_prepare.sql
-- KMDb / TMDb 영화 전처리
--
-- RAW 데이터는 수정하지 않음
-- TEMP 테이블은 현재 DB 세션에서만 유지
--
-- 필터링 정책
-- 1. KMDb 에로 장르 제외
-- 2. KMDb 줄거리 + 감독 모두 누락 시 제외
-- 3. KMDb / TMDb 제목 기반 성인물 의심 표현 제외
-- 4. TMDb adult=true 제외
-- 5. TMDb softcore 키워드 제외
-- 6. KMDb 성인물 제외 작품의 TMDb 재유입 방지
--
-- 중요:
-- KMDb 정보 부족만으로 제외된 작품은
-- TMDb에서 다시 확인할 수 있도록 허용
--
-- TMDb-only 메타데이터 부족 필터는
-- 매칭 결과 확정 후 별도 적용
-- =========================================================


-- =========================================================
-- 0. 제목 필터링 패턴
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_movie_filter_patterns;

CREATE TEMP TABLE tmp_movie_filter_patterns AS
SELECT
    '(^|[^가-힣a-zA-Z])(에로|포르노|스와핑|19금|성인비디오|AV배우|AV영화)([^가-힣a-zA-Z]|$)'
        ::TEXT AS strong_title_pattern,

    '(^|[^가-힣a-zA-Z])(에로영화|에로물|에로비디오|포르노영화)'
        ::TEXT AS compound_title_pattern,

    '(^|[^가-힣])(유부녀|새엄마|젊은엄마|처제|형수|장모|며느리|마사지|정사|섹스)([^가-힣]|$)'
        ::TEXT AS expanded_title_pattern,

    '(유부녀|젊은엄마|새엄마|스와핑|섹스)'
        ::TEXT AS compound_expanded_pattern;


-- =========================================================
-- 1. KMDb 기본 전처리
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_kmdb_prepared;

CREATE TEMP TABLE tmp_kmdb_prepared AS

WITH prepared AS (
    SELECT
        r.raw_kmdb_movie_id,
        r.raw_payload,

        NULLIF(TRIM(r.raw_payload->>'영화등록번호ID'), '') AS movie_id,
        NULLIF(TRIM(r.raw_payload->>'영화등록번호NO'), '') AS movie_no,

        REGEXP_REPLACE(
            LOWER(TRIM(r.raw_payload->>'영화명')),
            '[[:space:][:punct:]]',
            '',
            'g'
        ) AS normalized_title,

        CASE
            WHEN r.raw_payload->>'제작년도' ~ '^[0-9]{4}$'
            THEN (r.raw_payload->>'제작년도')::INTEGER
        END AS production_year,

        NULLIF(TRIM(r.raw_payload->>'감독'), '') AS director,

        NULLIF(TRIM(r.raw_payload->>'줄거리'), '') AS overview,

        COALESCE(r.raw_payload->>'장르', '') AS genre,

        COALESCE(r.raw_payload->>'영화명', '') AS original_title

    FROM public.raw_kmdb_movie r

    WHERE r.raw_payload->>'제작국가' LIKE '%대한민국%'

      AND r.raw_payload->>'유형' IN (
          '극영화',
          '애니메이션',
          '다큐멘터리',
          '실험영화',
          '(실황)공연물',
          'TV-영화',
          '미디어아트',
          '온라인 영화',
          '온라인 애니메이션',
          '온라인 다큐멘터리'
      )
),

identified AS (
    SELECT
        *,

        CASE
            WHEN movie_id IS NOT NULL
             AND movie_no IS NOT NULL
             AND LOWER(movie_id) <> 'null'
             AND LOWER(movie_no) <> 'null'
            THEN movie_id || ':' || movie_no

            ELSE 'raw:' || raw_kmdb_movie_id::TEXT
        END AS dedup_key

    FROM prepared
),

ranked AS (
    SELECT
        *,

        ROW_NUMBER() OVER (
            PARTITION BY dedup_key
            ORDER BY
                LENGTH(COALESCE(overview, '')) DESC,
                raw_kmdb_movie_id
        ) AS rn

    FROM identified
)

SELECT
    raw_kmdb_movie_id,
    dedup_key,
    normalized_title AS title,
    production_year AS year,
    director,
    overview,
    genre,
    original_title

FROM ranked

WHERE rn = 1
  AND normalized_title IS NOT NULL
  AND normalized_title <> ''
  AND production_year IS NOT NULL;


CREATE UNIQUE INDEX idx_tmp_kmdb_prepared_key
    ON tmp_kmdb_prepared (dedup_key);

CREATE INDEX idx_tmp_kmdb_prepared_title_year
    ON tmp_kmdb_prepared (title, year);

ANALYZE tmp_kmdb_prepared;


-- =========================================================
-- 2. KMDb 제외 사유별 플래그
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_kmdb_filter_flags;

CREATE TEMP TABLE tmp_kmdb_filter_flags AS

SELECT
    k.*,

    (
        k.genre LIKE '%에로%'
    ) AS excluded_genre,

    (
        k.overview IS NULL
        AND k.director IS NULL
    ) AS excluded_missing_metadata,

    (
        k.original_title ~* p.strong_title_pattern
        OR k.original_title ~* p.compound_title_pattern
        OR k.original_title ~* p.expanded_title_pattern
        OR k.original_title ~* p.compound_expanded_pattern
    ) AS excluded_title

FROM tmp_kmdb_prepared k
CROSS JOIN tmp_movie_filter_patterns p;


CREATE INDEX idx_tmp_kmdb_flags_title_year
    ON tmp_kmdb_filter_flags (title, year);

ANALYZE tmp_kmdb_filter_flags;


-- =========================================================
-- 3. KMDb 서비스 대상
--
-- 모든 제외 사유 적용
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_kmdb_dedup;

CREATE TEMP TABLE tmp_kmdb_dedup AS

SELECT
    raw_kmdb_movie_id,
    dedup_key,
    title,
    year,
    director

FROM tmp_kmdb_filter_flags

WHERE NOT excluded_genre
  AND NOT excluded_missing_metadata
  AND NOT excluded_title;


CREATE UNIQUE INDEX idx_tmp_kmdb_dedup_key
    ON tmp_kmdb_dedup (dedup_key);

CREATE INDEX idx_tmp_kmdb_dedup_title_year
    ON tmp_kmdb_dedup (title, year);

ANALYZE tmp_kmdb_dedup;


-- =========================================================
-- 4. KMDb 성인물 제외 작품 목록
--
-- TMDb 재유입 차단용
--
-- 정보 부족만으로 제외된 작품은
-- 차단 목록에 포함하지 않음
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_kmdb_excluded;

CREATE TEMP TABLE tmp_kmdb_excluded AS

SELECT DISTINCT
    title,
    year

FROM tmp_kmdb_filter_flags

WHERE excluded_genre
   OR excluded_title;


CREATE INDEX idx_tmp_kmdb_excluded_title_year
    ON tmp_kmdb_excluded (title, year);

ANALYZE tmp_kmdb_excluded;


-- =========================================================
-- 5. TMDb 최신 RAW 선택
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_tmdb_latest;

CREATE TEMP TABLE tmp_tmdb_latest AS

SELECT DISTINCT ON (tmdb_id)
    raw_tmdb_movie_id,
    tmdb_id,
    raw_payload,
    fetched_at

FROM public.raw_tmdb_movie

ORDER BY
    tmdb_id,
    fetched_at DESC,
    raw_tmdb_movie_id DESC;


CREATE UNIQUE INDEX idx_tmp_tmdb_latest_id
    ON tmp_tmdb_latest (tmdb_id);

ANALYZE tmp_tmdb_latest;


-- =========================================================
-- 6. TMDb 기본 전처리 및 필터링
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_tmdb_match;

CREATE TEMP TABLE tmp_tmdb_match AS

WITH tmdb_prepared AS (
    SELECT
        t.tmdb_id,
        t.raw_tmdb_movie_id,

        REGEXP_REPLACE(
            LOWER(TRIM(t.raw_payload->>'title')),
            '[[:space:][:punct:]]',
            '',
            'g'
        ) AS title,

        CASE
            WHEN t.raw_payload->>'release_date'
                 ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            THEN LEFT(t.raw_payload->>'release_date', 4)::INTEGER
        END AS year,

        COALESCE(t.raw_payload->>'title', '') AS original_title,

        t.raw_payload

    FROM tmp_tmdb_latest t

    WHERE (
        t.raw_payload->'production_countries'
            @> '[{"iso_3166_1":"KR"}]'::JSONB

        OR t.raw_payload->'origin_country' ? 'KR'
    )

    AND LOWER(
        COALESCE(t.raw_payload->>'adult', 'false')
    ) <> 'true'
),

tmdb_flags AS (
    SELECT
        t.*,

        (
            t.original_title ~* p.strong_title_pattern
            OR t.original_title ~* p.compound_title_pattern
            OR t.original_title ~* p.expanded_title_pattern
            OR t.original_title ~* p.compound_expanded_pattern
        ) AS excluded_title,

        EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(
                        t.raw_payload->'keywords'
                    ) = 'array'
                    THEN t.raw_payload->'keywords'

                    WHEN jsonb_typeof(
                        t.raw_payload->'keywords'->'keywords'
                    ) = 'array'
                    THEN t.raw_payload->'keywords'->'keywords'

                    ELSE '[]'::JSONB
                END
            ) kw

            WHERE kw->>'name' ILIKE '%softcore%'
               OR kw->>'id' = '155477'
        ) AS excluded_softcore

    FROM tmdb_prepared t
    CROSS JOIN tmp_movie_filter_patterns p
)

SELECT
    t.tmdb_id,
    t.raw_tmdb_movie_id,
    t.title,
    t.year

FROM tmdb_flags t

WHERE t.title IS NOT NULL
  AND t.title <> ''
  AND t.year IS NOT NULL

  AND NOT t.excluded_title
  AND NOT t.excluded_softcore

  -- KMDb에서 성인물 사유로 제외된 작품만 차단
  AND NOT EXISTS (
      SELECT 1
      FROM tmp_kmdb_excluded k
      WHERE k.title = t.title
        AND ABS(k.year - t.year) <= 1
  );


CREATE UNIQUE INDEX idx_tmp_tmdb_match_id
    ON tmp_tmdb_match (tmdb_id);

CREATE INDEX idx_tmp_tmdb_match_title_year
    ON tmp_tmdb_match (title, year);

ANALYZE tmp_tmdb_match;


-- =========================================================
-- 7. 전처리 결과 및 제외 사유 확인
-- =========================================================

SELECT
    'KMDb' AS source,
    COUNT(*) AS prepared_movies
FROM tmp_kmdb_dedup

UNION ALL

SELECT
    'TMDb' AS source,
    COUNT(*) AS prepared_movies
FROM tmp_tmdb_match;


-- =========================================================
-- 8. KMDb 제외 사유별 건수
-- =========================================================

SELECT
    COUNT(*) AS kmdb_candidates,

    COUNT(*) FILTER (
        WHERE excluded_genre
    ) AS excluded_genre,

    COUNT(*) FILTER (
        WHERE excluded_missing_metadata
    ) AS excluded_missing_metadata,

    COUNT(*) FILTER (
        WHERE excluded_title
    ) AS excluded_title,

    COUNT(*) FILTER (
        WHERE excluded_missing_metadata
          AND NOT excluded_genre
          AND NOT excluded_title
    ) AS missing_metadata_only,

    COUNT(*) FILTER (
        WHERE excluded_genre OR excluded_title
    ) AS tmdb_reentry_block_candidates,

    COUNT(*) FILTER (
        WHERE NOT excluded_genre
          AND NOT excluded_missing_metadata
          AND NOT excluded_title
    ) AS kmdb_remaining

FROM tmp_kmdb_filter_flags;