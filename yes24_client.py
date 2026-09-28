# YES24 API 호출 파일

import os
import requests
from dotenv import load_dotenv

load_dotenv()

YES24_API_KEY = os.environ["YES24_API_KEY"]

YES24_BASE_URL = "https://apis.yes24.com"
YES24_ITEM_LIST_ENDPOINT = "/v1/goods/itemList"


YES24_HEADERS = {
    "X-Api-Key": YES24_API_KEY,
    "accept": "application/json"
}

def yes24_get(endpoint= YES24_ITEM_LIST_ENDPOINT, params=None):
    response = requests.get(
        f"{YES24_BASE_URL}{endpoint}",
        headers=YES24_HEADERS,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()