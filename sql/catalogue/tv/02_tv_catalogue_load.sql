/*
============================================================
02_tv_catalogue_load.sql

목적
- TMDb TV 1차 필터 통과 작품을 content에 적재
- TMDb ID와 content_id를 content_source_map에 연결

적재 대상
- Scripted / Miniseries
- origin_country에 KR 포함
- adult != true

중복 처리
- TMDb ID 기준으로 식별
- 제목 + 연도만으로 자동 병합하지 않음
- 영화와 TV는 별도 content_id 유지

안전성
- 기존 영화 데이터 변경 없음
- 트랜잭션 사용
- 재실행 시 기존 TMDb ID 중복 적재 방지
- 기존 매핑 충돌 검사
- 시퀀스 안전성 검사
============================================================
*/

BEGIN;


-- =========================================================
-- 01. 적재 대상 임시 테이블 생성
-- =========================================================

CREATE TEMP TABLE tv_load_candidates
ON COMMIT DROP
AS
SELECT
    r.tmdb_id::text AS tmdb_id,

    NULLIF(
        BTRIM(r.raw_payload->>'name'),
        ''
    ) AS title,

    NULLIF(
        BTRIM(r.raw_payload->>'original_name'),
        ''
    ) AS original_title,

    CASE
        WHEN LEFT(
            r.raw_payload->>'first_air_date',
            4
        ) ~ '^[0-9]{4}$'

        AND LEFT(
            r.raw_payload->>'first_air_date',
            4
        ) <> '0000'

        THEN LEFT(
            r.raw_payload->>'first_air_date',
            4
        )::integer

        ELSE NULL
    END AS release_year,

    REGEXP_REPLACE(
        LOWER(
            COALESCE(r.raw_payload->>'name', '')
        ),
        '[[:space:][:punct:]]',
        '',
        'g'
    ) AS normalized_title

FROM raw_tmdb_tv r

WHERE COALESCE(
    r.raw_payload->>'type',
    ''
) IN ('Scripted', 'Miniseries')

  AND COALESCE(
      r.raw_payload->'origin_country' ? 'KR',
      FALSE
  )

  AND LOWER(
      BTRIM(
          COALESCE(r.raw_payload->>'adult', 'false')
      )
  ) <> 'true';


-- =========================================================
-- 02. 적재 전 안전성 검사
--
-- 제목 결측 / TMDb ID 중복 / 후보 수 확인
-- =========================================================

DO $$
BEGIN

    IF EXISTS (
        SELECT 1
        FROM tv_load_candidates
        WHERE title IS NULL
    ) THEN
        RAISE EXCEPTION
            'TV catalogue load aborted: missing title';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM tv_load_candidates
        GROUP BY tmdb_id
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION
            'TV catalogue load aborted: duplicate TMDb ID';
    END IF;

    IF (
        SELECT COUNT(*)
        FROM tv_load_candidates
    ) <> 2963 THEN
        RAISE EXCEPTION
            'TV catalogue load aborted: expected 2963 candidates';
    END IF;

END $$;


-- =========================================================
-- 03. 기존 매핑 충돌 검사
--
-- tmdb_tv 매핑이 영화나 책을 가리키면 중단
-- =========================================================

DO $$
BEGIN

    IF EXISTS (
        SELECT 1

        FROM content_source_map sm

        JOIN content c
            ON c.content_id = sm.content_id

        WHERE sm.source_name = 'tmdb_tv'
          AND c.content_type <> 'tv'
    ) THEN
        RAISE EXCEPTION
            'TV catalogue load aborted: tmdb_tv mapping points to non-TV content';
    END IF;

END $$;


-- =========================================================
-- 04. content_id 시퀀스 안전성 검사
--
-- 시퀀스가 현재 최대 content_id보다 뒤처져 있으면 중단
-- 자동 수정하지 않음
-- =========================================================

DO $$
DECLARE
    max_id BIGINT;
    sequence_last BIGINT;
    sequence_called BOOLEAN;
