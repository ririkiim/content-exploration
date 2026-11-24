"""Incremental book-author load. Default read-only dry run; --apply commits.
X excludes only the manual target relations, never an existing person.
"""
import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

VERSION = 'book-author-incremental-v1'
ROOT = next((p for p in Path(__file__).resolve().parents if (p/'.env').exists()), Path.cwd())
load_dotenv(ROOT/'.env')
BASE = 'http://lod.nl.go.kr/resource/'

def clean(x): return str(x or '').strip()
def values(x): return x if isinstance(x, list) else ([x] if x else [])
def kac(x):
    x = clean(x).upper()
    if not re.fullmatch(r'KAC[0-9A-Z]+', x): raise ValueError(f'Invalid KAC: {x}')
    return x

def isni(x):
    x = re.sub(r'[\s-]', '', clean(x)).upper()
    if not re.fullmatch(r'\d{15}[\dX]', x): raise ValueError(f'Invalid ISNI: {x}')
    t = 0
    for d in x[:15]: t = (t + int(d))*2
    c = (12-t%11)%11
    if x[-1] != ('X' if c==10 else str(c)): raise ValueError(f'ISNI checksum: {x}')
    return x

def identifiers(k, i):
    return {kac(x) for x in values(k)}, {isni(x) for x in values(i)}

def read_jsonl(p):
    return [json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]

def read_csv(p):
    for enc in ('utf-8-sig','cp949'):
        try:
            with p.open(encoding=enc,newline='') as f: return list(csv.DictReader(f))
        except UnicodeDecodeError: pass
    raise ValueError('Unsupported CSV encoding')

def save(p, data):
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    tmp.replace(p)

def key(r): return (int(r['book_edition_id']),int(r['content_id']),clean(r['author_name']))

def hints(rows, autos):
    out=[]
    for r in rows:
        for c in r.get('db_candidates',[]):
            out.append((c.get('kac_ids'),c.get('isni'),c.get('entity_id'),c.get('identifier_conflict')))
        for c in r.get('authority_candidates',[]):
            if c.get('author_name_match'):
                out.append((c.get('kac_ids'),c.get('isni'),None,c.get('identifier_conflict')))
    for r in autos:
        out.append((r.get('selected_kac_ids'),r.get('selected_isni'),r.get('selected_entity_id'),False))
    return out

def manual_identity(review, rows, autos):
    # CSV candidate identifiers are never used: Excel may have damaged them.
    k,i=identifiers(clean(review.get('verified_kac')) or None,clean(review.get('verified_isni')) or None)
    explicit=bool(k or i)
    if not explicit:
        for hk,hi,_,conflict in hints(rows,autos):
            if conflict: raise ValueError('Conflicting original candidate')
            a,b=identifiers(hk,hi); k.update(a); i.update(b)
        if len(k)>1 or len(i)>1: raise ValueError('Ambiguous original candidates')
    # If one identifier was explicitly entered, do not import the other from an ambiguous hint.
    if not (k or i): raise ValueError('No valid identifier')
    if len(i)>1: raise ValueError('Multiple ISNI')
    eid=clean(review.get('selected_entity_id'))
    return k,i,int(eid) if eid else None

