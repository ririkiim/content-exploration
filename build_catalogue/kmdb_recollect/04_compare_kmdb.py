
import os
import json
import re
from pathlib import Path
from collections import Counter, defaultdict

import psycopg
from dotenv import load_dotenv


# =========================================================
# 1. 환경 설정
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")

NEW_KMDB_PATH = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "new_kmdb"
    / "processed"
    / "kmdb_unique_movies.jsonl"
)

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL이 .env에 없습니다.")

if not NEW_KMDB_PATH.exists():
    raise FileNotFoundError(NEW_KMDB_PATH)

ROLE_GROUP_MAP = {
    "감독": "director",
    "출연": "cast",
    "각본": "screenwriter",
    "원작": "original_author",
}

ROLES = (
    "director",
    "cast",
    "screenwriter",
    "original_author",
)

SAMPLE_LIMIT = 15


# =========================================================
# 2. 공통 함수
# =========================================================

def clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_kmdb_id(value):
    value = clean(value)

    if ":" not in value:
        return value

    movie_id, movie_seq = value.split(":", 1)

    return f"{movie_id.strip().upper()}:{movie_seq.strip()}"


def get_nested_list(movie, parent, child):
    container = movie.get(parent) or {}

    if not isinstance(container, dict):
        return []

    items = container.get(child) or []

    if isinstance(items, dict):
        return [items]

    if isinstance(items, list):
        return [
            item for item in items
            if isinstance(item, dict)
        ]

    return []


def print_section(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def print_count(label, count, total):
    rate = count / total * 100 if total else 0

    print(
        f"{label:<30}"
        f"{count:>10,} "
        f"({rate:>6.2f}%)"
    )


# =========================================================
# 3. 서비스 카탈로그 KMDb ID 조회
# =========================================================

def load_catalogue_kmdb():
    """
    실제 content_source_map에 등록된 KMDb 연결만 조회.

    raw_kmdb_movie는 조회하지 않음.
    DB는 읽기 전용.
    """

    query = """
        SELECT content_id, source_id
        FROM public.content_source_map
        WHERE source_name = 'kmdb_movie'
    """

    mappings = defaultdict(set)
    total_rows = 0

    with psycopg.connect(
        DATABASE_URL,
        autocommit=True,
        options="-c default_transaction_read_only=on",
    ) as conn:

        with conn.cursor() as cur:
            cur.execute(query)

            for content_id, source_id in cur:
                total_rows += 1

                kmdb_id = normalize_kmdb_id(source_id)

                if kmdb_id:
                    mappings[kmdb_id].add(content_id)

    return mappings, total_rows


# =========================================================
# 4. 신규 KMDb JSONL 조회
# =========================================================

def load_new_kmdb(target_ids):
    """
    서비스 카탈로그에 등록된 KMDb ID만 메모리에 보관.
    JSONL 전체를 읽되 불필요한 영화는 저장하지 않음.
    """

    movies = {}

    total_rows = 0
    invalid_ids = 0
    duplicate_target_ids = 0

    with NEW_KMDB_PATH.open(
        "r", encoding="utf-8-sig"
    ) as f:

        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue

            total_rows += 1

            try:
                movie = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"JSON 파싱 실패: {line_no}번째 줄"
                ) from exc

            movie_id = clean(movie.get("movieId"))
            movie_seq = clean(movie.get("movieSeq"))

            if not movie_id or not movie_seq:
                invalid_ids += 1
                continue

            kmdb_id = normalize_kmdb_id(
                f"{movie_id}:{movie_seq}"
            )

            if kmdb_id not in target_ids:
                continue

            if kmdb_id in movies:
                duplicate_target_ids += 1
                continue

            movies[kmdb_id] = movie

    return (
        movies,
        total_rows,
        invalid_ids,
        duplicate_target_ids,
    )


# =========================================================
# 5. 크레딧 추출
# =========================================================

def make_person(
    name,
    name_en,
    person_id,
    role,
    source_field,
    role_detail="",
    source_detail="",
):
    return {
        "name": clean(name),
        "name_en": clean(name_en),
        "person_id": clean(person_id),
        "role": role,
        "source_field": source_field,
        "role_detail": clean(role_detail),
        "source_detail": clean(source_detail),
    }


