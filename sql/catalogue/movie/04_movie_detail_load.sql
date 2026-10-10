BEGIN;

-- =========================================================
-- 1. KMDb 대표 원본 복원
-- =========================================================

CREATE TEMP TABLE tmp_kmdb_detail ON COMMIT DROP AS
SELECT DISTINCT ON (m.content_id)
    m.content_id,
    r.raw_payload
FROM public.content_source_map m
JOIN public.raw_kmdb_movie r
    ON CONCAT(
        r.raw_payload->>'영화등록번호ID',
        ':',
        r.raw_payload->>'영화등록번호NO'
    ) = m.source_id
WHERE m.source_name = 'kmdb_movie'
ORDER BY
    m.content_id,
    LENGTH(COALESCE(TRIM(r.raw_payload->>'줄거리'), '')) DESC,
    r.raw_kmdb_movie_id;

CREATE UNIQUE INDEX ON tmp_kmdb_detail (content_id);


-- =========================================================
-- 2. TMDb 최신 원본 복원
-- =========================================================

CREATE TEMP TABLE tmp_tmdb_detail ON COMMIT DROP AS
SELECT DISTINCT ON (m.content_id)
    m.content_id,
    r.raw_payload
FROM public.content_source_map m
JOIN public.raw_tmdb_movie r
    ON r.tmdb_id::TEXT = m.source_id
WHERE m.source_name = 'tmdb_movie'
ORDER BY
    m.content_id,
    r.fetched_at DESC,
    r.raw_tmdb_movie_id DESC;

CREATE UNIQUE INDEX ON tmp_tmdb_detail (content_id);


-- =========================================================
-- 3. 영화별 원본 결합
-- =========================================================

CREATE TEMP TABLE tmp_movie_source ON COMMIT DROP AS
SELECT
    c.content_id,
    k.raw_payload AS kmdb,
    t.raw_payload AS tmdb,

    NULLIF(TRIM(k.raw_payload->>'대표개봉일'), '')
        AS kmdb_date_text,

    NULLIF(TRIM(t.raw_payload->>'release_date'), '')
        AS tmdb_date_text,

    NULLIF(TRIM(k.raw_payload->>'대표상영시간'), '')
        AS kmdb_runtime_text,

    NULLIF(TRIM(t.raw_payload->>'runtime'), '')
        AS tmdb_runtime_text

FROM public.content c

LEFT JOIN tmp_kmdb_detail k
    ON k.content_id = c.content_id

LEFT JOIN tmp_tmdb_detail t
    ON t.content_id = c.content_id

WHERE c.content_type = 'movie';

CREATE UNIQUE INDEX ON tmp_movie_source (content_id);


-- =========================================================
-- 4. 날짜 및 상영시간 정제
-- =========================================================

CREATE TEMP TABLE tmp_movie_clean ON COMMIT DROP AS

WITH normalized AS (
    SELECT
        s.*,

        -- KMDb: YYYYMMDD → YYYY-MM-DD
        CASE
            WHEN kmdb_date_text ~ '^[0-9]{8}$'
            THEN
                SUBSTRING(kmdb_date_text, 1, 4)
                || '-' ||
                SUBSTRING(kmdb_date_text, 5, 2)
                || '-' ||
                SUBSTRING(kmdb_date_text, 7, 2)
            ELSE NULL
        END AS kmdb_date_iso,

        -- TMDb: YYYY-MM-DD
        CASE
            WHEN tmdb_date_text ~
                 '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
            THEN tmdb_date_text
            ELSE NULL
        END AS tmdb_date_iso,

        -- KMDb 상영시간
        CASE
            WHEN kmdb_runtime_text ~ '^[0-9]{1,5}$'
             AND kmdb_runtime_text::NUMERIC
                 BETWEEN 1 AND 10000
            THEN kmdb_runtime_text::INTEGER
            ELSE NULL
        END AS kmdb_runtime,

        -- TMDb 상영시간
        CASE
            WHEN tmdb_runtime_text ~ '^[0-9]{1,5}$'
             AND tmdb_runtime_text::NUMERIC
                 BETWEEN 1 AND 10000
            THEN tmdb_runtime_text::INTEGER
            ELSE NULL
        END AS tmdb_runtime

    FROM tmp_movie_source s
)

