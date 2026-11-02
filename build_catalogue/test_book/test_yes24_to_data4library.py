from data4library_client import Data4LibraryClient

client = Data4LibraryClient()

books = {
    "칼의 노래": "9788954623360",
    "소년이 온다": "9788936434120",
    "난장이가 쏘아올린 작은 공": "9788995151228",
    "지구 끝의 온실": "9791191824001",
}

for title, isbn in books.items():
    print(f"\n{'=' * 60}")
    print(f"작품: {title}")
    print(f"ISBN: {isbn}")

    try:
        detail = client.get_book_detail(isbn)

        if not detail:
            print("정보나루 상세정보 없음")
            continue

        print("정보나루 조회 성공")

        print("반환 필드:", list(detail.keys()))

        description = detail.get("description") or ""

        print("책 소개 길이:", len(description))
        print("책 소개:", description[:300])

    except Exception as e:
        print("조회 오류:", e)