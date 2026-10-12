import re
import unicodedata
from datetime import datetime

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

# 대표 판본 선정에서 제외할 표현
EXCLUDE_TITLE_PATTERNS = [
    r"세트",
    r"전집",
    r"합본",
    r"필사",
    r"해설",
    r"문제집",
    r"청소년판",
    r"큰글자",
    r"큰글씨",
    r"대활자",
]

# 원저자 외 각색·개작 등이 포함된 경우
ADAPTATION_PATTERNS = [
    r"각색",
    r"개작",
    r"재구성",
    r"원작.*글",
    r"원작.*그림",
    r"원저.*글",
    r"원저.*그림",
]

client = Data4LibraryClient()


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"[\W_]+", "", text).lower()


def valid_isbn13(isbn):
    isbn = re.sub(r"[^0-9]", "", str(isbn or ""))

    if len(isbn) != 13:
        return False

    total = sum(
        int(digit) * (1 if i % 2 == 0 else 3)
        for i, digit in enumerate(isbn[:12])
    )

    check_digit = (10 - total % 10) % 10
    return check_digit == int(isbn[-1])


def description_valid(description):
    text = re.sub(r"\s+", "", str(description or ""))
    return len(text) >= 50


def classify_candidate(item, search_title, original_author):
    title = str(item.get("title") or "")
    author = str(item.get("author") or "")
    isbn = str(item.get("isbn13") or "")
    goods_type = str(item.get("goodsType") or "")

    if goods_type != "도서":
        return "excluded", "도서 유형 아님"

    if not valid_isbn13(isbn):
        return "excluded", "유효하지 않은 ISBN13"

    if any(re.search(pattern, title) for pattern in EXCLUDE_TITLE_PATTERNS):
        return "excluded", "대표 제외 판본"

    if any(re.search(pattern, author) for pattern in ADAPTATION_PATTERNS):
        return "excluded", "각색·개작 판본"

    if normalize(original_author) not in normalize(author):
        return "excluded", "원저자 불일치"

    normalized_title = normalize(title)
    normalized_search = normalize(search_title)

    # 정확히 일치하는 일반 판본
    if normalized_title == normalized_search:
        return "qualified", "정확한 제목 일치"

    # 토지처럼 여러 권으로 구성된 작품
    if search_title == "토지":
        if re.match(r"^토지\s*(?:제\s*)?1\s*(?:권|부)?(?:\b|$)", title):
            return "qualified", "다권 작품 1권"

    # 구운몽 번역본처럼 부제가 붙은 판본은 검토 대상으로 분류
    if normalized_title.startswith(normalized_search):
        return "review", "제목 확장 판본"

    return "excluded", "다른 제목"


def get_candidates(title):
    response = yes24_get(
        params={
            "query": title,
            "page": 1,
            "size": 30,
        }
    )

    return response.get("data", {}).get("items", [])


def get_book_detail(isbn):
    try:
        return client.get_book_detail(isbn)
    except Exception as exc:
        print(f"    정보나루 조회 실패: {isbn} ({exc})")
        return None


def select_representative(title, author):
    print(f"\n{'=' * 65}")
    print(f"작품: {title} / {author}")
    print(f"{'=' * 65}")

    items = get_candidates(title)

    qualified = []
    review = []

    for item in items:
        status, reason = classify_candidate(item, title, author)

        if status == "excluded":
            continue

        isbn = str(item.get("isbn13") or "")

        detail = get_book_detail(isbn)

        if not detail:
            continue

        description = detail.get("description") or ""

        if not description_valid(description):
            print(f"  제외: {item.get('title')} / 책소개 부족")
            continue

        candidate = {
            "title": item.get("title"),
            "author": item.get("author"),
            "isbn13": isbn,
            "publisher": detail.get("publisher") or item.get("publisher"),
            "description": description,
            "description_length": len(re.sub(r"\s+", "", description)),
            "publish_date": item.get("publishDate"),
            "reason": reason,
            "status": status,
        }

        if status == "qualified":
            qualified.append(candidate)
        else:
            review.append(candidate)

    # 자동 선정 대상이 없으면 검토 대상으로 넘김
    if not qualified:
        print("  자동 선정 가능한 판본 없음")

        for candidate in review:
            print(
                f"  검토: {candidate['title']} "
                f"/ ISBN {candidate['isbn13']}"
            )

        return None

    def rank(candidate):
        date = re.sub(
            r"[^0-9]",
            "",
            str(candidate.get("publish_date") or ""),
        )

        year = int(date[:4]) if len(date) >= 4 else 0

        return (
            candidate["description_length"],
            year,
        )

    # 책소개 정보량 → 출간연도 → ISBN
    qualified.sort(
        key=lambda candidate: (
            -rank(candidate)[0],
            -rank(candidate)[1],
            candidate["isbn13"],
        )
    )

    selected = qualified[0]

    print(f"  대표 판본: {selected['title']}")
    print(f"  ISBN: {selected['isbn13']}")
    print(f"  출판사: {selected['publisher']}")
    print(f"  책소개 길이: {selected['description_length']}자")
    print(f"  선정 이유: {selected['reason']}")

    if review:
        print(f"  추가 검토 후보: {len(review)}개")

    return selected


def main():
    results = []

    for title, author in BOOKS:
        try:
            selected = select_representative(title, author)

            results.append({
                "work_title": title,
                "original_author": author,
                "selected": selected,
            })

        except Exception as exc:
            print(f"\n오류: {title} / {exc}")

    print(f"\n{'=' * 65}")
    print("최종 대표 판본 선정 결과")
    print(f"{'=' * 65}")

    for result in results:
        selected = result["selected"]

        if selected:
            print(
                f"{result['work_title']:<20} "
                f"{selected['isbn13']} "
                f"{selected['publisher']}"
            )
        else:
            print(f"{result['work_title']:<20} 선정 보류")


if __name__ == "__main__":
    main()