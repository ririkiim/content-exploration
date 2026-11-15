"""Incremental structured facts loader for current content/entity schema.

Default is read-only preview. Requires sql/02_person_source_identity.sql for --write.
No auto-verification. Existing links/statuses are preserved.
"""
from __future__ import annotations
import argparse
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

ROLES = {'director','cast','screenwriter','author','original_author','original_story'}
JOB_ROLES = {'director':'director','screenplay':'screenwriter','writer':'screenwriter',
             'teleplay':'screenwriter','original story':'original_author',
             'comic book':'original_author'}

@dataclass(frozen=True)
class Fact:
    role: str
    name: str
    entity_type: str
    source_name: str
    source_ref: str
    field: str
    external_person_id: str | None = None
    identity_provider: str | None = None
    order: int | None = None
    detail: str | None = None

def obj(v):
    if isinstance(v, dict): return v
    if isinstance(v, str):
        try:
            x=json.loads(v)
            return x if isinstance(x,dict) else {}
        except (ValueError,TypeError): pass
    return {}

def arr(v): return v if isinstance(v,list) else []
def norm(v): return ' '.join(str(v or '').split())
def name_of(p,*keys):
    for k in keys:
        n=norm(p.get(k))
        if n: return n
    return ''

def person(role,p,source,ref,field,provider=None,id_key='id',order=None,detail=None,name_keys=('name',)):
    if not isinstance(p,dict): return None
    name=name_of(p,*name_keys)
    if not name: return None
    pid=p.get(id_key)
    pid=str(pid).strip() if pid not in (None,'') else None
    if pid and not provider: pid=None
    return Fact(role,name,'PERSON',source,str(ref),field,pid,provider,order,detail)

def tmdb_facts(payload,typ,ref):
    out=[]; credits=obj(obj(payload).get('credits'))
    for i,p in enumerate(arr(credits.get('cast'))):
        f=person('cast',p,f'tmdb_{typ}',ref,f'credits.cast[{i}]',
                 'tmdb_person',order=p.get('order') if isinstance(p,dict) else None)
        if f: out.append(f)
    for i,p in enumerate(arr(credits.get('crew'))):
        if not isinstance(p,dict): continue
        job=norm(p.get('job')).casefold()
        role=JOB_ROLES.get(job)
        if role:
            f=person(role,p,f'tmdb_{typ}',ref,f'credits.crew[{i}]',
                     'tmdb_person',detail=job)
            if f: out.append(f)
    # TV created_by has no unambiguous mapping to director/original_author.
    return out

def kmdb_facts(payload,ref):
    p=obj(payload)
    if 'Data' in p:
        data=arr(p.get('Data'))
        results=arr(data[0].get('Result')) if data and isinstance(data[0],dict) else []
        p=results[0] if len(results)==1 else {}
    out=[]
    for role,section,item,key,id_key in [
        ('director','directors','director','directorNm','directorId'),
        ('cast','actors','actor','actorNm','actorId')]:
        for i,x in enumerate(arr(obj(p.get(section)).get(item))):
            f=person(role,x,'kmdb_movie',ref,f'{section}.{item}[{i}]',
                     'kmdb_person',id_key, i if role=='cast' else None,
                     name_keys=(key,))
            if f: out.append(f)
    for i,x in enumerate(arr(obj(p.get('staffs')).get('staff'))):
        if not isinstance(x,dict): continue
        group=norm(x.get('staffRoleGroup'))
        role={'각본':'screenwriter','원작':'original_author'}.get(group)
        if role:
            f=person(role,x,'kmdb_movie',ref,f'staffs.staff[{i}]',
                     'kmdb_person','staffId',detail=group,name_keys=('staffNm',))
            if f: out.append(f)
        # Only explicit titled original works, not generic staffEtc prose.
        if group=='원작':
            extra=norm(x.get('staffEtc'))
            titles=re.findall(r'[《〈『「](.+?)[》〉』」]',extra)
            for j,title in enumerate(titles):
                title=norm(title)
                if title:
                    out.append(Fact('original_story',title,'WORK','kmdb_movie',str(ref),
                                    f'staffs.staff[{i}].staffEtc.title[{j}]',detail=extra))
    return out

def book_facts(edition):
    out=[]; edition_id=str(edition['book_edition_id'])
    for i,p in enumerate(arr(edition.get('contributors'))):
        if not isinstance(p,dict) or norm(p.get('role')).casefold()!='author': continue
        f=person('author',p,'book_edition',edition_id,f'contributors[{i}]',name_keys=('name',))
        if f: out.append(f)
    return out

def table_exists(cur,name):
    cur.execute('SELECT to_regclass(%s)',(f'public.{name}',))
    return cur.fetchone()['to_regclass'] is not None

