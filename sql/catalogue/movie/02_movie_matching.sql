-- =========================================================
-- 02_movie_matching.sql
-- KMDb / TMDb 영화 매칭
--
-- 1. 제목 + 연도 ±1년
-- 2. 감독명 직접 일치
-- 3. 공동 감독 분리 비교
-- 4. 양방향 1:1 매칭
-- 5. 미확정 후보 보류
-- =========================================================


-- =========================================================
-- 1. 제목 + 연도 후보
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_movie_match_candidates;

CREATE TEMP TABLE tmp_movie_match_candidates AS

SELECT
    k.raw_kmdb_movie_id,
    k.dedup_key AS kmdb_source_id,
    t.tmdb_id,

    k.title AS normalized_title,
    k.year AS kmdb_year,
    t.year AS tmdb_year,

    k.director AS kmdb_director,
    r.raw_payload AS tmdb_payload

FROM tmp_kmdb_dedup k

JOIN tmp_tmdb_match t
    ON k.title = t.title
   AND t.year BETWEEN k.year - 1 AND k.year + 1

JOIN tmp_tmdb_latest r
    ON r.tmdb_id = t.tmdb_id;


CREATE INDEX idx_movie_candidates_kmdb
    ON tmp_movie_match_candidates (raw_kmdb_movie_id);

CREATE INDEX idx_movie_candidates_tmdb
    ON tmp_movie_match_candidates (tmdb_id);

ANALYZE tmp_movie_match_candidates;


-- =========================================================
-- 2. 감독명 비교
--
-- KMDb 공동 감독 문자열을 개별 이름으로 분리
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_director_matched;

CREATE TEMP TABLE tmp_director_matched AS

SELECT DISTINCT
    m.raw_kmdb_movie_id,
    m.kmdb_source_id,
    m.tmdb_id

FROM tmp_movie_match_candidates m

WHERE EXISTS (
    SELECT 1

    FROM REGEXP_SPLIT_TO_TABLE(
        COALESCE(m.kmdb_director, ''),
        '[,;/|·、，]+'
    ) AS kd(name)

    JOIN LATERAL JSONB_ARRAY_ELEMENTS(
        CASE
            WHEN JSONB_TYPEOF(
                m.tmdb_payload->'credits'->'crew'
            ) = 'array'

            THEN m.tmdb_payload->'credits'->'crew'

            ELSE '[]'::JSONB
        END
    ) AS td(person)
        ON TRUE

    WHERE td.person->>'job' = 'Director'

      AND REGEXP_REPLACE(
          LOWER(TRIM(kd.name)),
          '[[:space:][:punct:]]',
          '',
          'g'
      ) <> ''

      AND REGEXP_REPLACE(
          LOWER(TRIM(kd.name)),
          '[[:space:][:punct:]]',
          '',
          'g'
      ) = REGEXP_REPLACE(
          LOWER(COALESCE(td.person->>'name', '')),
          '[[:space:][:punct:]]',
          '',
          'g'
      )
);


-- =========================================================
-- 3. 양방향 1:1 매칭
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_confirmed_movie_match;

CREATE TEMP TABLE tmp_confirmed_movie_match AS

WITH kmdb_unique AS (
    SELECT raw_kmdb_movie_id

    FROM tmp_director_matched

    GROUP BY raw_kmdb_movie_id

    HAVING COUNT(DISTINCT tmdb_id) = 1
),
tmdb_unique AS (
    SELECT tmdb_id

    FROM tmp_director_matched

    GROUP BY tmdb_id

    HAVING COUNT(DISTINCT raw_kmdb_movie_id) = 1
)

SELECT
    m.raw_kmdb_movie_id,
    m.kmdb_source_id,
    m.tmdb_id,

    'title_year_director_exact'::TEXT AS match_method

FROM tmp_director_matched m

JOIN kmdb_unique k
    ON m.raw_kmdb_movie_id = k.raw_kmdb_movie_id

JOIN tmdb_unique t
    ON m.tmdb_id = t.tmdb_id;


CREATE UNIQUE INDEX idx_confirmed_movie_kmdb
    ON tmp_confirmed_movie_match (raw_kmdb_movie_id);

CREATE UNIQUE INDEX idx_confirmed_movie_tmdb
    ON tmp_confirmed_movie_match (tmdb_id);


-- =========================================================
-- 4. 미확정 TMDb 후보 보류
--
-- 제목·연도 후보는 있지만
-- 감독 기준 1:1 매칭이 확정되지 않은 작품
-- =========================================================

DROP TABLE IF EXISTS pg_temp.tmp_pending_tmdb_match;

CREATE TEMP TABLE tmp_pending_tmdb_match AS

SELECT DISTINCT
    c.tmdb_id

FROM tmp_movie_match_candidates c

WHERE NOT EXISTS (
    SELECT 1

    FROM tmp_confirmed_movie_match m

    WHERE m.tmdb_id = c.tmdb_id
);


CREATE UNIQUE INDEX idx_pending_tmdb_id
    ON tmp_pending_tmdb_match (tmdb_id);


-- =========================================================
-- 5. 매칭 결과 확인
-- =========================================================

SELECT
    (SELECT COUNT(*)
     FROM tmp_movie_match_candidates)
        AS candidate_pairs,

    (SELECT COUNT(*)
     FROM tmp_director_matched)
        AS director_matched_pairs,

    (SELECT COUNT(*)
     FROM tmp_confirmed_movie_match)
        AS confirmed_matches,

    (SELECT COUNT(*)
     FROM tmp_pending_tmdb_match)
        AS pending_tmdb_movies;