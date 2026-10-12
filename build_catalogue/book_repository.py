import os
import re
import unicodedata

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL 환경변수가 설정되지 않았어.")


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"[\W_]+", "", text).lower()


def get_connection():
    return psycopg.connect(DATABASE_URL)


def find_existing_content(cur, title, author):
    """
    기존 작품 검색.

    현재 content 테이블에는 저자 컬럼이 없으므로
    제목이 일치하는 작품을 찾아도 저자까지 자동 검증할 수 없다.

    동일 제목 작품이 있으면 잘못 연결하지 않도록
    자동 적재를 보류한다.
    """
    normalized_title = normalize(title)

    cur.execute(
        """
        SELECT content_id, title
        FROM content
        WHERE content_type = 'book'
          AND normalized_title = %s
        """,
        (normalized_title,),
    )

    rows = cur.fetchall()

    if rows:
        raise ValueError(
            f"기존 도서 제목 발견: {title} / {author}. "
            "저자 확인이 필요하므로 자동 연결을 보류함. "
            f"기존 content_id: {[row[0] for row in rows]}"
        )

    return None


def ingest_book(title, author, selected):
    """
    대표 판본 1개를 content / book_edition에 적재.

    - ISBN이 이미 존재하면 기존 데이터 재사용
    - 신규 ISBN이면 기존 작품 중복 확인
    - 신규 작품과 판본을 하나의 트랜잭션으로 생성
    - 오류 발생 시 자동 롤백
    """
    isbn13 = selected["isbn13"]

    with get_connection() as conn:
        with conn.cursor() as cur:

            # 1. 기존 ISBN 확인
            cur.execute(
                """
                SELECT
                    be.book_edition_id,
                    be.content_id,
                    c.title,
                    c.content_type
                FROM book_edition be
                JOIN content c
                  ON c.content_id = be.content_id
                WHERE be.isbn13 = %s
                """,
                (isbn13,),
            )

            existing = cur.fetchone()

            if existing:
                edition_id, content_id, existing_title, content_type = existing

                if content_type != "book" or normalize(existing_title) != normalize(title):
                    raise ValueError(
                        f"ISBN {isbn13}의 기존 작품 정보가 요청과 일치하지 않음"
                    )

                print(f"  [REUSE] {title}")
                print(f"    content_id: {content_id}")
                print(f"    book_edition_id: {edition_id}")

                return {
                    "status": "reused",
                    "content_id": content_id,
                    "book_edition_id": edition_id,
                    "isbn13": isbn13,
                }

            # 2. 기존 작품 중복 확인
            find_existing_content(cur, title, author)

            # 3. content INSERT
            cur.execute(
                """
                INSERT INTO content (
                    content_type,
                    title,
                    original_title,
                    normalized_title
                )
                VALUES (
                    'book',
                    %s,
                    %s,
                    %s
                )
                RETURNING content_id
                """,
                (
                    title,
                    title,
                    normalize(title),
                ),
            )

            content_id = cur.fetchone()[0]

            # 4. 출간연도
            publish_date = re.sub(
                r"[^0-9]",
                "",
                str(selected.get("publish_date") or ""),
            )

            pub_year = (
                int(publish_date[:4])
                if len(publish_date) >= 4
                else None
            )

            # 5. book_edition INSERT
            cur.execute(
                """
                INSERT INTO book_edition (
                    content_id,
                    isbn13,
                    publisher,
                    pub_year,
                    contributors,
                    description,
                    description_source,
                    is_representative
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, TRUE
                )
                RETURNING book_edition_id
                """,
                (
                    content_id,
                    isbn13,
                    selected.get("publisher"),
                    pub_year,
                    Jsonb([
                        {
                            "name": author,
                            "role": "author",
                        }
                    ]),
                    selected.get("description"),
                    "data4library",
                ),
            )

            edition_id = cur.fetchone()[0]

            print(f"  [INSERT] {title}")
            print(f"    content_id: {content_id}")
            print(f"    book_edition_id: {edition_id}")
            print(f"    ISBN: {isbn13}")

            return {
                "status": "inserted",
                "content_id": content_id,
                "book_edition_id": edition_id,
                "isbn13": isbn13,
            }