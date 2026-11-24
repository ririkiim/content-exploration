"""Read-only book author matching against existing PostgreSQL entities.

Matching criteria:
    1. Normalized author name
    2. Exact book title
    3. Edition-normalized book title
    4. Series title
    5. Partial title

Author-name-only matches are not sufficient
for identity merging.

Input:
    03_author_review.jsonl

No DB writes or external API calls.
"""

import argparse
import csv
import json
import os
import re
import unicodedata

from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

from structured_relations.extraction.person.book.title_normalization import (
    POLICY_VERSION as TITLE_POLICY_VERSION,
    MATCH_SCORES,
    STRONG_TITLE_MATCHES,
    normalize_title,
    normalize_series_title,
    compare_titles,
)

# =========================================================
# 1. Configuration
# =========================================================

POLICY_VERSION = 'book-author-title-match-v3'

# =========================================================
# 2. Basic utilities
# =========================================================

def root():

    return next(
        (
            p
            for p in Path(__file__).resolve().parents
            if (p / '.env').exists()
        ),
        Path.cwd(),
    )

def clean(value):

    return re.sub(
        r'\s+',
        ' ',
        str(value or ''),
    ).strip()

def key(value):
    """Normalized author-name key."""

    value = unicodedata.normalize(
        'NFKC',
        clean(value),
    ).casefold()

    return re.sub(
        r'[.\s·ㆍ・･．]',
        '',
        value,
    )

def read_rows(path):

    with path.open(
        encoding='utf-8-sig',
    ) as f:

        return [
            json.loads(line)
            for line in f
            if line.strip()
        ]

def write_jsonl(path, rows):

    tmp = path.with_suffix(
        path.suffix + '.tmp'
    )

    with tmp.open(
        'w',
        encoding='utf-8',
    ) as f:

        for row in rows:

            f.write(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    default=str,
                ) + '\n'
            )

    tmp.replace(path)

def write_csv(path, rows, fields):

    tmp = path.with_suffix(
        path.suffix + '.tmp'
    )

    with tmp.open(
        'w',
        encoding='utf-8-sig',
        newline='',
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
            extrasaction='ignore',
        )

        writer.writeheader()
        writer.writerows(rows)

    tmp.replace(path)

def ids(value):

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        return value

    return []

# =========================================================
# 3. Identifier validation
# =========================================================

def valid_kac(value):

    value = clean(value).upper()

    if re.fullmatch(
        r'KAC[0-9A-Z]+',
        value,
    ):
        return value

    return None

def valid_isni(value):
    """Validate ISNI format and checksum."""

    value = re.sub(
        r'[\s-]',
        '',
        str(value or ''),
    ).upper()

    if not re.fullmatch(
        r'\d{15}[\dX]',
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
        'X'
        if check == 10
        else str(check)
    )

    if value[-1] != expected:
        return None

    return value

# =========================================================
# 4. Load existing book authors
# =========================================================

