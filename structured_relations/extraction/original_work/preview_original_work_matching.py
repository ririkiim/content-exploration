
import argparse
import os
import re
import unicodedata

from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv


# 1. 환경 설정 및 DB 연결

def connect_db():
    for parent in Path(__file__).resolve().parents:
        env_path = parent / ".env"
        if env_path.is_file():
            load_dotenv(env_path)
            print(f"[INFO] .env: {env_path}")
            break

    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL이 설정되어 있지 않습니다.")

    conn = psycopg.connect(
        url, row_factory=dict_row, autocommit=True
    )
    conn.execute("SET default_transaction_read_only = on")
    return conn


# 2. 문자열 정규화

EDITION_PREFIXES = ("초판본", "복간본")

EDITION_SUFFIXES = {
    "리커버", "리커버판", "리커버 에디션",
    "리마스터판", "리마스터 에디션",
    "개정판", "개정증보판",
    "양장", "양장본", "양장판", "양장 특별판",
    "특별판", "특별 에디션", "한정판", "초판본",
    "애니메이션 더블 커버", "선라이즈 에디션",
}

BRACKET_PAIRS = (
    ("[", "]"), ("(", ")"), ("「", "」"),
    ("『", "』"), ("《", "》"), ("〈", "〉"),
    ("【", "】"), ('"', '"'), ("'", "'"),
)