def extract_credits(movie):
    """
    감독: directors.director + staffs.staff(감독)
    배우: actors.actor + staffs.staff(출연)
    각본: staffs.staff(각본)
    원작자: staffs.staff(원작)

    이름이 없는 레코드도 원본 관찰을 위해 보존.
    중복 여부는 분석 단계에서 별도 집계.
    """

    credits = defaultdict(list)

    for person in get_nested_list(
        movie, "directors", "director"
    ):
        credits["director"].append(
            make_person(
                name=person.get("directorNm"),
                name_en=person.get("directorEnNm"),
                person_id=person.get("directorId"),
                role="director",
                source_field="directors",
            )
        )

    for person in get_nested_list(
        movie, "actors", "actor"
    ):
        credits["cast"].append(
            make_person(
                name=person.get("actorNm"),
                name_en=person.get("actorEnNm"),
                person_id=person.get("actorId"),
                role="cast",
                source_field="actors",
            )
        )

    for person in get_nested_list(
        movie, "staffs", "staff"
    ):
        role_group = clean(
            person.get("staffRoleGroup")
        )

        role = ROLE_GROUP_MAP.get(role_group)

        if not role:
            continue

        credits[role].append(
            make_person(
                name=person.get("staffNm"),
                name_en=person.get("staffEnNm"),
                person_id=person.get("staffId"),
                role=role,
                source_field="staffs",
                role_detail=person.get("staffRole"),
                source_detail=person.get("staffEtc"),
            )
        )

    return credits


def person_key(person):
    """
    같은 영화·역할 내 중복 탐지용 임시 키.

    ID가 있으면 ID 우선.
    ID가 없으면 이름 기준.

    서로 다른 영화 사이의 인물 동일성 판정에는
    사용하지 않음.
    """

    if person["person_id"]:
        return ("id", person["person_id"])

    if person["name"]:
        return ("name", person["name"].casefold())

    if person["name_en"]:
        return ("name_en", person["name_en"].casefold())

    return None


# =========================================================
# 6. 분석
# =========================================================

