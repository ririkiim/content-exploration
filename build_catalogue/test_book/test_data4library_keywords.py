import os
import json
import time
import requests

from pathlib import Path
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY = os.environ["DATA4LIBRARY_API_KEY"]

URL = "https://data4library.kr/api/keywordList"

BOOKS = {
    "빠샤 천사": "9788952745651",
    "바퀴 빌라의 여름방학": "9788992351065",
    "당신의 자리는 어디입니까": "9788954689953",
    "살인의 사계절: 한겨울의 제물": "9788983924780",
    "마음을 돌보는 뇌과학": "9788947549066",
}


def get_keywords(isbn):

    response = requests.get(
        URL,
        params={
            "authKey": API_KEY,
            "isbn13": isbn,
            "format": "json",
        },
        timeout=20,
    )

    response.raise_for_status()

    return response.json()


for title, isbn in BOOKS.items():

    print("\n" + "=" * 70)
    print(f"도서: {title}")
    print(f"ISBN: {isbn}")
    print("=" * 70)

    try:
        data = get_keywords(isbn)

        print(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2,
            )
        )

    except Exception as e:
        print(f"오류: {e}")

    time.sleep(0.3)