"""Deduplicate KMDb kmdb_new2 API pages and audit credits. Read-only for originals/DB.

Usage: python scripts/01_clean_validate_kmdb.py
       python scripts/01_clean_validate_kmdb.py --input-dir data/raw/new_kmdb/api_full
"""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

PAGE_RE = re.compile(r"^kmdb_start(\d+)_count\d+\.json$")
EXPECTED_TOTAL = 122627


def results_of(data):
    results = []
    for group in data.get("Data", []):
        if isinstance(group, dict) and isinstance(group.get("Result"), list):
            results.extend(group["Result"])
    return results


def items_of(movie, container, singular):
    block = movie.get(container)
    if isinstance(block, dict):
        block = block.get(singular, [])
    if isinstance(block, dict):
        block = [block]
    return [x for x in block if isinstance(x, dict)] if isinstance(block, list) else []


def clean(value):
    return str(value).strip() if value is not None else ""


def person_name(row, prefix):
    # KMDb person fields commonly include directorNm / actorNm / staffNm.
    for field in (prefix + "Nm", prefix + "Name", "name"):
        if clean(row.get(field)):
            return clean(row[field])
    return ""


def credit_stats(movie):
    d = items_of(movie, "directors", "director")
    a = items_of(movie, "actors", "actor")
    s = items_of(movie, "staffs", "staff")
    return {
        "directors": sum(bool(person_name(x, "director")) for x in d),
        "actors": sum(bool(person_name(x, "actor")) for x in a),
        "staffs": sum(bool(person_name(x, "staff")) for x in s),
        "staff_rows": len(s),
    }