def load_index(conn):

    people = {}

    index = defaultdict(
        lambda: defaultdict(set)
    )

    # -----------------------------------------------------
    # 4-1. Book authors only
    # -----------------------------------------------------

    for row in conn.execute("""
        SELECT DISTINCT
            e.entity_id,
            e.entity_name
        FROM public.entity e
        JOIN public.content_entity ce
            ON ce.entity_id = e.entity_id
        JOIN public.content c
            ON c.content_id = ce.content_id
        WHERE e.entity_type = 'PERSON'
          AND ce.link_role = 'author'
          AND c.content_type = 'book'
    """):

        eid = row['entity_id']

        people[eid] = {
            'entity_id': eid,
            'entity_name': row['entity_name'],
            'aliases': [],
            'works': {},
            'kac_ids': set(),
            'isni': set(),
            'invalid_isni': set(),
            'evidence': [],
        }

        name_key = key(
            row['entity_name']
        )

        if name_key:

            index[name_key][eid].add(
                'canonical_name'
            )

    # -----------------------------------------------------
    # 4-2. Aliases of existing book authors
    # -----------------------------------------------------

    if people:

        for row in conn.execute("""
            SELECT
                a.entity_id,
                a.alias_text,
                a.status
            FROM public.entity_alias a
            WHERE a.entity_id = ANY(%s)
        """, (list(people.keys()),)):

            eid = row['entity_id']

            people[eid]['aliases'].append({
                'alias': row['alias_text'],
                'status': row['status'],
            })

            alias_key = key(
                row['alias_text']
            )

            if (
                alias_key
                and row['status'] != 'rejected'
            ):

                index[alias_key][eid].add(
                    'alias:' + str(row['status'])
                )

    # -----------------------------------------------------
    # 4-3. Existing works and NLK evidence
    # -----------------------------------------------------

    for row in conn.execute("""
        SELECT
            ce.content_entity_id,
            ce.entity_id,
            ce.content_id,
            c.title,
            ce.fact_status,
            ce.evidence_status,
            ev.source_name,
            ev.source_ref,
            ev.evidence_location
        FROM public.content_entity ce
        JOIN public.content c
            ON c.content_id = ce.content_id
        LEFT JOIN public.content_entity_evidence ev
            ON ev.content_entity_id = ce.content_entity_id
            AND ev.source_name = 'nlk_lod'
        WHERE ce.link_role = 'author'
          AND c.content_type = 'book'
    """):

        person = people.get(
            row['entity_id']
        )

        if person is None:
            continue

        cid = row['content_id']

        if cid not in person['works']:

            person['works'][cid] = {
                'content_id': cid,
                'title': row['title'],

                'normalized_title': normalize_title(
                    row['title']
                ),

                'series_title': normalize_series_title(
                    row['title']
                ),

                'fact_status': row['fact_status'],

                'evidence_status': row[
                    'evidence_status'
                ],

                'has_direct_nlk_evidence': False,
            }

        location = row['evidence_location']

        if not isinstance(location, dict):
            continue

        kac = sorted({
            value
            for item in ids(
                location.get('kac_ids')
            )
            if (value := valid_kac(item))
        })

        isni = sorted({
            value
            for item in ids(
                location.get('isni')
            )
            if (value := valid_isni(item))
        })

        invalid_isni = [
            str(item)
            for item in ids(
                location.get('isni')
            )
            if clean(item)
            and not valid_isni(item)
        ]

        match_method = location.get(
            'match_method'
        )

        person['kac_ids'].update(kac)
        person['isni'].update(isni)

        person['invalid_isni'].update(
            invalid_isni
        )

        if (
            row['source_name'] == 'nlk_lod'
            and match_method == 'isbn_direct'
            and (kac or isni)
        ):

            person['works'][cid][
                'has_direct_nlk_evidence'
            ] = True

        if kac or isni or invalid_isni:

            person['evidence'].append({
                'content_entity_id': row[
                    'content_entity_id'
                ],

                'content_id': cid,

                'fact_status': row[
                    'fact_status'
                ],

                'evidence_status': row[
                    'evidence_status'
                ],

                'source': row['source_name'],
                'source_ref': row['source_ref'],
                'match_method': match_method,

                'kac_ids': kac,
                'isni': isni,
                'invalid_isni': invalid_isni,
            })

    # -----------------------------------------------------
    # 4-4. Identifier conflicts among book authors
    # -----------------------------------------------------

    kac_owners = defaultdict(set)
    isni_owners = defaultdict(set)

    for eid, person in people.items():

        for kac in person['kac_ids']:
            kac_owners[kac].add(eid)

        for isni in person['isni']:
            isni_owners[isni].add(eid)

    for eid, person in people.items():

        person['conflicting_kac_ids'] = sorted(
            kac
            for kac in person['kac_ids']
            if len(kac_owners[kac]) > 1
        )

        person['conflicting_isni'] = sorted(
            isni
            for isni in person['isni']
            if len(isni_owners[isni]) > 1
        )

        person['identifier_conflict'] = bool(
            len(person['isni']) > 1
            or person['invalid_isni']
            or person['conflicting_kac_ids']
            or person['conflicting_isni']
        )

    return people, index