def normalize_base(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = "".join(
        ch for ch in value
        if unicodedata.category(ch) not in {"Cc", "Cf"}
        or ch.isspace()
    )
    return re.sub(r"\s+", " ", value).strip()


def strip_outer_brackets(value):
    while value:
        for left, right in BRACKET_PAIRS:
            if value.startswith(left) and value.endswith(right):
                inner = value[len(left):-len(right)].strip()
                if not inner or left in inner or right in inner:
                    continue
                value = inner
                break
        else:
            return value
    return value


def normalize_title(value):
    value = strip_outer_brackets(normalize_base(value))

    for prefix in EDITION_PREFIXES:
        value = re.sub(
            rf"^{re.escape(prefix)}\s+", "", value, count=1
        ).strip()

    allowed = {
        re.sub(r"\s+", "", normalize_base(s))
        for s in EDITION_SUFFIXES
    }

    while match := re.search(r"\s*\(([^()]*)\)\s*$", value):
        suffix = re.sub(r"\s+", "", normalize_base(match.group(1)))
        if suffix not in allowed:
            break
        value = value[:match.start()].strip()

    return re.sub(r"\s+", "", strip_outer_brackets(value))


def normalize_author(value):
    return re.sub(r"\s+", "", normalize_base(value))


def make_key(value, normalizer, normalized):
    return normalizer(value) if normalized else (value or "").strip()


# 3. 데이터 조회

def fetch_kmdb_originals(conn):
    return conn.execute("""
        SELECT DISTINCT
            kc.credit_id,
            kc.kmdb_id,
            c.content_id,
            c.title AS content_title,
            c.content_type,
            kc.original_title,
            kc.original_type,
            kc.person_name AS author_name,
            kc.person_id
        FROM public.kmdb_credit kc
        JOIN public.content_source_map csm
            ON kc.kmdb_id = csm.source_id
           AND csm.source_name = 'kmdb_movie'
        JOIN public.content c
            ON csm.content_id = c.content_id
        WHERE kc.original_parse_status = 'parsed'
          AND kc.link_role = 'original_author'
          AND NULLIF(BTRIM(kc.original_title), '') IS NOT NULL
        ORDER BY c.content_id, kc.original_title, kc.credit_id
    """).fetchall()


def fetch_books(conn):
    return conn.execute("""
        SELECT DISTINCT
            c.content_id,
            c.title AS content_title,
            e.entity_id AS author_entity_id,
            e.entity_name AS author_name,
            ce.fact_status AS author_fact_status,
            ce.evidence_status AS author_evidence_status
        FROM public.content c
        LEFT JOIN public.content_entity ce
            ON c.content_id = ce.content_id
           AND ce.link_role = 'author'
           AND ce.fact_status IN ('verified', 'proposed')
        LEFT JOIN public.entity e
            ON ce.entity_id = e.entity_id
           AND e.entity_type = 'PERSON'
        WHERE c.content_type = 'book'
        ORDER BY c.content_id, e.entity_name
    """).fetchall()


# 4. 매칭 레코드 구성
# KMDb: content_id + 작품명 기준으로 그룹화
# 도서: content_id 기준으로 그룹화

def build_records(rows, source, normalized=True):
    records = {}

    for row in rows:
        is_kmdb = source == "kmdb"
        title = (
            row["original_title"] if is_kmdb
            else row["content_title"]
        )
        author = row["author_name"]

        title_key = make_key(title, normalize_title, normalized)
        author_key = make_key(author, normalize_author, normalized)

        if not title_key:
            continue

        key = (
            (row["content_id"], title_key)
            if is_kmdb else row["content_id"]
        )

        if key not in records:
            records[key] = {
                "source": source,
                "content_id": row["content_id"],
                "content_title": row["content_title"],
                "title_key": title_key,
                "raw_titles": set(),
                "authors": set(),
                "raw_authors": set(),
                "original_types": set(),
                "credit_ids": set(),
                "kmdb_ids": set(),
                "person_ids": defaultdict(set),
                "author_entity_ids": defaultdict(set),
                "author_statuses": defaultdict(set),
            }

        r = records[key]
        r["raw_titles"].add(title)

        if author_key:
            r["authors"].add(author_key)
            r["raw_authors"].add(author)

        if is_kmdb:
            original_type = (row["original_type"] or "").strip()
            if original_type:
                r["original_types"].add(original_type)

            r["credit_ids"].add(row["credit_id"])
            r["kmdb_ids"].add(row["kmdb_id"])

            person_id = str(row["person_id"] or "").strip()
            if author_key and person_id:
                r["person_ids"][author_key].add(person_id)

        elif author_key and row["author_entity_id"] is not None:
            r["author_entity_ids"][author_key].add(
                row["author_entity_id"]
            )
            r["author_statuses"][author_key].add((
                row["author_fact_status"],
                row["author_evidence_status"],
            ))

    return list(records.values())


# 5. 매칭 인덱스 및 판정

MATCH_TYPES = (
    "EXACT",
    "AUTHOR_ONLY",
    "TITLE_ONLY_MISSING_AUTHOR",
    "TITLE_ONLY_AUTHOR_MISMATCH",
)


def build_indexes(records):
    title_index = defaultdict(set)
    author_index = defaultdict(set)

    for i, r in enumerate(records):
        title_index[r["title_key"]].add(i)
        for author in r["authors"]:
            author_index[author].add(i)

    return title_index, author_index


def classify_match(a, b):
    title_match = a["title_key"] == b["title_key"]
    shared = a["authors"] & b["authors"]

    if title_match and shared:
        match_type = "EXACT"
    elif title_match:
        match_type = (
            "TITLE_ONLY_MISSING_AUTHOR"
            if not a["authors"] or not b["authors"]
            else "TITLE_ONLY_AUTHOR_MISMATCH"
        )
    elif shared:
        match_type = "AUTHOR_ONLY"
    else:
        return None

    # 동일 KMDb 출처 내 인물 ID 충돌 확인
    id_conflicts = set()

    if a["source"] == b["source"] == "kmdb":
        for author in shared:
            ids_a = a["person_ids"].get(author, set())
            ids_b = b["person_ids"].get(author, set())

            if ids_a and ids_b and ids_a.isdisjoint(ids_b):
                id_conflicts.add(author)

    return {
        "match_type": match_type,
        "left": a,
        "right": b,
        "shared_authors": shared,
        "author_set_equal": bool(shared) and a["authors"] == b["authors"],
        "person_id_conflicts": id_conflicts,
        "type_difference": (
            bool(a["original_types"])
            and bool(b["original_types"])
            and a["original_types"] != b["original_types"]
        ),
        "auto_merge_eligible": (
            match_type == "EXACT" and not id_conflicts
        ),
    }


def match_records(left, right=None):
    internal = right is None
    right = left if internal else right

    title_index, author_index = build_indexes(right)
    results = []

    for i, a in enumerate(left):
        candidates = set(title_index.get(a["title_key"], set()))

        for author in a["authors"]:
            candidates.update(author_index.get(author, set()))

        for j in sorted(candidates):
            b = right[j]

            if internal and (
                j <= i or a["content_id"] == b["content_id"]
            ):
                continue

            result = classify_match(a, b)
            if result:
                results.append(result)

    return results


# 6. 통계 및 예시 출력

def print_input_summary(kmdb_rows, book_rows, kmdb, books):
    print("\n" + "=" * 75)
    print("[INPUT SUMMARY]")
    print("=" * 75)

    print(f"KMDb 원본 credit: {len(kmdb_rows):,}")
    print(f"KMDb 고유 콘텐츠: {len({r['content_id'] for r in kmdb}):,}")
    print(f"KMDb 원작 매칭 단위: {len(kmdb):,}")
    print(f"도서 조회 행: {len(book_rows):,}")
    print(f"도서 고유 콘텐츠: {len(books):,}")
    print(f"저자 정보가 없는 도서: {sum(not r['authors'] for r in books):,}")

    status_counts = Counter(
        row["author_fact_status"]
        for row in book_rows
        if row["author_entity_id"] is not None
    )
    print(f"도서 저자 관계 상태: {dict(status_counts)}")

    print("\n[KMDb 원작 유형 분포]")
    for name, count in Counter(
        r["original_type"] or "(없음)" for r in kmdb_rows
    ).most_common():
        print(f"  {name}: {count:,}")


def print_normalization_examples(kmdb_rows, book_rows, limit):
    print("\n" + "=" * 75)
    print("[NORMALIZATION EXAMPLES]")
    print("=" * 75)

    for label, normalizer in (
        ("작품명", normalize_title),
        ("저자명", normalize_author),
    ):
        changed = set()

        for row in kmdb_rows + book_rows:
            raw = (
                row.get("original_title") or row["content_title"]
                if label == "작품명"
                else row["author_name"]
            )

            if raw and raw != normalizer(raw):
                changed.add((raw, normalizer(raw)))

        ordered = sorted(
            changed,
            key=lambda p: (
                re.sub(r"\s+", "", normalize_base(p[0])) == p[1],
                p[0],
            )
        )

        print(f"\n[{label} 변경] {len(changed):,}개")
        for raw, normalized in ordered[:limit]:
            print(f"  {raw} -> {normalized}")


def print_summary(label, before, after):
    old = Counter(r["match_type"] for r in before)

    print("\n" + "=" * 90)
    print(f"[MATCH SUMMARY] {label}")
    print("=" * 90)
    print(
        f"{'MATCH TYPE':<30}{'BEFORE':>9}{'AFTER':>9}"
        f"{'CHANGE':>9}{'LEFT':>9}{'RIGHT':>9}"
    )

    for match_type in MATCH_TYPES:
        selected = [r for r in after if r["match_type"] == match_type]
        left = {r["left"]["content_id"] for r in selected}
        right = {r["right"]["content_id"] for r in selected}

        print(
            f"{match_type:<30}{old[match_type]:>9,}"
            f"{len(selected):>9,}"
            f"{len(selected) - old[match_type]:>+9,}"
            f"{len(left):>9,}{len(right):>9,}"
        )

    exact = [r for r in after if r["match_type"] == "EXACT"]

    print("\n[EXACT MATCH CHECK]")
    print("  전체 EXACT:", len(exact))
    print("  자동 통합 가능 후보:", sum(r["auto_merge_eligible"] for r in exact))
    print("  KMDb 인물 ID 충돌:", sum(bool(r["person_id_conflicts"]) for r in exact))
    print("  원작 유형 차이:", sum(r["type_difference"] for r in exact))
    print("  전체 저자 집합 일치:", sum(r["author_set_equal"] for r in exact))
    print("  공동 저자 일부 일치:", sum(not r["author_set_equal"] for r in exact))


def print_match(r, i):
    print(f"\n[{i}] {r['match_type']}")

    for side, record in (("A", r["left"]), ("B", r["right"])):
        titles = " / ".join(sorted(record["raw_titles"]))
        authors = ", ".join(sorted(record["raw_authors"])) or "(없음)"

        print(
            f"  {side}: [{record['source']}] {record['content_title']}"
            f" (content_id={record['content_id']})"
        )
        print(f"     작품명: {titles} | 저자: {authors}")

        if record["original_types"]:
            print("     원작 유형:", ", ".join(sorted(record["original_types"])))

        if record["source"] == "book":
            for author in sorted(record["author_statuses"]):
                statuses = ", ".join(
                    f"{fact}/{evidence}"
                    for fact, evidence in sorted(record["author_statuses"][author])
                )
                print(f"     저자 상태: {author} ({statuses})")

    if r["shared_authors"]:
        print("     공통 저자:", ", ".join(sorted(r["shared_authors"])))

    if r["type_difference"]:
        print("     [INFO] 원작 유형 차이 (통합 허용)")

    if r["person_id_conflicts"]:
        print("     [WARN] 인물 ID 충돌:", ", ".join(sorted(r["person_id_conflicts"])))

    if r["match_type"] == "EXACT":
        print("     자동 통합 가능:", r["auto_merge_eligible"])


def print_examples(label, results, limit):
    print("\n" + "=" * 75)
    print(f"[MATCH EXAMPLES] {label}")
    print("=" * 75)

    for match_type in MATCH_TYPES:
        selected = [r for r in results if r["match_type"] == match_type]

        selected.sort(key=lambda r: (
            not bool(r["person_id_conflicts"]),
            not r["type_difference"],
            r["left"]["content_title"],
            r["right"]["content_title"],
        ))

        print(f"\n--- {match_type} (total={len(selected):,}) ---")

        seen = set()
        shown = 0

        for r in selected:
            # 동일 저자 또는 동일 원작의 반복 출력 제한
            key = (
                tuple(sorted(r["shared_authors"]))
                if match_type == "AUTHOR_ONLY"
                else r["left"]["title_key"]
            )

            if key in seen:
                continue

            seen.add(key)
            shown += 1
            print_match(r, shown)

            if shown >= limit:
                break

        if not shown:
            print("  매칭 결과 없음")


# 7. 실행

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if args.limit < 1:
        parser.error("--limit은 1 이상이어야 합니다.")

    with connect_db() as conn:
        kmdb_rows = fetch_kmdb_originals(conn)
        book_rows = fetch_books(conn)

    kb = build_records(kmdb_rows, "kmdb", normalized=False)
    bb = build_records(book_rows, "book", normalized=False)

    ka = build_records(kmdb_rows, "kmdb", normalized=True)
    ba = build_records(book_rows, "book", normalized=True)

    internal_before = match_records(kb)
    internal_after = match_records(ka)

    cross_before = match_records(kb, bb)
    cross_after = match_records(ka, ba)

    print_input_summary(kmdb_rows, book_rows, ka, ba)
    print_normalization_examples(kmdb_rows, book_rows, args.limit)

    print_summary("KMDb <-> KMDb", internal_before, internal_after)
    print_summary("KMDb <-> BOOK", cross_before, cross_after)

    print_examples("KMDb <-> KMDb", internal_after, args.limit)
    print_examples("KMDb <-> BOOK", cross_after, args.limit)

    print("\n[DONE] Preview completed. No database changes.")


if __name__ == "__main__":
    main()
