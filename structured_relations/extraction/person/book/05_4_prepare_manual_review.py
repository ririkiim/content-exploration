"""
Prepare selected book authors for manual authority review.

- Select only designated authors.
- Include known author-name spelling variants.
- Preserve existing 05-2 and 05-3 candidates.
- Separate author identity review and book-author review.
- Exclude unselected unresolved rows.
- No API calls.
- No database writes.
"""

import argparse
import csv
import json
import re
import unicodedata

from collections import Counter, defaultdict
from pathlib import Path


# =========================================================
# 1. Configuration
# =========================================================

ROOT = next(
    (
        p
        for p in Path(__file__).resolve().parents
        if (p / ".env").exists()
    ),
    Path.cwd(),
)

OUT = (
    ROOT
    / "structured_relations"
    / "data"
    / "author_authority"
)

VERSION = "book-author-manual-review-v1"

INPUT_05_2 = (
    OUT / "05_2_book_match_results.jsonl"
)

INPUT_05_3 = (
    OUT / "05_3_test_results.jsonl"
)

OUTPUT_AUTHORS = (
    OUT / "05_4_manual_authors.csv"
)

OUTPUT_BOOKS = (
    OUT / "05_4_manual_books.csv"
)

OUTPUT_AUTO = (
    OUT / "05_4_auto_candidates.jsonl"
)

OUTPUT_TARGETS = (
    OUT / "05_4_manual_targets.jsonl"
)

OUTPUT_EXCLUDED = (
    OUT / "05_4_excluded.jsonl"
)

OUTPUT_SUMMARY = (
    OUT / "05_4_summary.json"
)


# =========================================================
# 2. Selected authors and spelling variants
# =========================================================

# These groups are manual review groups.
# They do not automatically establish identity.

SELECTED_AUTHORS = {

    "J.K. 롤링": [
        "J.K 롤링",
        "J. K. 롤링",
        "J. K 롤링",
        "롤링, J. K.",
        "Rowling, J. K.",
        "조앤 K. 롤링",
    ],

    "싱숑": [],

    "솔체": [],

    "박경리": [],

    "김애란": [],

    "도스토예프스키": [
        "도스토옙스키",
        "표도르 도스토예프스키",
        "표도르 도스토옙스키",
        "표도르 미하일로비치 도스토예프스키",
        "표도르 미하일로비치 도스토옙스키",
        "표도르 미하일로비치 도스또예프스끼",
        "도스또예프스끼",
    ],

    "톨스토이": [
        "레프 톨스토이",
        "레프 니콜라예비치 톨스토이",
        "레오 톨스토이",
    ],

    "알베르 카뮈": [
        "알베르 까뮈",
        "알베르 카뮤",
    ],

    "요한 볼프강 폰 괴테": [
        "요한 볼프강 괴테",
        "괴테",
    ],

    "앙투안 드 생텍쥐페리": [
        "앙투안 마리 로제 드 생택쥐베리",
        "앙투안 마리 로제 드 생텍쥐페리",
        "앙투안 드 생택쥐베리",
        "앙투안 드 생텍쥐페리",
        "생텍쥐페리",
        "생택쥐베리",
    ],

    "프랜시스 스콧 피츠제럴드": [
        "F.스콧 피츠제럴드",
        "F. 스콧 피츠제럴드",
        "스콧 피츠제럴드",
        "프랜시스 스콧 키 피츠제럴드",
    ],

    "아서 코난 도일": [
        "아서 코넌 도일",
        "코난 도일",
        "코넌 도일",
    ],

    "류츠신": [
        "류츠씬",
        "리우츠신",
        "유자흔",
    ],

    "우케쓰": [
        "우케츠",
        "우케쯔",
    ],

    "이우혁": [],

    "히가시노 게이고": [
        "히가시노 케이고",
        "東野圭吾",
    ],

    "무라카미 하루키": [
        "村上春樹",
    ],

    "윌리엄 셰익스피어": [
        "윌리엄 쉐익스피어",
        "셰익스피어",
        "쉐익스피어",
    ],

    "프란츠 카프카": [
        "프란츠 카프가",
        "카프카",
    ],

    "베르나르 베르베르": [
        "베르나르 베르베르",
    ],

    "윤동주": [],

    "정세랑": [],

    "김초엽": [],

    "장강명": [],

    "최진영": [],

    "어니스트 헤밍웨이": [
        "어니스트 헤밍웨이",
        "어니스트 밀러 헤밍웨이",
        "헤밍웨이",
    ],

    "백석": [],

    "나태주": [],

    "성해나": [],

    "최인훈": [],

    "조예은": [],

    "이민진": [
        "Min Jin Lee",
        "Lee, Min Jin",
    ],
}


