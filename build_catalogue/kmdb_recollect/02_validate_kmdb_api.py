
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# =========================================================
# 1. 환경 설정
# =========================================================

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data/raw/new_kmdb/api_full"

PAGE_SIZE = 500
EXPECTED_TOTAL = 122627

FILE_PATTERN = re.compile(
    r"^kmdb_start(\d+)_count500\.json$"
)


# =========================================================
# 2. 응답 구조
# =========================================================

def get_movies(data):
    movies = []

    for group in data.get("Data", []):
        if isinstance(group, dict):
            results = group.get("Result") or []
            if isinstance(results, list):
                movies.extend(results)

    return movies


def has_value(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def has_credits(movie, key):
    value = movie.get(key)

    if isinstance(value, dict):
        return any(
            has_value(v)
            for v in value.values()
        )

    return has_value(value)


# =========================================================
# 3. 전체 파일 검증
# =========================================================

def main():
    expected_starts = list(
        range(0, EXPECTED_TOTAL, PAGE_SIZE)
    )

    files = {}
    unexpected_files = []

    for path in DATA_DIR.glob("kmdb_start*_count500.json"):
        match = FILE_PATTERN.match(path.name)

        if match:
            start = int(match.group(1))
            files[start] = path
        else:
            unexpected_files.append(path.name)

    missing_starts = sorted(
        set(expected_starts) - set(files)
    )
    extra_starts = sorted(
        set(files) - set(expected_starts)
    )

    print("========== 파일 검증 ==========")
    print("예상 파일 수:", len(expected_starts))
    print("실제 파일 수:", len(files))
    print("누락 페이지:", missing_starts)
    print("초과 페이지:", extra_starts)
    print("비정상 파일명:", unexpected_files)

    total_records = 0
    page_errors = []
    parse_errors = []
    id_counts = Counter()
    id_examples = defaultdict(list)
    missing = Counter()

    for start in sorted(files):
        path = files[start]

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            parse_errors.append((path.name, str(exc)))
            continue

        movies = get_movies(data)

        expected_count = min(
            PAGE_SIZE,
            EXPECTED_TOTAL - start
        )

        if len(movies) != expected_count:
            page_errors.append({
                "file": path.name,
                "expected": expected_count,
                "actual": len(movies),
            })

        total_records += len(movies)

        for movie in movies:
            movie_id = str(
                movie.get("movieId") or ""
            ).strip()

            movie_seq = str(
                movie.get("movieSeq") or ""
            ).strip()

            if not movie_id or not movie_seq:
                missing["movie_id_or_seq"] += 1
            else:
                key = (movie_id, movie_seq)
                id_counts[key] += 1

                if len(id_examples[key]) < 3:
                    id_examples[key].append(path.name)

            for field in ("title", "prodYear"):
                if not has_value(movie.get(field)):
                    missing[field] += 1

            for field in ("directors", "actors", "staffs"):
                if not has_credits(movie, field):
                    missing[field] += 1

    duplicate_ids = {
        key: count
        for key, count in id_counts.items()
        if count > 1
    }

    print("\n========== 레코드 검증 ==========")
    print("예상 레코드:", EXPECTED_TOTAL)
    print("실제 레코드:", total_records)
    print("고유 영화 ID:", len(id_counts))
    print("중복 영화 ID 종류:", len(duplicate_ids))
    print(
        "중복으로 발생한 추가 레코드:",
        sum(n - 1 for n in duplicate_ids.values())
    )

    print("\n========== 주요 필드 누락 ==========")
    for field in (
        "movie_id_or_seq",
        "title",
        "prodYear",
        "directors",
        "actors",
        "staffs",
    ):
        count = missing[field]
        rate = (
            count / total_records * 100
            if total_records else 0
        )
        print(f"{field}: {count:,}건 ({rate:.2f}%)")

    print("\n========== 오류 ==========")
    print("JSON 파싱 오류:", len(parse_errors))
    print("페이지 건수 오류:", len(page_errors))

    for error in parse_errors[:10]:
        print("[PARSE]", error)

    for error in page_errors[:10]:
        print("[PAGE]", error)

    print("\n========== 중복 샘플 ==========")
    for key, count in list(duplicate_ids.items())[:20]:
        print(
            key,
            "count=", count,
            "files=", id_examples[key]
        )

    print("\n========== 최종 판정 ==========")

    complete = (
        not missing_starts
        and not extra_starts
        and not parse_errors
        and not page_errors
        and total_records == EXPECTED_TOTAL
    )

    if complete and not duplicate_ids:
        print("[PASS] 파일·건수·식별자 검증 통과")
    elif complete:
        print("[REVIEW] 수집 건수 정상, ID 중복 검토 필요")
    else:
        print("[FAIL] 누락 또는 정합성 오류 확인 필요")


def analyze_duplicate_pages():
    from collections import defaultdict, Counter

    occurrences = defaultdict(list)

    for path in sorted(DATA_DIR.glob("kmdb_start*_count500.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        for movie in get_movies(data):
            movie_id = str(movie.get("movieId") or "").strip()
            movie_seq = str(movie.get("movieSeq") or "").strip()

            if movie_id and movie_seq:
                occurrences[(movie_id, movie_seq)].append({
                    "file": path.name,
                    "title": movie.get("title"),
                    "prodYear": movie.get("prodYear"),
                })

    page_pairs = Counter()
    affected_pages = Counter()
    different_records = []

    for key, records in occurrences.items():
        if len(records) <= 1:
            continue

        files = sorted(set(r["file"] for r in records))

        for file in files:
            affected_pages[file] += 1

        if len(files) == 2:
            page_pairs[tuple(files)] += 1

        if len({
            json.dumps(
                (r["title"], r["prodYear"]),
                ensure_ascii=False,
                default=str
            )
            for r in records
        }) > 1:
            different_records.append((key, records))

    print("\n========== 중복 집중 페이지 TOP 20 ==========")
    for filename, count in affected_pages.most_common(20):
        print(filename, count)

    print("\n========== 중복 페이지 쌍 TOP 20 ==========")
    for (file_a, file_b), count in page_pairs.most_common(20):
        print(file_a, "<->", file_b, ":", count)

    print("\n========== 동일 ID의 제목/연도 차이 ==========")
    print("차이 발생 ID:", len(different_records))

    for key, records in different_records[:10]:
        print(key, records)


analyze_duplicate_pages()


if __name__ == "__main__":
    main()
    analyze_duplicate_pages()