def prepare(data):
    auto=read_jsonl(data/'05_4_auto_candidates.jsonl')
    manual=read_jsonl(data/'05_4_manual_targets.jsonl')
    reviews=read_csv(data/'05_4_manual_authors.csv')
    by_id={r['manual_author_id']:r for r in reviews}
    if len(by_id)!=len(reviews): raise ValueError('Duplicate manual_author_id')
    groups=defaultdict(list)
    for r in manual: groups[r['manual_author_id']].append(r)
    if set(groups)!=set(by_id): raise ValueError('CSV / JSONL author IDs differ')
    if len({key(r) for r in auto+manual})!=len(auto)+len(manual): raise ValueError('Duplicate input book-author row')
    ready=[]; excluded=[]; issues=[]
    def add(r,k,i,eid,method,name,ref,raw):
        if not (k or i) or len(i)>1: raise ValueError('Missing/conflicting identifiers')
        ready.append(dict(book_edition_id=int(r['book_edition_id']),content_id=int(r['content_id']),title=r['title'],author_name=r['author_name'],name=name,kac_ids=sorted(k),isni=sorted(i),expected_entity_id=eid,method=method,source_ref=ref,raw=raw,isbn13=r.get('isbn13')))
    for r in auto:
        try:
            k,i=identifiers(r.get('selected_kac_ids'),r.get('selected_isni'))
            method={'05_2':'existing_author_title_match','05_3':'authority_title_match'}[r['auto_source']]
            add(r,k,i,r.get('selected_entity_id'),method,r.get('review_author_name') or r['author_name'],BASE+sorted(k)[0] if k else None,r['auto_candidate'])
        except (ValueError,KeyError) as e: issues.append(dict(row=key(r),reason=str(e)))
    for gid,rows in groups.items():
        rev=by_id[gid]
        if any(r['review_author_name']!=rev['review_author_name'] for r in rows) or int(rev['manual_book_count'])!=len(rows): raise ValueError(f'Stale CSV: {gid}')
        if clean(rev.get('review_note')).upper()=='X' or clean(rev.get('review_status')).lower()=='excluded':
            excluded.extend(dict(row=key(r),title=r['title'],author=r['author_name'],reason='manual_relation_excluded') for r in rows)
            continue
        try:
            k,i,eid=manual_identity(rev,rows,[r for r in auto if r.get('review_author_name')==rev['review_author_name']])
            ref=clean(rev.get('evidence_url')) or (BASE+sorted(k)[0] if k else None)
            for r in rows:
                add(r,k,i,eid,'manual_author_work_review',rev['review_author_name'],ref,dict(manual_author_id=gid,verified_author_name=rev.get('verified_author_name'),review_note=rev.get('review_note'),review_basis='user confirmed author identity and work; NLK edition match not required'))
        except ValueError as e: issues.append(dict(group=gid,reason=str(e)))
    return ready,excluded,issues

def db_index(conn):
    # Only book-author entities; include prior manual evidence for safe reruns.
    rows=conn.execute('''SELECT e.entity_id,e.entity_name,ev.evidence_location
      FROM public.entity e JOIN public.content_entity ce ON ce.entity_id=e.entity_id
      JOIN public.content c ON c.content_id=ce.content_id
      LEFT JOIN public.content_entity_evidence ev ON ev.content_entity_id=ce.content_entity_id
      WHERE e.entity_type='PERSON' AND ce.link_role='author' AND c.content_type='book' ''').fetchall()
    people={}; index=defaultdict(set)
    for r in rows:
        eid=r['entity_id']; p=people.setdefault(eid,dict(name=r['entity_name'],kac=set(),isni=set()))
        loc=r['evidence_location'] or {}
        if not isinstance(loc,dict): continue
        for field,fn,dest in [('kac_ids',kac,'kac'),('isni',isni,'isni')]:
            for x in values(loc.get(field)):
                try: v=fn(x)
                except ValueError: continue
                p[dest].add(v); index[v].add(eid)
    return people,index

def plan(conn,rows):
    people,index=db_index(conn)
    # Connected components by shared KAC/ISNI, including existing entity IDs.
    groups=[]
    for r in rows:
        ids=set(r['kac_ids']+r['isni']); eids=set().union(*(index[x] for x in ids))
        matches=[g for g in groups if ids & g['ids'] or eids & g['eids']]
        g=dict(ids=ids,eids=eids,rows=[r])
        for old in matches:
            g['ids'].update(old['ids']);g['eids'].update(old['eids']);g['rows'].extend(old['rows']);groups.remove(old)
        groups.append(g)
    result=[]; issues=[]
    for g in groups:
        eids=g['eids']; ks={x for x in g['ids'] if x.startswith('KAC')}; ins=g['ids']-ks
        for eid in eids: ins.update(people[eid]['isni'])
        expected={int(r['expected_entity_id']) for r in g['rows'] if r['expected_entity_id'] is not None}
        if len(eids)>1 or len(ins)>1 or (expected and expected!=eids):
            issues.append(dict(reason='identity conflict',ids=sorted(g['ids']),db_ids=sorted(eids),expected=sorted(expected)));continue
        eid=next(iter(eids),None)
        name=people[eid]['name'] if eid else Counter(r['name'] for r in g['rows']).most_common(1)[0][0]
        for r in g['rows']:
            found=conn.execute('''SELECT 1 FROM public.book_edition b JOIN public.content c ON c.content_id=b.content_id WHERE b.book_edition_id=%s AND b.content_id=%s AND c.content_type='book' ''',(r['book_edition_id'],r['content_id'])).fetchone()
            if not found: issues.append(dict(reason='edition/content mismatch',row=key(r)))
        result.append(dict(entity_id=eid,name=name,kac_ids=sorted(ks),isni=sorted(ins),rows=g['rows']))
    return result,issues