AUTO_05_2_STATUSES = {
    "reuse_existing_candidate",
}

AUTO_05_3_STATUSES = {
    "reuse_existing_authority_candidate",
    "new_authority_candidate",
}


# =========================================================
# 3. Basic utilities
# =========================================================

def clean(value):

    if value is None:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()


def name_key(value):

    value = unicodedata.normalize(
        "NFKC",
        clean(value),
    ).casefold()

    return re.sub(
        r"[\s.,·ㆍ・･．]",
        "",
        value,
    )


def valid_kac(value):

    value = clean(value).upper()

    if re.fullmatch(
        r"KAC[0-9A-Z]+",
        value,
    ):
        return value

    return None


def valid_isni(value):

    value = re.sub(
        r"[\s-]",
        "",
        clean(value),
    ).upper()

    if not re.fullmatch(
        r"\d{15}[\dX]",
        value,
    ):
        return None

    total = 0

    for digit in value[:15]:

        total = (
            total + int(digit)
        ) * 2

    check = (
        12 - total % 11
    ) % 11

    expected = (
        "X"
        if check == 10
        else str(check)
    )

    if value[-1] != expected:
        return None

    return value


def as_list(value):

    if value is None:
        return []

    if isinstance(
        value,
        (list, tuple, set),
    ):
        return list(value)

    return [value]


def join_values(values):

    return " | ".join(
        sorted({
            clean(value)
            for value in values
            if clean(value)
        })
    )


def row_key(row):

    return (
        str(row["book_edition_id"]),
        str(row["content_id"]),
        name_key(row["author_name"]),
    )


# =========================================================
# 4. File utilities
# =========================================================

def read_jsonl(path):

    if not path.exists():

        raise FileNotFoundError(
            f"Input file not found: {path}"
        )

    rows = []

    with path.open(
        encoding="utf-8-sig",
    ) as f:

        for line_no, line in enumerate(
            f,
            1,
        ):

            if not line.strip():
                continue

            try:

                rows.append(
                    json.loads(line)
                )

            except json.JSONDecodeError as e:

                raise ValueError(
                    f"Invalid JSON: "
                    f"{path}:{line_no}"
                ) from e

    return rows


def write_jsonl(path, rows):

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    with tmp.open(
        "w",
        encoding="utf-8",
    ) as f:

        for row in rows:

            f.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=str,
                ) + "\n"
            )

    tmp.replace(path)


def write_json(path, data):

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    tmp.replace(path)


def write_csv(path, fields, rows):

    tmp = path.with_suffix(
        path.suffix + ".tmp"
    )

    with tmp.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        writer.writerows(rows)

    tmp.replace(path)


def unique_index(rows, label):

    result = {}

    for row in rows:

        key = row_key(row)

        if key in result:

            if result[key] != row:

                raise ValueError(
                    f"Conflicting duplicate "
                    f"in {label}: {key}"
                )

            continue

        result[key] = row

    return result


# =========================================================
# 5. Build selected-author lookup
# =========================================================

def build_author_lookup():

    lookup = {}

    for canonical_name, variants in (
        SELECTED_AUTHORS.items()
    ):

        for name in [
            canonical_name,
            *variants,
        ]:

            key = name_key(name)

            if (
                key in lookup
                and lookup[key] != canonical_name
            ):

                raise ValueError(
                    f"Author variant conflict: "
                    f"{name}"
                )

            lookup[key] = canonical_name

    return lookup


# =========================================================
# 6. Extract existing DB candidates
# =========================================================

def extract_db_candidates(row):

    results = []

    for candidate in row.get(
        "candidates",
        [],
    ):

        kac_ids = sorted({
            valid
            for value in as_list(
                candidate.get("kac_ids")
            )
            if (valid := valid_kac(value))
        })

        isni_ids = sorted({
            valid
            for value in as_list(
                candidate.get("isni")
            )
            if (valid := valid_isni(value))
        })

        results.append({
            "source": "05_2_existing_db",

            "entity_id": candidate.get(
                "entity_id"
            ),

            "entity_name": candidate.get(
                "entity_name"
            ),

            "kac_ids": kac_ids,

            "isni": isni_ids,

            "identifier_conflict": bool(
                candidate.get(
                    "identifier_conflict"
                )
                or len(isni_ids) > 1
            ),

            "works": candidate.get(
                "works",
                [],
            ),

            "overlapping_works": (
                candidate.get(
                    "overlapping_works",
                    [],
                )
            ),

            "raw_candidate": candidate,
        })

    return results