SELECT
    n.*,

    -- PostgreSQL 18: 유효한 날짜만 DATE 변환
    CASE
        WHEN pg_input_is_valid(kmdb_date_iso, 'date')
        THEN kmdb_date_iso::DATE
        ELSE NULL
    END AS kmdb_release_date,

    CASE
        WHEN pg_input_is_valid(tmdb_date_iso, 'date')
        THEN tmdb_date_iso::DATE
        ELSE NULL
    END AS tmdb_release_date

FROM normalized n;


-- =========================================================
-- 5. 영화 상세정보 적재
-- =========================================================

INSERT INTO public.movie_detail (
    content_id,
    overview,
    release_date,
    runtime,
    genres,
    keywords,
    directors,
    cast_members,
    poster_path,
    updated_at
)

SELECT
    s.content_id,

    -- -----------------------------------------------------
    -- 5-1. 줄거리: KMDb 우선, TMDb 보완
    -- -----------------------------------------------------

    COALESCE(
        NULLIF(TRIM(s.kmdb->>'줄거리'), ''),
        NULLIF(TRIM(s.tmdb->>'overview'), '')
    ) AS overview,


    -- -----------------------------------------------------
    -- 5-2. 개봉일: KMDb 우선, TMDb 보완
    -- -----------------------------------------------------

    COALESCE(
        s.kmdb_release_date,
        s.tmdb_release_date
    ) AS release_date,


    -- -----------------------------------------------------
    -- 5-3. 상영시간: KMDb 우선, TMDb 보완
    -- -----------------------------------------------------

    COALESCE(
        s.kmdb_runtime,
        s.tmdb_runtime
    ) AS runtime,


    -- -----------------------------------------------------
    -- 5-4. 장르: KMDb + TMDb
    -- -----------------------------------------------------

    (
        SELECT COALESCE(
            jsonb_agg(x.item),
            '[]'::jsonb
        )
        FROM (
            SELECT jsonb_build_object(
                'source', 'kmdb',
                'name', TRIM(v)
            ) AS item

            FROM regexp_split_to_table(
                COALESCE(s.kmdb->>'장르', ''),
                '[,/;|]'
            ) AS v

            WHERE TRIM(v) <> ''

            UNION ALL

            SELECT jsonb_build_object(
                'source', 'tmdb',
                'id', g->'id',
                'name', g->>'name'
            ) AS item

            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(s.tmdb->'genres') = 'array'
                    THEN s.tmdb->'genres'
                    ELSE '[]'::jsonb
                END
            ) AS g

            WHERE NULLIF(TRIM(g->>'name'), '') IS NOT NULL
        ) x
    ) AS genres,


    -- -----------------------------------------------------
    -- 5-5. 키워드: KMDb + TMDb
    -- -----------------------------------------------------

    (
        SELECT COALESCE(
            jsonb_agg(x.item),
            '[]'::jsonb
        )
        FROM (
            SELECT jsonb_build_object(
                'source', 'kmdb',
                'name', TRIM(v)
            ) AS item

            FROM regexp_split_to_table(
                COALESCE(s.kmdb->>'키워드', ''),
                '[,/;|]'
            ) AS v

            WHERE TRIM(v) <> ''

            UNION ALL

            SELECT jsonb_build_object(
                'source', 'tmdb',
                'id', kw->'id',
                'name', kw->>'name'
            ) AS item

            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(
                        s.tmdb#>'{keywords,keywords}'
                    ) = 'array'
                    THEN s.tmdb#>'{keywords,keywords}'
                    ELSE '[]'::jsonb
                END
            ) AS kw

            WHERE NULLIF(TRIM(kw->>'name'), '') IS NOT NULL
        ) x
    ) AS keywords,


    -- -----------------------------------------------------
    -- 5-6. 감독: KMDb + TMDb
    -- -----------------------------------------------------

    (
        SELECT COALESCE(
            jsonb_agg(x.item),
            '[]'::jsonb
        )
        FROM (
            SELECT jsonb_build_object(
                'source', 'kmdb',
                'name', TRIM(v)
            ) AS item

            FROM regexp_split_to_table(
                COALESCE(s.kmdb->>'감독', ''),
                '[,;/|·、，]+'
            ) AS v

            WHERE TRIM(v) <> ''

            UNION ALL

            SELECT jsonb_build_object(
                'source', 'tmdb',
                'person_id', crew->'id',
                'name', crew->>'name'
            ) AS item

            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(
                        s.tmdb#>'{credits,crew}'
                    ) = 'array'
                    THEN s.tmdb#>'{credits,crew}'
                    ELSE '[]'::jsonb
                END
            ) AS crew

            WHERE crew->>'job' = 'Director'
              AND NULLIF(TRIM(crew->>'name'), '') IS NOT NULL
        ) x
    ) AS directors,


    -- -----------------------------------------------------
    -- 5-7. 배우: KMDb 상위 10명 + TMDb 상위 10명
    -- -----------------------------------------------------

    (
        SELECT COALESCE(
            jsonb_agg(
                a.item
                ORDER BY a.source_priority, a.billing_order
            ),
            '[]'::jsonb
        )

        FROM (

            -- KMDb: 원본 기재 순서 기준 상위 10명
            SELECT
                1 AS source_priority,
                km.billing_order,
                jsonb_build_object(
                    'source', 'kmdb',
                    'name', km.actor_name,
                    'character', km.character_name,
                    'billing_order', km.billing_order
                ) AS item

            FROM LATERAL (
                SELECT
                    p.actor_name,
                    p.character_name,
                    p.billing_order

                FROM (
                    SELECT
                        v.ordinality::INTEGER - 1
                            AS billing_order,

                        NULLIF(
                            TRIM(
                                regexp_replace(
                                    TRIM(v.actor_text),
                                    '\([^()]*\)$',
                                    ''
                                )
                            ),
                            ''
                        ) AS actor_name,

                        CASE
                            WHEN TRIM(v.actor_text) ~ '\([^()]*\)$'
                            THEN NULLIF(
                                TRIM(
                                    substring(
                                        TRIM(v.actor_text)
                                        FROM '\(([^()]*)\)$'
                                    )
                                ),
                                ''
                            )
                            ELSE NULL
                        END AS character_name

                    FROM regexp_split_to_table(
                        COALESCE(s.kmdb->>'출연', ''),
                        ','
                    ) WITH ORDINALITY
                      AS v(actor_text, ordinality)

                ) p

                WHERE p.actor_name IS NOT NULL

                ORDER BY p.billing_order

                LIMIT 10
            ) km

            UNION ALL

            -- TMDb: cast.order 기준 상위 10명
            SELECT
                2 AS source_priority,
                tm.billing_order,

                jsonb_build_object(
                    'source', 'tmdb',
                    'person_id', tm.person_id,
                    'name', tm.actor_name,
                    'character', tm.character_name,
                    'billing_order', tm.billing_order
                ) AS item

            FROM LATERAL (
                SELECT
                    p.person_id,
                    p.actor_name,
                    p.character_name,
                    p.billing_order

                FROM (
                    SELECT
                        actor.value->'id' AS person_id,
                        actor.value->>'name' AS actor_name,
                        actor.value->>'character' AS character_name,

                        CASE
                            WHEN actor.value->>'order'
                                 ~ '^[0-9]{1,5}$'
                            THEN (actor.value->>'order')::INTEGER
                            ELSE NULL
                        END AS billing_order,

                        actor.ordinality

                    FROM jsonb_array_elements(
                        CASE
                            WHEN jsonb_typeof(
                                s.tmdb#>'{credits,cast}'
                            ) = 'array'
                            THEN s.tmdb#>'{credits,cast}'
                            ELSE '[]'::jsonb
                        END
                    ) WITH ORDINALITY
                      AS actor(value, ordinality)

                ) p

                WHERE NULLIF(TRIM(p.actor_name), '') IS NOT NULL

                ORDER BY
                    p.billing_order NULLS LAST,
                    p.ordinality

                LIMIT 10
            ) tm

        ) a
    ) AS cast_members,


    -- -----------------------------------------------------
    -- 5-8. 포스터: TMDb
    -- -----------------------------------------------------

    NULLIF(
        TRIM(s.tmdb->>'poster_path'),
        ''
    ) AS poster_path,

    NOW()