def execute(conn,groups,apply):
    counts=Counter()
    for g in groups:
        eid=g['entity_id']; new=eid is None
        counts['new_entities' if new else 'reused_entities']+=1
        if new and apply:
            eid=conn.execute("INSERT INTO public.entity(entity_name,entity_type,wikidata_qid) VALUES (%s,'PERSON',NULL) RETURNING entity_id",(g['name'],)).fetchone()['entity_id']
        identity='ISNI:'+g['isni'][0] if g['isni'] else 'KAC:'+g['kac_ids'][0]
        by_content=defaultdict(list)
        for r in g['rows']: by_content[r['content_id']].append(r)
        for cid,rs in by_content.items():
            existing=conn.execute("SELECT content_entity_id FROM public.content_entity WHERE content_id=%s AND entity_id=%s AND link_role='author'",(cid,eid)).fetchall() if eid else []
            if len(existing)>1: raise RuntimeError('Duplicate existing author relation')
            rid=existing[0]['content_entity_id'] if existing else None
            counts['existing_relations' if rid else 'new_relations']+=1
            manual=any(r['method']=='manual_author_work_review' for r in rs)
            if not rid and apply:
                rid=conn.execute('''INSERT INTO public.content_entity(content_id,entity_id,link_role,fact_status,evidence_status,relation_basis,next_hop_eligible,policy_version) VALUES (%s,%s,'author',%s,%s,'source_fact',%s,%s) RETURNING content_entity_id''',(cid,eid,'verified' if manual else 'proposed','checked' if manual else 'unchecked',manual,VERSION)).fetchone()['content_entity_id']
            for r in rs:
                source='manual_review' if r['method']=='manual_author_work_review' else 'nlk_lod'
                payload=dict(policy_version=VERSION,identity_key=identity,kac_ids=r['kac_ids'],isni=r['isni'],book_edition_id=r['book_edition_id'],isbn13=r['isbn13'],match_method=r['method'],raw_evidence=r['raw'],source_author_name=r['author_name'])
                duplicate=conn.execute('''SELECT 1 FROM public.content_entity_evidence WHERE content_entity_id=%s AND source_group=%s AND evidence_location->>'book_edition_id'=%s AND evidence_location->>'match_method'=%s AND evidence_location->>'source_author_name'=%s''',(rid,VERSION,str(r['book_edition_id']),r['method'],r['author_name'])).fetchone() if rid else None
                if duplicate: counts['existing_evidence']+=1;continue
                counts['new_evidence']+=1
                if apply:
                    conn.execute('''INSERT INTO public.content_entity_evidence(content_entity_id,evidence_type,source_name,source_ref,source_group,evidence_text,evidence_location,quote_valid) VALUES (%s,'metadata',%s,%s,%s,%s,%s,NULL)''',(rid,source,r['source_ref'],VERSION,f"{r['title']} | {r['author_name']} | {r['method']}",Jsonb(payload)))
    return dict(counts)

def main():
    p=argparse.ArgumentParser();p.add_argument('--data-dir',type=Path,default=ROOT/'structured_relations/data/author_authority');p.add_argument('--apply',action='store_true');a=p.parse_args();data=a.data_dir
    rows,excluded,issues=prepare(data)
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as conn:
        if a.apply:
            conn.execute('LOCK TABLE public.entity, public.content_entity, public.content_entity_evidence IN SHARE ROW EXCLUSIVE MODE')
        else: conn.execute('SET TRANSACTION READ ONLY')
        groups,dbissues=plan(conn,rows);issues+=dbissues
        save(data/'06_plan.json',groups);save(data/'06_excluded.json',excluded);save(data/'06_issues.json',issues)
        summary=dict(mode='apply' if a.apply else 'dry_run',candidate_rows=len(rows),excluded_manual_rows=len(excluded),issues=len(issues),database_changed=False)
        if not issues:
            summary.update(execute(conn,groups,a.apply))
            if a.apply: conn.commit();summary['database_changed']=True
            else: conn.rollback()
        else: conn.rollback()
    save(data/'06_summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
    if issues: raise SystemExit('Review 06_issues.json; no DB changes made.')

if __name__=='__main__': main()
