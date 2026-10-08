import os
from datetime import datetime

import psycopg
from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]


# =========================================================
# SQL
# =========================================================

INSERT_CONTENT_SQL = """
INSERT INTO content (
    content_type,
    title,
    release_date,
    description,
    normalized_title
)
VALUES (
    %(content_type)s,
    %(title)s,
    %(release_date)s,
    %(description)s,
    %(normalized_title)s
)
RETURNING content_id;
"""


INSERT_MOVIE_SQL = """
INSERT INTO movie (
    content_id,
    genre,
    nation,
    keywords
)
VALUES (
    %(content_id)s,
    %(genre)s,
    %(nation)s,
    %(keywords)s
);
"""


INSERT_TV_SQL = """
INSERT INTO tv (
    content_id,
    genre,
    nation,
    keywords
)
VALUES (
    %(content_id)s,
    %(genre)s,
    %(nation)s,
    %(keywords)s
);
"""


INSERT_BOOK_SQL = """
INSERT INTO book (
    content_id,
    goods_sort_nm,
    isbn10,
    isbn13
)
VALUES (
    %(content_id)s,
    %(goods_sort_nm)s,
    %(isbn10)s,
    %(isbn13)s
);
"""


INSERT_SOURCE_MAP_SQL = """
INSERT INTO content_source_map (
    content_id,
    source_name,
    source_id,
    match_method
)
VALUES (
    %(content_id)s,
    %(source_name)s,
    %(source_id)s,
    %(match_method)s
);
"""


CHECK_SOURCE_MAP_SQL = """
SELECT content_id
FROM content_source_map
WHERE source_name = %(source_name)s
  AND source_id = %(source_id)s;
"""


# =========================================================
# 공통 함수
# =========================================================

def is_already_loaded(cursor, source_name, source_id):
    cursor.execute(
        CHECK_SOURCE_MAP_SQL,
        {
            "source_name": source_name,
            "source_id": source_id,
        },
    )

    row = cursor.fetchone()

    if row is not None:
        return True

    return False


def normalize_title(title):
    """
    제목을 소문자로 변환한 뒤
    영문/한글/숫자 등 문자와 숫자만 남긴다.
    """

    if not title:
        return None

    return "".join(
        character.lower()
        for character in title
        if character.isalnum()
    )


def convert_tmdb_date(date_text):
    """
    TMDb 날짜 문자열(YYYY-MM-DD)을
    Python date 객체로 변환한다.
    """

    if not date_text:
        return None

    return datetime.strptime(
        date_text,
        "%Y-%m-%d",
    ).date()


def convert_yes24_date(date_text):
    """
    YES24 날짜 문자열(YYYYMMDD)을
    Python date 객체로 변환한다.
    """

    if not date_text:
        return None

    return datetime.strptime(
        date_text,
        "%Y%m%d",
    ).date()


def extract_tmdb_names(items):
    """
    TMDb의 [{..., "name": "..."}] 형태 데이터를
    '이름1, 이름2, 이름3' 형태의 문자열로 변환한다.
    """

    names = []

    for item in items or []:
        name = item.get("name")

        if name:
            names.append(name)

    return ", ".join(names) or None


# =========================================================
# TMDb 적재
# =========================================================

def load_tmdb_content(cursor):

    cursor.execute(
        """
        SELECT
            media_type,
            tmdb_id,
            title,
            payload
        FROM raw_tmdb_content
        ORDER BY raw_id;
        """
    )

    rows = cursor.fetchall()

    loaded_count = 0
    skipped_count = 0

    for media_type, tmdb_id, title, data in rows:

        source_name = "tmdb"
        source_id = str(tmdb_id)

        # 이미 적재된 데이터인지 확인
        if is_already_loaded(
            cursor,
            source_name,
            source_id,
        ):
            skipped_count += 1
            continue

        # -------------------------------------------------
        # 영화
        # -------------------------------------------------

        if media_type == "movie":

            content_type = "movie"

            release_date = convert_tmdb_date(
                data.get("release_date")
            )

            genre = extract_tmdb_names(
                data.get("genres")
            )

            nation = extract_tmdb_names(
                data.get("production_countries")
            )

            keyword_data = data.get(
                "keywords",
                {},
            )

            keywords = extract_tmdb_names(
                keyword_data.get("keywords")
            )

        # -------------------------------------------------
        # 드라마
        # -------------------------------------------------

        elif media_type == "tv":

            content_type = "tv"

            release_date = convert_tmdb_date(
                data.get("first_air_date")
            )

            genre = extract_tmdb_names(
                data.get("genres")
            )

            nation = extract_tmdb_names(
                data.get("production_countries")
            )

            keyword_data = data.get(
                "keywords",
                {},
            )

            keywords = extract_tmdb_names(
                keyword_data.get("keywords")
            )

        else:
            continue

        # -------------------------------------------------
        # 공통 content 정보
        # -------------------------------------------------

        description = data.get("overview") or None

        normalized_title = normalize_title(title)

        # -------------------------------------------------
        # content 적재
        # -------------------------------------------------

        cursor.execute(
            INSERT_CONTENT_SQL,
            {
                "content_type": content_type,
                "title": title,
                "release_date": release_date,
                "description": description,
                "normalized_title": normalized_title,
            },
        )

        content_id = cursor.fetchone()[0]

        # -------------------------------------------------
        # subtype 적재
        # -------------------------------------------------

        if media_type == "movie":

            cursor.execute(
                INSERT_MOVIE_SQL,
                {
                    "content_id": content_id,
                    "genre": genre,
                    "nation": nation,
                    "keywords": keywords,
                },
            )

        elif media_type == "tv":

            cursor.execute(
                INSERT_TV_SQL,
                {
                    "content_id": content_id,
                    "genre": genre,
                    "nation": nation,
                    "keywords": keywords,
                },
            )

        # -------------------------------------------------
        # content_source_map 적재
        # -------------------------------------------------

        cursor.execute(
            INSERT_SOURCE_MAP_SQL,
            {
                "content_id": content_id,
                "source_name": source_name,
                "source_id": source_id,
                "match_method": "source_id",
            },
        )

        loaded_count += 1

    return loaded_count, skipped_count


