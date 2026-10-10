/*
============================================================
01_tv_prepare.sql

목적
1. TMDb TV 원본 및 기본 필터 검증
2. 제외 사유 집계
3. 제목 및 방송 연도 결측 확인
4. TV 내부 중복 후보 확인
5. 기존 영화와 중복 후보 확인
6. TV 키워드 구조 및 확보율 확인
7. episode_run_time 구조 및 확보율 확인
8. episode_run_time 실제 값 확인

기본 포함 조건
- type IN ('Scripted', 'Miniseries')
- origin_country에 'KR' 포함
- adult != true

주의
- 읽기 전용
- 테이블 생성 / 수정 / 삭제 없음
- 카탈로그 적재 없음
- 국가 미등록 작품은 이번 적재 대상에서 제외
============================================================
*/

BEGIN TRANSACTION READ ONLY;


-- =========================================================
-- 01. 원본 및 기본 필터링 건수
-- =========================================================

WITH classified AS (
    SELECT
        tmdb_id,

        COALESCE(
            raw_payload->>'type',
            ''
        ) IN ('Scripted', 'Miniseries') AS valid_type,

        COALESCE(
            raw_payload->'origin_country' ? 'KR',
            FALSE
        ) AS valid_country,

        LOWER(
            BTRIM(
                COALESCE(raw_payload->>'adult', 'false')
            )
        ) = 'true' AS is_adult

    FROM raw_tmdb_tv
)

SELECT
    COUNT(*) AS total_raw,

    COUNT(DISTINCT tmdb_id) AS unique_tmdb_ids,

    COUNT(*) - COUNT(DISTINCT tmdb_id)
        AS duplicate_tmdb_ids,

    COUNT(*) FILTER (
        WHERE valid_type
          AND valid_country
          AND NOT is_adult
    ) AS included_tv,

    COUNT(*) FILTER (
        WHERE NOT (
            valid_type
            AND valid_country
            AND NOT is_adult
        )
    ) AS excluded_tv

FROM classified;


-- =========================================================
-- 02. 제외 사유 집계
--
-- 사유 우선순위:
-- adult → type → country
--
-- 한 작품이 여러 조건에 해당하더라도
-- 첫 번째 제외 사유로만 집계
-- =========================================================

WITH classified AS (
    SELECT
        raw_payload,

        CASE
            WHEN LOWER(
                BTRIM(
                    COALESCE(raw_payload->>'adult', 'false')
                )
            ) = 'true'
                THEN 'adult'

            WHEN COALESCE(
                raw_payload->>'type',
                ''
            ) NOT IN ('Scripted', 'Miniseries')
                THEN 'invalid_type'

            WHEN NOT COALESCE(
                raw_payload->'origin_country' ? 'KR',
                FALSE
            )
                THEN 'invalid_country'

            ELSE 'included'
        END AS filter_status

    FROM raw_tmdb_tv
)

SELECT
    filter_status,
    COUNT(*) AS content_count

FROM classified

GROUP BY filter_status

ORDER BY
    CASE filter_status
        WHEN 'included' THEN 1
        WHEN 'adult' THEN 2
        WHEN 'invalid_type' THEN 3
        WHEN 'invalid_country' THEN 4
        ELSE 5
    END;


-- =========================================================
-- 03. 포함 대상 필수 필드 결측
--
-- 제목 및 방송 연도가 없어도 자동 제외하지 않음
-- 날짜 형식이 올바르지 않은 경우도 확인
-- =========================================================

WITH candidates AS (
    SELECT
        tmdb_id,

        NULLIF(
            BTRIM(raw_payload->>'name'),
            ''
        ) AS title,

        NULLIF(
            BTRIM(raw_payload->>'first_air_date'),
            ''
        ) AS first_air_date

    FROM raw_tmdb_tv

    WHERE COALESCE(
        raw_payload->>'type',
        ''
    ) IN ('Scripted', 'Miniseries')

      AND COALESCE(
          raw_payload->'origin_country' ? 'KR',
          FALSE
      )

      AND LOWER(
          BTRIM(
              COALESCE(raw_payload->>'adult', 'false')
          )
      ) <> 'true'
)

SELECT
    COUNT(*) AS total_candidates,

    COUNT(*) FILTER (
        WHERE title IS NULL
    ) AS missing_title,

    COUNT(*) FILTER (
        WHERE first_air_date IS NULL
    ) AS missing_first_air_date,

    COUNT(*) FILTER (
        WHERE first_air_date IS NULL
           OR first_air_date !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    ) AS missing_or_invalid_date_format,

    COUNT(*) FILTER (
        WHERE first_air_date IS NULL
           OR LEFT(first_air_date, 4) !~ '^[0-9]{4}$'
           OR LEFT(first_air_date, 4) = '0000'
    ) AS missing_or_invalid_release_year