def analyze(mappings, movies):
    matched_ids = set(movies)
    catalogue_ids = set(mappings)

    print_section("1. 서비스 카탈로그 매칭 현황")

    print(f"카탈로그 KMDb ID: {len(catalogue_ids):,}")
    print(f"신규 API 매칭:    {len(matched_ids):,}")
    print(f"매칭 실패:        {len(catalogue_ids - matched_ids):,}")

    print_count(
        "카탈로그 기준 매칭률",
        len(matched_ids),
        len(catalogue_ids),
    )

    # -----------------------------------------------------
    # 역할별 통계
    # -----------------------------------------------------

    role_movie_count = Counter()
    role_raw_count = Counter()
    role_named_count = Counter()
    role_id_count = Counter()
    role_en_count = Counter()
    role_unique_count = Counter()
    role_duplicate_count = Counter()

    original_movies = set()
    original_with_detail = set()
    original_with_name = set()
    original_detail_values = Counter()

    original_samples = []

    id_to_names = defaultdict(set)
    name_to_ids = defaultdict(set)

    duplicate_samples = []

    for kmdb_id, movie in movies.items():
        credits = extract_credits(movie)

        for role in ROLES:
            people = credits[role]

            role_raw_count[role] += len(people)

            named_people = [
                p for p in people
                if p["name"] or p["name_en"]
            ]

            if named_people:
                role_movie_count[role] += 1

            seen = set()

            for person in people:
                if person["name"] or person["name_en"]:
                    role_named_count[role] += 1

                if person["person_id"]:
                    role_id_count[role] += 1

                if person["name_en"]:
                    role_en_count[role] += 1

                key = person_key(person)

                if key is not None:
                    if key in seen:
                        role_duplicate_count[role] += 1

                        if len(duplicate_samples) < SAMPLE_LIMIT:
                            duplicate_samples.append({
                                "kmdb_id": kmdb_id,
                                "title": clean(movie.get("title")),
                                "role": role,
                                "person": person,
                            })
                    else:
                        seen.add(key)
                        role_unique_count[role] += 1

                person_id = person["person_id"]
                person_name = person["name"]

                if person_id and person_name:
                    id_to_names[person_id].add(person_name)
                    name_to_ids[person_name].add(person_id)

        # 원작 그룹은 원작자 이름이 없어도 분석
        for person in credits["original_author"]:
            original_movies.add(kmdb_id)

            if person["name"]:
                original_with_name.add(kmdb_id)

            detail = person["source_detail"]

            if detail:
                original_with_detail.add(kmdb_id)
                original_detail_values[detail] += 1

            if len(original_samples) < SAMPLE_LIMIT:
                original_samples.append({
                    "kmdb_id": kmdb_id,
                    "title": clean(movie.get("title")),
                    "person_name": person["name"],
                    "person_id": person["person_id"],
                    "staff_role": person["role_detail"],
                    "staff_etc": detail,
                })

    # -----------------------------------------------------
    # 역할별 결과
    # -----------------------------------------------------

    print_section("2. 역할별 정보 확보율")

    total_movies = len(movies)

    for role in ROLES:
        print()
        print(f"[{role}]")

        print_count(
            "이름 있는 영화 수",
            role_movie_count[role],
            total_movies,
        )

        print(f"전체 크레딧 행:       {role_raw_count[role]:,}")
        print(f"이름 있는 행:         {role_named_count[role]:,}")
        print(f"인물 ID 있는 행:      {role_id_count[role]:,}")
        print(f"영어명 있는 행:       {role_en_count[role]:,}")
        print(f"중복 제거 후 인물-역할: {role_unique_count[role]:,}")
        print(f"중복 감지 행:         {role_duplicate_count[role]:,}")

        print_count(
            "크레딧 행 기준 ID 보유율",
            role_id_count[role],
            role_raw_count[role],
        )

        print_count(
            "크레딧 행 기준 영어명 보유율",
            role_en_count[role],
            role_raw_count[role],
        )

    # -----------------------------------------------------
    # 원작 정보
    # -----------------------------------------------------

    print_section("3. 원작 정보 현황")

    print_count(
        "원작 그룹 존재 영화",
        len(original_movies),
        total_movies,
    )

    print_count(
        "원작자 이름 존재 영화",
        len(original_with_name),
        total_movies,
    )

    print_count(
        "staffEtc 존재 영화",
        len(original_with_detail),
        total_movies,
    )

    print()
    print("[원작 정보 예시]")

    for sample in original_samples:
        print(
            f"\n{sample['kmdb_id']} | "
            f"{sample['title']}"
        )

        print(
            f"  원작자: {sample['person_name']}"
            f" / ID: {sample['person_id']}"
        )

        print(
            f"  staffRole: {sample['staff_role']}"
        )

        print(
            f"  staffEtc: {sample['staff_etc']}"
        )

    print()
    print("[자주 등장하는 staffEtc 값 - 상위 15개]")

    for value, count in original_detail_values.most_common(15):
        print(f"  {count:>5,} | {value[:120]}")

    # -----------------------------------------------------
    # 인물 ID 품질
    # -----------------------------------------------------

    print_section("4. 인물 ID 품질 점검")

    conflicting_ids = {
        person_id: names
        for person_id, names in id_to_names.items()
        if len(names) > 1
    }

    multiple_ids = {
        name: ids
        for name, ids in name_to_ids.items()
        if len(ids) > 1
    }

    print(
        f"동일 인물 ID에 여러 이름: "
        f"{len(conflicting_ids):,}개"
    )

    print(
        f"동일 이름에 여러 인물 ID: "
        f"{len(multiple_ids):,}개"
    )

    print()
    print("[동일 ID에 여러 이름 - 최대 10개]")

    for person_id, names in list(
        sorted(conflicting_ids.items())
    )[:10]:
        print(f"  {person_id} | {sorted(names)}")

    print()
    print("[동일 이름에 여러 ID - 최대 10개]")

    for name, ids in list(
        sorted(multiple_ids.items())
    )[:10]:
        print(f"  {name} | {sorted(ids)}")

    # -----------------------------------------------------
    # 중복 크레딧
    # -----------------------------------------------------

    print_section("5. 중복 크레딧 예시")

    for sample in duplicate_samples:
        person = sample["person"]

        print(
            f"{sample['kmdb_id']} | "
            f"{sample['title']} | "
            f"{sample['role']} | "
            f"{person['name']} | "
            f"{person['person_id']} | "
            f"{person['source_field']}"
        )

    # -----------------------------------------------------
    # 매칭 실패
    # -----------------------------------------------------

    print_section("6. 매칭 실패 KMDb ID")

    missing_ids = sorted(catalogue_ids - matched_ids)

    print(f"총 {len(missing_ids):,}개")

    for kmdb_id in missing_ids[:20]:
        print(f"  {kmdb_id}")

    print_section("분석 완료")
    print("DB 변경: 없음")
    print("결과 파일 생성: 없음")


# =========================================================
# 7. 실행
# =========================================================

def main():
    print("[INFO] 서비스 카탈로그 KMDb 매핑 조회 중...")

    mappings, mapping_rows = load_catalogue_kmdb()

    print(f"[INFO] 매핑 행: {mapping_rows:,}")
    print(f"[INFO] 고유 KMDb ID: {len(mappings):,}")

    print("[INFO] 신규 KMDb JSONL 조회 중...")

    (
        movies,
        total_rows,
        invalid_ids,
        duplicate_target_ids,
    ) = load_new_kmdb(set(mappings))

    print(f"[INFO] 신규 JSONL 전체 행: {total_rows:,}")
    print(f"[INFO] 식별자 누락: {invalid_ids:,}")
    print(
        f"[INFO] 매칭 대상 중복 ID: "
        f"{duplicate_target_ids:,}"
    )

    analyze(mappings, movies)


if __name__ == "__main__":
    main()
