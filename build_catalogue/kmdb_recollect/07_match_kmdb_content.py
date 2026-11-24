"""Validate KMDb credit coverage against the existing service catalog (read-only).

Run:
    python build_catalogue/kmdb_recollect/07_match_kmdb_content.py
    python build_catalogue/kmdb_recollect/07_match_kmdb_content.py --output-dir data/processed/kmdb_match

Requirements: psycopg[binary], python-dotenv.
Does NOT create/update database tables or entities.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSONL = ROOT / 'data/raw/new_kmdb/processed/kmdb_unique_movies.jsonl'
DEFAULT_OUT = ROOT / 'data/processed/kmdb_match'
ROLES = ('director', 'cast', 'screenwriter', 'original_author')


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def read_source_ids(path: Path) -> set[str]:
    result = set()
    with path.open('r', encoding='utf-8-sig') as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            movie = json.loads(line)
            mid = str(movie.get('movieId') or '').strip()
            seq = str(movie.get('movieSeq') or '').strip()
            if not mid or not seq:
                raise ValueError(f'JSONL line {n}: missing movieId/movieSeq')
            key = f'{mid}:{seq}'
            if key in result:
                raise ValueError(f'JSONL line {n}: duplicate {key}')
            result.add(key)
    return result


def fetch_all(conn, query: str, params=()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, default=DEFAULT_JSONL)
    ap.add_argument('--output-dir', type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    if not args.input.is_file():
        ap.error(f'JSONL not found: {args.input}')
    load_dotenv(ROOT / '.env')
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        ap.error('DATABASE_URL missing from project .env')

    print('[INFO] Reading unique KMDb movie IDs...', flush=True)
    source_ids = read_source_ids(args.input)
    print(f'[INFO] New KMDb movies: {len(source_ids):,}', flush=True)

    # Read-only session; the DB is never modified by this script.
    with psycopg.connect(db_url, row_factory=dict_row,
                         options='-c default_transaction_read_only=on') as conn:
        mapping = fetch_all(conn, """
            SELECT content_id, source_id
            FROM public.content_source_map
            WHERE source_name = 'kmdb_movie'
            ORDER BY content_id, source_id
        """)
        mapping_ids = {r['source_id'] for r in mapping}
        mapped_content_ids = {r['content_id'] for r in mapping}
        duplicate_source_mappings = [
            {'source_id': k, 'mapping_rows': v}
            for k, v in Counter(r['source_id'] for r in mapping).items() if v > 1
        ]
        duplicate_content_mappings = [
            {'content_id': k, 'mapping_rows': v}
            for k, v in Counter(r['content_id'] for r in mapping).items() if v > 1
        ]

        unmatched = [r for r in mapping if r['source_id'] not in source_ids]
        matched_mapping = [r for r in mapping if r['source_id'] in source_ids]

        # Query returns one row per catalog mapping, including movies with zero credits.
        movie_coverage = fetch_all(conn, """
            SELECT m.content_id, m.source_id AS kmdb_id,
                   COUNT(k.credit_id) AS credit_count,
                   COUNT(k.credit_id) FILTER (WHERE k.link_role = 'director') AS director_count,
                   COUNT(k.credit_id) FILTER (WHERE k.link_role = 'cast') AS cast_count,
                   COUNT(k.credit_id) FILTER (WHERE k.link_role = 'screenwriter') AS screenwriter_count,
                   COUNT(k.credit_id) FILTER (WHERE k.link_role = 'original_author') AS original_author_count
            FROM public.content_source_map m
            LEFT JOIN public.kmdb_credit k ON k.kmdb_id = m.source_id
            WHERE m.source_name = 'kmdb_movie'
            GROUP BY m.content_id, m.source_id
            ORDER BY m.content_id
        """)

        role_stats = fetch_all(conn, """
            SELECT k.link_role,
                   COUNT(*) AS credit_count,
                   COUNT(DISTINCT m.content_id) AS content_count,
                   COUNT(*) FILTER (WHERE k.person_id IS NOT NULL) AS with_person_id,
                   COUNT(*) FILTER (WHERE k.source_field = 'staffs') AS from_staffs
            FROM public.content_source_map m
            JOIN public.kmdb_credit k ON k.kmdb_id = m.source_id
            WHERE m.source_name = 'kmdb_movie'
            GROUP BY k.link_role
            ORDER BY k.link_role
        """)
        original_stats = fetch_all(conn, """
            SELECT k.original_parse_status AS parse_status,
                   COUNT(*) AS credit_count,
                   COUNT(DISTINCT m.content_id) AS content_count
            FROM public.content_source_map m
            JOIN public.kmdb_credit k ON k.kmdb_id = m.source_id
            WHERE m.source_name = 'kmdb_movie' AND k.link_role = 'original_author'
            GROUP BY k.original_parse_status
            ORDER BY k.original_parse_status
        """)
        cast_order_issues = fetch_all(conn, """
            WITH x AS (
                SELECT m.content_id, m.source_id AS kmdb_id,
                       k.person_name, k.billing_order,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.content_id, m.source_id
                           ORDER BY k.billing_order, k.credit_id
                       ) - 1 AS expected_order
                FROM public.content_source_map m
                JOIN public.kmdb_credit k ON k.kmdb_id = m.source_id
                WHERE m.source_name = 'kmdb_movie' AND k.link_role = 'cast'
            )
            SELECT content_id, kmdb_id, person_name, billing_order, expected_order
            FROM x WHERE billing_order IS DISTINCT FROM expected_order
            ORDER BY content_id, expected_order
        """)
        # Diagnostic only: same person ID can legitimately have multiple original-work credits.
        duplicate_person_roles = fetch_all(conn, """
            SELECT m.content_id, m.source_id AS kmdb_id, k.link_role,
                   k.person_id, COUNT(*) AS occurrences
            FROM public.content_source_map m
            JOIN public.kmdb_credit k ON k.kmdb_id = m.source_id
            WHERE m.source_name = 'kmdb_movie' AND k.person_id IS NOT NULL
              AND k.link_role <> 'original_author'
            GROUP BY m.content_id, m.source_id, k.link_role, k.person_id
            HAVING COUNT(*) > 1
            ORDER BY occurrences DESC, m.content_id
        """)
        # Check that credits were loaded for all movies that had extractable credits.
        db_credit_movie_ids = fetch_all(conn, """
            SELECT DISTINCT kmdb_id FROM public.kmdb_credit
        """)

    db_credit_ids = {r['kmdb_id'] for r in db_credit_movie_ids}
    missing_credit = [r for r in movie_coverage
                      if r['kmdb_id'] in source_ids and r['credit_count'] == 0]
    with_credit = [r for r in movie_coverage if r['credit_count'] > 0]
    credit_ids_not_in_jsonl = sorted(db_credit_ids - source_ids)
    matched_source_ids = mapping_ids & source_ids

    summary = {
        'new_kmdb_movie_count': len(source_ids),
        'catalog_mapping_rows': len(mapping),
        'catalog_distinct_content_ids': len(mapped_content_ids),
        'catalog_distinct_kmdb_ids': len(mapping_ids),
        'catalog_matched_mapping_rows': len(matched_mapping),
        'catalog_matched_distinct_kmdb_ids': len(matched_source_ids),
        'catalog_unmatched_mapping_rows': len(unmatched),
        'catalog_matched_with_credits_rows': len([r for r in with_credit if r['kmdb_id'] in source_ids]),
        'catalog_matched_without_credits_rows': len(missing_credit),
        'catalog_credit_rows_total': sum(r['credit_count'] for r in movie_coverage),
        'db_credit_movie_ids_not_in_jsonl': len(credit_ids_not_in_jsonl),
        'duplicate_source_mapping_ids': len(duplicate_source_mappings),
        'duplicate_content_mapping_ids': len(duplicate_content_mappings),
        'cast_order_issue_rows': len(cast_order_issues),
        'duplicate_person_role_rows': len(duplicate_person_roles),
        'role_stats': role_stats,
        'original_parse_stats': original_stats,
    }

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / '07_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    write_csv(out / '07_unmatched_catalog.csv', unmatched, ['content_id', 'source_id'])
    write_csv(out / '07_matched_without_credits.csv', missing_credit,
              ['content_id', 'kmdb_id', 'credit_count', 'director_count', 'cast_count', 'screenwriter_count', 'original_author_count'])
    write_csv(out / '07_matched_movie_coverage.csv',
              [r for r in movie_coverage if r['kmdb_id'] in source_ids],
              ['content_id', 'kmdb_id', 'credit_count', 'director_count', 'cast_count', 'screenwriter_count', 'original_author_count'])
    write_csv(out / '07_cast_order_issues.csv', cast_order_issues,
              ['content_id', 'kmdb_id', 'person_name', 'billing_order', 'expected_order'])
    write_csv(out / '07_duplicate_person_roles.csv', duplicate_person_roles,
              ['content_id', 'kmdb_id', 'link_role', 'person_id', 'occurrences'])
    write_csv(out / '07_original_parse_stats.csv', original_stats,
              ['parse_status', 'credit_count', 'content_count'])
    write_csv(out / '07_role_stats.csv', role_stats,
              ['link_role', 'credit_count', 'content_count', 'with_person_id', 'from_staffs'])

    print('\n[RESULT] READ ONLY')
    for key, value in summary.items():
        if not isinstance(value, list):
            print(f'  {key}: {value:,}' if isinstance(value, int) else f'  {key}: {value}')
    print('\n[ROLE STATS]')
    for r in role_stats:
        print(f"  {r['link_role']}: credits={r['credit_count']:,}, contents={r['content_count']:,}, "
              f"person_ids={r['with_person_id']:,}, staffs={r['from_staffs']:,}")
    print('\n[ORIGINAL PARSE STATS]')
    for r in original_stats:
        print(f"  {r['parse_status']}: credits={r['credit_count']:,}, contents={r['content_count']:,}")
    print(f'\n[FILES] {out}')
    if duplicate_source_mappings or duplicate_content_mappings:
        print('[WARN] Duplicate catalog mappings exist; investigate before entity loading.')
    if credit_ids_not_in_jsonl:
        print('[WARN] DB contains KMDb credits absent from the input JSONL.')
    if cast_order_issues:
        print('[WARN] Cast order inconsistencies detected.')


if __name__ == '__main__':
    main()
