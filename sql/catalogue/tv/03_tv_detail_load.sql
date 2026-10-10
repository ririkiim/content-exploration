/*
============================================================
03_tv_detail_load.sql

목적
- TMDb TV 상세정보를 tv_detail에 적재
- 관계 후보 제작진 19개 직책만 선별
- 기존 content / content_source_map 유지
- 재실행 시 상세정보 갱신

원본
- raw_tmdb_tv.raw_payload

원칙
- 원본 crew 전체는 raw에 보존
- 서비스용 crew는 관계 후보 직책만 저장
- created_by와 credits.crew는 별도 보존
- 제목/연도 기준으로 콘텐츠 병합하지 않음
============================================================
*/

BEGIN;


-- =========================================================
-- 01. tv_detail 컬럼 확장
-- =========================================================

ALTER TABLE tv_detail
    ADD COLUMN IF NOT EXISTS crew
        JSONB NOT NULL DEFAULT '[]'::jsonb,

    ADD COLUMN IF NOT EXISTS networks
        JSONB NOT NULL DEFAULT '[]'::jsonb,

    ADD COLUMN IF NOT EXISTS production_companies
        JSONB NOT NULL DEFAULT '[]'::jsonb,

    ADD COLUMN IF NOT EXISTS episode_runtime
        INTEGER;


-- =========================================================
-- 02. 상세정보 적재 대상 검증
-- =========================================================

DO $$
DECLARE
    mapping_count BIGINT;
    raw_match_count BIGINT;
BEGIN

    SELECT COUNT(*)
    INTO mapping_count
    FROM content_source_map sm
    JOIN content c
        ON c.content_id = sm.content_id
    WHERE sm.source_name = 'tmdb_tv'
      AND c.content_type = 'tv';

    SELECT COUNT(*)
    INTO raw_match_count
    FROM content_source_map sm
    JOIN content c
        ON c.content_id = sm.content_id
    JOIN raw_tmdb_tv r
        ON r.tmdb_id::text = sm.source_id
    WHERE sm.source_name = 'tmdb_tv'
      AND c.content_type = 'tv';

    IF mapping_count <> 2963
       OR raw_match_count <> mapping_count
    THEN
        RAISE EXCEPTION
            'TV detail load aborted: mappings %, raw matches %, expected 2963',
            mapping_count,
            raw_match_count;
    END IF;

END $$;


-- =========================================================
-- 03. TV 상세정보 적재
-- =========================================================

WITH source_data AS (
    SELECT
        sm.content_id,
        r.raw_payload AS p

    FROM content_source_map sm

    JOIN content c
        ON c.content_id = sm.content_id
       AND c.content_type = 'tv'

    JOIN raw_tmdb_tv r
        ON r.tmdb_id::text = sm.source_id

    WHERE sm.source_name = 'tmdb_tv'
),

