"""Load original works using the existing preview module. Default: read-only."""
import argparse
import json
from collections import defaultdict, Counter
from psycopg.types.json import Jsonb
from preview_original_work_matching import (
    connect_db, fetch_kmdb_originals, fetch_books, build_records,
    match_records, normalize_title, normalize_author, strip_outer_brackets,
)

POLICY = 'original-work-v1'


def run(conn, apply):
    stats = Counter()
    planned_relations = set()
    planned_written = set()
    fake = -1
    def new_entity(name, kind):
        nonlocal fake
        stats['new_' + kind] += 1
        if apply:
            return conn.execute('INSERT INTO entity(entity_name,entity_type) VALUES (%s,%s) RETURNING entity_id', (name,kind)).fetchone()['entity_id']
        fake -= 1
        return fake

    rows = fetch_kmdb_originals(conn)
    books = build_records(fetch_books(conn), 'book')
    works = build_records(rows, 'kmdb')
    credits = {(r['content_id'], r['credit_id']): r for r in rows}
    entities = {r['entity_id']: r for r in conn.execute('SELECT entity_id,entity_name,entity_type,canonical_entity_id FROM entity')}
    roots = {i: r['canonical_entity_id'] or i for i,r in entities.items()}
    for i, root in roots.items():
        if root not in roots or roots[root] != root or entities[i]['entity_type'] != entities[root]['entity_type']:
            raise RuntimeError(f'Invalid canonical mapping: {i} -> {root}')
    def root(i):
        return roots.get(i,i)
    kmids = defaultdict(set)
    ids_by_person = defaultdict(set)
    for r in conn.execute('SELECT entity_id,kmdb_person_id FROM screen_person_identity_map WHERE kmdb_person_id IS NOT NULL'):
        pid = r['kmdb_person_id'].strip()
        if pid:
            kmids[pid].add(r['entity_id'])
            ids_by_person[r['entity_id']].add(pid)
    credit_people = defaultdict(set)
    book_identity = defaultdict(set)
    for r in conn.execute('''SELECT ce.content_id,ce.entity_id,ce.link_role,ce.fact_status,ce.evidence_status,ev.evidence_location
        FROM content_entity ce JOIN entity e USING(entity_id)
        JOIN content_entity_evidence ev USING(content_entity_id)
        WHERE e.entity_type='PERSON' AND ce.link_role IN ('author','original_author') AND ce.fact_status <> 'rejected' '''):
        loc = r['evidence_location'] or {}
        if not isinstance(loc,dict): continue
        credit = loc.get('credit') or {}
        if r['link_role']=='original_author' and credit.get('source')=='kmdb' and credit.get('credit_id') is not None:
            credit_people[(r['content_id'],str(credit['credit_id']))].add(r['entity_id'])
        if r['link_role']=='author' and r['fact_status']=='verified' and r['evidence_status']=='checked':
            for field in ('isni','kac_ids'):
                for val in loc.get(field) or []:
                    book_identity[r['entity_id']].add((field,val))
    # Previously loaded credit evidence also resolves ID-less credits on rerun.
    for r in conn.execute('''SELECT ce.content_id,ce.entity_id,ev.evidence_location FROM content_entity ce
        JOIN content_entity_evidence ev USING(content_entity_id)
        WHERE ce.link_role='original_author' AND ce.fact_status <> 'rejected' AND ev.source_group=%s''',(POLICY,)):
        loc=r['evidence_location'] or {}
        if 'credit_id' in loc:
            credit_people[(r['content_id'],str(loc['credit_id']))].add(r['entity_id'])
            pid=str(loc.get('person_id') or '').strip()
            if pid:
                kmids[pid].add(r['entity_id'])
                ids_by_person[r['entity_id']].add(pid)
    def members(i):
        return {x for x in roots if root(x)==root(i)} | {i}
    def identifiers(i, mapping):
        return set().union(*(mapping.get(x,set()) for x in members(i)))
    def merge(a,b):
        a,b=root(a),root(b)
        if a==b: return a
        for mapping in (ids_by_person,book_identity):
            aa,bb=identifiers(a,mapping),identifiers(b,mapping)
            if mapping is book_identity:
                conflict=any({v for k,v in aa if k==f} and {v for k,v in bb if k==f} and not ({v for k,v in aa if k==f}&{v for k,v in bb if k==f}) for f in ('isni','kac_ids'))
            else: conflict=bool(aa and bb and aa.isdisjoint(bb))
            if conflict: return None
        def rank(i):
            return (not any(x!=i and root(x)==i for x in roots),not bool(identifiers(i,book_identity)),not bool(identifiers(i,ids_by_person)),i)
        keep,other=sorted((a,b),key=rank)
        changed=[x for x in roots if root(x)==other]
        print(f'[PERSON] {other} -> {keep}')
        if apply:
            conn.execute('UPDATE entity SET canonical_entity_id=%s WHERE entity_id=ANY(%s)',(keep,changed))
        for x in changed: roots[x]=keep
        stats['canonical_updates']+=len(changed)
        return keep
    def relation(cid,eid,role,verified,source,ref,loc):
        status,ev=('verified','checked') if verified else ('proposed','unchecked')
        old=conn.execute('SELECT content_entity_id,fact_status FROM content_entity WHERE content_id=%s AND entity_id=%s AND link_role=%s',(cid,eid,role)).fetchone()
        if old and old['fact_status']=='rejected':
            stats['rejected_skipped']+=1; return
        if old: rid=old['content_entity_id']
        else:
            key = (cid, eid, role)
            if key in planned_relations and not apply: return
            planned_relations.add(key)
            stats['new_'+role]+=1
            if not apply: return
            rid=conn.execute('''INSERT INTO content_entity(content_id,entity_id,link_role,fact_status,evidence_status,relation_basis,next_hop_eligible,policy_version)
                VALUES (%s,%s,%s,%s,%s,'source_fact',%s,%s) RETURNING content_entity_id''',(cid,eid,role,status,ev,verified,POLICY)).fetchone()['content_entity_id']
        if apply:
            conn.execute('''INSERT INTO content_entity_evidence(content_entity_id,evidence_type,source_name,source_ref,source_group,evidence_text,evidence_location)
                SELECT %s,'metadata',%s,%s,%s,%s,%s WHERE NOT EXISTS
                (SELECT 1 FROM content_entity_evidence WHERE content_entity_id=%s AND source_group=%s AND source_ref=%s)''',
                (rid,source,ref,POLICY,role,Jsonb(loc),rid,POLICY,ref))
    # Group KMDb records, never merge on author alone.
    index={id(w):i for i,w in enumerate(works)}
    groups={i:{i} for i in range(len(works))}
    owner=list(range(len(works)))
    for m in match_records(works):
        if not m['auto_merge_eligible']: continue
        a,b=(owner[index[id(m[k])]] for k in ('left','right'))
        if a==b: continue
        combined=groups[a]|groups[b]
        ids=defaultdict(set)
        for i in combined:
            for name,vals in works[i]['person_ids'].items(): ids[name].update(vals)
        if any(len(v)>1 for v in ids.values()):
            stats['work_conflicts']+=1; continue
        groups[a]=combined; del groups[b]
        for i in combined: owner[i]=a
    matches=defaultdict(list)
    for m in match_records(works,books):
        if m['auto_merge_eligible']: matches[m['right']['content_id']].append(m)
    attached=defaultdict(list)
    for cid,ms in matches.items():
        gs={owner[index[id(m['left'])]] for m in ms}
        if len(gs)!=1:
            print(f'[HOLD] ambiguous book {cid}'); stats['ambiguous_books']+=1; continue
        attached[next(iter(gs))].extend(ms)
    existing=defaultdict(set)
    for r in conn.execute('''SELECT ce.content_id,ce.entity_id,e.entity_name,ce.fact_status FROM content_entity ce JOIN entity e USING(entity_id)
        WHERE ce.link_role='original_story' AND e.entity_type='WORK' '''):
        existing[(r['content_id'],normalize_title(r['entity_name']))].add(r['entity_id'])
    for g,indices in sorted(groups.items()):
        ws=[works[i] for i in sorted(indices)]
        old=set().union(*(existing[(w['content_id'],w['title_key'])] for w in ws))
        if len(old)>1:
            print(f'[HOLD] multiple existing WORKs {old}'); continue
        title=strip_outer_brackets(sorted(ws[0]['raw_titles'],key=lambda x:(len(x),x))[0].strip())
        wid=next(iter(old)) if old else new_entity(title,'WORK')
        print(f'[WORK] {wid}: {title}')
        ms=attached[g]
        author_links=defaultdict(set)
        work_people=defaultdict(set)
        for w in ws:
            for credit_id in sorted(w['credit_ids']):
                r=credits[(w['content_id'], credit_id)]; name=normalize_author(r['author_name']); pid=(r['person_id'] or '').strip()
                candidates=credit_people[(w['content_id'],str(credit_id))] | (kmids[pid] if pid else set())
                candidates={root(x) for x in candidates}
                if len(candidates)>1:
                    print(f'[HOLD] conflicting credit {credit_id}: {candidates}'); continue
                book_ids=set()
                verified_book_ids=set()
                for m in ms:
                    if m['left'] is w and name in m['shared_authors']:
                        b=m['right']
                        book_ids.update(root(x) for x in b['author_entity_ids'][name])
                        if b['author_statuses'][name] == {('verified','checked')}:
                            verified_book_ids.update(root(x) for x in b['author_entity_ids'][name] if identifiers(x,book_identity))
                if len(book_ids)>1:
                    print(f'[HOLD] conflicting book authors {credit_id}'); continue
                p=next(iter(candidates),None); b=next(iter(book_ids),None)
                if p is not None and b is not None:
                    if root(p) != root(b) and b not in verified_book_ids:
                        print(f'[HOLD] unverified identity {credit_id}: {p}, {b}')
                        stats['identity_holds'] += 1
                        b = None
                    merged=merge(p,b) if b is not None else p
                    if merged is None:
                        print(f'[HOLD] identity conflict {credit_id}'); continue
                    p=merged
                elif p is None: p=b
                if p is None:
                    # Reuse only within the same matched work, not across unrelated titles.
                    local = {root(x) for x in work_people[name]}
                    if pid:
                        local = {x for x in local if not identifiers(x,ids_by_person) or pid in identifiers(x,ids_by_person)}
                    if len(local)==1: p=next(iter(local))
                    elif len(local)>1:
                        print(f'[HOLD] ambiguous work author {credit_id}')
                        stats['identity_holds'] += 1
                        continue
                if p is None:
                    p=new_entity(r['author_name'],'PERSON'); roots[p]=p
                work_people[name].add(p)
                if pid: kmids[pid].add(p); ids_by_person[p].add(pid)
                loc=dict(r); loc['work_entity_id']=wid; loc['matched_book_ids']=sorted({m['right']['content_id'] for m in ms if m['left'] is w}); loc['resolved_person_id']=p
                ref=f'kmdb_credit:{credit_id}'
                relation(w['content_id'],wid,'original_story',True,'kmdb',ref,loc)
                # Preserve existing original_author edge instead of duplicating it at root.
                prior=credit_people[(w['content_id'],str(credit_id))]
                target=min(prior) if prior else p
                relation(w['content_id'],target,'original_author',bool(b is not None and b in verified_book_ids),'kmdb',ref,loc)
                credit_people[(w['content_id'],str(credit_id))].add(target)
                author_links[p].add(ref)
            # WORK relation remains valid even if author resolution was held.
            for credit_id in sorted(w['credit_ids']):
                r=credits[(w['content_id'], credit_id)]
                relation(w['content_id'],wid,'original_story',True,'kmdb',f'kmdb_credit:{credit_id}',dict(r))
            for alias in sorted(w['raw_titles']):
                if alias==title: continue
                if apply:
                    conn.execute('''INSERT INTO entity_alias(entity_id,alias_text,normalized_alias,lang,alias_kind,source,status,source_ref)
                        VALUES (%s,%s,%s,'und','label','kmdb','verified',%s) ON CONFLICT(entity_id,normalized_alias,lang) DO NOTHING''',(wid,alias,normalize_title(alias),f'kmdb_content:{w["content_id"]}'))
        for m in ms:
            b=m['right']; w=m['left']
            verified=any(('verified','checked') in b['author_statuses'][a] for a in m['shared_authors'])
            loc={'book_content_id':b['content_id'],'kmdb_content_id':w['content_id'],'credit_ids':sorted(w['credit_ids']),'shared_authors':sorted(m['shared_authors']),'author_entity_ids':{a:sorted(b['author_entity_ids'][a]) for a in m['shared_authors']},'author_statuses':{a:sorted(b['author_statuses'][a]) for a in m['shared_authors']}}
            relation(b['content_id'],wid,'represents_work',verified,'kmdb_nlk',f'original_work:{w["content_id"]}:{b["content_id"]}',loc)
        for p, refs in author_links.items():
            key = (wid, root(p))

            if key in planned_written:
                continue

            planned_written.add(key)

            existing_written_by = conn.execute(
                """
                SELECT 1
                FROM entity_relation er
                JOIN entity e
                    ON er.to_entity_id = e.entity_id
                WHERE er.from_entity_id = %s
                AND COALESCE(
                    e.canonical_entity_id,
                    e.entity_id
                ) = %s
                AND er.relation_type = 'WRITTEN_BY'
                """,
                key,
            ).fetchone()

            if existing_written_by:
                continue

            stats['new_WRITTEN_BY'] += 1

            if apply:
                conn.execute(
                    """
                    INSERT INTO entity_relation (
                        from_entity_id,
                        to_entity_id,
                        relation_type,
                        fact_status,
                        evidence_status,
                        relation_basis,
                        next_hop_eligible,
                        policy_version,
                        source_ref
                    )
                    VALUES (
                        %s, %s, 'WRITTEN_BY',
                        'proposed', 'unchecked',
                        'source_fact', false, %s, %s
                    )
                    ON CONFLICT (
                        from_entity_id,
                        to_entity_id,
                        relation_type
                    ) DO NOTHING
                    """,
                    (
                        wid,
                        root(p),
                        POLICY,
                        json.dumps(sorted(refs)),
                    ),
                )
            key=(wid,root(p))
            if key not in planned_written:
                planned_written.add(key)
                if not conn.execute("SELECT 1 FROM entity_relation WHERE from_entity_id=%s AND to_entity_id=%s AND relation_type='WRITTEN_BY'",key).fetchone():
                    stats['new_WRITTEN_BY'] += 1
            if apply:
                conn.execute('''INSERT INTO entity_relation(from_entity_id,to_entity_id,relation_type,fact_status,evidence_status,relation_basis,next_hop_eligible,policy_version,source_ref)
                    VALUES (%s,%s,'WRITTEN_BY','proposed','unchecked','source_fact',false,%s,%s)
                    ON CONFLICT(from_entity_id,to_entity_id,relation_type) DO NOTHING''',(wid,root(p),POLICY,json.dumps(sorted(refs))))
    print('[SUMMARY]',dict(stats))


def main():
    parser=argparse.ArgumentParser()
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    with connect_db() as conn:
        if args.apply: conn.execute('SET default_transaction_read_only = off')
        with conn.transaction():
            if args.apply:
                conn.execute('LOCK TABLE entity, content_entity, content_entity_evidence, entity_alias, entity_relation, screen_person_identity_map IN SHARE ROW EXCLUSIVE MODE')
            else: conn.execute('SET TRANSACTION READ ONLY')
            run(conn,args.apply)
    print('[DONE]', 'committed' if args.apply else 'read-only preview')

if __name__=='__main__':
    main()
