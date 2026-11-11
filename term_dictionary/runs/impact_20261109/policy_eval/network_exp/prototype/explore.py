#!/usr/bin/env python3
"""Offline content-path explorer PROTOTYPE (no DB, no LLM, no Wikidata; reads edges.csv + exported content/keyword CSVs only).
content A --[keyword a]--relation--[keyword b]--> content B   (1 hop)      |  A --kw a-rel-kw m-rel-kw b--> B  (2 hops)
EXACT is an edge, NOT a merge: original keywords/sources of both works are always returned. E5-only candidates are a separate layer, excluded unless --include-candidates.
Structural guard (not a score): a 2-hop path of two BROADER edges is allowed only as an upward chain (narrower->broader->broader); sibling paths through a shared abstract parent (protest->conflict->해전) are dropped.
Scores are deliberately simple and NOT tuned: score = prod(edge weight) * HOP_DECAY^(hops-1) * 1/(1+log10(max keyword df on the path)).
Usage: python3 explore.py --content "올드보이" [--content-id N] [--pick I] [--max-hops 1|2] [--top 15] [--include-shared] [--include-candidates] [--json out.json]
       python3 explore.py --stats [--sample 300]"""
import os, sys, re, csv, json, math, hashlib, argparse, collections, statistics
HERE = os.path.dirname(os.path.abspath(__file__)); NE = os.path.dirname(HERE); H = os.path.dirname(os.path.dirname(NE))
sys.path.insert(0, H)
import impact_lib as IL
HOP_DECAY = 0.7
LAYER = {"EXACT_code": ("EXACT", "exact"), "SAME_human": ("CLOSE", "close"), "SAME_llm": ("CLOSE", "close"), "REL_qid_only": ("RELATED", "related"), "REL_translit_only": ("RELATED", "related"), "REL_compose": ("RELATED", "related"),
         "REL_llm_unsure": ("RELATED", "related"), "DERIV_en": ("RELATED", "related"), "BROADER_wd": ("BROADER", "related"), "CTRL_e5_only": ("CANDIDATE_E5", "candidate")}
class Net:
    def __init__(self, edges_csv=None, include_candidates=False):
        d = self.d = IL.D(); nk = self.nk = nk_ = IL.norm
        self.c2n = {c: {nk(n): n for n in names} for c, names in d.c_kws.items()}                       # content -> node -> ORIGINAL keyword text
        self.n2c = collections.defaultdict(set)
        for c, m in self.c2n.items():
            for n in m: self.n2c[n].add(c)
        self.ksrc = collections.defaultdict(set)                                                         # (content,node) -> keyword sources (kmdb/tmdb)
        for name, rows in d.by_name.items():
            for c, s in rows: self.ksrc[(c, nk(name))].add(s)
        self.adj = collections.defaultdict(list); self.n_edges = 0; self.skipped_candidates = 0
        for r in csv.DictReader(open(edges_csv or os.path.join(NE, "edges.csv"), encoding="utf-8-sig", newline="")):
            rel, layer = LAYER[r["type"]]
            if layer == "candidate" and not include_candidates: self.skipped_candidates += 1; continue
            e = dict(narrow=nk_(r["term_a"]) if r["type"] == "BROADER_wd" else None, a=r["node_a"], b=r["node_b"], term_a=r["term_a"], term_b=r["term_b"], relation=rel, layer=layer, edge_type=r["type"], weight=float(r["weight"]), evidence=r["evidence"], provenance=r["provenance"])
            self.adj[e["a"]].append((e["b"], e)); self.adj[e["b"]].append((e["a"], e)); self.n_edges += 1
    def df(self, n): return len(self.n2c.get(n, ()))
    def find(self, q, content_id=None):
        if content_id: return [c for c in self.d.content if c == str(content_id)]
        qn = self.nk(q); out = []
        for c, r in self.d.content.items():
            if qn and (qn in self.nk(r["title"]) or qn in self.nk(r["original_title"] or "")): out.append(c)
        return sorted(out, key=lambda c: (self.nk(self.d.content[c]["title"]) != qn, -len(self.c2n.get(c, {}))))[:20]
    def cinfo(self, c): r = self.d.content[c]; return dict(content_id=c, title=r["title"], original_title=r["original_title"], year=r["release_year"], type=r["content_type"], sources=sorted(self.d.csrc.get(c, [])), n_keywords=len(self.c2n.get(c, {})))
    def explore(self, c, max_hops=1, include_shared=False):
        """returns (results sorted by score, stats). Best path per end content; alt_paths counts other paths to the same content."""
        start_nodes = self.c2n.get(c, {}); best = {}; alts = collections.Counter(); shared_excluded = set()
        def consider(path, nodes_end, max_df, hops):
            sc = 1.0
            for e in path: sc *= e["weight"]
            sc *= HOP_DECAY ** (hops - 1) / (1 + math.log10(max(1, max_df)))
            for c2 in self.n2c.get(nodes_end, ()):
                if c2 == c: continue
                if not include_shared and (self.c2n[c2].keys() & start_nodes.keys()): shared_excluded.add(c2); continue
                key = c2; alts[key] += 1
                if key not in best or sc > best[key][0]: best[key] = (sc, path, nodes_end, hops)
        for n1 in start_nodes:
            for (n2, e1) in self.adj.get(n1, ()):
                path1 = [dict(e1, frm=n1, to=n2)]
                consider(path1, n2, max(self.df(n1), self.df(n2)), 1)
                if max_hops >= 2:
                    for (n3, e2) in self.adj.get(n2, ()):
                        if n3 == n1 or n3 == n2: continue
                        if e1["relation"] == "BROADER" and e2["relation"] == "BROADER" and not (e1["narrow"] == n1 and e2["narrow"] == n2): continue     # structural guard: BROADER+BROADER only as an upward chain (no sibling-via-abstract-parent / parent-to-child drift)
                        consider(path1 + [dict(e2, frm=n2, to=n3)], n3, max(self.df(n1), self.df(n2), self.df(n3)), 2)
        res = []
        for c2, (sc, path, nend, hops) in best.items():
            hopsd = []
            for e in path:
                hopsd.append(dict(from_keyword=self.nodename(e["frm"], c, c2), to_keyword=self.nodename(e["to"], c, c2), relation=e["relation"], layer=e["layer"], edge_type=e["edge_type"], weight=e["weight"], evidence=e["evidence"], provenance=e["provenance"],
                                  df_from=self.df(e["frm"]), df_to=self.df(e["to"]), note=("narrower→broader" if e["relation"] == "BROADER" else "")))
            a_src = set().union(*[self.ksrc.get((c, path[0]["frm"]), set())]); b_src = self.ksrc.get((c2, nend), set())
            cs_a, cs_b = self.d.csrc.get(c, set()), self.d.csrc.get(c2, set())
            res.append(dict(score=round(sc, 4), hops=hops, alt_paths_to_this_work=alts[c2], start_keyword=self.c2n[c][path[0]["frm"]], start_keyword_source=sorted(a_src), path=hopsd, end_keyword=self.c2n[c2][nend], end_keyword_source=sorted(b_src),
                            end_work=self.cinfo(c2), cross_source=bool(cs_a and cs_b and (cs_a ^ cs_b) and not (cs_a & cs_b))))
        res.sort(key=lambda r: (-r["score"], r["hops"], r["end_work"]["title"]))
        return res, dict(n_results=len(res), excluded_already_sharing_keyword=len(shared_excluded), start_keywords=len(start_nodes), start_keywords_with_edges=sum(1 for n in start_nodes if n in self.adj))
    def nodename(self, node, c, c2):
        return self.c2n.get(c, {}).get(node) or self.c2n.get(c2, {}).get(node) or node
def fmt(res, top):
    out = []
    for i, r in enumerate(res[:top], 1):
        p = r["path"]; chain = f'[{r["start_keyword"]}]'
        for h in p: chain += f' —{h["relation"]}({h["weight"]})→ [{h["to_keyword"] if h is not p[-1] else r["end_keyword"]}]'
        out.append(f'{i:>2}. {r["score"]:.3f}  {r["end_work"]["title"]} ({r["end_work"]["year"]}, {r["end_work"]["type"]}, {"/".join(r["end_work"]["sources"])})  hops={r["hops"]}{"  CROSS-SOURCE" if r["cross_source"] else ""}\n      {chain}\n      근거: ' + " | ".join(f'{h["evidence"]} [{h["provenance"]}]' for h in p))
    return "\n".join(out)
def stats(net, n_sample):
    cs = sorted(net.c2n, key=lambda c: hashlib.sha1(("st|" + c).encode()).hexdigest())[:n_sample]; out = {}
    for hops in (1, 2):
        cnt, top10_rel, top10_cross, top10_n = [], collections.Counter(), 0, 0; zero = 0
        for c in cs:
            res, _ = net.explore(c, hops)
            cnt.append(len(res)); zero += (len(res) == 0)
            for r in res[:10]:
                top10_n += 1; top10_cross += r["cross_source"]; top10_rel[r["path"][-1]["relation"] if hops == 1 else "+".join(h["relation"] for h in r["path"])] += 1
        out[f"max_hops={hops}"] = dict(sample=len(cs), start_works_with_zero_new_results=zero, share_with_results=round(1 - zero / len(cs), 3), median_results=statistics.median(cnt), p90_results=sorted(cnt)[int(.9 * len(cnt))], top10_cross_source_share=round(top10_cross / max(1, top10_n), 3), top10_relation_mix=dict(top10_rel.most_common(8)))
    return out
if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--content", action="append", default=[]); ap.add_argument("--content-id"); ap.add_argument("--pick", type=int, default=0); ap.add_argument("--max-hops", type=int, default=1, choices=[1, 2])
    ap.add_argument("--top", type=int, default=15); ap.add_argument("--include-shared", action="store_true"); ap.add_argument("--include-candidates", action="store_true"); ap.add_argument("--json"); ap.add_argument("--only-hops", type=int, choices=[1, 2], help="show only paths with exactly this many keyword-relation hops"); ap.add_argument("--stats", action="store_true"); ap.add_argument("--sample", type=int, default=300)
    a = ap.parse_args(); net = Net(include_candidates=a.include_candidates)
    print(f"[net] works with keywords={len(net.c2n)} keyword nodes={len(net.n2c)} edges={net.n_edges} (E5 candidate edges excluded: {net.skipped_candidates})", file=sys.stderr)
    if a.stats:
        s = stats(net, a.sample); print(json.dumps(s, ensure_ascii=False, indent=1))
        if a.json: json.dump(s, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        sys.exit(0)
    payload = []
    for q in (a.content or [None]):
        cands = net.find(q or "", a.content_id)
        if not cands: print(f"no work matches {q or a.content_id!r}"); continue
        if len(cands) > 1: print(f'matches for "{q}": ' + "; ".join(f'{i}:{net.d.content[c]["title"]}({net.d.content[c]["release_year"]},id={c},kw={len(net.c2n.get(c, {}))})' for i, c in enumerate(cands[:8])))
        c = cands[min(a.pick, len(cands) - 1)]; info = net.cinfo(c)
        if not net.c2n.get(c): print(f'"{info["title"]}" has no keywords in DB export -> cannot start a keyword path (53% of works have none).'); payload.append(dict(start_work=info, results=[], note="no keywords")); continue
        res, st = net.explore(c, a.max_hops, a.include_shared)
        if a.only_hops: res = [r for r in res if r["hops"] == a.only_hops]; st = dict(st, n_results=len(res))
        print(f'\n=== {info["title"]} ({info["year"]}, {info["type"]}, {"/".join(info["sources"])}) | keywords: {", ".join(net.c2n[c].values())}\n    hops<={a.max_hops}: {st["n_results"]} works (excluded {st["excluded_already_sharing_keyword"]} already sharing a keyword); start keywords with relation edges: {st["start_keywords_with_edges"]}/{st["start_keywords"]}')
        print(fmt(res, a.top)); payload.append(dict(start_work=info, start_keywords=list(net.c2n[c].values()), max_hops=a.max_hops, stats=st, results=res[:a.top]))
    if a.json: json.dump(payload, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
