import csv
import re
import unicodedata

from yes24_client import yes24_get
from data4library_client import Data4LibraryClient


BOOKS = [
    ("칼의 노래", "김훈"),
    ("소년이 온다", "한강"),
    ("난장이가 쏘아올린 작은 공", "조세희"),
    ("지구 끝의 온실", "김초엽"),
    ("토지", "박경리"),
    ("구운몽", "김만중"),
    ("살인자의 기억법", "김영하"),
    ("아몬드", "손원평"),
    ("완득이", "김려령"),
    ("82년생 김지영", "조남주"),
]


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"[\W_]+", "", text).lower()


def valid_isbn13(isbn):
    isbn = str(isbn or "").replace("-", "")

    if len(isbn) != 13 or not isbn.isdigit():
        return False

    total = sum(
        int(digit) * (1 if i % 2 == 0 else 3)
        for i, digit in enumerate(isbn[:12])
    )

    return (10 - total % 10) % 10 == int(isbn[-1])


def classify_title(search_title, item_title):
    """
    제목 분류:
    exact       : 정확히 일치
    volume      : 권차가 붙은 제목
    subtitle    : 부제 또는 시리즈 표기가 붙은 제목
    other       : 기타
    """

    target = normalize(search_title)
    actual = normalize(item_title)

    if target == actual:
        return "exact"

    # 예: 토지 1, 토지 2권, 토지 1부 1권
    volume_pattern = (
        rf"^{re.escape(search_title)}"
        rf"\s*(?:제?\s*\d+\s*(?:부\s*\d+\s*)?(?:권|부|편)?)(?:\b|$)"
    )

    if re.search(volume_pattern, item_title):
        return "volume"

    # 예: 칼의 노래 - 한국문학전집 014
    if actual.startswith(target):
        return "subtitle"

    return "other"


def classify_author(search_author, item_author):
    """
    저자 역할 분류:
    original     : 원저자
    translation  : 번역·현대어역
    adaptation   : 각색·재구성
    coauthor     : 공동 저작
    mismatch     : 저자 불일치
    """

    text = str(item_author or "")
    normalized = normalize(text)

    if normalize(search_author) not in normalized:
        return "mismatch"

    if re.search(r"각색|개작|재구성|(?:원작|원저).*?(?:글|그림)", text):
        return "adaptation"

    if re.search(r"공저|등저|외\s*\d*명", text):
        return "coauthor"

    if re.search(r"번역|현대어|역(?:\s|$|/)", text):
        return "translation"

    return "original"


def classify_candidate(search_title, search_author, item):
    """
    검색 후보의 분류 결과 반환.
    자동 적재 승인 여부가 아니라 검토 우선순위 판단용.
    """

    item_title = item.get("title") or ""
    item_author = item.get("author") or ""
    goods_type = item.get("goodsType") or ""
    isbn = str(item.get("isbn13") or "")

    title_type = classify_title(search_title, item_title)
    author_type = classify_author(search_author, item_author)

    if goods_type != "도서":
        status = "excluded"
        reason = "not_print_book"

    elif not valid_isbn13(isbn):
        status = "excluded"
        reason = "invalid_isbn"

    elif title_type == "other":
        status = "excluded"
        reason = "title_mismatch"

    elif author_type == "mismatch":
        status = "excluded"
        reason = "author_mismatch"

    elif re.search(r"세트|전집|합본|해설서|문제집", item_title):
        status = "excluded"
        reason = "bundle_or_reference"

    elif author_type == "adaptation":
        status = "adaptation_review"
        reason = "adapted_work"

    elif author_type == "coauthor":
        status = "coauthor_review"
        reason = "multiple_authors"

    elif title_type == "volume":
        status = "volume_review"
        reason = "multi_volume_work"

    elif title_type == "subtitle":
        status = "title_review"
        reason = "subtitle_or_series"

    elif author_type == "translation":
        status = "translation_review"
        reason = "translated_or_modernized"

    else:
        status = "same_work_candidate"
        reason = "exact_title_and_author"

    return {
        "status": status,
        "reason": reason,
        "title_type": title_type,
        "author_type": author_type,
    }


def main():
    library = Data4LibraryClient()

    rows = []
    total_candidates = 0

    for title, author in BOOKS:
        print(f"\n{'=' * 65}")
        print(f"{title} / {author}")

        try:
            response = yes24_get(
                params={
                    "query": title,
                    "page": 1,
                    "size": 30,
                }
            )

            if response.get("success") is not True:
                print("YES24 응답 실패")
                continue

            items = response.get("data", {}).get("items", [])

        except Exception as e:
            print("YES24 오류:", e)
            continue

        print(f"YES24 검색 결과: {len(items)}개")

        for item in items:
            result = classify_candidate(title, author, item)

            # 명확히 제외된 상품은 정보나루를 조회하지 않음
            if result["status"] == "excluded":
                continue

            total_candidates += 1

            isbn = str(item.get("isbn13") or "")
            detail = None
            lookup_status = "not_checked"

            try:
                detail = library.get_book_detail(isbn)
                lookup_status = "found" if detail else "not_found"

            except Exception as e:
                lookup_status = f"error: {type(e).__name__}"

            content_detail = item.get("contentDetail") or {}

            intro = content_detail.get("bookIntroduction") or ""
            toc = content_detail.get("tableOfContents") or ""

            row = {
                "search_title": title,
                "search_author": author,
                "yes24_title": item.get("title"),
                "yes24_author": item.get("author"),
                "isbn13": isbn,
                "publisher": item.get("publisher"),
                "publish_date": item.get("publishDate"),
                "title_type": result["title_type"],
                "author_type": result["author_type"],
                "classification": result["status"],
                "reason": result["reason"],
                "data4library_status": lookup_status,
                "data4library_description_length": len(
                    (detail or {}).get("description") or ""
                ),
                "yes24_toc_available": bool(toc.strip()),
                "yes24_toc_length": len(toc),
                "yes24_intro_length": len(intro),
            }

            rows.append(row)

            print(
                f"  [{result['status']}] "
                f"{item.get('title')} | "
                f"{item.get('author')} | "
                f"ISBN: {isbn} | "
                f"정보나루: {lookup_status} | "
                f"목차: {'O' if toc.strip() else 'X'}"
            )

    if rows:
        path = "book_pipeline_test_v2.csv"

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        print(f"\nCSV 저장 완료: {path}")

    print(f"전체 검토 후보: {total_candidates}개")

    print("\n분류별 결과")
    for status in sorted({r["classification"] for r in rows}):
        count = sum(r["classification"] == status for r in rows)
        print(f"  {status}: {count}개")


if __name__ == "__main__":
    main()