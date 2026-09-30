# 선택된 YES24 책의 상세 원본 데이터를 JSON 파일로 저장

import argparse
import json

import requests

from paths import YES24_RAW_DIR
from yes24_client import yes24_get


YES24_ITEM_DETAIL_ENDPOINT = "/v1/goods/itemDetail"


def fetch_yes24_content(yes24_item_id: int) -> None:
    result = yes24_get(
        endpoint=YES24_ITEM_DETAIL_ENDPOINT,
        params={
            "searchType": "ItemId",
            "query": yes24_item_id,
            "detail": "Y",
        },
    )

    items = result["data"]["items"]

    if not items:
        raise ValueError(
            f"YES24 상세 정보를 찾지 못했습니다: {yes24_item_id}"
        )

    item = items[0]

    # 요청한 ID와 다른 상품이 반환되는 경우를 방지
    if item["itemId"] != yes24_item_id:
        raise ValueError(
            "요청 ID와 응답 상품 ID가 다릅니다: "
            f"요청={yes24_item_id}, 응답={item['itemId']}"
        )

    YES24_RAW_DIR.mkdir(parents=True, exist_ok=True)
    file_path = YES24_RAW_DIR / f"{yes24_item_id}.json"

    with file_path.open("w", encoding="utf-8") as file:
        json.dump(item, file, ensure_ascii=False, indent=2)

    print(f"저장 완료: {file_path}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--ids",
        type=int,
        nargs="+",
        required=True,
        help="상세 정보를 수집할 YES24 item ID 목록",
    )

    args = parser.parse_args()

    for yes24_item_id in args.ids:
        try:
            fetch_yes24_content(yes24_item_id)
        except (requests.RequestException, ValueError) as error:
            print(f"수집 실패: {yes24_item_id} / {error}")


if __name__ == "__main__":
    main()