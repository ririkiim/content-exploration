import argparse, json, os, re, time
from pathlib import Path
from collections import Counter
import requests
from dotenv import load_dotenv

ROOT = next((p for p in Path(__file__).resolve().parents if (p/'.env').exists()), Path.cwd())
load_dotenv(ROOT/'.env')
OUT = ROOT/'structured_relations/data/author_authority'
BASE = 'http://lod.nl.go.kr/resource/'
PREFIX = '''PREFIX nlon: <http://lod.nl.go.kr/ontology/>
PREFIX bibo: <http://purl.org/ontology/bibo/>
PREFIX dcterms: <http://purl.org/dc/terms/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
'''
VERSION = 'paper-creator-20261121'

def read_cache(name, key, version=True):
    path = OUT/name
    return {r[key]: r for r in (json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip())
            if not version or r.get('version') == VERSION} if path.exists() else {}

def append(name, row):
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT/name).open('a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str)+'\n')

def save(name, rows):
    path = OUT/name
    tmp = path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False, default=str)+'\n')
    tmp.replace(path)

def isbn_forms(value):
    s = re.sub(r'[\s-]', '', str(value or ''))
    if not re.fullmatch(r'97[89]\d{10}', s): return []
    if sum(int(c)*(1 if i%2==0 else 3) for i,c in enumerate(s))%10: return []
    forms = [s]
    if s.startswith('978'):
        body = s[3:-1]; check = (-sum(int(c)*(10-i) for i,c in enumerate(body)))%11
        forms.append(body+('X' if check==10 else str(check)))
    return forms

def load_targets():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        rows = conn.execute('''SELECT be.book_edition_id, be.content_id, c.title, be.isbn13,
            contributor->>'name' AS author_name FROM book_edition be
            JOIN content c ON c.content_id=be.content_id
            CROSS JOIN LATERAL jsonb_array_elements(be.contributors) AS contributor
            WHERE contributor->>'role'='author' AND NULLIF(trim(contributor->>'name'),'') IS NOT NULL
            ORDER BY be.book_edition_id, author_name''').fetchall()
    for r in rows: r['isbn13'] = re.sub(r'[\s-]', '', str(r['isbn13'] or ''))
    return rows

class Client:
    def __init__(self, delay=1.5): self.session=requests.Session(); self.delay=delay
    def query(self, q):
        for attempt in range(2):
            try:
                r=self.session.get(os.environ['NL_LOD_ENDPOINT'], params={'query':PREFIX+q,'format':'application/sparql-results+json'}, headers={'Accept':'application/sparql-results+json'}, timeout=(10,60))
                r.raise_for_status()
                rows=[{k:v['value'] for k,v in x.items()} for x in r.json()['results']['bindings']]
                if len(rows)>=100: raise ValueError('Possible 100-row truncation')
                return rows
            except (requests.RequestException, ValueError, KeyError, TypeError) as e: error=str(e)
            finally: time.sleep(self.delay*(attempt+1))
        raise RuntimeError(error)

def collect(client, keys, name, key, build, batch, offline=False):
    cache=read_cache(name,key)
    pending=sorted(set(keys)-{k for k,v in cache.items() if v['status']=='ok'})
    if offline and pending: raise RuntimeError(f'{name}: {len(pending)} missing/failed cache entries')
    def run(items):
        try: rows=client.query(build(items))
        except RuntimeError as e:
            if len(items)>1:
                mid=len(items)//2; run(items[:mid]); run(items[mid:]); return
            records=[{key:items[0],'status':'error','error':str(e)}]
        else:
            records=[{key:k,'status':'ok','rows':[r for r in rows if r.get(key)==k]} for k in items]
        for r in records:
            r['version']=VERSION; append(name,r); cache[r[key]]=r
        print(name, len(items), records[0]['status'], flush=True)
    for i in range(0,len(pending),batch): run(pending[i:i+batch])
    return cache

def literals(xs): return ' '.join(json.dumps(x) for x in xs)
def uris(xs): return ' '.join('<'+x+'>' for x in xs)
def isbn_query(xs):
    return 'SELECT DISTINCT ?isbn ?book WHERE { VALUES ?isbn { '+literals(xs)+' } ?book bibo:isbn ?isbn . }'
def book_query(xs):
    return '''SELECT DISTINCT ?book ?predicate ?value WHERE { VALUES ?book { '''+uris(xs)+''' }
    VALUES ?predicate { a dcterms:creator rdfs:label }
    ?book ?predicate ?value . }'''.replace('VALUES ?predicate { a ', 'VALUES ?predicate { <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ')
def inverse_query(xs):
    return 'SELECT DISTINCT ?book ?author WHERE { VALUES ?book { '+uris(xs)+' } ?author nlon:create ?book . }'

def main():
    p=argparse.ArgumentParser(); p.add_argument('--limit',type=int,default=30); p.add_argument('--batch-size',type=int,default=3); p.add_argument('--delay',type=float,default=1.5); p.add_argument('--reclassify-only',action='store_true'); p.add_argument('--probe',action='store_true'); a=p.parse_args()
    if a.limit<0 or a.batch_size<1 or a.delay<1: p.error('invalid arguments')
    client=Client(a.delay); targets=load_targets() if not a.probe else [{'isbn13':'9788937460586'}]
    ids=list(dict.fromkeys(r['isbn13'] for r in targets if isbn_forms(r['isbn13'])))
    if a.limit: ids=ids[:a.limit]
    forms={x for i in ids for x in isbn_forms(i)}
    def get(keys,name,key,query): return collect(client,keys,name,key,query,a.batch_size,a.reclassify_only)
    ic=get(forms,'isbn_lookup_raw.jsonl','isbn',isbn_query)
    books={r['book'] for f in forms for r in ic[f].get('rows',[]) if r.get('book')}
    bc=get(books,'book_creator_raw.jsonl','book',book_query)
    paper={b for b in books if {'http://lod.nl.go.kr/ontology/Book','http://lod.nl.go.kr/ontology/OfflineMaterial'} <= {r['value'] for r in bc[b].get('rows',[]) if r['predicate'].endswith('#type')} and not any(r['value'].endswith('/OnlineMaterial') for r in bc[b].get('rows',[]) if r['predicate'].endswith('#type'))}
    iv=get(paper,'book_inverse_raw.jsonl','book',inverse_query)
    if a.probe or a.limit:
        print('\n[서지 조회 상세]')
        for isbn in ids:
            for form in isbn_forms(isbn):
                for r in ic.get(form,{}).get('rows',[]):
                    b=r['book']; kinds={x['value'].rsplit('/',1)[-1] for x in bc.get(b,{}).get('rows',[]) if x.get('predicate','').endswith('#type')}
                    print(f'ISBN={form} | 서지={b.rsplit("/",1)[-1]} | 종이책={b in paper} | 유형={",".join(sorted(kinds))}')
                    if b in paper:
                        links=[x['value'] for x in bc[b].get('rows',[]) if x.get('predicate','').endswith('/creator') and x['value'].startswith(BASE+'KAC')]
                        links += [x['author'] for x in iv.get(b,{}).get('rows',[]) if x.get('author','').startswith(BASE+'KAC')]
                        print('  KAC 후보:', ', '.join(sorted({x.rsplit('/',1)[-1] for x in links})) or '없음')
    print(json.dumps({'isbn_forms':len(forms),'books':len(books),'paper_books':len(paper),'errors':sum(v[k]['status']!='ok' for v,ks in [(ic,forms),(bc,books),(iv,paper)] for k in ks)},indent=2))

if __name__=='__main__': main()
