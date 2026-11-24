"""Prepare book-author manual review CSVs, grouped by displayed author name.

Read-only: no DB/API access; original JSONL and existing DB are unchanged.

Usage:
  python 05_prepare_manual_authors.py --exclude-pub-year 2026
  python 05_prepare_manual_authors.py --data-dir path/to/author_authority

IMPORTANT: An author-name group is a REVIEW CANDIDATE, not a verified identity.
If namesakes are present, split group_id in BOTH group and mapping CSVs before loading.
"""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


def project_root():
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / '.env').exists():
            return parent
    return here.parent


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()

def normalize_author_key(value):
    name = clean(value).casefold()
    name = re.sub(r'[.\s·ㆍ・･．]', '', name)
    return name

def read_jsonl(path):
    if not path.is_file():
        raise FileNotFoundError(f'입력 파일이 없습니다: {path}')
    with path.open(encoding='utf-8-sig') as f:
        for number, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f'{path.name} {number}행 JSON 오류') from exc


def compact_candidates(row):
    """Display-only hints; never automatically accept candidates."""
    found = []
    for source in ('fallback_candidates', 'all_candidates'):
        for candidate in row.get(source) or []:
            kac = clean(candidate.get('kac') or candidate.get('author')).rsplit('/', 1)[-1]
            if not kac.startswith('KAC'):
                kac = ''
            isnis = candidate.get('isni') or []
            if isinstance(isnis, str):
                isnis = [isnis]
            hint = f"{kac or '-'} / {','.join(map(str, isnis)) or '-'}"
            if hint not in found:
                found.append(hint)
    return ' | '.join(found[:8])