# =========================================================
# 7. Extract 05-3 authority candidates
# =========================================================

def extract_authority_candidates(row):

    if not row:
        return []

    results = []

    for candidate in row.get(
        "authority_candidates",
        [],
    ):

        kac_ids = sorted({
            valid
            for value in [
                candidate.get("kac"),
                *as_list(
                    candidate.get("kac_ids")
                ),
            ]
            if (valid := valid_kac(value))
        })

        isni_ids = sorted({
            valid
            for value in as_list(
                candidate.get("isni")
            )
            if (valid := valid_isni(value))
        })

        results.append({
            "source": "05_3_nlk_lod",

            "kac_ids": kac_ids,

            "isni": isni_ids,

            "names": candidate.get(
                "names",
                [],
            ),

            "birth_years": candidate.get(
                "birth_years",
                [],
            ),

            "fields_of_activity": (
                candidate.get(
                    "fields_of_activity",
                    [],
                )
            ),

            "jobs": candidate.get(
                "jobs",
                [],
            ),

            "sources": candidate.get(
                "sources",
                [],
            ),

            "authority_uri": candidate.get(
                "authority_uri"
            ),

            "author_name_match": bool(
                candidate.get(
                    "author_name_match"
                )
            ),

            "existing_entity_ids": (
                candidate.get(
                    "existing_entity_ids",
                    [],
                )
            ),

            "work_evidence": candidate.get(
                "work_evidence",
                [],
            ),

            "identifier_conflict": bool(
                candidate.get(
                    "identifier_conflict"
                )
                or candidate.get(
                    "existing_identifier_conflict"
                )
                or len(isni_ids) > 1
            ),

            "raw_candidate": candidate,
        })

    return results


# =========================================================
# 8. Collect automatically recommended candidates
# =========================================================

def get_auto_candidate(row_05_2, row_05_3):

    status_05_2 = row_05_2.get(
        "match_status"
    )

    if status_05_2 in AUTO_05_2_STATUSES:

        entity_id = row_05_2.get(
            "recommended_entity_id"
        )

        if entity_id is None:
            return None

        matches = [
            candidate
            for candidate in extract_db_candidates(
                row_05_2
            )
            if str(candidate["entity_id"])
            == str(entity_id)
        ]

        if len(matches) != 1:
            return None

        candidate = matches[0]

        if candidate["identifier_conflict"]:
            return None

        if not (
            candidate["kac_ids"]
            or candidate["isni"]
        ):
            return None

        return {
            **row_05_2,

            "manual_review_version": VERSION,

            "auto_source": "05_2",

            "auto_status": status_05_2,

            "selected_entity_id": entity_id,

            "selected_kac_ids": (
                candidate["kac_ids"]
            ),

            "selected_isni": (
                candidate["isni"]
            ),

            "auto_candidate": candidate,

            "review_status": "auto_candidate",
        }

    if not row_05_3:
        return None

    status_05_3 = row_05_3.get(
        "authority_status"
    )

    if status_05_3 not in AUTO_05_3_STATUSES:
        return None

    selected = row_05_3.get(
        "selected_authority"
    ) or {}

    kac_ids = sorted({
        valid
        for value in as_list(
            selected.get("kac_ids")
        )
        if (valid := valid_kac(value))
    })

    isni_ids = sorted({
        valid
        for value in as_list(
            selected.get("isni")
        )
        if (valid := valid_isni(value))
    })

    if not kac_ids and not isni_ids:
        return None

    if len(isni_ids) > 1:
        return None

    if not row_05_3.get(
        "lookup_complete",
        False,
    ):
        return None

    if not selected.get("evidence"):
        return None

    entity_id = selected.get(
        "existing_entity_id"
    )

    if (
        status_05_3
        == "reuse_existing_authority_candidate"
        and entity_id is None
    ):
        return None

    return {
        **row_05_2,

        "manual_review_version": VERSION,

        "auto_source": "05_3",

        "auto_status": status_05_3,

        "selected_entity_id": entity_id,

        "selected_kac_ids": kac_ids,

        "selected_isni": isni_ids,

        "auto_candidate": selected,

        "authority_search_result": row_05_3,

        "review_status": "auto_candidate",
    }