def load_facts(cur,content_id):
    cur.execute('SELECT content_type FROM public.content WHERE content_id=%s',(content_id,))
    r=cur.fetchone()
    if not r: raise ValueError(f'Unknown content_id={content_id}')
    typ=r['content_type']; facts=[]
    if typ in ('movie','tv'):
        table='raw_tmdb_movie' if typ=='movie' else 'raw_tmdb_tv'
        pk='raw_tmdb_movie_id' if typ=='movie' else 'raw_tmdb_tv_id'
        cur.execute(f'''SELECT r.raw_payload,r.tmdb_id FROM public.content_source_map m
            JOIN public.{table} r ON r.tmdb_id::text=m.source_id
            WHERE m.content_id=%s AND m.source_name=%s
            ORDER BY r.fetched_at DESC,r.{pk} DESC LIMIT 1''',
            (content_id,f'tmdb_{typ}'))
        tmdb=cur.fetchone()
        if tmdb: facts.extend(tmdb_facts(tmdb['raw_payload'],typ,tmdb['tmdb_id']))
        if typ=='movie':
            # A validated matching record is preferred over unchecked KMDb search candidates.
            if tmdb and table_exists(cur,'movie_source_match'):
                cur.execute('''SELECT kmdb_doc_id,matched_candidate FROM public.movie_source_match
                    WHERE tmdb_id=%s AND match_status='matched'
                      AND kmdb_doc_id IS NOT NULL AND matched_candidate IS NOT NULL''',
                    (tmdb['tmdb_id'],))
                match=cur.fetchone()
                if match:
                    candidate=obj(match['matched_candidate'])
                    if str(candidate.get('DOCID') or '')==str(match['kmdb_doc_id']):
                        facts.extend(kmdb_facts(candidate,match['kmdb_doc_id']))
            # Also support independently mapped KMDb records, when present.
            cur.execute('''SELECT r.source_id,r.raw_payload FROM public.content_source_map m
                JOIN public.raw_kmdb_movie r ON r.source_id=m.source_id
                WHERE m.content_id=%s AND m.source_name='kmdb_movie'
                ORDER BY r.loaded_at DESC,r.raw_kmdb_movie_id DESC LIMIT 1''',(content_id,))
            km=cur.fetchone()
            if km: facts.extend(kmdb_facts(km['raw_payload'],km['source_id']))
    elif typ=='book':
        cur.execute('''SELECT book_edition_id,contributors FROM public.book_edition
            WHERE content_id=%s ORDER BY is_representative DESC,book_edition_id LIMIT 1''',
            (content_id,))
        ed=cur.fetchone()
        if ed: facts.extend(book_facts(ed))
    else: raise ValueError(f'Unsupported content_type={typ}')
    seen=set(); result=[]
    for f in facts:
        key=(f.role,f.name,f.entity_type,f.source_name,f.source_ref,f.field)
        if key not in seen: seen.add(key);result.append(f)
    return result

def resolve_entity(cur,f):
    if f.entity_type=='WORK':
        # Name-only matching is unsafe for works with shared titles.
        cur.execute('''SELECT entity_id FROM public.entity WHERE entity_type='WORK'
                       AND entity_name=%s ORDER BY entity_id''',(f.name,))
        matches=cur.fetchall()
        if len(matches)==1: return matches[0]['entity_id'],'existing_work_name_review'
        if matches: return None,'ambiguous_work'
        cur.execute('''INSERT INTO public.entity(entity_name,entity_type)
                       VALUES (%s,'WORK') RETURNING entity_id''',(f.name,))
        return cur.fetchone()['entity_id'],'new_work_review'
    if f.external_person_id and f.identity_provider:
        cur.execute('''SELECT entity_id FROM public.structured_person_identity
                       WHERE source_name=%s AND source_person_id=%s''',
                    (f.identity_provider,f.external_person_id))
        r=cur.fetchone()
        if r: return r['entity_id'],'mapped_person'
        # No automatic cross-provider name matching.
        cur.execute('''INSERT INTO public.entity(entity_name,entity_type)
                       VALUES (%s,'PERSON') RETURNING entity_id''',(f.name,))
        eid=cur.fetchone()['entity_id']
        cur.execute('''INSERT INTO public.structured_person_identity
            (source_name,source_person_id,entity_id) VALUES (%s,%s,%s)''',
            (f.identity_provider,f.external_person_id,eid))
        return eid,'new_person_identity'
    # No stable ID: a unique exact-name match is still tentative.
    cur.execute('''SELECT entity_id FROM public.entity WHERE entity_type='PERSON'
                   AND entity_name=%s ORDER BY entity_id''',(f.name,))
    matches=cur.fetchall()
    if len(matches)==1: return matches[0]['entity_id'],'name_only_review'
    if len(matches)>1: return None,'ambiguous_person'
    # Do not create a name-only person: avoids irreversible homonym collisions.
    return None,'unresolved_person'

