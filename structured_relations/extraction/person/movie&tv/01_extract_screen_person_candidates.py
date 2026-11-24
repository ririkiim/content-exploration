"""Extract movie/TV PERSON credit candidates from KMDb and TMDb.

Read-only. Python 3.10+, psycopg 3, python-dotenv.
The catalog is connected through content_source_map, never by title.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import unicodedata

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRUCTURED_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / '.env')

ROLE_MAP = {
    'director': 'director',
    'screenplay': 'screenwriter',
    'writer': 'screenwriter',
    'screenwriter': 'screenwriter',
    'original story': 'original_author',
    'original_story': 'original_author',
    'original author': 'original_author',
    'original_author': 'original_author',
}


def norm(value):
    if value is None:
        return None
    text = unicodedata.normalize('NFC', re.sub(r'\s+', ' ', str(value)).strip())
    return text or None


def identifier(value):
    value = norm(value)
    return None if value is None or value.casefold() in ('null', 'none') else value


def json_obj(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            pass
    return {}


def json_array(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            pass
    return []


def as_int(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute('''SELECT column_name FROM information_schema.columns
                       WHERE table_schema = 'public' AND table_name = %s''', (table,))
        return {r['column_name'] for r in cur}


def required(conn, table, columns):
    actual = table_columns(conn, table)
    missing = set(columns) - actual
    if missing:
        raise RuntimeError(f'public.{table} missing columns: {sorted(missing)}')
    return actual


def make_row(content_id, content_type, source, source_content_id, role,
             name, original_name, person_id, billing_order, source_path,
             raw_credit, **extra):
    name = norm(name) or norm(original_name)
    if not name:
        return None
    row = {
        'content_id': content_id,
        'content_type': content_type,
        'source': source,
        'source_content_id': source_content_id,
        'role': role,
        'name': name,
        'original_name': norm(original_name),
        'tmdb_person_id': identifier(person_id) if source == 'tmdb' else None,
        'kmdb_person_id': identifier(person_id) if source == 'kmdb' else None,
        'billing_order': billing_order,
        'source_path': source_path,
        'original': raw_credit,
    }
    row.update(extra)
    return row


def load_source_maps(conn, types, selected_content_id):
    required(conn, 'content_source_map', ['content_id', 'source_name', 'source_id'])
    allowed = {'kmdb_movie', 'tmdb_movie', 'tmdb_tv'}
    if types == ['movie']:
        allowed = {'kmdb_movie', 'tmdb_movie'}
    elif types == ['tv']:
        allowed = {'tmdb_tv'}
    query = '''SELECT DISTINCT content_id, source_name, source_id
               FROM public.content_source_map
               WHERE source_name = ANY(%s) AND source_id IS NOT NULL'''
    params = [list(allowed)]
    if selected_content_id is not None:
        query += ' AND content_id = %s'
        params.append(selected_content_id)
    query += ' ORDER BY content_id, source_name, source_id'
    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()
    maps = defaultdict(set)
    for row in rows:
        maps[(row['source_name'], str(row['source_id']).strip())].add(row['content_id'])
    return maps


def extract_kmdb(conn, source_maps, cast_limit, stats):
    required(conn, 'kmdb_credit', ['credit_id', 'kmdb_id', 'person_id',
             'person_name', 'person_name_en', 'link_role', 'billing_order',
             'source_field', 'source_index', 'source_payload'])
    targets = {key[1]: cids for key, cids in source_maps.items() if key[0] == 'kmdb_movie'}
    if not targets:
        return
    # Filter directly by mapped KMDb IDs, then expand to content IDs in Python.
    sql = '''SELECT k.kmdb_id, k.credit_id, k.person_id,
                    k.person_name, k.person_name_en, k.link_role,
                    k.billing_order, k.source_field, k.source_index, k.source_payload,
                    k.role_detail, k.original_title, k.original_type,
                    k.original_parse_status, k.raw_staff_etc
             FROM public.kmdb_credit AS k
             WHERE k.kmdb_id = ANY(%s)
             ORDER BY k.kmdb_id, k.credit_id'''
    params = (sorted(targets),)
    cast_buckets = defaultdict(list)
    noncast = []
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for rec in cur:
            role = norm(rec['link_role'])
            role = ROLE_MAP.get(role.casefold()) if role else None
            if role is None and norm(rec['link_role']) == 'cast':
                role = 'cast'
            if role is None:
                stats['kmdb_unsupported_roles'] += 1
                continue
            for cid in sorted(targets.get(rec['kmdb_id'], ())):
                row = make_row(
                    cid, 'movie', 'kmdb', rec['kmdb_id'], role,
                    rec['person_name'], rec['person_name_en'], rec['person_id'],
                    rec['billing_order'], f"kmdb_credit:{rec['credit_id']}",
                    rec['source_payload'], credit_id=rec['credit_id'],
                    source_field=rec['source_field'], source_index=rec['source_index'],
                    role_detail=rec['role_detail'], original_title=rec['original_title'],
                    original_type=rec['original_type'],
                    original_parse_status=rec['original_parse_status'],
                    raw_staff_etc=rec['raw_staff_etc'],
                )
                if row is None:
                    stats['kmdb_missing_names'] += 1
                    continue
                if role == 'cast':
                    cast_buckets[(cid, rec['kmdb_id'])].append(row)
                else:
                    noncast.append(row)
    for row in noncast:
        yield row
    for bucket in sorted(cast_buckets):
        records = cast_buckets[bucket]
        records.sort(key=lambda r: (r['billing_order'] is None,
                                   r['billing_order'] if r['billing_order'] is not None else 10**9,
                                   r['source_index'] if r['source_index'] is not None else 10**9,
                                   r['credit_id']))
        stats['kmdb_cast_before_limit'] += len(records)
        for row in records[:cast_limit]:
            yield row
        stats['kmdb_cast_excluded_by_limit'] += max(0, len(records)-cast_limit)


def extract_tmdb_payload(payload, content_id, kind, source_id, cast_limit, stats):
    credits = json_obj(payload.get('credits'))
    cast = [r for r in json_array(credits.get('cast')) if isinstance(r, dict)]
    cast.sort(key=lambda r: (as_int(r.get('order')) is None,
                             as_int(r.get('order')) if as_int(r.get('order')) is not None else 10**9,
                             as_int(r.get('id')) if as_int(r.get('id')) is not None else 10**9))
    stats[f'tmdb_{kind}_cast_before_limit'] += len(cast)
    stats[f'tmdb_{kind}_cast_excluded_by_limit'] += max(0, len(cast)-cast_limit)
    for index, obj in enumerate(cast[:cast_limit]):
        row = make_row(content_id, kind, 'tmdb', source_id, 'cast',
                       obj.get('name'), obj.get('original_name'), obj.get('id'),
                       as_int(obj.get('order')), f'credits.cast[{index}]', obj,
                       credit_id=obj.get('credit_id'), character=obj.get('character'))
        if row:
            yield row
        else:
            stats['tmdb_missing_names'] += 1
    for index, obj in enumerate(json_array(credits.get('crew'))):
        if not isinstance(obj, dict):
            continue
        job = norm(obj.get('job'))
        role = ROLE_MAP.get(job.casefold()) if job else None
        if role is None:
            continue
        row = make_row(content_id, kind, 'tmdb', source_id, role,
                       obj.get('name'), obj.get('original_name'), obj.get('id'),
                       None, f'credits.crew[{index}]', obj,
                       credit_id=obj.get('credit_id'), job=job,
                       department=obj.get('department'))
        if row:
            yield row
        else:
            stats['tmdb_missing_names'] += 1


def extract_tmdb(conn, source_maps, kind, cast_limit, stats):
    table = 'raw_tmdb_movie' if kind == 'movie' else 'raw_tmdb_tv'
    columns = table_columns(conn, table)
    if not columns:
        stats[f'{table}_missing'] += 1
        return
    payload_col = 'payload' if 'payload' in columns else 'raw_payload' if 'raw_payload' in columns else None
    id_col = 'tmdb_id' if 'tmdb_id' in columns else None
    if not payload_col or not id_col:
        raise RuntimeError(f'{table}: need tmdb_id and payload/raw_payload; actual={sorted(columns)}')
    source_name = 'tmdb_movie' if kind == 'movie' else 'tmdb_tv'
    target_ids = {sid for (src, sid) in source_maps if src == source_name}
    if not target_ids:
        return
    # A single source may have multiple raw snapshots: prefer latest by fetched_at/raw_id.
    pk_col = 'raw_tmdb_movie_id' if kind == 'movie' else 'raw_tmdb_tv_id'
    order_cols = [c for c in ('fetched_at', pk_col, 'raw_id') if c in columns]
    order_sql = ', '.join(f'r.{c} DESC NULLS LAST' for c in order_cols) or f'r.{id_col} DESC'
    sql = f'''SELECT DISTINCT ON (r.{id_col}) r.{id_col} AS tmdb_id,
                     r.{payload_col} AS payload
              FROM public.{table} AS r
              WHERE r.{id_col} = ANY(%s)
              ORDER BY r.{id_col}, {order_sql}'''
    numeric_ids = sorted({int(sid) for sid in target_ids if sid.isdigit()})
    if not numeric_ids:
        stats[f'tmdb_{kind}_invalid_source_ids'] += len(target_ids)
        return
    with conn.cursor() as cur:
        cur.execute(sql, (numeric_ids,))
        for rec in cur:
            source_id = str(rec['tmdb_id'])
            cids = source_maps.get((source_name, source_id), set())
            if not cids:
                continue
            payload = json_obj(rec['payload'])
            if not payload:
                stats[f'tmdb_{kind}_invalid_payload'] += 1
                continue
            for cid in sorted(cids):
                yield from extract_tmdb_payload(payload, cid, kind, source_id, cast_limit, stats)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--types', nargs='+', choices=['movie', 'tv'], default=['movie', 'tv'])
    parser.add_argument('--content-id', type=int)
    parser.add_argument('--cast-limit', type=int, default=20)
    parser.add_argument('--output', default=str(STRUCTURED_ROOT / 'data/01_screen_person_candidates.jsonl'))
    args = parser.parse_args()
    if args.cast_limit < 1:
        parser.error('--cast-limit must be >= 1')
    url = os.getenv('DATABASE_URL')
    if not url:
        parser.error('DATABASE_URL is missing from .env')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    stats = Counter()
    print('[INFO] PostgreSQL 연결 시도 (최대 10초)...', flush=True)
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=10) as conn:
        print('[INFO] PostgreSQL 연결 성공', flush=True)
        conn.execute('SET TRANSACTION READ ONLY')
        maps = load_source_maps(conn, args.types, args.content_id)
        stats['mapped_source_keys'] = len(maps)
        stats['mapped_contents'] = len({cid for ids in maps.values() for cid in ids})
        with output.open('w', encoding='utf-8') as file:
            if 'movie' in args.types:
                for row in extract_kmdb(conn, maps, args.cast_limit, stats):
                    file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
                    stats[f"{row['source']}_{row['role']}"] += 1
                for row in extract_tmdb(conn, maps, 'movie', args.cast_limit, stats):
                    file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
                    stats[f"tmdb_movie_{row['role']}"] += 1
            if 'tv' in args.types:
                for row in extract_tmdb(conn, maps, 'tv', args.cast_limit, stats):
                    file.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
                    stats[f"tmdb_tv_{row['role']}"] += 1
        conn.rollback()
    print(json.dumps({'output': str(output), 'cast_limit_per_source': args.cast_limit,
                      'database_changed': False, 'counts': dict(sorted(stats.items()))},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