prepared AS (
    SELECT
        s.content_id,

        -- 줄거리
        NULLIF(
            BTRIM(s.p->>'overview'),
            ''
        ) AS overview,


        -- 첫 방송일
        CASE
            WHEN s.p->>'first_air_date'
                 ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
             AND LEFT(s.p->>'first_air_date', 4) <> '0000'
            THEN (s.p->>'first_air_date')::date
            ELSE NULL
        END AS first_air_date,


        -- 마지막 방송일
        CASE
            WHEN s.p->>'last_air_date'
                 ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
             AND LEFT(s.p->>'last_air_date', 4) <> '0000'
            THEN (s.p->>'last_air_date')::date
            ELSE NULL
        END AS last_air_date,


        -- 시즌 수
        CASE
            WHEN s.p->>'number_of_seasons' ~ '^[0-9]+$'
            THEN (s.p->>'number_of_seasons')::integer
            ELSE NULL
        END AS number_of_seasons,


        -- 회차 수
        CASE
            WHEN s.p->>'number_of_episodes' ~ '^[0-9]+$'
            THEN (s.p->>'number_of_episodes')::integer
            ELSE NULL
        END AS number_of_episodes,


        -- 장르
        CASE
            WHEN jsonb_typeof(s.p->'genres') = 'array'
            THEN s.p->'genres'
            ELSE '[]'::jsonb
        END AS genres,


        -- 키워드: TMDb TV는 keywords.results
        CASE
            WHEN jsonb_typeof(
                s.p #> '{keywords,results}'
            ) = 'array'
            THEN s.p #> '{keywords,results}'
            ELSE '[]'::jsonb
        END AS keywords,


        -- 창작자: created_by 원본 보존
        CASE
            WHEN jsonb_typeof(s.p->'created_by') = 'array'
            THEN s.p->'created_by'
            ELSE '[]'::jsonb
        END AS creators,


        -- 출연진: order 기준 상위 10명
        CASE
            WHEN jsonb_typeof(
                s.p #> '{credits,cast}'
            ) = 'array'
            THEN (
                SELECT COALESCE(
                    jsonb_agg(
                        actor.value
                        ORDER BY
                            CASE
                                WHEN actor.value->>'order'
                                     ~ '^[0-9]+$'
                                THEN (
                                    actor.value->>'order'
                                )::integer
                                ELSE 999999
                            END,
                            actor.ordinality
                    ),
                    '[]'::jsonb
                )

                FROM (
                    SELECT
                        value,
                        ordinality

                    FROM jsonb_array_elements(
                        s.p #> '{credits,cast}'
                    ) WITH ORDINALITY

                    ORDER BY
                        CASE
                            WHEN value->>'order' ~ '^[0-9]+$'
                            THEN (value->>'order')::integer
                            ELSE 999999
                        END,
                        ordinality

                    LIMIT 10
                ) actor
            )
            ELSE '[]'::jsonb
        END AS cast_members,


        -- 제작진: 관계 후보 직책 19개만 저장
        (
            SELECT COALESCE(
                jsonb_agg(
                    member.value
                    ORDER BY member.ordinality
                ),
                '[]'::jsonb
            )

            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(
                        s.p #> '{credits,crew}'
                    ) = 'array'
                    THEN s.p #> '{credits,crew}'
                    ELSE '[]'::jsonb
                END
            ) WITH ORDINALITY AS member(value, ordinality)

            WHERE member.value->>'job' IN (

                -- 감독
                'Director',

                -- 작가 / 각본
                'Writer',
                'Screenplay',
                'Story',
                'Screenstory',
                'Co-Writer',
                'Scenario Writer',
                'Staff Writer',
                'Teleplay',

                -- 창작 / 원안
                'Creator',
                'Original Series Creator',
                'Original Story',
                'Original Concept',
                'Original Film Writer',

                -- 원작 / 각색
                'Novel',
                'Comic Book',
                'Book',
                'Author',
                'Adaptation'
            )
        ) AS crew,


        -- 방송사 / 네트워크
        CASE
            WHEN jsonb_typeof(s.p->'networks') = 'array'
            THEN s.p->'networks'
            ELSE '[]'::jsonb
        END AS networks,


        -- 제작사
        CASE
            WHEN jsonb_typeof(
                s.p->'production_companies'
            ) = 'array'
            THEN s.p->'production_companies'
            ELSE '[]'::jsonb
        END AS production_companies,


        -- 회당 상영시간: 첫 번째 유효한 양수
        (
            SELECT
                (runtime.value #>> '{}')::integer

            FROM jsonb_array_elements(
                CASE
                    WHEN jsonb_typeof(
                        s.p->'episode_run_time'
                    ) = 'array'
                    THEN s.p->'episode_run_time'
                    ELSE '[]'::jsonb
                END
            ) WITH ORDINALITY AS runtime(value, ordinality)

            WHERE runtime.value #>> '{}' ~ '^[0-9]+$'
              AND (runtime.value #>> '{}')::numeric > 0
              AND (runtime.value #>> '{}')::numeric <= 2147483647

            ORDER BY runtime.ordinality

            LIMIT 1
        ) AS episode_runtime,


        -- 포스터
        NULLIF(
            BTRIM(s.p->>'poster_path'),
            ''
        ) AS poster_path

    FROM source_data s
)


INSERT INTO tv_detail (
    content_id,
    overview,
    first_air_date,
    last_air_date,
    number_of_seasons,
    number_of_episodes,
    genres,
    keywords,
    creators,
    cast_members,
    crew,
    networks,
    production_companies,
    episode_runtime,
    poster_path,
    updated_at
)

SELECT
    content_id,
    overview,
    first_air_date,
    last_air_date,
    number_of_seasons,
    number_of_episodes,
    genres,
    keywords,
    creators,
    cast_members,
    crew,
    networks,
    production_companies,
    episode_runtime,
    poster_path,
    NOW()

FROM prepared

ON CONFLICT (content_id)
DO UPDATE SET
    overview = EXCLUDED.overview,
    first_air_date = EXCLUDED.first_air_date,
    last_air_date = EXCLUDED.last_air_date,
    number_of_seasons = EXCLUDED.number_of_seasons,
    number_of_episodes = EXCLUDED.number_of_episodes,
    genres = EXCLUDED.genres,
    keywords = EXCLUDED.keywords,
    creators = EXCLUDED.creators,
    cast_members = EXCLUDED.cast_members,
    crew = EXCLUDED.crew,
    networks = EXCLUDED.networks,
    production_companies = EXCLUDED.production_companies,
    episode_runtime = EXCLUDED.episode_runtime,
    poster_path = EXCLUDED.poster_path,
    updated_at = NOW();


-- =========================================================
-- 04. 적재 결과 검증
-- =========================================================

DO $$
DECLARE
    expected_count BIGINT;
    actual_count BIGINT;
BEGIN

    SELECT COUNT(DISTINCT sm.content_id)
    INTO expected_count

    FROM content_source_map sm

    JOIN content c
        ON c.content_id = sm.content_id

    WHERE sm.source_name = 'tmdb_tv'
      AND c.content_type = 'tv';


    SELECT COUNT(*)
    INTO actual_count

    FROM tv_detail d

    JOIN content c
        ON c.content_id = d.content_id

    WHERE c.content_type = 'tv';


    IF expected_count <> 2963
       OR actual_count <> expected_count
    THEN
        RAISE EXCEPTION
            'TV detail verification failed: expected %, actual %',
            expected_count,
            actual_count;
    END IF;

END $$;


-- =========================================================
-- 05. 상세정보 확보율 확인
-- =========================================================

SELECT
    COUNT(*) AS total_tv,

    COUNT(*) FILTER (
        WHERE overview IS NOT NULL
    ) AS overview_count,

    COUNT(*) FILTER (
        WHERE first_air_date IS NOT NULL
    ) AS first_air_date_count,

    COUNT(*) FILTER (
        WHERE last_air_date IS NOT NULL
    ) AS last_air_date_count,

    COUNT(*) FILTER (
        WHERE number_of_episodes IS NOT NULL
    ) AS episodes_count,

    COUNT(*) FILTER (
        WHERE genres <> '[]'::jsonb
    ) AS genres_count,

    COUNT(*) FILTER (
        WHERE keywords <> '[]'::jsonb
    ) AS keywords_count,

    COUNT(*) FILTER (
        WHERE creators <> '[]'::jsonb
    ) AS creators_count,

    COUNT(*) FILTER (
        WHERE cast_members <> '[]'::jsonb
    ) AS cast_count,

    COUNT(*) FILTER (
        WHERE crew <> '[]'::jsonb
    ) AS crew_count,

    COUNT(*) FILTER (
        WHERE networks <> '[]'::jsonb
    ) AS networks_count,

    COUNT(*) FILTER (
        WHERE production_companies <> '[]'::jsonb
    ) AS production_companies_count,

    COUNT(*) FILTER (
        WHERE episode_runtime IS NOT NULL
    ) AS runtime_count,

    COUNT(*) FILTER (
        WHERE poster_path IS NOT NULL
    ) AS poster_count

FROM tv_detail;


-- =========================================================
-- 06. 감독 / 작가 / 원작 관련 정보 확보율
-- =========================================================

SELECT
    COUNT(*) AS total_tv,

    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(d.crew) AS member(value)
            WHERE member.value->>'job' = 'Director'
        )
    ) AS director_tv_count,

    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(d.crew) AS member(value)
            WHERE member.value->>'job' IN (
                'Writer',
                'Screenplay',
                'Story',
                'Screenstory',
                'Co-Writer',
                'Scenario Writer',
                'Staff Writer',
                'Teleplay'
            )
        )
    ) AS writer_tv_count,

    COUNT(*) FILTER (
        WHERE EXISTS (
            SELECT 1
            FROM jsonb_array_elements(d.crew) AS member(value)
            WHERE member.value->>'job' IN (
                'Original Story',
                'Original Concept',
                'Original Film Writer',
                'Novel',
                'Comic Book',
                'Book',
                'Author',
                'Adaptation'
            )
        )
    ) AS original_work_candidate_count,

    COUNT(*) FILTER (
        WHERE d.creators <> '[]'::jsonb
    ) AS creator_tv_count

FROM tv_detail d;


-- =========================================================
-- 07. 샘플 확인
-- =========================================================

SELECT
    c.content_id,
    c.title,
    d.first_air_date,
    d.number_of_episodes,
    d.episode_runtime,

    jsonb_array_length(d.keywords) AS keyword_count,
    jsonb_array_length(d.creators) AS creator_count,
    jsonb_array_length(d.cast_members) AS cast_count,
    jsonb_array_length(d.crew) AS crew_count,
    jsonb_array_length(d.networks) AS network_count

FROM content c

JOIN tv_detail d
    ON d.content_id = c.content_id

WHERE c.content_type = 'tv'

ORDER BY c.content_id

LIMIT 20;


-- =========================================================
-- 08. 테이블 크기 확인
-- =========================================================

SELECT
    pg_size_pretty(
        pg_total_relation_size('tv_detail')
    ) AS tv_detail_total_size;


-- =========================================================
-- 09. 기존 영화 건수 확인
-- =========================================================

SELECT
    content_type,
    COUNT(*) AS content_count

FROM content

WHERE content_type IN ('movie', 'tv')

GROUP BY content_type

ORDER BY content_type;


COMMIT;