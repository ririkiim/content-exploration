"""NLK LOD title/author fallback and year-adjusted evaluation.

Does not modify DB or 01/02 results.
Run from project root:
 python structured_relations/extraction/book/03_match_unmatched_authors.py --limit 10
 python structured_relations/extraction/book/03_match_unmatched_authors.py --limit 0
"""
import argparse
import importlib
import json
import re
from collections import Counter, defaultdict

m = importlib.import_module('01_collect_author_authority')
a = importlib.import_module('02_enrich_author_aliases')

TARGET_STATUSES = {
    'isbn_not_found', 'authority_not_found', 'no_confirmed_paper_book',
    'author_name_mismatch', 'invalid_isbn',
}
BOOK_TYPE = 'http://lod.nl.go.kr/ontology/Book'
PAPER_TYPE = 'http://lod.nl.go.kr/ontology/OfflineMaterial'
ONLINE_TYPE = 'http://lod.nl.go.kr/ontology/OnlineMaterial'
RDF_TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'
KAC_RE = re.compile(re.escape(m.BASE) + r'KAC[0-9A-Z]+$')
SUCCESS = 'fallback_author_candidate'


def jsonl(path):
    if not path.exists():
        raise FileNotFoundError(f'Run 02 full mode first: {path}')
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def load_pub_years():
    """Read-only DB lookup; book_edition_id is the stable join key."""
    import os
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as conn:
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            records = conn.execute(
                'SELECT book_edition_id, pub_year FROM book_edition'
            ).fetchall()
    years = {}
    for r in records:
        value = r['pub_year']
        if value is None:
            years[r['book_edition_id']] = None
        else:
            try:
                years[r['book_edition_id']] = int(value)
            except (ValueError, TypeError):
                years[r['book_edition_id']] = None
    return years


def with_year(row, years):
    return {**row, 'pub_year': years.get(row.get('book_edition_id'))}


def norm_title(s):
    return re.sub(r'\s+', ' ', a.norm(s)).strip()


def title_variants(title):
    """Conservative search keys; variants require verification."""
    t = a.clean(title)
    result = {t}
    if re.search(r'\s*[:：]\s*', t):
        result.add(re.split(r'\s*[:：]\s*', t, maxsplit=1)[0].strip())
    v = re.fullmatch(r'(.+?)\s+(\d{1,2})\s*(?:권|부)?', t)
    if v and len(v.group(1).replace(' ', '')) >= 3:
        result.add(v.group(1).strip())
    return sorted(x for x in result if x)


def title_query(titles):
    return (
        'SELECT DISTINCT ?title ?book WHERE { VALUES ?title { '
        + m.literals(titles)
        + ' } { ?book dcterms:title ?title . } UNION '
        + '{ ?book dcterms:alternative ?title . } }'
    )


def book_query(books):
    return (
        'SELECT DISTINCT ?book ?predicate ?value WHERE { VALUES ?book { '
        + m.uris(books)
        + ' } VALUES ?predicate { <' + RDF_TYPE + '> dcterms:creator '
        + 'dcterms:title dcterms:alternative rdfs:label bibo:isbn } '
        + '?book ?predicate ?value . }'
    )


def rows(cache, key):
    return cache.get(key, {}).get('rows', [])


def ok(cache, key):
    return cache.get(key, {}).get('status') == 'ok'


def paper(rr):
    types = {r['value'] for r in rr if r['predicate'] == RDF_TYPE}
    return BOOK_TYPE in types and PAPER_TYPE in types and ONLINE_TYPE not in types


def isbn13(s):
    x = re.match(r'^\s*(97[89]\d{10})(?!\d)', s or '')
    return x.group(1) if x and m.isbn_forms(x.group(1)) else None


def match_title(requested, observed, query_key):
    n = norm_title(observed)
    if n == norm_title(requested):
        return 'exact'
    if n == norm_title(query_key) and norm_title(query_key) != norm_title(requested):
        return 'variant'
    return None


