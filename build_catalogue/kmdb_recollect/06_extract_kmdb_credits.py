"""Extract four KMDb credit roles from unique movie JSONL into public.kmdb_credit.

Run from anywhere:
  python build_catalogue/kmdb_recollect/06_extract_kmdb_credits.py
  python build_catalogue/kmdb_recollect/06_extract_kmdb_credits.py --dry-run

Requires: pip install psycopg[binary] python-dotenv
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / 'data/raw/new_kmdb/processed/kmdb_unique_movies.jsonl'
TABLE = 'public.kmdb_credit'
COLUMNS = (
    'kmdb_id', 'movie_title', 'person_id', 'person_name', 'person_name_en',
    'link_role', 'billing_order', 'role_detail', 'original_title',
    'original_type', 'original_parse_status', 'raw_staff_etc',
    'source_field', 'source_index', 'source_payload',
)


def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip() or None


def records(obj, container, key):
    section = obj.get(container) or {}
    if not isinstance(section, dict):
        return []
    arr = section.get(key) or []
    if isinstance(arr, dict):
        arr = [arr]
    return arr if isinstance(arr, list) else []


def identity(person_id, person_name):
    # Same name with different nonempty IDs must never be merged.
    return ('id', person_id) if person_id else ('name', person_name)


def is_duplicate(existing, candidate):
    # If both IDs are present, ID alone decides identity. If one is absent,
    # match by name only when no conflicting known ID exists.
    cid, cname = candidate['person_id'], candidate['person_name']
    for old in existing:
        oid, oname = old['person_id'], old['person_name']
        if cid and oid:
            if cid == oid:
                return True
        elif cname == oname:
            return True
    return False


# Conservative extraction: only explicitly typed 'novel/webtoon/film/etc TITLE'.
WORK_PATTERN = re.compile(
    r'^\s*(웹툰|웹소설|소설|장편소설|단편소설|만화|영화|드라마|연극|뮤지컬|희곡|동화|애니메이션|오리지널\s*숏스토리)\s*[「『\"“\']?(.+?)\s*[」』\"”\']?\s*$',
    re.IGNORECASE,
)
NON_TITLES = {'원안', '원작', '각색', '글', '그림', '일본', '한국', '미국', '중국', '기획'}


def parse_original(raw):
    if not raw:
        return None, None, 'missing'
    m = WORK_PATTERN.fullmatch(raw)
    if not m:
        return None, None, 'ambiguous'
    kind = clean(m.group(1))
    title = clean(m.group(2))
    if title:
        title = title.strip('「」『』"“”\' ').strip()
    if not title or title in NON_TITLES or len(title) > 150:
        return None, None, 'ambiguous'
    return title, kind, 'parsed'


def extract_movie(movie):
    movie_id, movie_seq = clean(movie.get('movieId')), clean(movie.get('movieSeq'))
    if not movie_id or not movie_seq:
        raise ValueError('movieId or movieSeq missing')
    kmdb_id = f'{movie_id}:{movie_seq}'
    title = clean(movie.get('title'))
    extracted = []
    seen_by_role = {'director': [], 'cast': [], 'screenwriter': [], 'original_author': []}
    next_cast_order = 0

    def add(source, index, item, role):
        nonlocal next_cast_order
        if not isinstance(item, dict):
            return
        if source == 'directors':
            name, en, pid = (clean(item.get(k)) for k in ('directorNm', 'directorEnNm', 'directorId'))
            detail, etc = None, None
        elif source == 'actors':
            name, en, pid = (clean(item.get(k)) for k in ('actorNm', 'actorEnNm', 'actorId'))
            detail, etc = None, None
        else:
            name, en, pid = (clean(item.get(k)) for k in ('staffNm', 'staffEnNm', 'staffId'))
            detail, etc = clean(item.get('staffRole')), clean(item.get('staffEtc'))
        if not name:
            return
        candidate = {'person_id': pid, 'person_name': name}
        # One original author can be credited for distinct source works.
        # Keep distinct original-work evidence even when the person is the same.
        if role == 'original_author':
            candidate['original_evidence'] = (etc, detail)
            duplicate = any(
                is_duplicate([old], candidate)
                and old.get('original_evidence') == candidate['original_evidence']
                for old in seen_by_role[role]
            )
        else:
            duplicate = is_duplicate(seen_by_role[role], candidate)
        if duplicate:
            return
        seen_by_role[role].append(candidate)
        order = next_cast_order if role == 'cast' else None
        if role == 'cast':
            next_cast_order += 1
        work_title = work_type = status = None
        if role == 'original_author':
            work_title, work_type, status = parse_original(etc)
        extracted.append((
            kmdb_id, title, pid, name, en, role, order, detail,
            work_title, work_type, status, etc if role == 'original_author' else None,
            source, index, json.dumps(item, ensure_ascii=False),
        ))

    # Dedicated arrays are authoritative for ordering and have priority over staff entries.
    for i, item in enumerate(records(movie, 'directors', 'director')):
        add('directors', i, item, 'director')
    for i, item in enumerate(records(movie, 'actors', 'actor')):
        add('actors', i, item, 'cast')

    staff = records(movie, 'staffs', 'staff')
    group_to_role = {'감독': 'director', '출연': 'cast', '각본': 'screenwriter', '원작': 'original_author'}
    for i, item in enumerate(staff):
        if not isinstance(item, dict):
            continue
        role = group_to_role.get(clean(item.get('staffRoleGroup')))
        if role:
            add('staffs', i, item, role)
    return kmdb_id, extracted


def iter_movies(path):
    with path.open('r', encoding='utf-8-sig') as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid JSON at line {line_no}: {exc}') from exc
            if not isinstance(item, dict):
                raise ValueError(f'JSON object required at line {line_no}')
            yield line_no, item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--dry-run', action='store_true', help='Extract and validate without writing to DB')
    parser.add_argument('--batch-size', type=int, default=1000, help='Movie count per DB batch')
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('--batch-size must be positive')
    if not args.input.is_file():
        parser.error(f'Input not found: {args.input}')

    load_dotenv(PROJECT_ROOT / '.env')
    db_url = os.getenv('DATABASE_URL')
    if not args.dry_run and not db_url:
        parser.error('DATABASE_URL missing from project .env')

    counts = Counter()
    seen_movie_ids = set()
    batch = []
    insert_sql = f'''INSERT INTO {TABLE} ({', '.join(COLUMNS)})
        VALUES ({', '.join(['%s'] * len(COLUMNS))})
        ON CONFLICT (kmdb_id, source_field, source_index)
        DO UPDATE SET
            movie_title = EXCLUDED.movie_title,
            person_id = EXCLUDED.person_id,
            person_name = EXCLUDED.person_name,
            person_name_en = EXCLUDED.person_name_en,
            link_role = EXCLUDED.link_role,
            billing_order = EXCLUDED.billing_order,
            role_detail = EXCLUDED.role_detail,
            original_title = EXCLUDED.original_title,
            original_type = EXCLUDED.original_type,
            original_parse_status = EXCLUDED.original_parse_status,
            raw_staff_etc = EXCLUDED.raw_staff_etc,
            source_payload = EXCLUDED.source_payload'''

    def flush(cur):
        if batch:
            cur.executemany(insert_sql, batch)
            batch.clear()

    def process(cur):
        for line_no, movie in iter_movies(args.input):
            try:
                kmdb_id, rows = extract_movie(movie)
            except ValueError as exc:
                raise ValueError(f'Line {line_no}: {exc}') from exc
            if kmdb_id in seen_movie_ids:
                raise ValueError(f'Duplicate kmdb_id at line {line_no}: {kmdb_id}')
            seen_movie_ids.add(kmdb_id)
            counts['movies'] += 1
            counts['movies_with_credits'] += bool(rows)
            for row in rows:
                counts[row[5]] += 1
                if row[5] == 'original_author':
                    counts[f'original_{row[10]}'] += 1
            if cur is not None:
                batch.extend(rows)
                if counts['movies'] % args.batch_size == 0:
                    flush(cur)
            if counts['movies'] % 10000 == 0:
                print(f"[INFO] movies={counts['movies']:,}, credits={sum(counts[k] for k in ('director','cast','screenwriter','original_author')):,}", flush=True)
        if cur is not None:
            flush(cur)

    if args.dry_run:
        process(None)
    else:
        # One transaction: any failure rolls back the whole import.
        # Replace the table contents so re-running reflects the current JSONL,
        # including credits removed by revised extraction rules.
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(f'TRUNCATE TABLE {TABLE} RESTART IDENTITY')
                process(cur)

    print('\n[RESULT]', 'DRY RUN' if args.dry_run else 'COMMITTED')
    for key in ('movies', 'movies_with_credits', 'director', 'cast', 'screenwriter',
                'original_author', 'original_parsed', 'original_ambiguous', 'original_missing'):
        print(f'  {key}: {counts[key]:,}')


if __name__ == '__main__':
    main()
