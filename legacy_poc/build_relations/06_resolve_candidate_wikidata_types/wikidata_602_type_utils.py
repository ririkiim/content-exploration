import json
import re
import time
from pathlib import Path
from urllib.parse import unquote

import requests

from wikidata_601_type_config import (
    CACHE_DIR,
    REQUEST_DELAY_SECONDS,
)


SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "content-exploration-capstone/1.0 "
        "(academic project; Wikidata type resolution)"
    )
})


def normalize_title(value) -> str:
    """제목 비교용 정규화. DB 제목 자체는 변경하지 않는다."""
    if value is None:
        return ""

    value = unquote(str(value))
    value = value.replace("_", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip().casefold()


def get_evidence_dict(value) -> dict:
    """DB evidence를 dict로 통일한다."""
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}

        return parsed if isinstance(parsed, dict) else {}

    return {}


def batched(items, batch_size):
    """목록을 지정한 크기로 나눈다."""
    for start in range(0, len(items), batch_size):
        yield items[start:start + batch_size]


def request_json(url, params=None, max_retries=6):
    """API 요청에 재시도, 지수형 대기, 요청 간격을 적용한다."""
    for attempt in range(max_retries + 1):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=30,
            )

            if response.status_code in (429, 500, 502, 503, 504):
                if attempt == max_retries:
                    response.raise_for_status()

                retry_after = response.headers.get("Retry-After")

                try:
                    wait_seconds = (
                        float(retry_after)
                        if retry_after
                        else min(
                            REQUEST_DELAY_SECONDS * (2 ** (attempt + 1)),
                            120,
                        )
                    )
                except ValueError:
                    wait_seconds = min(
                        REQUEST_DELAY_SECONDS * (2 ** (attempt + 1)),
                        120,
                    )

                print(
                    f"[WARN] API HTTP {response.status_code}. "
                    f"{wait_seconds:.1f}초 후 재시도 "
                    f"({attempt + 1}/{max_retries})"
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            data = response.json()

            time.sleep(REQUEST_DELAY_SECONDS)
            return data

        except requests.RequestException as exc:
            if attempt == max_retries:
                raise

            wait_seconds = min(
                REQUEST_DELAY_SECONDS * (2 ** (attempt + 1)),
                120,
            )

            print(
                f"[WARN] API 요청 오류: {exc}. "
                f"{wait_seconds:.1f}초 후 재시도 "
                f"({attempt + 1}/{max_retries})"
            )
            time.sleep(wait_seconds)

    raise RuntimeError("API 요청 재시도 횟수를 초과했습니다.")


def cache_path(qid: str) -> Path:
    """QID별 캐시 경로를 반환한다."""
    return CACHE_DIR / f"{qid}.json"


def load_json_cache(path: Path):
    """캐시 파일을 읽는다. 없거나 손상되었으면 None을 반환한다."""
    if not path.exists():
        return None

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def save_json_cache(path: Path, data) -> None:
    """JSON 캐시를 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )