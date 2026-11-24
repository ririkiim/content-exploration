"""Reconcile KMDb/TMDb screen PERSON credits without modifying the database.

Input: structured_relations/data/02_screen_person_normalized.jsonl
Outputs: structured_relations/data/03_person_groups.jsonl
         structured_relations/data/03_content_person_relations.jsonl
         structured_relations/data/03_person_review.jsonl

IDs are authoritative only within their own source. Cross-source identity requires
shared movie+role and name agreement, with reciprocal evidence-based selection.
A person may have multiple roles. No transliteration or book-author matching.
ID-less unmatched KMDb credits are excluded. Python 3.10+, standard library.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data'
DEFAULT_INPUT = DATA / '02_screen_person_normalized.jsonl'
ROLES = {'director', 'cast', 'screenwriter', 'original_author'}


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def identity(row):
    value = row.get('source_person_key')
    return value if isinstance(value, str) and value else None


def keys(row, kind):
    field = 'key' if kind == 'strict' else 'loose_key'
    result = {v[field] for v in row.get('name_variants', [])
              if isinstance(v, dict) and isinstance(v.get(field), str) and v[field]}
    # Compatible with 02 output if name_variants is absent.
    if not result:
        for prefix in ('name', 'original_name'):
            val = row.get(prefix + ('_key' if kind == 'strict' else '_loose_key'))
            if isinstance(val, str) and val:
                result.add(val)
    return result


def evidence(row):
    return {
        'source': row['source'], 'source_content_id': row.get('source_content_id'),
        'source_path': row.get('source_path'), 'credit_id': row.get('credit_id'),
        'role': row['role'], 'name': row.get('name'),
        'original_name': row.get('original_name'),
        'billing_order': row.get('billing_order'),
    }


class DSU:
    def __init__(self, ids):
        self.parent = {x: x for x in ids}
        self.members = {x: {x} for x in ids}

    def find(self, x):
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return True
        combined = self.members[a] | self.members[b]
        if sum(x.startswith('kmdb:') for x in combined) > 1:
            return False
        if sum(x.startswith('tmdb:') for x in combined) > 1:
            return False
        if len(self.members[a]) < len(self.members[b]):
            a, b = b, a
        self.parent[b] = a
        self.members[a] = combined
        del self.members[b]
        return True


def context_matches(krows, trows):
    """Return pair -> best match strength within one content+role context.

    Strict matching wins over loose matching. Ambiguous aliases are NOT treated
    as a unique match; all plausible pairs are kept for global scoring/review.
    """
    kmap, tmap = defaultdict(set), defaultdict(set)
    k_by_key, t_by_key = {}, {}
    for r in krows:
        key = identity(r) or f'noid:{r["_idx"]}'
        k_by_key[key] = r
        for kind in ('strict', 'loose'):
            for name in keys(r, kind):
                kmap[(kind, name)].add(key)
    for r in trows:
        key = identity(r)
        if not key:
            continue
        t_by_key[key] = r
        for kind in ('strict', 'loose'):
            for name in keys(r, kind):
                tmap[(kind, name)].add(key)
    result = {}
    for namekey in kmap.keys() & tmap.keys():
        kind = namekey[0]
        for k in kmap[namekey]:
            for t in tmap[namekey]:
                pair = (k, t)
                score = 2 if kind == 'strict' else 1
                result[pair] = max(score, result.get(pair, 0))
    return result


def candidate_summary(k, t, contexts, rows_by_id, limit=5):
    examples = sorted(contexts, key=lambda x: (x[0], x[1]))
    return {
        'kmdb_person_key': k, 'tmdb_person_key': t,
        'kmdb_names': sorted({n for i in rows_by_id.get(k, [])
                              for n in (i.get('name'), i.get('original_name')) if n}),
        'tmdb_names': sorted({n for i in rows_by_id.get(t, [])
                              for n in (i.get('name'), i.get('original_name')) if n}),
        'common_content_role_count': len(contexts),
        'strict_content_role_count': sum(s == 2 for _, _, s in contexts),
        'loose_only_content_role_count': sum(s == 1 for _, _, s in contexts),
        'common_examples': [{'content_id': cid, 'role': role, 'match_type': 'strict' if s == 2 else 'loose'}
                            for cid, role, s in examples[:limit]],
    }


def rank_key(contexts):
    """Primary: strict supporting contexts; secondary: all contexts."""
    return (sum(s == 2 for _, _, s in contexts), len(contexts))


def choose_winner(options):
    """Select a clear winner, or None.

    Unique candidate: at least one strict same-content+role match.
    Competing candidates: winner must have >=2 strict contexts, strictly more
    strict contexts than runner-up, and >=2x runner-up's strict support.
    Loose-only evidence is kept for review, never sufficient for ID merging.
    """
    ordered = sorted(options, key=lambda x: (rank_key(x[1]), x[0]), reverse=True)
    if not ordered:
        return None
    best_id, best_ctx = ordered[0]
    best_strict, _ = rank_key(best_ctx)
    if not best_strict:
        return None
    if len(ordered) == 1:
        return best_id
    runner_strict, _ = rank_key(ordered[1][1])
    if best_strict >= 2 and best_strict > runner_strict and best_strict >= 2 * max(1, runner_strict):
        return best_id
    return None


def atomic_write(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                      prefix=path.name + '.', suffix='.tmp',
                                      dir=path.parent, delete=False)
    try:
        with tmp as f:
            for record in records:
                f.write(dump(record) + '\n')
        os.replace(tmp.name, path)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)


def run(input_path, outdir, cast_limit):
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    outpaths = {
        'groups': outdir / '03_person_groups.jsonl',
        'relations': outdir / '03_content_person_relations.jsonl',
        'review': outdir / '03_person_review.jsonl',
    }
    if input_path.resolve() in {p.resolve() for p in outpaths.values()}:
        raise ValueError('Input must not overlap output')
    stats = Counter()
    rows = []
    by_context = defaultdict(lambda: {'kmdb': [], 'tmdb': []})
    rows_by_id = defaultdict(list)
    with input_path.open(encoding='utf-8-sig') as fin:
        for lineno, line in enumerate(fin, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('role') not in ROLES or row.get('source') not in ('kmdb', 'tmdb'):
                raise ValueError(f'Unexpected role/source at line {lineno}')
            row['_idx'] = len(rows)
            rows.append(row)
            by_context[(row['content_id'], row['role'])][row['source']].append(row)
            if identity(row):
                rows_by_id[identity(row)].append(row)
            stats['input_rows'] += 1

    dsu = DSU(rows_by_id.keys())
    # Each pair's evidence is a set of distinct (content, role, strength).
    pair_evidence = defaultdict(dict)
    noid_candidates = defaultdict(dict)
    for (cid, role), sides in sorted(by_context.items()):
        if not sides['kmdb'] or not sides['tmdb']:
            continue
        for (k, t), strength in context_matches(sides['kmdb'], sides['tmdb']).items():
            if k.startswith('noid:'):
                idx = int(k[5:])
                noid_candidates[idx][t] = max(strength, noid_candidates[idx].get(t, 0))
            else:
                pair_evidence[(k, t)][(cid, role)] = max(strength, pair_evidence[(k, t)].get((cid, role), 0))

    pair_contexts = {pair: [(cid, role, strength) for (cid, role), strength in evidence.items()]
                     for pair, evidence in pair_evidence.items()}
    k_options, t_options = defaultdict(list), defaultdict(list)
    for (k, t), contexts in pair_contexts.items():
        k_options[k].append((t, contexts))
        t_options[t].append((k, contexts))
    k_winners = {k: choose_winner(opts) for k, opts in k_options.items()}
    t_winners = {t: choose_winner(opts) for t, opts in t_options.items()}
    matched = {}
    for k in sorted(k_options):
        t = k_winners[k]
        if t is not None and t_winners.get(t) == k:
            if dsu.union(k, t):
                matched[k] = t
                stats['cross_id_merged'] += 1
            else:
                stats['cross_id_conflicts'] += 1
        else:
            stats['cross_id_not_confirmed'] += 1
    stats['cross_id_multiple_targets'] = sum(len(opts) > 1 for opts in k_options.values())
    stats['cross_id_multiple_sources'] = sum(len(opts) > 1 for opts in t_options.values())

    # Attach ID-less KMDb credits only with a unique strict match, and only if
    # that TMDb ID is not contested by an identified KMDb ID in this context.
    attached = {}
    for idx, candidates in noid_candidates.items():
        strict_targets = {t for t, s in candidates.items() if s == 2}
        if len(strict_targets) != 1:
            stats['noid_ambiguous_or_loose_only'] += 1
            continue
        t = next(iter(strict_targets))
        row = rows[idx]
        cid, role = row['content_id'], row['role']
        if any((cid, role) in pair_evidence[(k, t)] for k, _ in t_options.get(t, [])):
            stats['noid_competing_identified_kmdb'] += 1
            continue
        attached[idx] = t
        stats['noid_attached_to_tmdb'] += 1

    groups = defaultdict(list)
    for idx, row in enumerate(rows):
        src = identity(row)
        if src:
            root = dsu.find(src)
        elif idx in attached:
            root = dsu.find(attached[idx])
        else:
            continue
        groups[root].append(idx)

    group_records, group_id, display_by_gid = [], {}, {}
    for root, indexes in groups.items():
        ids = sorted(dsu.members[root])
        kmdb_hangul = [rows[i]['name'] for i in indexes if rows[i]['source'] == 'kmdb'
                       and rows[i].get('name_script') == 'hangul' and rows[i].get('name')]
        any_hangul = [rows[i]['name'] for i in indexes if rows[i].get('name_script') == 'hangul'
                      and rows[i].get('name')]
        names = [rows[i].get('name') or rows[i].get('original_name') for i in indexes]
        display = (kmdb_hangul or any_hangul or names)[0]
        gid = '|'.join(ids)
        group_id[root] = gid
        display_by_gid[gid] = display
        aliases = sorted({v.get('value') for i in indexes for v in rows[i].get('name_variants', [])
                          if isinstance(v, dict) and v.get('value')})
        group_records.append({
            'person_group_key': gid, 'display_name': display,
            'kmdb_person_id': next((x[5:] for x in ids if x.startswith('kmdb:')), None),
            'tmdb_person_id': next((x[5:] for x in ids if x.startswith('tmdb:')), None),
            'name_variants': aliases, 'credit_count': len(indexes),
            'roles': sorted({rows[i]['role'] for i in indexes}),
            'match_status': 'cross_source_matched' if len(ids) == 2 else 'source_id_only',
            'db_insert_ready': False,
        })

    rels = defaultdict(list)
    for root, indexes in groups.items():
        gid = group_id[root]
        for i in indexes:
            r = rows[i]
            rels[(r['content_id'], r['content_type'], r['role'], gid)].append(r)
    selected, casts = [], defaultdict(list)
    for (cid, ctype, role, gid), credits in rels.items():
        order_values = [r['billing_order'] for r in credits
                        if type(r.get('billing_order')) is int and r['billing_order'] >= 0]
        record = {
            'content_id': cid, 'content_type': ctype, 'link_role': role,
            'person_group_key': gid, 'display_name': display_by_gid[gid],
            'billing_order_min': min(order_values) if order_values else None,
            'sources': sorted({r['source'] for r in credits}),
            'evidence': [evidence(r) for r in credits],
            'fact_status_proposal': 'proposed', 'db_insert_ready': False,
        }
        if role == 'cast':
            casts[cid].append(record)
        else:
            selected.append(record)
    for cid, candidates in casts.items():
        candidates.sort(key=lambda r: (
                    r['billing_order_min'] if r['billing_order_min'] is not None else 10**9,
                    -len(r['sources']),
                    r['person_group_key'] ))
        selected.extend(candidates[:cast_limit])
        stats['cast_excluded_final_limit'] += max(0, len(candidates) - cast_limit)

    reviews = []
    for idx, row in enumerate(rows):
        if row['source'] == 'kmdb' and not identity(row) and idx not in attached:
            reviews.append({
                'reason': 'kmdb_no_id_unmatched_or_ambiguous',
                'content_id': row['content_id'], 'role': row['role'],
                'name': row.get('name'), 'original_name': row.get('original_name'),
                'source_path': row.get('source_path'), 'entity_insert': False,
            })
            stats['noid_excluded'] += 1
    for k in sorted(k_options):
        if k in matched:
            continue
        opts = sorted(k_options[k], key=lambda x: (rank_key(x[1]), x[0]), reverse=True)
        reviews.append({
            'reason': 'cross_source_id_match_not_confirmed',
            'kmdb_person_key': k,
            'kmdb_names': sorted({n for r in rows_by_id[k]
                                  for n in (r.get('name'), r.get('original_name')) if n}),
            'tmdb_candidates': [candidate_summary(k, t, contexts, rows_by_id) for t, contexts in opts],
            'preferred_tmdb_candidate': k_winners[k],
            'reciprocal_confirmed': False,
            'entity_insert': False,
            'note': 'Cross-source merge not confirmed; source-ID person groups remain available.',
        })

    payloads = {
        'groups': sorted(group_records, key=lambda x: x['person_group_key']),
        'relations': sorted(selected, key=lambda x: (x['content_id'], x['link_role'], x['person_group_key'])),
        'review': reviews,
    }
    for label, records in payloads.items():
        atomic_write(outpaths[label], records)
        stats['output_' + label] = len(records)
    stats['identified_person_groups'] = len(groups)
    stats['cross_source_groups'] = sum(len(dsu.members[root]) == 2 for root in groups)
    return outpaths, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--outdir', type=Path, default=DATA)
    parser.add_argument('--cast-limit', type=int, default=15)
    args = parser.parse_args()
    if args.cast_limit < 1:
        parser.error('--cast-limit must be >= 1')
    print('[INFO] Loading normalized credits...', flush=True)
    outputs, stats = run(args.input, args.outdir, args.cast_limit)
    print(dump({
        'input': str(args.input), 'outputs': {k: str(v) for k, v in outputs.items()},
        'database_changed': False, 'transliteration_used': False,
        'book_author_matching': False, 'cast_limit_per_content': args.cast_limit,
        'counts': dict(sorted(stats.items())),
    }))


if __name__ == '__main__':
    main()
