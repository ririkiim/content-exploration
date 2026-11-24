import argparse, importlib, json, re, unicodedata
from collections import Counter, defaultdict
m=importlib.import_module('01_collect_author_authority')
AUTHOR=set('저 저자 공저 등저 지음 글 글그림 원저'.split())
EXCLUDE=set('역 역자 등역 공역 번역 옮김 편역 편 편저 공편 등편 편집 엮음 감수 그림 삽화 일러스트 해설 기획 평역 역해 각색 원안 원작'.split())
ROLES='|'.join(sorted(AUTHOR|EXCLUDE,key=lambda x:(-len(x),x)))
def clean(s): return ' '.join(unicodedata.normalize('NFKC',s or '').split())
def norm(s): return clean(s).casefold()
def parse(raw):
    s=clean(raw); s={'레프 톨스토이 저이명현 역':'레프 톨스토이 저/이명현 역'}.get(s,s)
    parts=[x.strip() for x in re.split(r'[/;]',s) if x.strip()]; authors=[]; excluded=[]; review=[]
    for part in parts:
        match=re.fullmatch(rf'(.+?)\s+({ROLES})',part)
        prefix=re.match(r'^(지은이|저자|글쓴이|옮긴이|역자|번역자|편집자|엮은이)\s*:\s*',part)
        name,role=match.groups() if match else (part,None)
        if prefix:
            name=name[prefix.end():]; role=role or ('저' if prefix[1] in {'지은이','저자','글쓴이'} else '역')
        if role in EXCLUDE: excluded.append((name,role)); continue
        if not role and len(parts)!=1: review.append(part); continue
        name=re.sub(r'\s+외$','',name).strip()
        group=[x.strip() for x in name.split(',')]
        if len(group)>1 and not (role and all(re.fullmatch(r'(?:[A-Za-z]\.\s*)*[가-힣][가-힣\s]*',x) for x in group)):
            review.append(part); continue
        authors.extend(x for x in group if x)
    return list(dict.fromkeys(authors)),excluded,review

def variants(s):
    s=norm(s); result={s}
    if ',' in s and s.count(',')==1:
        a,b=map(str.strip,s.split(',')); result.add(b+' '+a)
    result|={x.replace(' ','') for x in result if re.fullmatch(r'[가-힣 ]+',x) and len(x.replace(' ',''))>=3}
    return result

def valid_isni(s):
    s=re.sub(r'[\s-]','',s or '').upper()
    if not re.fullmatch(r'\d{15}[\dX]',s): return None
    t=0
    for c in s[:15]: t=(t+int(c))*2
    n=(12-t%11)%11
    return s if s[-1]==('X' if n==10 else str(n)) else None

def authority_query(xs):
    return '''SELECT DISTINCT ?author ?predicate ?value WHERE { VALUES ?author { '''+m.uris(xs)+''' }
    VALUES ?predicate { foaf:name skos:prefLabel skos:altLabel rdfs:label nlon:isni }
    ?author ?predicate ?value . }'''

