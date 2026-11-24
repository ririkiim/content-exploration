"""Load verified movie/TV genre relations. Default: read-only preview."""
import argparse
from collections import Counter
import os
from pathlib import Path
import unicodedata

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

POLICY_VERSION = 'genre-v1'
GENRE_MAP = {
    ('kmdb', '사극'): '시대극',
    ('kmdb', '시대극'): '시대극',
    ('kmdb', '반공'): '분단',
    ('kmdb', '분단'): '분단',
    ('kmdb', '멜로'): '멜로',
    ('kmdb', '로맨스'): '멜로',
    ('kmdb', '멜로드라마'): '로맨스',
    ('tmdb', '로맨스'): '로맨스',
    ('kmdb', '코메디'): '코미디',
    ('kmdb', '공포(호러)'): '공포',
    ('kmdb', '어드벤처'): '모험',
    ('kmdb', '뮤직'): '음악',
    ('kmdb', '해양액션'): '액션',
}
TV_GENRE_MAP = {
    'Sci-Fi & Fantasy': 'SF·판타지',
    'Action & Adventure': '액션·모험',
    'War & Politics': '전쟁·정치',
    'Soap': '연속극',
    'Kids': '아동',
    'Reality': '리얼리티',
}
EXCLUDED_GENRES = {
    ('kmdb', 'TV드라마'), ('tmdb', 'TV 영화'),
    ('kmdb', '합작(번안물)'), ('kmdb', '기록'), ('kmdb', '연쇄극'),
}


def database_url():
    for parent in Path(__file__).resolve().parents:
        env = parent / '.env'
        if env.is_file():
            load_dotenv(env, override=False)
            print(f'[INFO] .env: {env}')
            break
    url = os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError('DATABASE_URL is missing from environment or .env')
    return url


def collect(conn):
    rows = conn.execute('''
        SELECT content_id, 'movie_detail' AS source_table, genres
        FROM public.movie_detail
        UNION ALL
        SELECT content_id, 'tv_detail' AS source_table, genres
        FROM public.tv_detail
    ''')
    records = {}
    stats = Counter()
    excluded = Counter()
    invalid = []
    for row in rows:
        cid, table, genres = row['content_id'], row['source_table'], row['genres']
        stats['contents_scanned'] += 1
        if not isinstance(genres, list):
            invalid.append((cid, table, 'genres is not an array'))
            continue
        for item in genres:
            stats['raw_items'] += 1
            if not isinstance(item, dict):
                invalid.append((cid, table, 'genre is not an object'))
                continue
            original = item.get('name')
            source = 'tmdb' if table == 'tv_detail' else item.get('source')
            if source not in ('kmdb', 'tmdb') or not isinstance(original, str) or not original.strip():
                invalid.append((cid, table, 'invalid source or name'))
                continue
            name = unicodedata.normalize('NFC', original).strip()
            if (source, name) in EXCLUDED_GENRES:
                excluded[(source, name)] += 1
                stats['excluded_items'] += 1
                continue
            standard = GENRE_MAP.get((source, name), name)
            if table == 'tv_detail':
                standard = TV_GENRE_MAP.get(name, standard)
            ref = f'{table}:{cid}:genres:{source}:{original}'
            key = (cid, standard, source, ref)
            location = {
                'table': table, 'column': 'genres', 'content_id': cid,
                'original_genre': original, 'normalized_genre': standard,
                'policy_version': POLICY_VERSION,
            }
            if source == 'tmdb' and item.get('id') is not None:
                location['tmdb_genre_id'] = item['id']
            if key in records:
                stats['duplicate_items'] += 1
                previous = records[key]
                old_id = previous.get('tmdb_genre_id')
                new_id = location.get('tmdb_genre_id')
                if old_id is not None and new_id is not None and old_id != new_id:
                    invalid.append((cid, table, 'conflicting TMDb genre IDs'))
                elif old_id is None and new_id is not None:
                    records[key] = location
                continue
            records[key] = location
    print('[INFO] Input statistics:', dict(stats))
    print('[INFO] Excluded:', dict(excluded))
    print(f'[INFO] Invalid items: {len(invalid)}')
    for item in invalid[:20]:
        print('[WARN]', item)
    if invalid:
        raise RuntimeError('Invalid input found; resolve before loading.')
    return records


