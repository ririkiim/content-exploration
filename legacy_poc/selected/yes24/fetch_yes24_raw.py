# 선택된 YES24 책의 원본 데이터를 JSON 파일로 저장

import argparse
import json
import requests

from paths import YES24_RAW_DIR
from legacy_poc.yes24_client import yes24_get


def fetch_yes24_content(yes24_item_id: int, title: str,) -> None:
    result = yes24_get(
        params={
            "query": title, #itemlist API는 title로 검색해야 함
            "page": 1,
            "pageSize": 10,
        },
    )

    items = result["data"]["items"]

    item = next(
        (
            item
            for item in items
            if item["itemId"] == yes24_item_id
        ),
        None,
    )

    if item is None:
        raise ValueError(
            f"YES24 ID를 찾지 못했습니다: {yes24_item_id}"
        )

    YES24_RAW_DIR.mkdir(parents=True, exist_ok=True)

    file_path = YES24_RAW_DIR / f"{yes24_item_id}.json"

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(item, file, ensure_ascii=False, indent=2)

    print(f"저장 완료: {file_path}")

# 제목이랑 id 세트로 관리 -> 추후 바뀔 수 있음 ..
def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--items",
        nargs=2,
        action="append",
        metavar=("ITEM_ID", "TITLE"),
        required=True,
        help="YES24 item ID와 책 제목",
    )

    args = parser.parse_args()

    for item_id, title in args.items:
        try:
            fetch_yes24_content(
                yes24_item_id=int(item_id),
                title=title,
            )
        except (
            requests.RequestException,
            ValueError,
        ) as error:
            print(f"수집 실패: {item_id} / {error}")


if __name__ == "__main__":
    main() 