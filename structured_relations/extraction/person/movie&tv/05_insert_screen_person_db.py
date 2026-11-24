"""Import 03 screen PERSON groups, relations and source evidence into PostgreSQL.

Default: READ-ONLY preflight. --apply performs one transaction.
Requires psycopg v3, DATABASE_URL in project .env or environment.
Existing PERSON rows (including book authors) are never name-matched/updated.
Creates screen_person_identity_map to make repeat imports idempotent.

Usage:
  python "structured_relations/extraction/movie&tv/05_insert_screen_person_db.py"
  python "structured_relations/extraction/movie&tv/05_insert_screen_person_db.py" --apply
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

try:
    import psycopg
except ImportError as exc:
    raise SystemExit('Install psycopg: pip install "psycopg[binary]"') from exc

STRUCTURED_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = STRUCTURED_ROOT.parent
DATA = STRUCTURED_ROOT / 'data'
ROLES = {'cast', 'director', 'screenwriter', 'original_author'}


def load_env():
    path = PROJECT_ROOT / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if line.startswith('DATABASE_URL=') and 'DATABASE_URL' not in os.environ:
                os.environ['DATABASE_URL'] = line.split('=', 1)[1].strip().strip('"\'')


def read_jsonl(path):
    with path.open(encoding='utf-8-sig') as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f'{path}:{line_no}: {exc}') from exc


def normalize_alias(value):
    return ' '.join(value.strip().casefold().split())


def alias_language(value):
    if re.search('[가-힣]', value):
        return 'ko'
    if re.search('[A-Za-z]', value):
        return 'en'
    return 'und'


def make_evidence_ref(cid, role, gid, e):
    # Deterministic fingerprint: repeated imports do not duplicate evidence.
    material = json.dumps([cid, role, gid, e], ensure_ascii=False, sort_keys=True,
                          separators=(',', ':'), default=str)
    return 'screen_person_03:' + hashlib.sha256(material.encode('utf-8')).hexdigest()


def validate(groups, relations):
    by_gid = {}
    source_ids = set()
    for g in groups:
        gid = g.get('person_group_key')
        name = g.get('display_name')
        if not isinstance(gid, str) or not gid or gid in by_gid:
            raise ValueError(f'Invalid/duplicate person_group_key: {gid!r}')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'Invalid display name: {gid}')
        kmdb, tmdb = g.get('kmdb_person_id'), g.get('tmdb_person_id')
        if not kmdb and not tmdb:
            raise ValueError(f'Group without source ID: {gid}')
        expected = '|'.join(sorted([f'kmdb:{kmdb}' if kmdb else '', f'tmdb:{tmdb}' if tmdb else '']))
        expected = expected.strip('|')
        if gid != expected:
            raise ValueError(f'Inconsistent source IDs: {gid} vs {expected}')
        for source, sid in (('kmdb', kmdb), ('tmdb', tmdb)):
            if sid:
                pair = (source, str(sid))
                if pair in source_ids:
                    raise ValueError(f'Source ID appears in multiple groups: {pair}')
                source_ids.add(pair)
        by_gid[gid] = g
    rel_keys = set()
    content_ids = set()
    evidence_count = 0
    for r in relations:
        cid, gid, role = r.get('content_id'), r.get('person_group_key'), r.get('link_role')
        if type(cid) is not int or cid <= 0 or gid not in by_gid or role not in ROLES:
            raise ValueError(f'Invalid relation: {cid}, {gid}, {role}')
        if r.get('content_type') not in ('movie', 'tv'):
            raise ValueError(f'Invalid content_type: {cid}')
        key = (cid, gid, role)
        if key in rel_keys:
            raise ValueError(f'Duplicate relation: {key}')
        rel_keys.add(key)
        content_ids.add(cid)
        evidences = r.get('evidence')
        if not isinstance(evidences, list) or not evidences:
            raise ValueError(f'Missing evidence: {key}')
        for e in evidences:
            if not isinstance(e, dict) or e.get('source') not in ('kmdb', 'tmdb'):
                raise ValueError(f'Invalid evidence: {key}')
            evidence_count += 1
    return by_gid, content_ids, evidence_count


def create_map(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS public.screen_person_identity_map (
            person_group_key text PRIMARY KEY,
            entity_id bigint NOT NULL UNIQUE REFERENCES public.entity(entity_id),
            kmdb_person_id text,
            tmdb_person_id text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT screen_person_map_has_source CHECK (
                kmdb_person_id IS NOT NULL OR tmdb_person_id IS NOT NULL
            )
        )
    ''')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS screen_person_map_kmdb_uidx
                    ON public.screen_person_identity_map (kmdb_person_id)
                    WHERE kmdb_person_id IS NOT NULL''')
    conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS screen_person_map_tmdb_uidx
                    ON public.screen_person_identity_map (tmdb_person_id)
                    WHERE tmdb_person_id IS NOT NULL''')


def copy_rows(conn, table, columns, iterable):
    with conn.cursor() as cur:
        with cur.copy(f'COPY {table} ({", ".join(columns)}) FROM STDIN') as copy:
            for row in iterable:
                copy.write_row(row)


def stage(conn, groups, relations):
    conn.execute('''CREATE TEMP TABLE sp_groups (
        gid text PRIMARY KEY, display_name text NOT NULL,
        kmdb_id text, tmdb_id text
    ) ON COMMIT DROP''')
    conn.execute('''CREATE TEMP TABLE sp_aliases (
        gid text, alias_text text, normalized_alias text, lang text
    ) ON COMMIT DROP''')
    conn.execute('''CREATE TEMP TABLE sp_relations (
        content_id bigint, gid text, role text
    ) ON COMMIT DROP''')
    conn.execute('''CREATE TEMP TABLE sp_evidence (
        content_id bigint, gid text, role text,
        source_name text, source_ref text, evidence_text text, evidence_location jsonb
    ) ON COMMIT DROP''')
    copy_rows(conn, 'sp_groups', ['gid','display_name','kmdb_id','tmdb_id'],
              ((g['person_group_key'], g['display_name'].strip(),
                str(g['kmdb_person_id']) if g.get('kmdb_person_id') else None,
                str(g['tmdb_person_id']) if g.get('tmdb_person_id') else None)
               for g in groups))

    def aliases():
        for g in groups:
            gid = g['person_group_key']
            seen = set()
            for text in [g['display_name'], *(g.get('name_variants') or [])]:
                if not isinstance(text, str) or not text.strip():
                    continue
                text = text.strip()
                normalized = normalize_alias(text)
                lang = alias_language(text)
                if (normalized, lang) not in seen:
                    seen.add((normalized, lang))
                    yield (gid, text, normalized, lang)
    copy_rows(conn, 'sp_aliases', ['gid','alias_text','normalized_alias','lang'], aliases())
    copy_rows(conn, 'sp_relations', ['content_id','gid','role'],
              ((r['content_id'],r['person_group_key'],r['link_role']) for r in relations))

    def evidences():
        for r in relations:
            cid, gid, role = r['content_id'], r['person_group_key'], r['link_role']
            seen = set()
            for e in r['evidence']:
                ref = make_evidence_ref(cid, role, gid, e)
                if ref in seen:
                    continue
                seen.add(ref)
                source = e['source']
                summary = f'{source} {role}: {e.get("name") or ""}'
                # Store the complete source credit for later inspection.
                location = json.dumps({'pipeline':'screen_person_03', 'credit':e},
                                      ensure_ascii=False, default=str)
                yield (cid, gid, role, source, ref, summary, location)
    copy_rows(conn, 'sp_evidence', ['content_id','gid','role','source_name',
                                    'source_ref','evidence_text','evidence_location'], evidences())
    conn.execute('CREATE INDEX ON sp_relations (gid)')
    conn.execute('CREATE INDEX ON sp_evidence (gid, content_id, role)')


def check_existing_map(conn):
    # Refuse to change prior identity mappings when 03 group membership changes.
    conflicts = conn.execute('''
        SELECT g.gid, m.person_group_key, m.kmdb_person_id, m.tmdb_person_id
        FROM sp_groups g JOIN public.screen_person_identity_map m
          ON m.person_group_key = g.gid
        WHERE m.kmdb_person_id IS DISTINCT FROM g.kmdb_id
           OR m.tmdb_person_id IS DISTINCT FROM g.tmdb_id
        LIMIT 5
    ''').fetchall()
    if conflicts:
        raise ValueError(f'Existing group mapping changed: {conflicts}')
    conflicts = conn.execute('''
        SELECT g.gid, m.person_group_key
        FROM sp_groups g JOIN public.screen_person_identity_map m
          ON (g.kmdb_id IS NOT NULL AND g.kmdb_id = m.kmdb_person_id)
          OR (g.tmdb_id IS NOT NULL AND g.tmdb_id = m.tmdb_person_id)
        WHERE g.gid <> m.person_group_key
        LIMIT 5
    ''').fetchall()
    if conflicts:
        raise ValueError('Existing source IDs mapped to different groups; manual reconciliation required: '
                         + str(conflicts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path, default=DATA/'03_person_groups.jsonl')
    parser.add_argument('--relations', type=Path, default=DATA/'03_content_person_relations.jsonl')
    parser.add_argument('--apply', action='store_true', help='Actually write to DB; default is read-only')
    args = parser.parse_args()
    load_env()
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL missing in project .env / environment')
    print('[INFO] Validating 03 outputs...', flush=True)
    groups, relations = list(read_jsonl(args.groups)), list(read_jsonl(args.relations))
    by_gid, content_ids, evidence_count = validate(groups, relations)
    with psycopg.connect(url, autocommit=False) as conn:
        if not args.apply:
            conn.execute('SET TRANSACTION READ ONLY')
            with conn.cursor() as cur:
                cur.execute('SELECT count(*) FROM public.content WHERE content_id = ANY(%s)',
                            (list(content_ids),))
                existing = cur.fetchone()[0]
                cur.execute("SELECT to_regclass('public.screen_person_identity_map') IS NOT NULL")
                map_exists = cur.fetchone()[0]
                mapped = 0
                if map_exists:
                    cur.execute('SELECT count(*) FROM public.screen_person_identity_map')
                    mapped = cur.fetchone()[0]
            print(json.dumps({'mode':'dry_run', 'database_changed':False,
                'person_groups':len(groups), 'relations':len(relations),
                'evidence_rows':evidence_count, 'content_ids':len(content_ids),
                'content_ids_existing':existing, 'mapping_table_exists':map_exists,
                'mapping_rows_existing':mapped,
                'note':'No DB writes. Use --apply after reviewing the counts.'}, ensure_ascii=False))
            conn.rollback()
            if existing != len(content_ids):
                raise SystemExit('Missing content IDs: aborting')
            return
        print('[WARN] --apply: changes will be committed as one transaction.', flush=True)
        create_map(conn)
        stage(conn, groups, relations)
        check_existing_map(conn)
        missing = conn.execute('''SELECT count(*) FROM
            (SELECT DISTINCT r.content_id FROM sp_relations r
             LEFT JOIN public.content c ON c.content_id=r.content_id
             WHERE c.content_id IS NULL) q''').fetchone()[0]
        if missing:
            raise ValueError(f'{missing} content IDs not in DB')
        # Identity ALWAYS: let PostgreSQL generate entity_id; RETURNING captures it.
        new_count = 0
        with conn.cursor() as cur:
            cur.execute('''SELECT g.gid, g.display_name, g.kmdb_id, g.tmdb_id
                FROM sp_groups g LEFT JOIN public.screen_person_identity_map m
                ON m.person_group_key=g.gid WHERE m.person_group_key IS NULL ORDER BY g.gid''')
            for gid, name, kmdb, tmdb in cur:
                with conn.cursor() as writer:
                    writer.execute('''INSERT INTO public.entity (entity_name,entity_type)
                                      VALUES (%s,'PERSON') RETURNING entity_id''', (name,))
                    entity_id = writer.fetchone()[0]
                    writer.execute('''INSERT INTO public.screen_person_identity_map
                        (person_group_key,entity_id,kmdb_person_id,tmdb_person_id)
                        VALUES (%s,%s,%s,%s)''', (gid,entity_id,kmdb,tmdb))
                    new_count += 1
                if new_count % 10000 == 0:
                    print(f'[INFO] Created {new_count} PERSON entities...', flush=True)
        conn.execute('''INSERT INTO public.entity_alias
            (entity_id,alias_text,normalized_alias,lang,alias_kind,source,status,source_ref)
            SELECT m.entity_id,a.alias_text,a.normalized_alias,a.lang,
                   'label','screen_person_03','proposed',g.gid
            FROM sp_aliases a
            JOIN public.screen_person_identity_map m ON m.person_group_key=a.gid
            JOIN sp_groups g ON g.gid=a.gid
            ON CONFLICT (entity_id,normalized_alias,lang) DO NOTHING''')
        alias_count = conn.execute('SELECT count(*) FROM sp_aliases').fetchone()[0]
        conn.execute('''INSERT INTO public.content_entity
            (content_id,entity_id,link_role,fact_status,evidence_status,
             relation_basis,next_hop_eligible,policy_version)
            SELECT r.content_id,m.entity_id,r.role,'proposed','unchecked',
                   'source_fact',false,'screen_person_03'
            FROM sp_relations r
            JOIN public.screen_person_identity_map m ON m.person_group_key=r.gid
            ON CONFLICT (content_id,entity_id,link_role) DO NOTHING''')
        # Idempotent evidence: source_ref is a deterministic hash of the original credit.
        conn.execute('''INSERT INTO public.content_entity_evidence
            (content_entity_id,evidence_type,source_name,source_ref,source_group,
             evidence_text,evidence_location)
            SELECT ce.content_entity_id,'metadata',e.source_name,e.source_ref,
                   'screen_person_03',e.evidence_text,e.evidence_location
            FROM sp_evidence e
            JOIN public.screen_person_identity_map m ON m.person_group_key=e.gid
            JOIN public.content_entity ce
              ON ce.content_id=e.content_id AND ce.entity_id=m.entity_id AND ce.link_role=e.role
            WHERE NOT EXISTS (
                SELECT 1 FROM public.content_entity_evidence old
                WHERE old.content_entity_id=ce.content_entity_id
                  AND old.evidence_type='metadata'
                  AND old.source_ref=e.source_ref
            )''')
        conn.commit()
    print(json.dumps({'mode':'apply','database_changed':True,
        'new_person_entities':new_count,'input_groups':len(groups),
        'input_relations':len(relations),'input_evidence':evidence_count,
        'staged_aliases':alias_count,
        'note':'Committed. Re-run is supported when group-to-source-ID mapping is unchanged.'},
        ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