BEGIN

    SELECT COALESCE(MAX(content_id), 0)
    INTO max_id
    FROM content;

    SELECT last_value, is_called
    INTO sequence_last, sequence_called
    FROM content_content_id_seq;

    IF sequence_last < max_id
       OR (
           sequence_last = max_id
           AND NOT sequence_called
       )
    THEN
        RAISE EXCEPTION
            'TV catalogue load aborted: sequence behind content IDs (max %, sequence %, called %)',
            max_id,
            sequence_last,
            sequence_called;
    END IF;

END $$;


-- =========================================================
-- 05. 신규 TV content 생성
--
-- 기존 tmdb_tv 매핑이 있는 작품은 제외
-- 제목/연도가 같아도 TMDb ID가 다르면 별도 생성
-- =========================================================

CREATE TEMP TABLE tv_new_content_map (
    tmdb_id TEXT PRIMARY KEY,
    content_id BIGINT NOT NULL UNIQUE
)
ON COMMIT DROP;


DO $$
DECLARE
    rec RECORD;
    new_content_id BIGINT;
BEGIN

    FOR rec IN

        SELECT
            t.tmdb_id,
            t.title,
            t.original_title,
            t.release_year,
            t.normalized_title

        FROM tv_load_candidates t

        WHERE NOT EXISTS (
            SELECT 1
            FROM content_source_map sm
            WHERE sm.source_name = 'tmdb_tv'
              AND sm.source_id = t.tmdb_id
        )

        ORDER BY t.tmdb_id

    LOOP

        INSERT INTO content (
            content_type,
            title,
            original_title,
            release_year,
            normalized_title
        )
        VALUES (
            'tv',
            rec.title,
            rec.original_title,
            rec.release_year,
            rec.normalized_title
        )
        RETURNING content_id
        INTO new_content_id;

        INSERT INTO tv_new_content_map (
            tmdb_id,
            content_id
        )
        VALUES (
            rec.tmdb_id,
            new_content_id
        );

    END LOOP;

END $$;


-- =========================================================
-- 06. TMDb TV 출처 매핑 생성
--
-- 영화와 동일하게 source_import 사용
-- =========================================================

INSERT INTO content_source_map (
    content_id,
    source_name,
    source_id,
    source_url,
    match_method,
    match_confidence
)

SELECT
    m.content_id,

    'tmdb_tv',

    m.tmdb_id,

    'https://www.themoviedb.org/tv/' || m.tmdb_id,

    'source_import',

    1.0

FROM tv_new_content_map m

ON CONFLICT (source_name, source_id)
DO NOTHING;


-- =========================================================
-- 07. 적재 결과 검증
-- =========================================================

DO $$
DECLARE
    candidate_count BIGINT;
    mapped_count BIGINT;
BEGIN

    SELECT COUNT(*)
    INTO candidate_count
    FROM tv_load_candidates;

    SELECT COUNT(*)
    INTO mapped_count

    FROM tv_load_candidates t

    JOIN content_source_map sm
        ON sm.source_name = 'tmdb_tv'
       AND sm.source_id = t.tmdb_id

    JOIN content c
        ON c.content_id = sm.content_id
       AND c.content_type = 'tv';

    IF candidate_count <> mapped_count THEN
        RAISE EXCEPTION
            'TV catalogue load verification failed: candidates %, mapped %',
            candidate_count,
            mapped_count;
    END IF;

END $$;


-- =========================================================
-- 08. 최종 적재 결과 출력
-- =========================================================

SELECT
    COUNT(*) AS new_tv_content_count
FROM tv_new_content_map;


SELECT
    content_type,
    COUNT(*) AS content_count

FROM content

WHERE content_type IN ('movie', 'tv', 'book')

GROUP BY content_type

ORDER BY content_type;


SELECT
    COUNT(*) AS tmdb_tv_mapping_count

FROM content_source_map

WHERE source_name = 'tmdb_tv';


SELECT
    COUNT(*) AS tv_missing_release_year

FROM content

WHERE content_type = 'tv'
  AND release_year IS NULL;


COMMIT;