def score(movie):
    c = credit_stats(movie)
    return (int(c["directors"] > 0) + int(c["actors"] > 0) + int(c["staffs"] > 0),
            c["directors"] + c["actors"] + c["staffs"],
            int(bool(clean(movie.get("title")))),
            int(bool(clean(movie.get("prodYear")))))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--expected-total", type=int, default=EXPECTED_TOTAL)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    # Supports a script saved at project root OR in a one-level-deep scripts/ directory.
    if not (root / "data").exists() and (root.parent / "data").exists():
        root = root.parent
    input_dir = args.input_dir or root / "data/raw/new_kmdb/api_full"
    output_dir = args.output_dir or root / "data/raw/new_kmdb/processed"
    if not input_dir.is_dir():
        raise SystemExit(f"입력 폴더 없음: {input_dir}")
    paths = []
    for p in input_dir.glob("kmdb_start*_count*.json"):
        m = PAGE_RE.match(p.name)
        if m:
            paths.append((int(m.group(1)), p))
    paths.sort()
    if not paths:
        raise SystemExit("수집 JSON 파일이 없습니다.")
    starts = [s for s, _ in paths]
    if len(starts) != len(set(starts)):
        raise SystemExit("동일 startCount의 파일이 여러 개입니다. 먼저 확인하세요.")

    best = {}
    occurrences = defaultdict(list)
    total = 0
    missing_ids = 0
    for start, path in paths:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        movies = results_of(data)
        total += len(movies)
        for index, movie in enumerate(movies):
            key = (clean(movie.get("movieId")), clean(movie.get("movieSeq")))
            if not all(key):
                missing_ids += 1
                continue
            quality = score(movie)
            occurrences[key].append((path.name, index, quality))
            if key not in best or quality > best[key][0]:
                best[key] = (quality, movie, path.name)

    if missing_ids:
        raise SystemExit(f"식별자 없는 레코드 {missing_ids}건 발견. 저장을 중단합니다.")
    duplicate_ids = sum(len(v) > 1 for v in occurrences.values())
    duplicate_extra = sum(len(v) - 1 for v in occurrences.values())
    missing_fields = Counter()
    credits = Counter()
    roles = Counter()
    role_examples = {}
    unknown_role_rows = 0

    for _, movie, _ in best.values():
        for field in ("title", "prodYear"):
            if not clean(movie.get(field)):
                missing_fields[field] += 1
        stats = credit_stats(movie)
        for field in ("directors", "actors", "staffs"):
            if stats[field]:
                credits[field + "_movies"] += 1
            else:
                missing_fields[field + "_no_named_person"] += 1
            credits[field + "_people"] += stats[field]
        for staff in items_of(movie, "staffs", "staff"):
            if not person_name(staff, "staff"):
                continue
            # Do not assume a role schema; record raw role fields for inspection.
            group = clean(staff.get("staffRoleGroup"))
            role = clean(staff.get("staffRole"))
            key = (group, role)
            roles[key] += 1
            if key not in role_examples:
                role_examples[key] = person_name(staff, "staff")
            if not group and not role:
                unknown_role_rows += 1

    output_dir.mkdir(parents=True, exist_ok=True)
    unique_path = output_dir / "kmdb_unique_movies.jsonl"
    with unique_path.open("w", encoding="utf-8") as f:
        for _, movie, _ in best.values():
            f.write(json.dumps(movie, ensure_ascii=False) + "\n")

    duplicate_path = output_dir / "kmdb_duplicate_report.csv"
    with duplicate_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["movieId", "movieSeq", "occurrences", "source_file", "record_index", "selected", "quality_score"])
        for key, records in occurrences.items():
            if len(records) < 2:
                continue
            chosen_file = best[key][2]
            chosen_score = best[key][0]
            selected_once = False
            for filename, index, quality in records:
                selected = filename == chosen_file and quality == chosen_score and not selected_once
                if selected:
                    selected_once = True
                writer.writerow([*key, len(records), filename, index, int(selected), str(quality)])

    roles_path = output_dir / "kmdb_staff_roles.csv"
    with roles_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["staffRoleGroup", "staffRole", "count", "example_staff_name"])
        for (group, role), count in roles.most_common():
            writer.writerow([group, role, count, role_examples[(group, role)]])

    expected_starts = set(range(0, args.expected_total, 500))
    report = {
        "expected_total": args.expected_total,
        "input_files": len(paths),
        "missing_page_starts": sorted(expected_starts - set(starts)),
        "extra_page_starts": sorted(set(starts) - expected_starts),
        "input_records": total,
        "unique_movies": len(best),
        "duplicate_movie_ids": duplicate_ids,
        "duplicate_extra_records": duplicate_extra,
        "missing_fields": dict(missing_fields),
        "credit_counts": dict(credits),
        "staff_named_people_without_role": unknown_role_rows,
        "staff_role_categories": len(roles),
        "note": "Credit presence means at least one named person. No DB changes. Completeness vs live API not guaranteed.",
    }
    report_path = output_dir / "kmdb_validation_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n========== KMDb 정제 결과 ==========")
    print(f"원본 파일: {len(paths):,}개 / 원본 레코드: {total:,}건")
    print(f"고유 영화: {len(best):,}건")
    print(f"중복 ID: {duplicate_ids:,}개 / 제거한 추가 레코드: {duplicate_extra:,}건")
    print(f"누락 페이지: {report['missing_page_starts']}")
    print("\n========== 주요 필드 / 크레딧 ==========")
    for k, v in sorted(missing_fields.items()):
        print(f"{k}: {v:,}건 ({v / len(best) * 100:.2f}%)")
    for k, v in sorted(credits.items()):
        print(f"{k}: {v:,}")
    print(f"스태프 역할 종류: {len(roles):,}개")
    print(f"역할값 없는 이름 있는 스태프: {unknown_role_rows:,}명")
    print("\n========== 스태프 역할 TOP 30 ==========")
    for (group, role), count in roles.most_common(30):
        print(f"{group!r} / {role!r}: {count:,}")
    print("\n========== 저장 파일 ==========")
    for p in (unique_path, duplicate_path, roles_path, report_path):
        print(p)


if __name__ == "__main__":
    main()
