import os
import re
import requests

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

class Data4LibraryClient:
    BASE_URL = "https://data4library.kr/api"

    def __init__(self):
        self.api_key = os.environ["DATA4LIBRARY_API_KEY"]
        self.session = requests.Session()

    def _get(self, endpoint, params):
        response = self.session.get(
            f"{self.BASE_URL}/{endpoint}",
            params={
                "authKey": self.api_key,
                "format": "json",
                **params,
            },
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        if "response" not in data:
            raise ValueError(f"Unexpected API response: {data}")

        return data["response"]

    def search_books(self, keyword, page=1, page_size=20):
        data = self._get(
            "srchBooks",
            {
                "keyword": keyword,
                "pageNo": page,
                "pageSize": page_size,
            },
        )

        books = []

        for item in data.get("docs", []):
            book = item.get("doc", {})

            books.append({
                "isbn13": book.get("isbn13"),
                "title": book.get("bookname"),
                "authors": book.get("authors"),
                "publisher": book.get("publisher"),
                "description": book.get("description"),
            })

        return books

    def get_book_detail(self, isbn13):
        data = self._get(
            "srchDtlList",
            {
                "isbn13": isbn13,
                "loaninfoYN": "N",
            },
        )

        details = data.get("detail", [])

        if not details:
            return None

        book = details[0].get("book", {})

        return {
            "isbn13": book.get("isbn13") or isbn13,
            "title": book.get("bookname"),
            "authors": book.get("authors"),
            "publisher": book.get("publisher"),
            "description": book.get("description"),
            "raw_data": book,
        }