FROM candidates;


-- =========================================================
-- 04. TV 내부 제목 + 연도 중복 후보
--
-- 영화와 동일한 정규화 계열 사용:
-- 공백 및 문장부호 제거
--
-- 주의:
-- 동일 제목 + 연도는 중복 '후보'일 뿐
-- 자동 삭제하거나 병합하지 않음
-- =========================================================

WITH candidates AS (
    SELECT
        tmdb_id,

        raw_payload->>'name' AS title,

        CASE
            WHEN LEFT(
                raw_payload->>'first_air_date',
                4
            ) ~ '^[0-9]{4}$'

            AND LEFT(
                raw_payload->>'first_air_date',
                4
            ) <> '0000'

            THEN LEFT(
                raw_payload->>'first_air_date',
                4
            )::integer

            ELSE NULL
        END AS release_year,

        REGEXP_REPLACE(
            LOWER(
                COALESCE(raw_payload->>'name', '')
            ),
            '[[:space:][:punct:]]',
            '',
            'g'
        ) AS comparison_title

    FROM raw_tmdb_tv

    WHERE COALESCE(
        raw_payload->>'type',
        ''
    ) IN ('Scripted', 'Miniseries')

      AND COALESCE(
          raw_payload->'origin_country' ? 'KR',
          FALSE
      )

      AND LOWER(
          BTRIM(
              COALESCE(raw_payload->>'adult', 'false')
          )
      ) <> 'true'
)

SELECT
    comparison_title,
    release_year,

    COUNT(*) AS duplicate_count,

    ARRAY_AGG(
        tmdb_id ORDER BY tmdb_id
    ) AS tmdb_ids,

    ARRAY_AGG(
        title ORDER BY tmdb_id
    ) AS titles

FROM candidates

WHERE comparison_title <> ''
  AND release_year IS NOT NULL

GROUP BY
    comparison_title,
    release_year

HAVING COUNT(*) > 1

ORDER BY
    duplicate_count DESC,
    comparison_title;


-- =========================================================
-- 05. 기존 영화와 제목 + 연도 중복 후보
--
-- 동일 연도 및 ±1년 차이 확인
-- 중복 여부를 자동 확정하지 않음
-- =========================================================

WITH tv_candidates AS (
    SELECT
        tmdb_id,

        raw_payload->>'name' AS tv_title,

        CASE
            WHEN LEFT(
                raw_payload->>'first_air_date',
                4
            ) ~ '^[0-9]{4}$'

            AND LEFT(
                raw_payload->>'first_air_date',
                4
            ) <> '0000'

            THEN LEFT(
                raw_payload->>'first_air_date',
                4
            )::integer

            ELSE NULL
        END AS release_year,

        REGEXP_REPLACE(
            LOWER(
                COALESCE(raw_payload->>'name', '')
            ),
            '[[:space:][:punct:]]',
            '',
            'g'
        ) AS comparison_title

    FROM raw_tmdb_tv

    WHERE COALESCE(
        raw_payload->>'type',
        ''
    ) IN ('Scripted', 'Miniseries')

      AND COALESCE(
          raw_payload->'origin_country' ? 'KR',
          FALSE
      )

      AND LOWER(
          BTRIM(
              COALESCE(raw_payload->>'adult', 'false')
          )
      ) <> 'true'
),

movie_candidates AS (
    SELECT
        content_id,

        title AS movie_title,

        release_year,

        REGEXP_REPLACE(
            LOWER(
                COALESCE(title, '')
            ),
            '[[:space:][:punct:]]',
            '',
            'g'
        ) AS comparison_title

    FROM content

    WHERE content_type = 'movie'
)

SELECT
    t.tmdb_id,
    t.tv_title,

    t.release_year AS tv_year,

    m.content_id AS movie_content_id,
    m.movie_title,

    m.release_year AS movie_year,

    ABS(
        t.release_year - m.release_year
    ) AS year_difference,

    CASE
        WHEN t.release_year = m.release_year
            THEN 'same_year'

        ELSE 'within_1_year'
    END AS overlap_type

FROM tv_candidates t

JOIN movie_candidates m
    ON t.comparison_title = m.comparison_title

   AND ABS(
       t.release_year - m.release_year
   ) <= 1

WHERE t.comparison_title <> ''
  AND t.release_year IS NOT NULL
  AND m.release_year IS NOT NULL

ORDER BY
    year_difference,
    t.tv_title,
    m.content_id;


-- =========================================================
-- 06. TV 키워드 구조 및 확보율
--
-- TV: keywords.results
-- 영화: keywords.keywords
--
-- JSONB 타입이 배열이 아닐 경우
-- 빈 배열로 처리하여 오류 방지
-- =========================================================