# =========================================================
# 9. Prepare manual target
# =========================================================

def make_manual_target(
    row_05_2,
    row_05_3,
    canonical_name,
):

    return {
        **row_05_2,

        "manual_review_version": VERSION,

        "review_author_name": canonical_name,

        "previous_group_id": row_05_2.get(
            "group_id"
        ),

        "previous_match_status": (
            row_05_2.get(
                "match_status"
            )
        ),

        "authority_status": (
            row_05_3.get(
                "authority_status"
            )
            if row_05_3
            else "not_searched"
        ),

        "db_candidates": (
            extract_db_candidates(
                row_05_2
            )
        ),

        "authority_candidates": (
            extract_authority_candidates(
                row_05_3
            )
        ),

        "authority_search_result": (
            row_05_3
        ),

        "review_status": "pending",
    }


# =========================================================
# 10. Summarize candidate hints
# =========================================================

def collect_hints(
    manual_rows,
    auto_rows,
):

    kac_ids = set()
    isni_ids = set()
    entity_ids = set()

    candidate_names = set()
    authority_urls = set()

    candidate_details = set()
    existing_works = set()
    authority_works = set()

    identifier_conflict = False

    for row in manual_rows:

        # Existing DB author candidates.

        for candidate in row[
            "db_candidates"
        ]:

            eid = candidate.get(
                "entity_id"
            )

            if eid is not None:
                entity_ids.add(eid)

            kac_ids.update(
                candidate["kac_ids"]
            )

            isni_ids.update(
                candidate["isni"]
            )

            candidate_names.add(
                candidate.get(
                    "entity_name"
                )
            )

            candidate_details.add(
                f"DB entity={eid}; "
                f"name={candidate.get('entity_name')}; "
                f"KAC={','.join(candidate['kac_ids'])}; "
                f"ISNI={','.join(candidate['isni'])}"
            )

            if candidate[
                "identifier_conflict"
            ]:

                identifier_conflict = True

            for work in candidate.get(
                "works",
                [],
            ):

                if isinstance(work, dict):

                    title = work.get(
                        "title"
                    )

                    if title:

                        existing_works.add(
                            f"entity {eid}: "
                            f"{title}"
                        )

            for kac in candidate[
                "kac_ids"
            ]:

                authority_urls.add(
                    "http://lod.nl.go.kr/resource/"
                    + kac
                )

        # NLK authority candidates.

        for candidate in row[
            "authority_candidates"
        ]:

            # Do not suggest unrelated creators
            # or translators as identity hints.

            if not candidate[
                "author_name_match"
            ]:
                continue

            kac_ids.update(
                candidate["kac_ids"]
            )

            isni_ids.update(
                candidate["isni"]
            )

            entity_ids.update(
                candidate[
                    "existing_entity_ids"
                ]
            )

            candidate_names.update(
                candidate["names"]
            )

            uri = candidate.get(
                "authority_uri"
            )

            if uri:
                authority_urls.add(uri)

            candidate_details.add(
                f"KAC={','.join(candidate['kac_ids'])}; "
                f"ISNI={','.join(candidate['isni'])}; "
                f"name={','.join(candidate['names'])}; "
                f"birth={','.join(candidate['birth_years'])}; "
                f"field={','.join(candidate['fields_of_activity'])}"
            )

            if candidate[
                "identifier_conflict"
            ]:

                identifier_conflict = True

            for work in candidate[
                "work_evidence"
            ]:

                title = work.get(
                    "matched_title"
                )

                if title:

                    authority_works.add(
                        f"{title} "
                        f"[{work.get('title_match')}]"
                    )

    # Preserve identifiers from already
    # recommended rows of the same review group.
    #
    # These are hints, not group-level
    # automatic identity decisions.

    for row in auto_rows:

        kac_ids.update(
            row["selected_kac_ids"]
        )

        isni_ids.update(
            row["selected_isni"]
        )

        eid = row.get(
            "selected_entity_id"
        )

        if eid is not None:
            entity_ids.add(eid)

        candidate_details.add(
            f"AUTO {row['auto_source']}: "
            f"{row['title']}; "
            f"KAC={','.join(row['selected_kac_ids'])}; "
            f"ISNI={','.join(row['selected_isni'])}; "
            f"entity={eid}"
        )

        for kac in row[
            "selected_kac_ids"
        ]:

            authority_urls.add(
                "http://lod.nl.go.kr/resource/"
                + kac
            )

    return {
        "candidate_kac": join_values(
            kac_ids
        ),

        "candidate_isni": join_values(
            isni_ids
        ),

        "candidate_entity_ids": join_values(
            entity_ids
        ),

        "candidate_names": join_values(
            candidate_names
        ),

        "candidate_details": join_values(
            candidate_details
        ),

        "existing_db_works": join_values(
            existing_works
        ),

        "authority_work_evidence": join_values(
            authority_works
        ),

        "candidate_authority_urls": join_values(
            authority_urls
        ),

        "identifier_conflict_hint": (
            "yes"
            if identifier_conflict
            else "no"
        ),

        "multiple_identity_hint": (
            "yes"
            if (
                len(isni_ids) > 1
                or len(entity_ids) > 1
                or len(kac_ids) > 1
            )
            else "no"
        ),
    }