def groups(records):
    # Connected components by KAC or valid ISNI; reject whole conflicting component.
    parent={k:k for k in records}
    def find(k):
        while parent[k]!=k: parent[k]=parent[parent[k]]; k=parent[k]
        return k
    owners={}
    for k,r in records.items():
        for i in r['isni']:
            if i in owners: parent[find(k)]=find(owners[i])
            else: owners[i]=k
    components=defaultdict(set)
    for k in records: components[find(k)].add(k)
    return {k:g for g in components.values() for k in g}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=30); p.add_argument('--batch-size',type=int,default=3); p.add_argument('--delay',type=float,default=1.5); p.add_argument('--reclassify-only',action='store_true'); a=p.parse_args()
    if a.limit<0 or a.batch_size<1 or a.delay<1: p.error('invalid arguments')
    targets=m.load_targets(); ids=list(dict.fromkeys(r['isbn13'] for r in targets if m.isbn_forms(r['isbn13'])))
    if a.limit: ids=ids[:a.limit]; targets=[r for r in targets if r['isbn13'] in ids]
    ic=m.read_cache('isbn_lookup_raw.jsonl','isbn'); bc=m.read_cache('book_creator_raw.jsonl','book'); iv=m.read_cache('book_inverse_raw.jsonl','book')
    def ok(cache,k): return cache.get(k,{}).get('status')=='ok'
    def rows(cache,k): return cache.get(k,{}).get('rows',[])
    def paper(b):
        types={r['value'] for r in rows(bc,b) if r['predicate'].endswith('#type')}
        return {'http://lod.nl.go.kr/ontology/Book','http://lod.nl.go.kr/ontology/OfflineMaterial'}<=types and not any(x.endswith('/OnlineMaterial') for x in types)
    by_isbn={}; authorities=set()
    for i in ids:
        forms=m.isbn_forms(i); books={r['book'] for f in forms for r in rows(ic,f)}; pb={b for b in books if paper(b)}
        evidence=[]
        for b in sorted(pb):
            links=[(r['value'],'dcterms:creator') for r in rows(bc,b) if r['predicate'].endswith('/creator')]+[(r['author'],'inverse:nlon:create') for r in rows(iv,b)]
            for k,rel in links:
                if re.fullmatch(re.escape(m.BASE)+r'KAC[0-9A-Z]+',k):
                    authorities.add(k); evidence.append({'book':b,'author':k,'relation':rel,'matched_isbns':[f for f in forms if any(r['book']==b for r in rows(ic,f))],'labels':[r['value'] for r in rows(bc,b) if r['predicate'].endswith('#label')]})
        status=('lookup_error' if not all(ok(ic,f) for f in forms) or not all(ok(bc,b) for b in books) or not all(ok(iv,b) for b in pb) else 'isbn_not_found' if not books else 'no_confirmed_paper_book' if not pb else 'authority_not_found' if not evidence else 'ok')
        by_isbn[i]=(status,evidence)
    ac=m.collect(m.Client(a.delay),authorities,'author_alias_raw.jsonl','author',authority_query,a.batch_size,a.reclassify_only)
    details={}
    for k in authorities:
        rr=rows(ac,k); raw={r['value'] for r in rr if r['predicate'].endswith('/isni')}
        details[k]={'names':sorted({r['value'] for r in rr if not r['predicate'].endswith('/isni')}),'isni':sorted({v for s in raw if (v:=valid_isni(s))}),'invalid_isni':sorted(s for s in raw if not valid_isni(s))}
    clusters=groups(details); good=[]; bad=[]; reviews=[]; excluded=[]; seen=set()
    for r in targets:
        raw=r['author_name']; names,ex,rev=parse(raw); base={**r,'raw_author_name':raw}
        excluded.extend({**base,'name':n,'role':role} for n,role in ex); reviews.extend({**base,'fragment':x} for x in rev)
        for name in names:
            key=(r['book_edition_id'],norm(name))
            if key in seen: continue
            seen.add(key); status,ev=by_isbn.get(r['isbn13'],('invalid_isbn',[])); ks={e['author'] for e in ev}
            matches={k for k in ks if any(variants(name)&variants(n) for n in details[k]['names'])}
            gs={frozenset(clusters[k]) for k in matches}; group=set().union(*gs) if gs else set(); isni={s for k in group for s in details[k]['isni']}
            if status=='ok':
                if not all(ok(ac,k) for k in ks): status='alias_lookup_error'
                elif not matches: status='author_name_mismatch'
                elif len(gs)>1: status='ambiguous_author'
                elif len(isni)>1 or any(details[k]['invalid_isni'] for k in group): status='identifier_conflict'
                elif any(variants(n)&variants(name) for n,_ in ex): status='role_conflict'
                else: status='isbn_author_candidate'
            obj={**base,'author_name':name,'status':status,'kac_ids':sorted(k.rsplit('/',1)[-1] for k in group),'isni':sorted(isni),'evidence':[e for e in ev if e['author'] in matches],'all_candidates':[{**e,**details[e['author']]} for e in ev]}
            if status=='isbn_author_candidate':
                obj['identity_key']='ISNI:'+next(iter(isni)) if isni else 'KAC:'+min(obj['kac_ids']); good.append(obj)
            else: bad.append(obj)
    # Limited runs never replace full result files.
    summary={'target_author_rows':len(good)+len(bad),'matched':len(good),'status_counts':dict(Counter(r['status'] for r in good+bad)),'parse_review_rows':len(reviews),'excluded_rows':len(excluded),'matched_book_editions':len({r['book_edition_id'] for r in good}),'unique_identity_keys':len({r['identity_key'] for r in good})}
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if a.limit:
        print('\n[저자 매칭 상세]')
        for r in sorted(good+bad,key=lambda x:(x['book_edition_id'],x['author_name'])):
            print(f"[{r['status']}] {r['title']} | 저자={r['author_name']} | KAC={','.join(r['kac_ids']) or '-'} | ISNI={','.join(r['isni']) or '-'}")
        if reviews:
            print('\n[저자 파싱 검토]')
            for r in reviews: print(f"{r['title']} | {r['fragment']}")
        print('Limited test: result files unchanged')
        return
    if any(r['status'] in {'lookup_error','alias_lookup_error'} for r in bad): raise RuntimeError('Incomplete collection; existing results preserved. Retry 01/02.')
    for name,data in [('author_candidates.jsonl',good),('unmatched_authors.jsonl',bad),('author_parse_review.jsonl',reviews),('excluded_contributors.jsonl',excluded)]: m.save(name,data)
    (m.OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')

if __name__=='__main__': main()