# =========================================================
# 5. Evaluate individual book-author candidates
# =========================================================

def evaluate_candidate(row, person, methods):

    requested_title = clean(
        row.get('title')
    )

    title_matches = []

    for work in person['works'].values():

        method = compare_titles(
            requested_title=requested_title,

            existing_title=work['title'],

            requested_content_id=row.get(
                'content_id'
            ),

            existing_content_id=work[
                'content_id'
            ],
        )

        if method == 'name_only':
            continue

        title_matches.append({
            **work,

            'title_match_method': method,

            'title_match_score': MATCH_SCORES[
                method
            ],
        })

    title_matches.sort(
        key=lambda item: (
            -item['title_match_score'],
            item['fact_status'] != 'verified',
            not item['has_direct_nlk_evidence'],
            item['content_id'],
        )
    )

    best = (
        title_matches[0]
        if title_matches
        else None
    )

    best_method = (
        best['title_match_method']
        if best
        else 'name_only'
    )

    strong_matches = [
        work
        for work in title_matches
        if work['title_match_method']
        in STRONG_TITLE_MATCHES
    ]

    verified_strong_matches = [
        work
        for work in strong_matches
        if work['fact_status'] == 'verified'
        and work['evidence_status'] == 'checked'
        and work['has_direct_nlk_evidence']
    ]

    has_reliable_name_match = (
        'canonical_name' in methods
        or 'alias:verified' in methods
    )

    has_identifier = bool(
        person['kac_ids']
        or person['isni']
    )

    # A recommendation is not final identity verification.
    reuse_eligible = bool(
        verified_strong_matches
        and has_reliable_name_match
        and has_identifier
        and not person['identifier_conflict']
    )

    return {
        'entity_id': person['entity_id'],

        'entity_name': person['entity_name'],

        'methods': sorted(methods),

        'aliases': person['aliases'],

        'kac_ids': sorted(
            person['kac_ids']
        ),

        'isni': sorted(
            person['isni']
        ),

        'invalid_isni': sorted(
            person['invalid_isni']
        ),

        'works': list(
            person['works'].values()
        ),

        'overlapping_works': title_matches,

        'evidence': person['evidence'],

        'best_title_match': best_method,

        'best_title_match_score': MATCH_SCORES[
            best_method
        ],

        'strong_title_match_count': len(
            strong_matches
        ),

        'verified_strong_title_match_count': len(
            verified_strong_matches
        ),

        'identifier_conflict': person[
            'identifier_conflict'
        ],

        'conflicting_kac_ids': person[
            'conflicting_kac_ids'
        ],

        'conflicting_isni': person[
            'conflicting_isni'
        ],

        'reuse_eligible': reuse_eligible,
    }

# =========================================================
# 6. Classify individual book-author rows
# =========================================================

def classify_row(matches):

    if not matches:

        return (
            'no_existing_candidate',
            None,
        )

    strong = [
        match
        for match in matches
        if match['best_title_match']
        in STRONG_TITLE_MATCHES
    ]

    if len(strong) > 1:

        return (
            'multiple_strong_candidates',
            None,
        )

    if len(strong) == 1:

        candidate = strong[0]

        if candidate['identifier_conflict']:

            return (
                'identifier_conflict',
                None,
            )

        if candidate['reuse_eligible']:

            return (
                'reuse_existing_candidate',
                candidate['entity_id'],
            )

        return (
            'strong_title_review',
            None,
        )

    if any(
        match['best_title_match'] == 'series_title'
        for match in matches
    ):

        return (
            'series_title_review',
            None,
        )

    if any(
        match['best_title_match'] == 'partial_title'
        for match in matches
    ):

        return (
            'partial_title_review',
            None,
        )

    # Never merge based on author name alone.
    return (
        'name_only_review',
        None,
    )