# =========================================================
# 11. Author review CSV
# =========================================================

AUTHOR_FIELDS = [

    "manual_author_id",

    "review_author_name",

    "observed_name_variants",

    "manual_book_count",

    "auto_book_count",

    "manual_book_titles",

    "auto_book_titles",

    "candidate_kac",

    "candidate_isni",

    "candidate_entity_ids",

    "candidate_names",

    "candidate_details",

    "existing_db_works",

    "authority_work_evidence",

    "candidate_authority_urls",

    "identifier_conflict_hint",

    "multiple_identity_hint",

    # Manual input fields

    "verified_author_name",

    "verified_kac",

    "verified_isni",

    "selected_entity_id",

    "identity_action",

    "same_person_confirmed",

    "evidence_url",

    "review_status",

    "review_note",
]


def make_author_review(
    manual_author_id,
    canonical_name,
    manual_rows,
    auto_rows,
):

    hints = collect_hints(
        manual_rows,
        auto_rows,
    )

    return {
        "manual_author_id": (
            manual_author_id
        ),

        "review_author_name": (
            canonical_name
        ),

        "observed_name_variants": join_values(
            row["author_name"]
            for row in [
                *manual_rows,
                *auto_rows,
            ]
        ),

        "manual_book_count": len(
            manual_rows
        ),

        "auto_book_count": len(
            auto_rows
        ),

        "manual_book_titles": join_values(
            row["title"]
            for row in manual_rows
        ),

        "auto_book_titles": join_values(
            row["title"]
            for row in auto_rows
        ),

        **hints,

        # Do not automatically populate
        # manually verified fields.

        "verified_author_name": "",

        "verified_kac": "",

        "verified_isni": "",

        "selected_entity_id": "",

        "identity_action": "",

        "same_person_confirmed": "pending",

        "evidence_url": "",

        "review_status": "pending",

        "review_note": "",
    }


# =========================================================
# 12. Book-author review CSV
# =========================================================

BOOK_FIELDS = [

    "manual_book_id",

    "manual_author_id",

    "previous_group_id",

    "review_author_name",

    "author_name",

    "book_edition_id",

    "content_id",

    "title",

    "normalized_title",

    "isbn13",

    "pub_year",

    "previous_match_status",

    "authority_status",

    "candidate_kac",

    "candidate_isni",

    "candidate_entity_ids",

    "candidate_details",

    "authority_work_evidence",

    # Manual input fields

    "book_author_confirmed",

    "evidence_isbn",

    "evidence_url",

    "review_status",

    "review_note",
]


