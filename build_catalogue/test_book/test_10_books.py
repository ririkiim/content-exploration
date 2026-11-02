from data4library_client import Data4LibraryClient

client = Data4LibraryClient()

test_books = [
    "칼의 노래",
    "소년이 온다",
    "난장이가 쏘아올린 작은 공",
    "지구 끝의 온실",
]

for title in test_books:
    print(f"\n{'=' * 60}")
    print(f"검색어: {title}")
    print("=" * 60)

    results = client.search_books(
        keyword=title,
        page=1,
        page_size=20,
    )

    print(f"API 반환: {len(results)}권")

    for i, book in enumerate(results, 1):
        print(
            f"{i:2}. {book.get('title')} "
            f"| {book.get('authors')} "
            f"| {book.get('isbn13')}"
        )