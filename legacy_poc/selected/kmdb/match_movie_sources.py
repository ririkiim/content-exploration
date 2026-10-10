# TMDB 영화와 KMDb 후보가 같은 작품인지 판단
import html
import os
import re
from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS movie_source_match (
    match_id BIGSERIAL PRIMARY KEY,

    tmdb_id BIGINT NOT NULL UNIQUE,
    kmdb_doc_id TEXT,

    tmdb_title TEXT NOT NULL,
    kmdb_title TEXT,

    tmdb_year INTEGER,
    kmdb_year INTEGER,

    tmdb_directors TEXT,
    kmdb_directors TEXT,

    title_matched BOOLEAN NOT NULL DEFAULT FALSE,
    year_matched BOOLEAN NOT NULL DEFAULT FALSE,
    director_matched BOOLEAN NOT NULL DEFAULT FALSE,

    match_status VARCHAR(30) NOT NULL
        CHECK (
            match_status IN (
                'matched',
                'review_required',
                'no_candidate'
            )
        ),

    matched_candidate JSONB,
    matched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


SELECT_MOVIES_SQL = """
SELECT
    tmdb.tmdb_id,
    tmdb.title,
    tmdb.payload,
    kmdb.payload
FROM raw_tmdb_content AS tmdb
JOIN raw_kmdb_search AS kmdb
    ON tmdb.tmdb_id = kmdb.requested_tmdb_id
WHERE tmdb.media_type = 'movie'
ORDER BY tmdb.tmdb_id;
"""


UPSERT_SQL = """
INSERT INTO movie_source_match (
    tmdb_id,
    kmdb_doc_id,
    tmdb_title,
    kmdb_title,
    tmdb_year,
    kmdb_year,
    tmdb_directors,
    kmdb_directors,
    title_matched,
    year_matched,
    director_matched,
    match_status,
    matched_candidate,
    matched_at
)
VALUES (
    %(tmdb_id)s,
    %(kmdb_doc_id)s,
    %(tmdb_title)s,
    %(kmdb_title)s,
    %(tmdb_year)s,
    %(kmdb_year)s,
    %(tmdb_directors)s,
    %(kmdb_directors)s,
    %(title_matched)s,
    %(year_matched)s,
    %(director_matched)s,
    %(match_status)s,
    %(matched_candidate)s,
    %(matched_at)s
)
ON CONFLICT (tmdb_id)
DO UPDATE SET
    kmdb_doc_id = EXCLUDED.kmdb_doc_id,
    tmdb_title = EXCLUDED.tmdb_title,
    kmdb_title = EXCLUDED.kmdb_title,
    tmdb_year = EXCLUDED.tmdb_year,
    kmdb_year = EXCLUDED.kmdb_year,
    tmdb_directors = EXCLUDED.tmdb_directors,
    kmdb_directors = EXCLUDED.kmdb_directors,
    title_matched = EXCLUDED.title_matched,
    year_matched = EXCLUDED.year_matched,
    director_matched = EXCLUDED.director_matched,
    match_status = EXCLUDED.match_status,
    matched_candidate = EXCLUDED.matched_candidate,
    matched_at = EXCLUDED.matched_at;
"""

# 텍스트 정규화

# KMDb 텍스트 강조 표시와 태그 제거 
def clean_text(value):
    if not value:
        return ""

    value = html.unescape(str(value))
    value = re.sub(r"!HS|!HE", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value)

    return value.strip()

# 비교를 위한 공백과 문장부호 제거
def normalize_text(value):
    value = clean_text(value).lower()

    return re.sub(
        r"[^0-9a-z가-힣]",
        "",
        value,
    )


def parse_year(value):
    if not value:
        return None

    matched = re.search(r"\d{4}", str(value))

    if not matched:
        return None

    return int(matched.group())

# 매칭에 필요한 값 추출

# 개봉년도
def get_tmdb_year(tmdb_payload):
    release_date = tmdb_payload.get(
        "release_date",
        "",
    )

    return parse_year(release_date)

# 제작진
def get_tmdb_directors(tmdb_payload):
    crew = (
        tmdb_payload
        .get("credits", {})
        .get("crew", [])
    )

    directors = []

    for person in crew:
        if person.get("job") == "Director":
            name = clean_text(person.get("name"))

            if name:
                directors.append(name)

    return directors

# KMDb 응답에서 영화 후보 목록 추출
def get_kmdb_candidates(kmdb_payload):
    data_list = kmdb_payload.get("Data", [])

    if not data_list:
        return []

    candidates = data_list[0].get(
        "Result",
        [],
    )

    if not isinstance(candidates, list):
        return []

    return candidates

# KMDb 에서 감독 이름 목록 추출
def get_kmdb_directors(candidate):
    director_list = (
        candidate
        .get("directors", {})
        .get("director", [])
    )

    directors = []

    for director in director_list:
        name = clean_text(
            director.get("directorNm")
        )

        if name:
            directors.append(name)

    return directors

# 매칭 규칙 확인 
def titles_match(tmdb_title, kmdb_title):
    return (
        normalize_text(tmdb_title)
        == normalize_text(kmdb_title)
    )

# 제작연도와 개봉연도 차이 고려를 위한 ±1년
def years_match(tmdb_year, kmdb_year):
    if (
        tmdb_year is None
        or kmdb_year is None
    ):
        return False

    return abs(tmdb_year - kmdb_year) <= 1

# TMDB 와 KMDb 감독 목록에 같은 사람이 한 명 이상 있으면 True 반환
def directors_match(
    tmdb_directors,
    kmdb_directors,
):
    normalized_tmdb = {
        normalize_text(name)
        for name in tmdb_directors
        if name
    }

    normalized_kmdb = {
        normalize_text(name)
        for name in kmdb_directors
        if name
    }

    if not normalized_tmdb or not normalized_kmdb:
        return False

    return bool(
        normalized_tmdb & normalized_kmdb
    )

# 매칭 가능 여부 반환
# 제목 / 연도 / 감독이 모두 일치할 때만 자동 매칭
def compare_candidate(
    tmdb_title,
    tmdb_year,
    tmdb_directors,
    candidate,
):
    kmdb_title = clean_text(
        candidate.get("title")
    )
    kmdb_year = parse_year(
        candidate.get("prodYear")
    )
    kmdb_directors = get_kmdb_directors(
        candidate
    )

    title_matched = titles_match(
        tmdb_title,
        kmdb_title,
    )
    year_matched = years_match(
        tmdb_year,
        kmdb_year,
    )
    director_matched = directors_match(
        tmdb_directors,
        kmdb_directors,
    )

    return {
        "candidate": candidate,
        "kmdb_title": kmdb_title,
        "kmdb_year": kmdb_year,
        "kmdb_directors": kmdb_directors,
        "title_matched": title_matched,
        "year_matched": year_matched,
        "director_matched": director_matched,
        "is_match": (
            title_matched
            and year_matched
            and director_matched
        ),
    }

# TMDb 와 KMDb 원본으로 비교한 후 매칭 결과 저장
def match_movie_sources():
    matched_count = 0
    review_count = 0
    no_candidate_count = 0

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(CREATE_TABLE_SQL)
            cursor.execute(SELECT_MOVIES_SQL)

            movies = cursor.fetchall()

            for (
                tmdb_id,
                tmdb_title,
                tmdb_payload,
                kmdb_payload,
            ) in movies:
                tmdb_year = get_tmdb_year(
                    tmdb_payload
                )
                tmdb_directors = get_tmdb_directors(
                    tmdb_payload
                )
                candidates = get_kmdb_candidates(
                    kmdb_payload
                )

                comparisons = [
                    compare_candidate(
                        tmdb_title=tmdb_title,
                        tmdb_year=tmdb_year,
                        tmdb_directors=tmdb_directors,
                        candidate=candidate,
                    )
                    for candidate in candidates
                ]

                successful_matches = [
                    comparison
                    for comparison in comparisons
                    if comparison["is_match"]
                ]

                if not candidates:
                    status = "no_candidate"
                    selected = None
                    no_candidate_count += 1

                elif len(successful_matches) == 1:
                    status = "matched"
                    selected = successful_matches[0]
                    matched_count += 1

                else:
                    status = "review_required"
                    selected = None
                    review_count += 1

                if selected:
                    candidate = selected["candidate"]

                    save_data = {
                        "tmdb_id": tmdb_id,
                        "kmdb_doc_id": candidate.get(
                            "DOCID"
                        ),
                        "tmdb_title": tmdb_title,
                        "kmdb_title": selected[
                            "kmdb_title"
                        ],
                        "tmdb_year": tmdb_year,
                        "kmdb_year": selected[
                            "kmdb_year"
                        ],
                        "tmdb_directors": ", ".join(
                            tmdb_directors
                        ),
                        "kmdb_directors": ", ".join(
                            selected[
                                "kmdb_directors"
                            ]
                        ),
                        "title_matched": selected[
                            "title_matched"
                        ],
                        "year_matched": selected[
                            "year_matched"
                        ],
                        "director_matched": selected[
                            "director_matched"
                        ],
                        "match_status": status,
                        "matched_candidate": Jsonb(
                            candidate
                        ),
                        "matched_at": datetime.now(
                            timezone.utc
                        ),
                    }

                else:
                    save_data = {
                        "tmdb_id": tmdb_id,
                        "kmdb_doc_id": None,
                        "tmdb_title": tmdb_title,
                        "kmdb_title": None,
                        "tmdb_year": tmdb_year,
                        "kmdb_year": None,
                        "tmdb_directors": ", ".join(
                            tmdb_directors
                        ),
                        "kmdb_directors": None,
                        "title_matched": False,
                        "year_matched": False,
                        "director_matched": False,
                        "match_status": status,
                        "matched_candidate": None,
                        "matched_at": datetime.now(
                            timezone.utc
                        ),
                    }

                cursor.execute(
                    UPSERT_SQL,
                    save_data,
                )

                if status != "matched":
                    print(
                    f"{status}: {tmdb_title} "
                    f"/ 후보 수: {len(candidates)}"
                    )

            connection.commit()

    print()
    print(f"자동 매칭: {matched_count}개")
    print(f"검토 필요: {review_count}개")
    print(f"후보 없음: {no_candidate_count}개")


if __name__ == "__main__":
    match_movie_sources()