# =========================================================
# 7. Main
# =========================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        '--data-dir',
        type=Path,
        default=(
            root()
            / 'structured_relations/data/author_authority'
        ),
    )

    parser.add_argument(
        '--exclude-pub-year',
        type=int,
        action='append',
        default=[],
    )

    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='Replace existing 05_2 outputs.',
    )

    args = parser.parse_args()

    folder = args.data_dir.resolve()

    names = [
        '05_2_existing_author_candidates.jsonl',
        '05_2_author_review.csv',
        '05_2_unmatched_authors.jsonl',
        '05_2_author_book_map.jsonl',
        '05_2_summary.json',
        '05_2_book_match_results.jsonl',
        '05_2_reuse_candidates.jsonl',
    ]

    paths = {
        name: folder / name
        for name in names
    }

    if (
        not args.overwrite
        and any(
            path.exists()
            for path in paths.values()
        )
    ):

        raise FileExistsError(
            '05_2 outputs already exist. '
            'Back up review files and use --overwrite.'
        )

    rows = read_rows(
        folder / '03_author_review.jsonl'
    )

    groups = defaultdict(list)

    seen = {}

    excluded = 0

    excluded_years = {
        str(year)
        for year in args.exclude_pub_year
    }

    # -----------------------------------------------------
    # 7-1. Group input rows by normalized author name
    # -----------------------------------------------------

    for row in rows:

        author_key = key(
            row.get('author_name')
        )

        if (
            not author_key
            or row.get('content_id') is None
            or row.get('book_edition_id') is None
        ):

            raise ValueError(
                f'Missing required values: {row}'
            )

        identity = (
            row['book_edition_id'],
            row['content_id'],
            clean(row['author_name']),
        )

        if identity in seen:

            if seen[identity] != row:

                raise ValueError(
                    f'Conflicting duplicate: {identity}'
                )

            continue

        seen[identity] = row

        if str(
            row.get('pub_year')
        ) in excluded_years:

            excluded += 1

            continue

        groups[author_key].append(row)

    # -----------------------------------------------------
    # 7-2. Read-only database connection
    # -----------------------------------------------------

    load_dotenv(
        root() / '.env'
    )

    url = os.getenv(
        'DATABASE_URL'
    )

    if not url:

        raise RuntimeError(
            'DATABASE_URL missing'
        )

    with psycopg.connect(
        url,
        row_factory=dict_row,
    ) as conn:

        conn.execute(
            'SET TRANSACTION READ ONLY'
        )

        people, index = load_index(conn)

        conn.rollback()

    print(
        f'[INFO] Existing book authors: {len(people)}'
    )

    print(
        f'[INFO] Author-name groups: {len(groups)}'
    )

    print(
        f'[INFO] Title policy: {TITLE_POLICY_VERSION}'
    )

    # -----------------------------------------------------
    # 7-3. Prepare outputs
    # -----------------------------------------------------

    candidates = []
    review = []
    unmatched = []
    mapping = []
    book_results = []
    reuse_candidates = []

    group_counts = Counter()
    row_counts = Counter()

    name_candidate_counts = Counter()
    title_match_counts = Counter()

    # -----------------------------------------------------
    # 7-4. Evaluate author-name groups
    # -----------------------------------------------------

    for i, (author_key, items) in enumerate(
        sorted(groups.items()),
        1,
    ):

        gid = f'EA{i:04d}'

        variants = Counter(
            clean(row['author_name'])
            for row in items
        )

        name = sorted(
            variants,
            key=lambda value: (
                -variants[value],
                value,
            ),
        )[0]

        titles = sorted({
            clean(row.get('title'))
            for row in items
        })

        existing = index.get(
            author_key,
            {},
        )

        if not existing:

            name_status = 'no_existing_candidate'

        elif len(existing) == 1:

            name_status = 'single_existing_candidate'

        else:

            name_status = 'multiple_existing_candidates'

        name_candidate_counts[name_status] += 1

        group_book_results = []
        unresolved_books = []

        group_candidate_map = {}

        # -------------------------------------------------
        # 7-5. Evaluate each individual book row
        # -------------------------------------------------

        for row in items:

            row_matches = []

            for eid, methods in sorted(
                existing.items()
            ):

                candidate = evaluate_candidate(
                    row=row,
                    person=people[eid],
                    methods=methods,
                )

                row_matches.append(
                    candidate
                )

            row_matches.sort(
                key=lambda item: (
                    -item['best_title_match_score'],
                    not item['reuse_eligible'],
                    item['entity_id'],
                )
            )

            status, selected_id = classify_row(
                row_matches
            )

            row_counts[status] += 1

            best_method = (
                row_matches[0]['best_title_match']
                if row_matches
                else 'no_candidate'
            )

            title_match_counts[best_method] += 1

            normalized_title = normalize_title(
                row.get('title')
            )

            series_title = normalize_series_title(
                row.get('title')
            )

            result = {
                **row,

                'group_id': gid,

                'policy_version': POLICY_VERSION,

                'title_policy_version': TITLE_POLICY_VERSION,

                'normalized_author_name': author_key,

                'normalized_title': normalized_title,

                'series_title': series_title,

                'name_match_status': name_status,

                'match_status': status,

                'best_title_match': best_method,

                'candidate_count': len(row_matches),

                'recommended_entity_id': selected_id,

                'same_person_confirmed': False,

                'candidates': row_matches,
            }

            book_results.append(result)

            group_book_results.append({
                'book_edition_id': row[
                    'book_edition_id'
                ],

                'content_id': row['content_id'],

                'title': row.get('title'),

                'normalized_title': normalized_title,

                'series_title': series_title,

                'pub_year': row.get('pub_year'),

                'match_status': status,

                'best_title_match': best_method,

                'recommended_entity_id': selected_id,
            })

            mapping.append({
                **row,

                'group_id': gid,

                'normalized_title': normalized_title,

                'series_title': series_title,

                'match_status': status,

                'best_title_match': best_method,

                'recommended_entity_id': selected_id,
            })

            if status == 'reuse_existing_candidate':

                reuse_candidates.append(result)

            else:

                unresolved_books.append(result)

            # ---------------------------------------------
            # Aggregate candidate information
            # ---------------------------------------------

            for candidate in row_matches:

                eid = candidate['entity_id']

                if eid not in group_candidate_map:

                    group_candidate_map[eid] = {
                        **candidate,

                        'overlapping_works': [],

                        'matched_input_books': [],

                        'strong_title_match_count': 0,

                        'verified_strong_title_match_count': 0,
                    }

                aggregated = group_candidate_map[eid]

                if (
                    candidate['best_title_match_score']
                    > aggregated['best_title_match_score']
                ):

                    aggregated['best_title_match'] = (
                        candidate['best_title_match']
                    )

                    aggregated['best_title_match_score'] = (
                        candidate['best_title_match_score']
                    )

                aggregated['reuse_eligible'] = (
                    aggregated['reuse_eligible']
                    or candidate['reuse_eligible']
                )

                aggregated['strong_title_match_count'] += (
                    candidate['strong_title_match_count']
                )

                aggregated[
                    'verified_strong_title_match_count'
                ] += candidate[
                    'verified_strong_title_match_count'
                ]

                for work in candidate['overlapping_works']:

                    aggregated['overlapping_works'].append({
                        **work,

                        'requested_content_id': row[
                            'content_id'
                        ],

                        'requested_title': row.get(
                            'title'
                        ),
                    })

                aggregated['matched_input_books'].append({
                    'book_edition_id': row[
                        'book_edition_id'
                    ],

                    'content_id': row['content_id'],

                    'title': row.get('title'),

                    'best_title_match': candidate[
                        'best_title_match'
                    ],

                    'reuse_eligible': candidate[
                        'reuse_eligible'
                    ],
                })

            # ---------------------------------------------
            # 7-6. Individual review CSV row
            # ---------------------------------------------

            review.append({

                'group_id': gid,

                'book_edition_id': row[
                    'book_edition_id'
                ],

                'content_id': row['content_id'],

                'isbn13': row.get(
                    'isbn13',
                    '',
                ),

                'pub_year': row.get('pub_year'),

                'author_name': row['author_name'],

                'name_variants': ' | '.join(
                    sorted(variants)
                ),

                'title': row.get('title'),

                'normalized_title': normalized_title,

                'series_title': series_title,

                'name_match_status': name_status,

                'match_status': status,

                'best_title_match': best_method,

                'candidate_count': len(row_matches),

                'candidate_entity_ids': ' | '.join(
                    str(match['entity_id'])
                    for match in row_matches
                ),

                'candidate_names': ' | '.join(
                    match['entity_name']
                    for match in row_matches
                ),

                'candidate_methods': ' | '.join(
                    f"{match['entity_id']}: "
                    f"{','.join(match['methods'])}"
                    for match in row_matches
                ),

                'candidate_identifiers': ' | '.join(
                    f"{match['entity_id']}: "
                    f"{','.join(match['kac_ids'])} / "
                    f"{','.join(match['isni'])}"
                    for match in row_matches
                ),

                'existing_works': ' | '.join(
                    f"{match['entity_id']}: "
                    + ', '.join(
                        str(work['title'])
                        for work in match['works'][:15]
                    )
                    for match in row_matches
                ),

                'overlapping_works': ' | '.join(
                    f"{match['entity_id']}: "
                    f"{work['title']} "
                    f"[{work['title_match_method']}] "
                    f"[{work['fact_status']}]"
                    for match in row_matches
                    for work in match['overlapping_works']
                ),

                'identifier_conflict': any(
                    match['identifier_conflict']
                    for match in row_matches
                ),

                'recommended_entity_id': (
                    selected_id
                    if selected_id is not None
                    else ''
                ),

                'selected_entity_id': '',

                'same_person_confirmed': 'pending',

                'review_status': 'pending',

                'verified_author_name': '',

                'verified_kac': '',

                'verified_isni': '',

                'evidence_url': '',

                'review_note': '',
            })

            print(
                f'[{status}] '
                f'{row.get("title")} | '
                f'{row["author_name"]} | '
                f'{best_method} | '
                f'entity_id={selected_id}'
            )

        # -------------------------------------------------
        # 7-7. Aggregate group status
        # -------------------------------------------------

        reusable_count = sum(
            item['match_status']
            == 'reuse_existing_candidate'
            for item in group_book_results
        )

        recommended_ids = {
            item['recommended_entity_id']
            for item in group_book_results
            if item['recommended_entity_id'] is not None
        }

        if reusable_count == len(items):

            if len(recommended_ids) == 1:

                group_status = 'all_books_reuse_candidate'

            else:

                group_status = 'multiple_identities_review'

        elif reusable_count > 0:

            group_status = 'partially_reuse_candidate'

        elif not existing:

            group_status = 'no_existing_candidate'

        else:

            group_status = 'all_books_need_review'

        group_counts[group_status] += 1

        group_candidates = sorted(
            group_candidate_map.values(),
            key=lambda item: (
                -item['best_title_match_score'],
                item['entity_id'],
            ),
        )

        record = {
            'group_id': gid,

            'author_name': name,

            'name_variants': sorted(variants),

            'book_row_count': len(items),

            'titles': titles,

            'name_match_status': name_status,

            'match_status': group_status,

            'reusable_book_count': reusable_count,

            'unresolved_book_count': len(
                unresolved_books
            ),

            'recommended_entity_ids': sorted(
                recommended_ids
            ),

            'book_results': group_book_results,

            'candidates': group_candidates,
        }

        candidates.append(record)

        # Only unresolved rows are passed to the next stage.
        if unresolved_books:

            unmatched.append({

                **record,

                'original_book_row_count': len(items),

                'book_row_count': len(
                    unresolved_books
                ),

                'titles': sorted({
                    clean(item.get('title'))
                    for item in unresolved_books
                }),

                'book_results': [
                    item
                    for item in group_book_results
                    if item['match_status']
                    != 'reuse_existing_candidate'
                ],

                'unresolved_books': unresolved_books,
            })

    # =====================================================
    # 8. Write output files
    # =====================================================

    fields = [
        'group_id',

        'book_edition_id',
        'content_id',
        'isbn13',
        'pub_year',

        'author_name',
        'name_variants',

        'title',
        'normalized_title',
        'series_title',

        'name_match_status',
        'match_status',
        'best_title_match',

        'candidate_count',
        'candidate_entity_ids',
        'candidate_names',
        'candidate_methods',
        'candidate_identifiers',

        'existing_works',
        'overlapping_works',
        'identifier_conflict',

        'recommended_entity_id',
        'selected_entity_id',

        'same_person_confirmed',
        'review_status',

        'verified_author_name',
        'verified_kac',
        'verified_isni',

        'evidence_url',
        'review_note',
    ]

    write_jsonl(
        paths['05_2_existing_author_candidates.jsonl'],
        candidates,
    )

    write_csv(
        paths['05_2_author_review.csv'],
        review,
        fields,
    )

    write_jsonl(
        paths['05_2_unmatched_authors.jsonl'],
        unmatched,
    )

    write_jsonl(
        paths['05_2_author_book_map.jsonl'],
        mapping,
    )

    write_jsonl(
        paths['05_2_book_match_results.jsonl'],
        book_results,
    )

    write_jsonl(
        paths['05_2_reuse_candidates.jsonl'],
        reuse_candidates,
    )

    # =====================================================
    # 9. Summary
    # =====================================================

    summary = {
        'policy_version': POLICY_VERSION,

        'title_policy_version': TITLE_POLICY_VERSION,

        'input_rows': len(rows),

        'excluded_rows': excluded,

        'duplicate_rows': (
            len(rows) - len(seen)
        ),

        'processed_rows': len(mapping),

        'author_name_groups': len(groups),

        'db_book_person_count': len(people),

        'name_candidate_group_counts': dict(
            name_candidate_counts
        ),

        'group_status_counts': dict(
            group_counts
        ),

        'row_status_counts': dict(
            row_counts
        ),

        'best_title_match_counts': dict(
            title_match_counts
        ),

        'recommended_reuse_rows': len(
            reuse_candidates
        ),

        'remaining_review_rows': (
            len(mapping) - len(reuse_candidates)
        ),

        'remaining_review_groups': len(
            unmatched
        ),

        'recommended_reuse_rate_pct': (
            round(
                len(reuse_candidates)
                / len(mapping) * 100,
                2,
            )
            if mapping
            else 0
        ),

        'automatically_verified': 0,

        'database_changed': False,

        'outputs': [
            str(path)
            for path in paths.values()
        ],
    }

    summary_path = paths[
        '05_2_summary.json'
    ]

    tmp = summary_path.with_suffix(
        '.json.tmp'
    )

    tmp.write_text(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    tmp.replace(summary_path)

    print('\n=== SUMMARY ===')

    print(
        json.dumps(
            summary,
            ensure_ascii=False,
            indent=2,
        )
    )

if __name__ == '__main__':
    main()