def evaluation_stats(success_rows, failure_rows, years):
    """Successes count regardless of year; only 2026 failures are excluded.

    Unknown-year failures are shown separately and excluded from adjusted rate.
    """
    successes = len(success_rows)
    failures_2026 = sum(years.get(r.get('book_edition_id')) == 2026 for r in failure_rows)
    failures_unknown = sum(years.get(r.get('book_edition_id')) is None for r in failure_rows)
    failures_other = len(failure_rows) - failures_2026 - failures_unknown
    denominator = successes + failures_other
    return {
        'success_all_years': successes,
        'failed_before_or_after_2026': failures_other,
        'failed_2026_excluded': failures_2026,
        'failed_unknown_year_separate': failures_unknown,
        'evaluated_total': denominator,
        'adjusted_success_rate_pct': round(successes / denominator * 100, 2) if denominator else None,
        'raw_success_rate_pct': round(successes / (successes + len(failure_rows)) * 100, 2)
        if successes + len(failure_rows) else None,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--limit', type=int, default=10, help='0 = all unmatched rows')
    p.add_argument('--batch-size', type=int, default=1)
    p.add_argument('--delay', type=float, default=1.5)
    p.add_argument('--reclassify-only', action='store_true')
    args = p.parse_args()
    if args.limit < 0 or args.batch_size < 1 or args.delay < 1:
        p.error('limit >= 0, batch-size >= 1, delay >= 1 required')

    years = load_pub_years()
    all_good = [with_year(r, years) for r in jsonl(m.OUT / 'author_candidates.jsonl')]
    all_bad = [with_year(r, years) for r in jsonl(m.OUT / 'unmatched_authors.jsonl')]
    candidates = [r for r in all_bad if r.get('status') in TARGET_STATUSES]
    if args.limit:
        candidates = candidates[:args.limit]
    if not candidates:
        print('No eligible unmatched rows.')
        if not args.limit:
            print(json.dumps({'overall_evaluation': evaluation_stats(all_good, all_bad, years)},
                             ensure_ascii=False, indent=2))
        return

    requested_to_keys = {
        a.clean(r['title']): title_variants(r['title'])
        for r in candidates if r.get('title')
    }
    keys = sorted({key for vals in requested_to_keys.values() for key in vals})
    client = m.Client(args.delay)
    tc = m.collect(client, keys, '03_title_lookup_raw.jsonl', 'title',
                   title_query, args.batch_size, args.reclassify_only)
    books = sorted({x['book'] for key in keys for x in rows(tc, key) if x.get('book')})
    bc = m.collect(client, books, '03_book_detail_raw.jsonl', 'book',
                   book_query, args.batch_size, args.reclassify_only)
    paper_books = sorted(b for b in books if ok(bc, b) and paper(rows(bc, b)))
    iv = m.collect(client, paper_books, '03_book_inverse_raw.jsonl', 'book',
                   m.inverse_query, args.batch_size, args.reclassify_only)
    auths = sorted({k for b in paper_books for k in
                    ([x['value'] for x in rows(bc, b) if x['predicate'].endswith('/creator')]
                     + [x['author'] for x in rows(iv, b)]) if KAC_RE.fullmatch(k)})
    ac = m.collect(client, auths, 'author_alias_raw.jsonl', 'author',
                   a.authority_query, args.batch_size, args.reclassify_only)

    out, success = [], []
    for r in candidates:
        title = a.clean(r.get('title', ''))
        name = r.get('parsed_author_name') or r.get('author_name', '')
        if not r.get('parsed_author_name'):
            parsed, _, review = a.parse(name)
            if len(parsed) != 1 or review:
                out.append({**r, 'fallback_status': 'author_parse_review',
                            'fallback_candidates': []})
                continue
            name = parsed[0]

        search_keys = requested_to_keys.get(title, [])
        failed_keys = [key for key in search_keys if not ok(tc, key)]
        hits = defaultdict(set)
        for key in search_keys:
            for x in rows(tc, key):
                if x.get('book'):
                    hits[x['book']].add(key)
        found, incomplete = [], bool(failed_keys)
        for b, matched_keys in sorted(hits.items()):
            if not ok(bc, b):
                incomplete = True
                continue
            br = rows(bc, b)
            if not paper(br):
                continue
            if not ok(iv, b):
                incomplete = True
                continue
            observed_titles = [
                x['value'] for x in br
                if x['predicate'].endswith('/title') or x['predicate'].endswith('/alternative')
            ]
            strength = None
            for key in matched_keys:
                for observed in observed_titles:
                    s = match_title(title, observed, key)
                    if s == 'exact':
                        strength = 'exact'
                        break
                    if s == 'variant' and strength is None:
                        strength = 'variant'
                if strength == 'exact':
                    break
            if not strength:
                continue
            kacs = {x['value'] for x in br if x['predicate'].endswith('/creator')}
            kacs |= {x['author'] for x in rows(iv, b)}
            for k in sorted(x for x in kacs if KAC_RE.fullmatch(x)):
                if not ok(ac, k):
                    incomplete = True
                    continue
                rr = rows(ac, k)
                names = [x['value'] for x in rr if not x['predicate'].endswith('/isni')]
                if not any(a.variants(name) & a.variants(n) for n in names):
                    continue
                raw_isni = [x['value'] for x in rr if x['predicate'].endswith('/isni')]
                valid = sorted({v for s in raw_isni if (v := a.valid_isni(s))})
                found.append({
                    'book': b, 'kac': k.rsplit('/', 1)[-1], 'isni': valid,
                    'invalid_isni': [s for s in raw_isni if not a.valid_isni(s)],
                    'matched_names': names, 'title_match': strength,
                    'isbn13': sorted({v for x in br if x['predicate'].endswith('/isbn')
                                      if (v := isbn13(x['value']))}),
                })

        kacs = {x['kac'] for x in found}
        isnis = {s for x in found for s in x['isni']}
        invalid = any(x['invalid_isni'] for x in found)
        if incomplete:
            status = 'lookup_incomplete'
        elif not hits:
            status = 'title_not_found'
        elif not found:
            status = 'no_verified_author_candidate'
        elif invalid or len(isnis) > 1:
            status = 'identifier_conflict'
        elif len(kacs) == 1:
            status = SUCCESS
        elif len(isnis) == 1 and all(x['isni'] == sorted(isnis) for x in found):
            status = SUCCESS
        else:
            status = 'multiple_identities_review'

        result = {**r, 'fallback_status': status, 'fallback_candidates': found,
                  'fallback_search_keys': search_keys}
        if status == SUCCESS:
            all_kacs = sorted(kacs)
            all_isnis = sorted(isnis)
            result['fallback_identity_key'] = (
                'ISNI:' + all_isnis[0] if all_isnis else 'KAC:' + all_kacs[0]
            )
            result['fallback_kac_ids'] = all_kacs
            result['fallback_isni'] = all_isnis
            result['fallback_matched_books'] = len({x['book'] for x in found})
            result['fallback_title_match'] = (
                'exact' if any(x['title_match'] == 'exact' for x in found) else 'variant'
            )
            success.append(result)
        out.append(result)

    failed = [x for x in out if x['fallback_status'] != SUCCESS]
    result_summary = {
        'processed': len(out),
        'matched': len(success),
        'statuses': dict(Counter(x['fallback_status'] for x in out)),
        'fallback_evaluation': evaluation_stats(success, failed, years),
    }
    if not args.limit:
        # All unmatched rows are included, even statuses not handled by 03.
        # 02's successful rows are never in unmatched_authors.jsonl.
        skipped_bad = [r for r in all_bad if r.get('status') not in TARGET_STATUSES]
        overall_success = all_good + success
        overall_failed = failed + skipped_bad
        result_summary['overall_evaluation'] = evaluation_stats(
            overall_success, overall_failed, years
        )
        result_summary['unhandled_02_failure_statuses'] = dict(
            Counter(r.get('status', 'unknown') for r in skipped_bad)
        )

    print(json.dumps(result_summary, ensure_ascii=False, indent=2))
    for x in out:
        print(f"[{x['fallback_status']}] {x.get('title')} | "
              f"{x.get('parsed_author_name') or x.get('author_name')} | "
              f"pub_year={x.get('pub_year')} | "
              f"candidates={len(x['fallback_candidates'])}")
    if args.limit:
        print('Limited test: final outputs not written.')
        return

    m.OUT.mkdir(parents=True, exist_ok=True)
    m.save('03_author_candidates.jsonl', success)
    m.save('03_author_review.jsonl', failed)
    (m.OUT / '03_summary.json').write_text(
        json.dumps(result_summary, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print('Saved 03_author_candidates.jsonl, 03_author_review.jsonl, 03_summary.json; DB unchanged.')
    if any(x['fallback_status'] == 'lookup_incomplete' for x in out):
        print('WARNING: Some lookups are incomplete. They were saved for review, not counted as matches.')


if __name__ == '__main__':
    main()