def make_book_review(
    manual_book_id,
    manual_author_id,
    row,
):

    hints = collect_hints(
        [row],
        [],
    )

    return {
        "manual_book_id": (
            manual_book_id
        ),

        "manual_author_id": (
            manual_author_id
        ),

        "previous_group_id": row.get(
            "previous_group_id"
        ),

        "review_author_name": row[
            "review_author_name"
        ],

        "author_name": row[
            "author_name"
        ],

        "book_edition_id": row[
            "book_edition_id"
        ],

        "content_id": row[
            "content_id"
        ],

        "title": row[
            "title"
        ],

        "normalized_title": row.get(
            "normalized_title",
            row["title"],
        ),

        "isbn13": row.get(
            "isbn13",
            "",
        ),

        "pub_year": row.get(
            "pub_year",
            "",
        ),

        "previous_match_status": row.get(
            "previous_match_status"
        ),

        "authority_status": row.get(
            "authority_status"
        ),

        "candidate_kac": hints[
            "candidate_kac"
        ],

        "candidate_isni": hints[
            "candidate_isni"
        ],

        "candidate_entity_ids": hints[
            "candidate_entity_ids"
        ],

        "candidate_details": hints[
            "candidate_details"
        ],

        "authority_work_evidence": hints[
            "authority_work_evidence"
        ],

        "book_author_confirmed": "pending",

        "evidence_isbn": "",

        "evidence_url": "",

        "review_status": "pending",

        "review_note": "",
    }


