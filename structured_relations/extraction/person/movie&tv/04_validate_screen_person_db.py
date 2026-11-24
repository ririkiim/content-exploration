"""Read-only preflight for importing 03 screen-person identity and relation JSONL.

Usage: python 04_validate_screen_person_db.py
Requires psycopg (v3) and DATABASE_URL in environment or project .env.
No INSERT/UPDATE/DELETE. Produces a JSON summary and a limited CSV review.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
from pathlib import Path
from collections import Counter, defaultdict

try:
    import psycopg
except ImportError as exc:
    raise SystemExit('Install psycopg: pip install "psycopg[binary]"') from exc

ROOT = Path(__file__).resolve().parents[3]
STRUCTURED_ROOT = Path(__file__).resolve().parents[2]

DATA = STRUCTURED_ROOT / "data"

def read_jsonl(path):
    with path.open('r', encoding='utf-8-sig') as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f'{path}:{n}: {exc}') from exc


def load_env():
    p = ROOT / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            if k == 'DATABASE_URL' and k not in os.environ:
                os.environ[k] = v.strip().strip('"').strip("'")


def table_columns(conn, table):
    with conn.cursor() as cur:
        cur.execute('''SELECT column_name, data_type, is_nullable, column_default
                       FROM information_schema.columns
                       WHERE table_schema = current_schema() AND table_name = %s
                       ORDER BY ordinal_position''', (table,))
        return [dict(zip(('name','type','nullable','default'), r)) for r in cur.fetchall()]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--groups', type=Path, default=DATA/'03_person_groups.jsonl')
    ap.add_argument('--relations', type=Path, default=DATA/'03_content_person_relations.jsonl')
    ap.add_argument('--outdir', type=Path, default=DATA/'04_preflight')
    ap.add_argument('--review-limit', type=int, default=500)
    args = ap.parse_args()
    load_env()
    url = os.getenv('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL missing in environment / project .env')
    groups = list(read_jsonl(args.groups))
    rels = list(read_jsonl(args.relations))
    by_gid = {}
    problems = []
    for g in groups:
        gid = g.get('person_group_key')
        if not gid or gid in by_gid:
            problems.append(('invalid_or_duplicate_group', str(gid), ''))
        else:
            by_gid[gid] = g
    roles = {'cast','director','screenwriter','original_author'}
    rel_keys = set()
    source_counts = Counter()
    for r in rels:
        gid = r.get('person_group_key')
        if gid not in by_gid:
            problems.append(('missing_person_group', str(gid), str(r.get('content_id'))))
        if r.get('link_role') not in roles:
            problems.append(('unexpected_role', str(gid), str(r.get('link_role'))))
        k = (r.get('content_id'), r.get('link_role'), gid)
        if k in rel_keys:
            problems.append(('duplicate_relation', str(gid), str(r.get('content_id'))))
        rel_keys.add(k)
        ev = r.get('evidence')
        if not isinstance(ev, list) or not ev:
            problems.append(('missing_evidence', str(gid), str(r.get('content_id'))))
        for e in ev or []:
            source_counts[e.get('source')] += 1
    ids = sorted({r.get('content_id') for r in rels if type(r.get('content_id')) is int})
    group_names = {g.get('display_name') for g in groups if isinstance(g.get('display_name'), str) and g.get('display_name')}
    db_name_rows = defaultdict(list)
    existing_content = set()
    schema = {}
    with psycopg.connect(url) as conn:
        conn.execute('BEGIN READ ONLY')
        for table in ('entity','entity_alias','content','content_entity','content_entity_evidence'):
            schema[table] = table_columns(conn, table)
        entity_cols = {x['name'] for x in schema['entity']}
        content_cols = {x['name'] for x in schema['content']}
        if not {'entity_id','entity_name','entity_type'} <= entity_cols:
            problems.append(('entity_schema_missing_columns', '', 'entity_id/entity_name/entity_type'))
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT entity_id, entity_name FROM entity WHERE entity_type = 'PERSON'")
                for eid, name in cur:
                    db_name_rows[name].append(eid)
        if 'content_id' not in content_cols:
            problems.append(('content_schema_missing_content_id', '', ''))
        else:
            with conn.cursor() as cur:
                # Query only relevant content IDs in bounded batches.
                for start in range(0, len(ids), 2000):
                    cur.execute('SELECT content_id FROM content WHERE content_id = ANY(%s)', (ids[start:start+2000],))
                    existing_content.update(row[0] for row in cur)
        conn.rollback()
    reviews = []
    status = Counter()
    for g in groups:
        gid = g.get('person_group_key', '')
        name = g.get('display_name')
        found = db_name_rows.get(name, [])
        # Name-only identity is NOT sufficient: any existing name match needs manual resolution.
        if len(found) == 0:
            tag = 'no_exact_name_in_db'
        elif len(found) == 1:
            tag = 'existing_same_name_requires_id_verification'
        else:
            tag = 'multiple_existing_same_name'
        status[tag] += 1
        if tag != 'no_exact_name_in_db' and len(reviews) < args.review_limit:
            reviews.append({'reason':tag,'person_group_key':gid,'display_name':name,
                            'existing_entity_ids':';'.join(map(str,found)),
                            'kmdb_person_id':g.get('kmdb_person_id'), 'tmdb_person_id':g.get('tmdb_person_id')})
    missing = sorted(set(ids) - existing_content)
    for cid in missing[:args.review_limit]:
        problems.append(('missing_content_id', str(cid), ''))
    report = {
        'database_changed':False, 'ready_to_insert':False,
        'group_count':len(groups), 'relation_count':len(rels),
        'relation_evidence_rows':sum(source_counts.values()),
        'evidence_sources':dict(source_counts),
        'content_ids_referenced':len(ids), 'content_ids_missing':len(missing),
        'missing_content_id_sample':missing[:30],
        'existing_person_name_status':dict(status),
        'validation_problem_count':len(problems), 'validation_problem_sample':problems[:30],
        'schema':schema,
        'next_step':'Review exact-name collisions and schema, then implement explicit ID mapping and INSERT separately.'
    }
    args.outdir.mkdir(parents=True, exist_ok=True)
    (args.outdir/'04_preflight_summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.outdir/'04_existing_person_name_review.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['reason','person_group_key','display_name','existing_entity_ids','kmdb_person_id','tmdb_person_id'])
        w.writeheader(); w.writerows(reviews)
    print(json.dumps({k:v for k,v in report.items() if k not in ('schema','validation_problem_sample')}, ensure_ascii=False, indent=2))
    print(f'[INFO] Schema details: {args.outdir / "04_preflight_summary.json"}')
    print(f'[INFO] Name collision samples: {args.outdir / "04_existing_person_name_review.csv"}')

if __name__ == '__main__':
    main()
