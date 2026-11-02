
import argparse
import json
from datetime import datetime
from pathlib import Path

from build_catalogue.yes24_client import yes24_get

ENDPOINT = "/v1/category/bestseller"
OUTPUT_DIR = Path("data/raw/yes24/bestsellers")


def fetch_bestsellers(category_id, limit):
    results = []
    page = 1

    while len(results) < limit:
        page_size = min(100, limit - len(results))

        response = yes24_get(
            endpoint=ENDPOINT,
            params={
                "categoryId": category_id,
                "page": page,
                "pageSize": page_size,
                "detail": "Y",
            },
        )

        if not response.get("success"):
            raise RuntimeError(
                f"YES24 API 실패: {response.get('message')}"
            )

        data = response.get("data") or {}
        items = data.get("items") or []

        if not items:
            break

        results.extend(items)
        print(f"페이지 {page}: {len(items)}권 수집")

        if len(items) < page_size:
            break

        page += 1

    return results[:limit]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category-id", default="001")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit은 1 이상이어야 합니다.")

    items = fetch_bestsellers(args.category_id, args.limit)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / (
        f"bestsellers_{args.category_id}_{timestamp}.json"
    )

    payload = {
        "source": "yes24",
        "category_id": args.category_id,
        "collected_at": datetime.now().isoformat(),
        "count": len(items),
        "items": items,
    }

    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n수집 완료: {len(items)}권")
    print(f"저장 위치: {output_path}")

    for i, item in enumerate(items, 1):
        print(
            f"{i:3d}. {item.get('title')} "
            f"| ISBN: {item.get('isbn13')}"
        )


if __name__ == "__main__":
    main()
