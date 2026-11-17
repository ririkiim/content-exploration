import json

from yes24_client import yes24_get


SEARCH_TITLES = [
    "칼의 노래",
    "소년이 온다",
    "난장이가 쏘아올린 작은 공",
    "지구 끝의 온실",
]


def main():
    for title in SEARCH_TITLES:
        print(f"\n{'=' * 60}")
        print(f"검색어: {title}")
        print("=" * 60)

        try:
            result = yes24_get(
                params={
                    "query": title,
                    "page": 1,
                    "size": 5,
                }
            )

            print(
                json.dumps(
                    result,
                    ensure_ascii=False,
                    indent=2,
                )[:5000]
            )

        except Exception as e:
            print(f"API 호출 실패: {e}")


if __name__ == "__main__":
    main()