# =========================================================
# YES24 적재
# =========================================================

def load_yes24_content(cursor):

    cursor.execute(
        """
        SELECT
            yes24_item_id,
            title,
            payload
        FROM raw_yes24_item
        ORDER BY raw_id;
        """
    )

    rows = cursor.fetchall()

    loaded_count = 0
    skipped_count = 0

    for yes24_item_id, title, data in rows:

        source_name = "yes24"
        source_id = str(yes24_item_id)

        # 이미 적재된 데이터인지 확인
        if is_already_loaded(
            cursor,
            source_name,
            source_id,
        ):
            skipped_count += 1
            continue

        # -------------------------------------------------
        # 출판일
        # -------------------------------------------------

        release_date = convert_yes24_date(
            data.get("publishDate")
        )

        # -------------------------------------------------
        # 설명
        # -------------------------------------------------

        content_detail = data.get(
            "contentDetail",
            {},
        )

        book_introduction = content_detail.get(
            "bookIntroduction"
        )

        book_summary = content_detail.get(
            "bookSummary"
        )

        description_parts = []

        if book_introduction:
            description_parts.append(
                book_introduction
            )

        if book_summary:
            description_parts.append(
                "[Summary] " + book_summary
            )

        description = (
            "\n".join(description_parts)
            or None
        )

        # -------------------------------------------------
        # 도서 정보
        # -------------------------------------------------

        goods_sort_nm = data.get(
            "goodsSortNm"
        )

        isbn10 = data.get(
            "isbn10"
        )

        isbn13 = data.get(
            "isbn13"
        )

        # -------------------------------------------------
        # 제목 정규화
        # -------------------------------------------------

        normalized_title = normalize_title(title)

        # -------------------------------------------------
        # content 적재
        # -------------------------------------------------

        cursor.execute(
            INSERT_CONTENT_SQL,
            {
                "content_type": "book",
                "title": title,
                "release_date": release_date,
                "description": description,
                "normalized_title": normalized_title,
            },
        )

        content_id = cursor.fetchone()[0]

        # -------------------------------------------------
        # book 적재
        # -------------------------------------------------

        cursor.execute(
            INSERT_BOOK_SQL,
            {
                "content_id": content_id,
                "goods_sort_nm": goods_sort_nm,
                "isbn10": isbn10,
                "isbn13": isbn13,
            },
        )

        # -------------------------------------------------
        # content_source_map 적재
        # -------------------------------------------------

        cursor.execute(
            INSERT_SOURCE_MAP_SQL,
            {
                "content_id": content_id,
                "source_name": source_name,
                "source_id": source_id,
                "match_method": "source_id",
            },
        )

        loaded_count += 1

    return loaded_count, skipped_count


# =========================================================
# 전체 적재
# =========================================================

def load_content():

    with psycopg.connect(DATABASE_URL) as connection:

        with connection.cursor() as cursor:

            tmdb_loaded, tmdb_skipped = load_tmdb_content(
                cursor
            )

            yes24_loaded, yes24_skipped = load_yes24_content(
                cursor
            )

        connection.commit()

    total_loaded = (
        tmdb_loaded
        + yes24_loaded
    )

    total_skipped = (
        tmdb_skipped
        + yes24_skipped
    )

    print()
    print(f"TMDb 신규 적재: {tmdb_loaded}개")
    print(f"TMDb 중복 건너뜀: {tmdb_skipped}개")
    print(f"YES24 신규 적재: {yes24_loaded}개")
    print(f"YES24 중복 건너뜀: {yes24_skipped}개")
    print(f"전체 신규 적재: {total_loaded}개")
    print(f"전체 중복 건너뜀: {total_skipped}개")


if __name__ == "__main__":
    load_content()