# =========================================================
# 13. Main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Overwrite existing 05-4 outputs. "
            "Do not use after manual review "
            "without backing up CSV files."
        ),
    )

    args = parser.parse_args()

    output_paths = [
        OUTPUT_AUTHORS,
        OUTPUT_BOOKS,
        OUTPUT_AUTO,
        OUTPUT_TARGETS,
        OUTPUT_EXCLUDED,
        OUTPUT_SUMMARY,
    ]

    existing_outputs = [
        path
        for path in output_paths
        if path.exists()
    ]

    if existing_outputs and not args.overwrite:

        raise FileExistsError(
            "05-4 output files already exist.\n"
            "Back up manual review CSV files "
            "before using --overwrite."
        )

    OUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    author_lookup = (
        build_author_lookup()
    )

    rows_05_2 = read_jsonl(
        INPUT_05_2
    )

    rows_05_3 = read_jsonl(
        INPUT_05_3
    )

    index_05_2 = unique_index(
        rows_05_2,
        "05_2",
    )

    index_05_3 = unique_index(
        rows_05_3,
        "05_3",
    )

    # Prevent mixing stale 05-3 data with
    # a different 05-2 input dataset.

    unknown_keys = (
        set(index_05_3)
        - set(index_05_2)
    )

    if unknown_keys:

        raise ValueError(
            f"05-3 contains "
            f"{len(unknown_keys)} rows "
            f"not found in 05-2."
        )

    auto_candidates = []
    manual_targets = []
    excluded = []

    manual_groups = defaultdict(list)
    auto_groups = defaultdict(list)

    observed_selected_names = set()

    print(
        f"[INFO] 05-2 rows: "
        f"{len(index_05_2)}"
    )

    print(
        f"[INFO] 05-3 test rows: "
        f"{len(index_05_3)}"
    )

    print(
        f"[INFO] Selected authors: "
        f"{len(SELECTED_AUTHORS)}"
    )

    # -----------------------------------------------------
    # Classify each book-author row
    # -----------------------------------------------------

    for key, row_05_2 in (
        index_05_2.items()
    ):

        row_05_3 = index_05_3.get(
            key
        )

        canonical_name = author_lookup.get(
            name_key(
                row_05_2["author_name"]
            )
        )

        if canonical_name:

            observed_selected_names.add(
                canonical_name
            )

        auto = get_auto_candidate(
            row_05_2,
            row_05_3,
        )

        # Preserve all previously recommended
        # automatic candidates.

        if auto:

            auto[
                "review_author_name"
            ] = canonical_name

            auto_candidates.append(
                auto
            )

            if canonical_name:

                auto_groups[
                    canonical_name
                ].append(auto)

            continue

        # Unselected unresolved rows
        # are excluded from further work.

        if not canonical_name:

            excluded.append({
                **row_05_2,

                "manual_review_version": VERSION,

                "exclusion_reason": (
                    "author_not_selected"
                ),

                "authority_search_result": (
                    row_05_3
                ),

                "review_status": "excluded",
            })

            continue

        # Selected unresolved rows
        # become manual review targets.

        target = make_manual_target(
            row_05_2,
            row_05_3,
            canonical_name,
        )

        manual_groups[
            canonical_name
        ].append(target)

    # -----------------------------------------------------
    # Generate author and book review rows
    # -----------------------------------------------------

    author_review_rows = []
    book_review_rows = []

    book_counter = 0

    for author_number, canonical_name in enumerate(
        SELECTED_AUTHORS,
        1,
    ):

        group_rows = manual_groups.get(
            canonical_name,
            [],
        )

        group_auto = auto_groups.get(
            canonical_name,
            [],
        )

        if not group_rows:
            continue

        manual_author_id = (
            f"MA{author_number:03d}"
        )

        group_rows.sort(
            key=lambda row: (
                row["title"],
                str(row["content_id"]),
                str(row["book_edition_id"]),
            )
        )

        author_review_rows.append(
            make_author_review(
                manual_author_id,
                canonical_name,
                group_rows,
                group_auto,
            )
        )

        print(
            f"\n[AUTHOR] "
            f"{manual_author_id} | "
            f"{canonical_name} | "
            f"manual={len(group_rows)} | "
            f"auto={len(group_auto)}"
        )

        for row in group_rows:

            book_counter += 1

            manual_book_id = (
                f"MB{book_counter:04d}"
            )

            row[
                "manual_author_id"
            ] = manual_author_id

            row[
                "manual_book_id"
            ] = manual_book_id

            manual_targets.append(
                row
            )

            book_review_rows.append(
                make_book_review(
                    manual_book_id,
                    manual_author_id,
                    row,
                )
            )

            print(
                f"  [MANUAL] "
                f"{row['title']} | "
                f"{row['author_name']}"
            )

    # -----------------------------------------------------
    # Validate row accounting
    # -----------------------------------------------------

    total_classified = (
        len(auto_candidates)
        + len(manual_targets)
        + len(excluded)
    )

    if total_classified != len(
        index_05_2
    ):

        raise RuntimeError(
            "Row accounting mismatch: "
            f"{total_classified} "
            f"!= {len(index_05_2)}"
        )

    missing_selected = [
        name
        for name in SELECTED_AUTHORS
        if name not in observed_selected_names
    ]

    auto_only_selected = [
        name
        for name in SELECTED_AUTHORS
        if auto_groups.get(name)
        and not manual_groups.get(name)
    ]

    auto_counts = Counter(
        row["auto_source"]
        for row in auto_candidates
    )

    manual_status_counts = Counter(
        row["authority_status"]
        for row in manual_targets
    )

    summary = {
        "version": VERSION,

        "input_05_2_rows": len(
            index_05_2
        ),

        "input_05_3_test_rows": len(
            index_05_3
        ),

        "selected_author_count": len(
            SELECTED_AUTHORS
        ),

        "selected_authors_found": len(
            observed_selected_names
        ),

        "selected_authors_not_found": (
            missing_selected
        ),

        "selected_authors_auto_only": (
            auto_only_selected
        ),

        "manual_author_count": len(
            author_review_rows
        ),

        "manual_book_author_rows": len(
            manual_targets
        ),

        "auto_candidate_rows": len(
            auto_candidates
        ),

        "auto_candidate_source_counts": dict(
            auto_counts
        ),

        "manual_authority_status_counts": dict(
            manual_status_counts
        ),

        "excluded_unselected_rows": len(
            excluded
        ),

        "total_classified_rows": (
            total_classified
        ),

        "manual_authors": [
            {
                "manual_author_id": row[
                    "manual_author_id"
                ],

                "author_name": row[
                    "review_author_name"
                ],

                "manual_book_count": row[
                    "manual_book_count"
                ],

                "auto_book_count": row[
                    "auto_book_count"
                ],
            }

            for row in author_review_rows
        ],

        "api_calls": 0,

        "database_changed": False,

        "outputs": [
            str(path)
            for path in output_paths
        ],
    }

    # -----------------------------------------------------
    # Write outputs
    # -----------------------------------------------------

    write_csv(
        OUTPUT_AUTHORS,
        AUTHOR_FIELDS,
        author_review_rows,
    )

    write_csv(
        OUTPUT_BOOKS,
        BOOK_FIELDS,
        book_review_rows,
    )

    write_jsonl(
        OUTPUT_AUTO,
        auto_candidates,
    )

    write_jsonl(
        OUTPUT_TARGETS,
        manual_targets,
    )

    write_jsonl(
        OUTPUT_EXCLUDED,
        excluded,
    )

    write_json(
        OUTPUT_SUMMARY,
        summary,
    )

    print("\n=== SUMMARY ===")

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(
        "\n[DONE] Manual review files created."
    )

    print(
        "[DONE] API calls: 0"
    )

    print(
        "[DONE] Database changed: False"
    )


if __name__ == "__main__":
    main()