def write_csv(path, fields, rows):
    # Atomic replacement avoids partially written CSVs.
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def join_unique(values):
    return ' | '.join(dict.fromkeys(v for v in values if v))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path,
                        default=project_root() / 'structured_relations/data/author_authority')
    parser.add_argument('--exclude-pub-year', type=int, action='append', default=[],
                        help='해당 출판연도는 검토용 CSV에서만 제외(반복 지정 가능)')
    parser.add_argument('--overwrite-reviewed', action='store_true',
                        help='기존 수동 검토 CSV가 있어도 덮어씀. 검토 입력값이 사라질 수 있음')
    args = parser.parse_args()
    folder = args.data_dir.resolve()
    records = list(read_jsonl(folder / '03_author_review.jsonl'))
    excluded_years = set(args.exclude_pub_year)

    unique = {}
    duplicates = []
    for r in records:
        edition_id, content_id = r.get('book_edition_id'), r.get('content_id')
        author = clean(r.get('author_name'))
        if edition_id is None or content_id is None or not author:
            raise ValueError(f'필수값 누락: {r}')
        key = (str(edition_id), str(content_id), author)
        if key in unique:
            if unique[key] != r:
                raise ValueError(f'동일 edition/content/author에 서로 다른 레코드: {key}')
            duplicates.append(key)
            continue
        unique[key] = r

    kept, excluded = [], []
    for r in unique.values():
        year = r.get('pub_year')
        if year is not None and str(year).isdigit() and int(year) in excluded_years:
            excluded.append(r)
        else:
            kept.append(r)

    by_author = defaultdict(list)

    for r in kept:
        key = normalize_author_key(r['author_name'])
        by_author[key].append(r)

    detail, summary, groups, mapping = [], [], [], []
    for index, (key, items) in enumerate(sorted(by_author.items()), 1):
        group_id = f'MA{index:04d}'

        # 원본 저자명 중 가장 자주 등장하는 표기를 대표명으로 사용
        name_counts = Counter(clean(r['author_name']) for r in items)

        name = sorted(
            name_counts,
            key=lambda n: (-name_counts[n], n)
        )[0]
        items = sorted(items, key=lambda x: (clean(x.get('title')), str(x['book_edition_id'])))
        titles = join_unique(clean(r.get('title')) for r in items)
        statuses = join_unique(sorted({clean(r.get('fallback_status')) for r in items}))
        hints = join_unique(compact_candidates(r) for r in items)
        content_count = len({str(r['content_id']) for r in items})

        summary.append({
            'group_id': group_id, 'author_name': name,
            'row_count': len(items), 'distinct_content_count': content_count,
            'titles': titles, 'fallback_statuses': statuses,
            'has_candidate_hints': 'yes' if hints else 'no',
        })
        groups.append({
            'group_id': group_id, 'author_name': name,
            'book_row_count': len(items), 'distinct_content_count': content_count,
            'titles': titles, 'fallback_statuses': statuses,
            'candidate_hints': hints,
            'same_person_confirmed': 'pending',
            'review_status': 'pending',
            'verified_author_name': '', 'verified_kac': '', 'verified_isni': '',
            'evidence_url': '', 'review_note': '',
        })
        for r in items:
            base = {
                'group_id': group_id,
                'book_edition_id': r['book_edition_id'],
                'content_id': r['content_id'],
                'title': r.get('title') or '',
                'isbn13': r.get('isbn13') or '',
                'author_name': name,
                'raw_author_name': r.get('raw_author_name') or '',
                'pub_year': r.get('pub_year') if r.get('pub_year') is not None else '',
                'status': r.get('status') or '',
                'fallback_status': r.get('fallback_status') or '',
                'candidate_hints': compact_candidates(r),
            }
            mapping.append(dict(base))
            detail.append({
                **base, 'author_name_row_count': len(items),
                'candidate_count': len(r.get('fallback_candidates') or []) + len(r.get('all_candidates') or []),
                'review_status': 'pending', 'verified_kac': '', 'verified_isni': '',
                'evidence_url': '', 'verified_author_name': '', 'review_note': '',
            })

    summary.sort(key=lambda r: (-r['row_count'], r['author_name']))
    detail_fields = [
        'group_id', 'book_edition_id', 'content_id', 'title', 'isbn13', 'author_name',
        'raw_author_name', 'pub_year', 'status', 'fallback_status',
        'author_name_row_count', 'candidate_count', 'candidate_hints',
        'review_status', 'verified_kac', 'verified_isni', 'evidence_url',
        'verified_author_name', 'review_note',
    ]
    summary_fields = ['group_id', 'author_name', 'row_count', 'distinct_content_count',
                      'titles', 'fallback_statuses', 'has_candidate_hints']
    group_fields = [
        'group_id', 'author_name', 'book_row_count', 'distinct_content_count',
        'titles', 'fallback_statuses', 'candidate_hints',
        'same_person_confirmed', 'review_status', 'verified_author_name',
        'verified_kac', 'verified_isni', 'evidence_url', 'review_note',
    ]
    map_fields = ['group_id', 'book_edition_id', 'content_id', 'title', 'isbn13',
                  'author_name', 'raw_author_name', 'pub_year', 'status',
                  'fallback_status', 'candidate_hints']

    outputs = [
        (folder / '05_manual_authors.csv', detail_fields, detail),
        (folder / '05_author_review_summary.csv', summary_fields, summary),
        (folder / '05_manual_author_groups.csv', group_fields, groups),
        (folder / '05_manual_author_book_map.csv', map_fields, mapping),
    ]
    # Never silently erase manual annotations from a prior run.
    protected = {'05_manual_authors.csv', '05_manual_author_groups.csv', '05_manual_author_book_map.csv'}
    if not args.overwrite_reviewed:
        for path, _, _ in outputs:
            if path.exists() and path.name in protected:
                with path.open(encoding='utf-8-sig', newline='') as f:
                    existing = list(csv.DictReader(f))
                review_columns = {'review_status', 'same_person_confirmed', 'verified_kac',
                                  'verified_isni', 'evidence_url', 'verified_author_name', 'review_note'}
                if any(any(clean(row.get(col)) not in ('', 'pending') for col in review_columns)
                       for row in existing):
                    raise RuntimeError(
                        f'기존 수동 검토값이 있는 파일은 덮어쓰지 않습니다: {path}\n'
                        '기존 CSV를 백업한 뒤 --overwrite-reviewed를 사용하세요.'
                    )
    for path, fields, rows in outputs:
        write_csv(path, fields, rows)

    print(json.dumps({
        'input_rows': len(records),
        'exact_duplicate_rows': len(duplicates),
        'excluded_pub_years': sorted(excluded_years),
        'excluded_rows': len(excluded),
        'manual_review_rows': len(detail),
        'author_name_groups_not_verified_identities': len(groups),
        'fallback_status_counts': dict(Counter(r.get('fallback_status') for r in kept)),
        'outputs': [str(p) for p, _, _ in outputs],
        'database_changed': False,
    }, ensure_ascii=False, indent=2))
    print('주의: group_id는 저자명 기반 임시 그룹입니다. 동명이인일 경우 그룹을 분리해야 합니다.')


if __name__ == '__main__':
    main()