def persist(cur,content_id,f):
    eid,identity_status=resolve_entity(cur,f)
    if eid is None: return identity_status
    cur.execute('''INSERT INTO public.content_entity
        (content_id,entity_id,link_role,relation_basis,fact_status,evidence_status,
         next_hop_eligible,policy_version)
        VALUES (%s,%s,%s,'source_fact','proposed','unchecked',false,'structured-fact-v2')
        ON CONFLICT (content_id,entity_id,link_role) DO NOTHING
        RETURNING content_entity_id''',(content_id,eid,f.role))
    new=cur.fetchone()
    if new: link_id=new['content_entity_id']
    else:
        cur.execute('''SELECT content_entity_id FROM public.content_entity
                       WHERE content_id=%s AND entity_id=%s AND link_role=%s''',
                    (content_id,eid,f.role))
        link_id=cur.fetchone()['content_entity_id']
    loc={'field':f.field,'order':f.order,'role':f.role,'entity_name':f.name}
    if f.detail: loc['detail']=f.detail
    # Match by full provenance location to avoid losing evidence for multiple credits.
    cur.execute('''INSERT INTO public.content_entity_evidence
        (content_entity_id,evidence_type,source_name,source_ref,source_group,
         evidence_text,evidence_location)
        SELECT %s,'metadata',%s,%s,'structured_fact',%s,%s
        WHERE NOT EXISTS (
            SELECT 1 FROM public.content_entity_evidence e
            WHERE e.content_entity_id=%s AND e.evidence_type='metadata'
              AND e.source_name=%s AND e.source_ref=%s
              AND e.evidence_location->>'field'=%s
              AND e.evidence_location->>'role'=%s
        ) RETURNING evidence_id''',
        (link_id,f.source_name,f.source_ref,f'{f.role}: {f.name}',Jsonb(loc),
         link_id,f.source_name,f.source_ref,f.field,f.role))
    evidence_new=cur.fetchone() is not None
    return ('new_link' if new else 'existing_link')+('_new_evidence' if evidence_new else '_existing_evidence')

def check_roles(cur):
    cur.execute('''SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
                   WHERE conrelid='public.content_entity'::regclass
                     AND conname='content_concept_link_role_check' ''')
    r=cur.fetchone()
    if r:
        definition=r['definition']
        missing=[x for x in ROLES if f"'{x}'" not in definition]
        if missing: raise RuntimeError(f'content_entity link_role CHECK lacks: {missing}. Apply migration first.')
    cur.execute('''SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
                   WHERE conrelid='public.entity'::regclass
                     AND conname='concept_concept_type_check' ''')
    r=cur.fetchone()
    if r and "'WORK'" not in r['definition']: raise RuntimeError('entity CHECK lacks WORK. Apply migration first.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--content-id',type=int,action='append',help='Repeat to process multiple contents')
    p.add_argument('--write',action='store_true',help='Explicitly commit inserts')
    p.add_argument('--limit',type=int,default=None,help='Limit number of contents for testing')
    args=p.parse_args()
    if args.limit is not None and args.limit<1: p.error('--limit must be >= 1')
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root/'.env');load_dotenv()
    dsn=os.getenv('DATABASE_URL')
    if not dsn: raise RuntimeError('DATABASE_URL missing from environment/.env')
    stats=Counter()
    with psycopg.connect(dsn,row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            if not args.write: cur.execute('SET TRANSACTION READ ONLY')
            else:
                if not table_exists(cur,'structured_person_identity'):
                    raise RuntimeError('Apply sql/02_person_source_identity.sql first')
                check_roles(cur)
            if args.content_id: ids=list(dict.fromkeys(args.content_id))
            else:
                q='SELECT content_id FROM public.content ORDER BY content_id'
                if args.limit: q+=' LIMIT %s';cur.execute(q,(args.limit,))
                else: cur.execute(q)
                ids=[r['content_id'] for r in cur.fetchall()]
            if args.limit: ids=ids[:args.limit]
            for cid in ids:
                facts=load_facts(cur,cid)
                print(f'\n[content_id={cid}] extracted={len(facts)}')
                stats['contents']+=1
                for f in facts:
                    stats[f.role]+=1
                    if args.write: stats[persist(cur,cid,f)]+=1
                    else: print(f'  {f.role:16} {f.name:25} {f.source_name}:{f.source_ref} {f.field}')
    print('\nMODE:', 'WRITE' if args.write else 'READ-ONLY PREVIEW')
    for k,v in sorted(stats.items()): print(f'{k}: {v}')

if __name__=='__main__': main()
