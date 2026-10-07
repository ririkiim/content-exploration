import os

import psycopg
from dotenv import load_dotenv


load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]


INSERT_CONTENT_SQL = """
INSERT INTO content (
    content_type,
    title,
    creator,
    release_date,
    description
)
VALUES (
    %(content_type)s,
    %(title)s,
    %(creator)s,
    %(release_date)s,
    %(description)s
)
RETURNING content_id;
"""


INSERT_SOURCE_MAP_SQL = """
INSERT INTO content_source_map (
    content_id,
    source_name,
    source_id
)
VALUES (
    %(content_id)s,
    %(source_name)s,
    %(source_id)s
);
"""


CHECK_SOURCE_MAP_SQL = """
SELECT content_id
FROM content_source_map
WHERE source_name = %(source_name)s
  AND source_id = %(source_id)s;
"""


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

        if media_type == "movie":

            content_type = "movie"
            release_date = data.get("release_date")

            directors = []

            crew = (
                data.get("credits", {})
                .get("crew", [])
            )

            for person in crew:
                if person.get("job") == "Director":
                    name = person.get("name")

                    if name:
                        directors.append(name)

            creator = ", ".join(directors) or None

        elif media_type == "tv":

            content_type = "tv"
            release_date = data.get("first_air_date")

            creators = []

            for person in data.get("created_by", []):
                name = person.get("name")

                if name:
                    creators.append(name)

            creator = ", ".join(creators) or None

        else:
            continue

        # 기본 설명
        description = data.get("overview") or None

        # TMDb keywords 추가
        keywords = data.get("keywords", {})
        keyword_list = keywords.get("keywords", [])

        keyword_names = []

        for keyword in keyword_list:
            name = keyword.get("name")

            if name:
                keyword_names.append(name)

        if keyword_names:
            keyword_text = (
                "[Keywords] "
                + ", ".join(keyword_names)
            )

            if description:
                description += "\n" + keyword_text
            else:
                description = keyword_text

        # content 적재
        cursor.execute(
            INSERT_CONTENT_SQL,
            {
                "content_type": content_type,
                "title": title,
                "creator": creator,
                "release_date": release_date,
                "description": description,
            },
        )

        content_id = cursor.fetchone()[0]

        # content_source_map 적재
        cursor.execute(
            INSERT_SOURCE_MAP_SQL,
            {
                "content_id": content_id,
                "source_name": source_name,
                "source_id": source_id,
            },
        )

        loaded_count += 1

    return loaded_count, skipped_count


def remove_author_roles(author):
    if not author:
        return None

    author = author.replace(" 저", "")
    author = author.replace(" 역", "")

    return author.strip() or None


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

        # 저자
        author = data.get("author")
        creator = remove_author_roles(author)

        # 출판일
        release_date = data.get("publishDate")

        # 설명
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

        # content 적재
        cursor.execute(
            INSERT_CONTENT_SQL,
            {
                "content_type": "book",
                "title": title,
                "creator": creator,
                "release_date": release_date,
                "description": description,
            },
        )

        content_id = cursor.fetchone()[0]

        # content_source_map 적재
        cursor.execute(
            INSERT_SOURCE_MAP_SQL,
            {
                "content_id": content_id,
                "source_name": source_name,
                "source_id": source_id,
            },
        )

        loaded_count += 1

    return loaded_count, skipped_count


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

    total_loaded = tmdb_loaded + yes24_loaded
    total_skipped = tmdb_skipped + yes24_skipped

    print()
    print(f"TMDb 신규 적재: {tmdb_loaded}개")
    print(f"TMDb 중복 건너뜀: {tmdb_skipped}개")
    print(f"YES24 신규 적재: {yes24_loaded}개")
    print(f"YES24 중복 건너뜀: {yes24_skipped}개")
    print(f"전체 신규 적재: {total_loaded}개")
    print(f"전체 중복 건너뜀: {total_skipped}개")


if __name__ == "__main__":
    load_content()