def preview(conn, records):
    names = {key[1] for key in records}
    pairs = {(key[0], key[1]) for key in records}
    existing = conn.execute('''
        SELECT entity_name FROM public.entity WHERE entity_type = 'GENRE'
    ''').fetchall()
    existing_names = {row['entity_name'] for row in existing}
    print(f'[INFO] Genre entities in input: {len(names)}')
    print(f'[INFO] New genre entities: {len(names - existing_names)}')
    print(f'[INFO] Distinct content-genre relations in input: {len(pairs)}')
    print(f'[INFO] Distinct evidence records in input: {len(records)}')
    counts = Counter(name for _, name in pairs)
    for name, count in sorted(counts.items()):
        print(f'  {name}: {count:,}')


def apply(conn, records):
    conn.execute('''
        CREATE TEMP TABLE genre_stage (
            content_id bigint NOT NULL,
            genre_name text NOT NULL,
            source_name text NOT NULL,
            source_ref text NOT NULL,
            evidence_text text NOT NULL,
            evidence_location jsonb NOT NULL
        ) ON COMMIT DROP
    ''')
    with conn.cursor() as cur:
        with cur.copy('COPY genre_stage FROM STDIN') as copy:
            for (cid, name, source, ref), location in records.items():
                original = location['original_genre']
                copy.write_row((cid, name, source, ref,
                                f'{source} genre: {original} -> {name}', Jsonb(location)))
    conn.execute('ANALYZE genre_stage')
    entities = conn.execute('''
        INSERT INTO public.entity (entity_name, entity_type)
        SELECT DISTINCT genre_name, 'GENRE' FROM genre_stage
        ON CONFLICT (entity_name) WHERE entity_type = 'GENRE' DO NOTHING
    ''').rowcount
    relations = conn.execute('''
        INSERT INTO public.content_entity (
            content_id, entity_id, link_role, fact_status, evidence_status,
            relation_basis, next_hop_eligible, policy_version
        )
        SELECT DISTINCT s.content_id, e.entity_id, 'genre', 'verified',
            'checked', 'source_fact', false, %s
        FROM genre_stage s
        JOIN public.entity e ON e.entity_type = 'GENRE' AND e.entity_name = s.genre_name
        ON CONFLICT (content_id, entity_id, link_role) DO NOTHING
    ''', (POLICY_VERSION,)).rowcount
    evidence = conn.execute('''
        INSERT INTO public.content_entity_evidence (
            content_entity_id, evidence_type, source_name, source_ref,
            source_group, evidence_text, evidence_location
        )
        SELECT ce.content_entity_id, 'metadata', s.source_name, s.source_ref,
            'genre', s.evidence_text, s.evidence_location
        FROM genre_stage s
        JOIN public.entity e ON e.entity_type = 'GENRE' AND e.entity_name = s.genre_name
        JOIN public.content_entity ce ON ce.content_id = s.content_id
            AND ce.entity_id = e.entity_id AND ce.link_role = 'genre'
        WHERE NOT EXISTS (
            SELECT 1 FROM public.content_entity_evidence ev
            WHERE ev.content_entity_id = ce.content_entity_id
                AND ev.evidence_type = 'metadata'
                AND ev.source_name = s.source_name AND ev.source_ref = s.source_ref
        )
    ''').rowcount
    return entities, relations, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Commit genre inserts')
    args = parser.parse_args()
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        if args.apply:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('load_genre_entities_v1'))")
        else:
            conn.execute('SET TRANSACTION READ ONLY')
        records = collect(conn)
        preview(conn, records)
        if not args.apply:
            conn.rollback()
            print('[PREVIEW] No changes made. Use --apply to load.')
            return
        entities, relations, evidence = apply(conn, records)
        conn.commit()
        print(f'[COMMITTED] New entities={entities:,}, relations={relations:,}, evidence={evidence:,}')


if __name__ == '__main__':
    main()