WITH candidates AS (
    SELECT
        raw_payload,

        CASE
            WHEN JSONB_TYPEOF(
                raw_payload #> '{keywords,results}'
            ) = 'array'

            THEN raw_payload #> '{keywords,results}'

            ELSE '[]'::jsonb
        END AS tv_keywords,

        CASE
            WHEN JSONB_TYPEOF(
                raw_payload #> '{keywords,keywords}'
            ) = 'array'

            THEN raw_payload #> '{keywords,keywords}'

            ELSE '[]'::jsonb
        END AS movie_style_keywords

    FROM raw_tmdb_tv

    WHERE COALESCE(
        raw_payload->>'type',
        ''
    ) IN ('Scripted', 'Miniseries')

      AND COALESCE(
          raw_payload->'origin_country' ? 'KR',
          FALSE
      )

      AND LOWER(
          BTRIM(
              COALESCE(raw_payload->>'adult', 'false')
          )
      ) <> 'true'
)

SELECT
    COUNT(*) AS total_tv,

    COUNT(*) FILTER (
        WHERE JSONB_TYPEOF(
            raw_payload #> '{keywords,results}'
        ) = 'array'
    ) AS keywords_array_count,

    COUNT(*) FILTER (
        WHERE JSONB_ARRAY_LENGTH(tv_keywords) > 0
    ) AS keywords_available_count,

    COUNT(*) FILTER (
        WHERE JSONB_ARRAY_LENGTH(
            movie_style_keywords
        ) > 0
    ) AS movie_style_keywords_path_count,

    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE JSONB_ARRAY_LENGTH(tv_keywords) > 0
        ) / NULLIF(COUNT(*), 0),
        1
    ) AS keywords_coverage_pct

FROM candidates;


-- =========================================================
-- 07. episode_run_time 구조 및 확보율
--
-- 배열 / 빈 배열 / 기타 타입 구분
-- JSONB 타입이 배열이 아니면 빈 배열 처리
-- =========================================================

WITH candidates AS (
    SELECT
        raw_payload,

        JSONB_TYPEOF(
            raw_payload->'episode_run_time'
        ) AS runtime_type,

        CASE
            WHEN JSONB_TYPEOF(
                raw_payload->'episode_run_time'
            ) = 'array'

            THEN raw_payload->'episode_run_time'

            ELSE '[]'::jsonb
        END AS runtime_array

    FROM raw_tmdb_tv

    WHERE COALESCE(
        raw_payload->>'type',
        ''
    ) IN ('Scripted', 'Miniseries')

      AND COALESCE(
          raw_payload->'origin_country' ? 'KR',
          FALSE
      )

      AND LOWER(
          BTRIM(
              COALESCE(raw_payload->>'adult', 'false')
          )
      ) <> 'true'
)

SELECT
    COUNT(*) AS total_tv,

    COUNT(*) FILTER (
        WHERE runtime_type = 'array'
    ) AS runtime_array_count,

    COUNT(*) FILTER (
        WHERE JSONB_ARRAY_LENGTH(runtime_array) > 0
    ) AS runtime_nonempty_count,

    COUNT(*) FILTER (
        WHERE runtime_type = 'array'
          AND JSONB_ARRAY_LENGTH(runtime_array) = 0
    ) AS runtime_empty_count,

    COUNT(*) FILTER (
        WHERE runtime_type IS DISTINCT FROM 'array'
    ) AS runtime_non_array_count,

    ROUND(
        100.0 * COUNT(*) FILTER (
            WHERE JSONB_ARRAY_LENGTH(runtime_array) > 0
        ) / NULLIF(COUNT(*), 0),
        1
    ) AS runtime_coverage_pct

FROM candidates;


-- =========================================================
-- 08. episode_run_time 실제 값 샘플
--
-- 회당 상영시간을 첫 값으로 사용할지,
-- 평균값으로 사용할지 결정하기 위한 확인
-- =========================================================

WITH candidates AS (
    SELECT
        tmdb_id,

        raw_payload->>'name' AS title,

        CASE
            WHEN JSONB_TYPEOF(
                raw_payload->'episode_run_time'
            ) = 'array'

            THEN raw_payload->'episode_run_time'

            ELSE '[]'::jsonb
        END AS runtime_array

    FROM raw_tmdb_tv

    WHERE COALESCE(
        raw_payload->>'type',
        ''
    ) IN ('Scripted', 'Miniseries')

      AND COALESCE(
          raw_payload->'origin_country' ? 'KR',
          FALSE
      )

      AND LOWER(
          BTRIM(
              COALESCE(raw_payload->>'adult', 'false')
          )
      ) <> 'true'
)

SELECT
    tmdb_id,
    title,
    runtime_array AS episode_run_time

FROM candidates

WHERE JSONB_ARRAY_LENGTH(runtime_array) > 0

ORDER BY tmdb_id

LIMIT 20;


COMMIT;