FROM tmp_movie_clean s

ON CONFLICT (content_id)
DO UPDATE SET
    overview = EXCLUDED.overview,
    release_date = EXCLUDED.release_date,
    runtime = EXCLUDED.runtime,
    genres = EXCLUDED.genres,
    keywords = EXCLUDED.keywords,
    directors = EXCLUDED.directors,
    cast_members = EXCLUDED.cast_members,
    poster_path = EXCLUDED.poster_path,
    updated_at = NOW();


-- =========================================================
-- 6. 적재 검증
-- =========================================================

DO $$
DECLARE
    expected_count BIGINT;
    actual_count BIGINT;

    kmdb_count BIGINT;
    tmdb_count BIGINT;

    missing_source_count BIGINT;
    invalid_cast_count BIGINT;
    missing_detail_count BIGINT;

BEGIN

    -- 전체 영화 수
    SELECT COUNT(*)
    INTO expected_count
    FROM public.content
    WHERE content_type = 'movie';

    -- 실제 상세정보 수
    SELECT COUNT(*)
    INTO actual_count
    FROM public.movie_detail;

    -- KMDb 연결 수
    SELECT COUNT(*)
    INTO kmdb_count
    FROM tmp_kmdb_detail;

    -- TMDb 연결 수
    SELECT COUNT(*)
    INTO tmdb_count
    FROM tmp_tmdb_detail;

    -- 원본 없는 영화
    SELECT COUNT(*)
    INTO missing_source_count
    FROM tmp_movie_source
    WHERE kmdb IS NULL
      AND tmdb IS NULL;

    -- 상세정보 누락 영화
    SELECT COUNT(*)
    INTO missing_detail_count
    FROM public.content c
    LEFT JOIN public.movie_detail d
        ON d.content_id = c.content_id
    WHERE c.content_type = 'movie'
      AND d.content_id IS NULL;

    -- 출처별 배우 10명 제한 검증
    SELECT COUNT(*)
    INTO invalid_cast_count
    FROM public.movie_detail d
    WHERE (
        SELECT COUNT(*)
        FROM jsonb_array_elements(d.cast_members) a
        WHERE a->>'source' = 'kmdb'
    ) > 10
    OR (
        SELECT COUNT(*)
        FROM jsonb_array_elements(d.cast_members) a
        WHERE a->>'source' = 'tmdb'
    ) > 10;

    -- 검증 1: 전체 건수
    IF actual_count <> expected_count THEN
        RAISE EXCEPTION
            '영화 상세정보 건수 불일치: expected %, actual %',
            expected_count,
            actual_count;
    END IF;

    -- 검증 2: 원본 연결
    IF missing_source_count > 0 THEN
        RAISE EXCEPTION
            '원본 연결 실패: %건',
            missing_source_count;
    END IF;

    -- 검증 3: 상세정보 누락
    IF missing_detail_count > 0 THEN
        RAISE EXCEPTION
            '상세정보 누락: %건',
            missing_detail_count;
    END IF;

    -- 검증 4: 배우 수 제한
    IF invalid_cast_count > 0 THEN
        RAISE EXCEPTION
            '출처별 배우 10명 제한 위반: %건',
            invalid_cast_count;
    END IF;

    RAISE NOTICE '================================';
    RAISE NOTICE '영화 상세정보 적재 검증 통과';
    RAISE NOTICE '전체 영화: %건', actual_count;
    RAISE NOTICE 'KMDb 연결: %건', kmdb_count;
    RAISE NOTICE 'TMDb 연결: %건', tmdb_count;
    RAISE NOTICE '배우 제한: 출처별 최대 10명';
    RAISE NOTICE '================================';

END $$;

COMMIT;