# TMDB API 호출 통일 파일

import os

import requests
from dotenv import load_dotenv


load_dotenv()

TMDB_TOKEN = os.environ["TMDB_ACCESS_TOKEN"]
TMDB_BASE_URL = "https://api.themoviedb.org/3"

TMDB_HEADERS = {
    "Authorization": f"Bearer {TMDB_TOKEN}",
    "accept": "application/json",
}

# 필요한 endpoint와 파라미터로 TMDB를 호출하고 JSON 반환
def tmdb_get(endpoint, params=None):
    response = requests.get(
        f"{TMDB_BASE_URL}{endpoint}",
        headers=TMDB_HEADERS,
        params=params,
        timeout=30,
    )
    response.raise_for_status()

    return response.json()