
import os
import json
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


# =========================================================
# 1. 환경 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("KMDB_API_KEY")
if not API_KEY:
    raise RuntimeError("KMDB_API_KEY가 없습니다.")

API_URL = (
    "https://api.koreafilm.or.kr/openapi-data2/"
    "wisenut/search_api/search_json2.jsp"
)

OUTPUT_DIR = ROOT / "data/raw/new_kmdb/api_full"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = OUTPUT_DIR / "collection_state.json"

PAGE_SIZE = 500
TOTAL_COUNT = 122627

# 전체 한도 100회 중 여유를 두고 설정
DAILY_LIMIT = 90

# 오늘 이미 사용한 요청 수를 확인한 후 수정
TODAY_PREVIOUS_REQUESTS = 0

KST = ZoneInfo("Asia/Seoul")


# =========================================================
# 2. 상태 관리
# =========================================================

def today():
    return datetime.now(KST).date().isoformat()


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)

    return {"daily_requests": {}}


def save_state(state):
    temp = STATE_FILE.with_suffix(".tmp")

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    temp.replace(STATE_FILE)


def get_movies(data):
    movies = []

    for group in data.get("Data", []):
        if isinstance(group, dict):
            movies.extend(group.get("Result") or [])

    return movies


def valid_page(path, expected_count):
    if not path.exists():
        return False

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        return len(get_movies(data)) == expected_count

    except (OSError, ValueError, TypeError):
        return False


# =========================================================
# 3. 전체 수집
# =========================================================

state = load_state()
date = today()

# 기존 테스트 호출을 오늘 사용량에 반영
if date not in state["daily_requests"]:
    state["daily_requests"][date] = TODAY_PREVIOUS_REQUESTS
    save_state(state)

session = requests.Session()

for start in range(0, TOTAL_COUNT, PAGE_SIZE):

    expected_count = min(PAGE_SIZE, TOTAL_COUNT - start)

    output_path = (
        OUTPUT_DIR / f"kmdb_start{start:06d}_count500.json"
    )

    if valid_page(output_path, expected_count):
        print(f"[SKIP] 이미 수집됨: {start}")
        continue

    # 날짜가 바뀌면 새로운 일일 한도 적용
    date = today()

    if date not in state["daily_requests"]:
        state["daily_requests"][date] = 0
        save_state(state)

    used = state["daily_requests"][date]

    if used >= DAILY_LIMIT:
        print("\n[STOP] 일일 요청 한도 도달")
        print("다음 날 다시 실행하세요.")
        break

    params = {
        "ServiceKey": API_KEY,
        "collection": "kmdb_new2",
        "detail": "Y",
        "startCount": start,
        "listCount": PAGE_SIZE,
        "sort": "prodYear,1",
    }

    # 네트워크 요청 전에 카운트 기록
    # 요청 실패도 트래픽으로 계산될 수 있기 때문
    state["daily_requests"][date] += 1
    save_state(state)

    print(
        f"\n[REQUEST] start={start} "
        f"({state['daily_requests'][date]}/{DAILY_LIMIT})"
    )

    try:
        response = session.get(
            API_URL,
            params=params,
            timeout=(10, 120)
        )
        response.raise_for_status()

        data = response.json()
        movies = get_movies(data)

        print("API TotalCount:", data.get("TotalCount"))
        print("현재 페이지 영화 수:", len(movies))

        if len(movies) != expected_count:
            print(
                f"[STOP] 예상 {expected_count}건, "
                f"실제 {len(movies)}건"
            )
            break

        # 임시 파일에 저장한 후 원자적으로 교체
        temp_path = output_path.with_suffix(".tmp")

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

        temp_path.replace(output_path)

        print(
            f"[OK] {len(movies)}편 저장 / "
            f"{output_path.name}"
        )

        time.sleep(2)

    except (requests.RequestException, ValueError) as exc:
        print("[ERROR]", exc)
        print("수집을 중단합니다.")
        break

print("\n수집 실행 종료")
