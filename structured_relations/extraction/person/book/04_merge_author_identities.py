"""Merge verified NLK author matches and optionally load them into PostgreSQL.

Default: read-only preview. --apply: transactional DB insertion.
No API calls, no schema changes, no Wikidata lookup.
Requires: psycopg>=3, python-dotenv
"""
import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from dotenv import load_dotenv

VERSION = "nlk-author-merge-v1"
ROOT = next((p for p in Path(__file__).resolve().parents if (p / ".env").exists()), Path.cwd())
load_dotenv(ROOT / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

def read_jsonl(path):
    if not path.is_file():
        raise FileNotFoundError(f"입력 파일이 없습니다: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]

def normalize_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip())

def normalize_ids(values, prefix):
    return sorted({str(x).strip() for x in (values or []) if str(x).strip().startswith(prefix)})

def get_match(row, source):
    if source == "02":
        if row.get("status") != "isbn_author_candidate":
            return None
        kac = normalize_ids(row.get("kac_ids"), "KAC")
        isni = sorted(set(str(x).replace(" ", "") for x in row.get("isni", []) if x))
        ev = row.get("evidence", [])
        chosen = [c for c in row.get("all_candidates", []) if c.get("author", "").rsplit("/", 1)[-1] in kac]
        aliases = [n for c in chosen for n in c.get("names", [])]
        method = "isbn_direct"
        match_type = "isbn"
        books = sorted({e.get("book") for e in ev if e.get("book")})
        raw = {"evidence": ev, "selected_candidates": chosen}
    else:
        if row.get("fallback_status") != "fallback_author_candidate":
            return None
        kac = normalize_ids(row.get("fallback_kac_ids"), "KAC")
        isni = sorted(set(str(x).replace(" ", "") for x in row.get("fallback_isni", []) if x))
        selected = row.get("fallback_candidates", [])
        aliases = [n for c in selected for n in c.get("matched_names", [])]
        method = "title_author_fallback"
        match_type = row.get("fallback_title_match") or "unknown"
        books = sorted({c.get("book") for c in selected if c.get("book")})
        raw = {"fallback_candidates": selected, "fallback_search_keys": row.get("fallback_search_keys", [])}
    if not kac or len(isni) > 1:
        raise ValueError(f"식별자 불완전/복수 ISNI: book_edition_id={row.get('book_edition_id')}")
    key = f"ISNI:{isni[0]}" if isni else f"KAC:{kac[0]}"
    supplied = row.get("identity_key") if source == "02" else row.get("fallback_identity_key")
    if supplied and supplied != key:
        raise ValueError(f"identity_key 불일치: {supplied} != {key}")
    name = normalize_name(row.get("author_name"))
    if not name:
        raise ValueError(f"저자명 없음: book_edition_id={row.get('book_edition_id')}")
    return {
        "book_edition_id": int(row["book_edition_id"]),
        "content_id": int(row["content_id"]),
        "title": row.get("title"),
        "isbn13": row.get("isbn13"),
        "author_name": name,
        "identity_key": key,
        "kac_ids": kac,
        "isni": isni,
        "aliases": sorted({normalize_name(x) for x in aliases if normalize_name(x)}),
        "source_stage": source,
        "match_method": method,
        "title_match": match_type,
        "matched_books": books,
        "raw_evidence": raw,
    }

def merge_rows(rows02, rows03):
    selected = []
    errors = []
    for stage, rows in (("02", rows02), ("03", rows03)):
        for row in rows:
            try:
                match = get_match(row, stage)
                if match:
                    selected.append(match)
            except (ValueError, TypeError, KeyError) as exc:
                errors.append({"stage": stage, "book_edition_id": row.get("book_edition_id"), "error": str(exc)})
    # An identity must never silently span conflicting ISNIs, or a KAC map to multiple ISNIs.
    kac_to_isni = defaultdict(set)
    for r in selected:
        for k in r["kac_ids"]:
            kac_to_isni[k].add(r["isni"][0] if r["isni"] else "")
    ambiguous_kac = {k for k, v in kac_to_isni.items() if len(v) > 1}
    # Same visible name but different IDs: do not merge or create duplicate indistinguishable PERSON names automatically.
    name_to_keys = defaultdict(set)
    for r in selected:
        name_to_keys[r["author_name"]].add(r["identity_key"])
    ambiguous_names = {n for n, keys in name_to_keys.items() if len(keys) > 1}
    # Same author/content relation must not point to multiple identities.
    content_to_keys = defaultdict(set)
    for r in selected:
        content_to_keys[r["content_id"], r["author_name"]].add(r["identity_key"])
    ambiguous_relations = {x for x, keys in content_to_keys.items() if len(keys) > 1}
    safe, review = [], []
    for r in selected:
        reasons = []
        if any(k in ambiguous_kac for k in r["kac_ids"]):
            reasons.append("kac_identity_conflict")
        if r["author_name"] in ambiguous_names:
            reasons.append("same_name_different_identity")
        if (r["content_id"], r["author_name"]) in ambiguous_relations:
            reasons.append("content_author_identity_conflict")
        if reasons:
            review.append({**r, "review_reasons": reasons})
        else:
            safe.append(r)
    # Drop exact duplicate input rows, retaining one record per edition, author and method.
    uniq = {}
    for r in safe:
        k = (r["book_edition_id"], r["identity_key"], r["match_method"])
        if k in uniq and uniq[k] != r:
            review.append({**r, "review_reasons": ["duplicate_input_disagreement"]})
            review.append({**uniq.pop(k), "review_reasons": ["duplicate_input_disagreement"]})
        elif k not in uniq:
            uniq[k] = r
    safe = list(uniq.values())
    return safe, review, errors

def identity_groups(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[r["identity_key"]].append(r)
    # ISNI and KAC identity keys may refer to same KAC. Merge KAC-only group into an ISNI group
    # only when a single ISNI group contains that KAC.
    kac_to_isni = defaultdict(set)
    for key, items in groups.items():
        if key.startswith("ISNI:"):
            for r in items:
                for kac in r["kac_ids"]:
                    kac_to_isni[kac].add(key)
    result = defaultdict(list)
    for key, items in groups.items():
        target = key
        if key.startswith("KAC:") and len(kac_to_isni.get(key[4:], set())) == 1:
            target = next(iter(kac_to_isni[key[4:]]))
        result[target].extend(items)
    return dict(result)

def output_files(out, safe, review, errors, summary):
    out.mkdir(parents=True, exist_ok=True)
    for filename, rows in (
        ("04_author_merged.jsonl", safe),
        ("04_author_review.jsonl", review),
        ("04_author_errors.jsonl", errors),
    ):
        tmp = out / (filename + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        tmp.replace(out / filename)
    (out / "04_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

def load_db_snapshot(conn, safe):
    ids = sorted({r["content_id"] for r in safe})
    editions = sorted({r["book_edition_id"] for r in safe})
    with conn.cursor() as cur:
        counts = cur.execute("""
            SELECT
              (SELECT count(*) FROM public.entity WHERE entity_type='PERSON') person_count,
              (SELECT count(*) FROM public.content_entity WHERE link_role='author') author_count
        """).fetchone()
        db_content = {r["content_id"] for r in cur.execute(
            'SELECT content_id FROM public."content" WHERE content_id = ANY(%s)', (ids,)
        ).fetchall()}
        db_editions = {r["book_edition_id"]: r["content_id"] for r in cur.execute(
            "SELECT book_edition_id, content_id FROM public.book_edition WHERE book_edition_id = ANY(%s)",
            (editions,),
        ).fetchall()}
    missing = [r for r in safe if r["content_id"] not in db_content or db_editions.get(r["book_edition_id"]) != r["content_id"]]
    return counts, missing

def apply_rows(conn, safe):
    groups = identity_groups(safe)
    # Explicitly refuse to load into a populated PERSON/author DB; avoids guessing existing identities.
    # --apply is designed for the empty baseline the user confirmed.
    counts, missing = load_db_snapshot(conn, safe)
    if counts["person_count"] or counts["author_count"]:
        raise RuntimeError("이미 PERSON/author 데이터가 있습니다. 중복 적재 방지를 위해 중단합니다. 재실행 시 기존 적재 내역을 먼저 확인하세요.")
    if missing:
        raise RuntimeError(f"DB에 없거나 edition-content 연결이 다른 레코드 {len(missing)}건. 적재 중단.")
    created_entities = 0
    created_relations = 0
    created_evidence = 0
    created_aliases = 0
    with conn.cursor() as cur:
        identity_to_id = {}
        for key, items in sorted(groups.items()):
            names = Counter(r["author_name"] for r in items)
            name = sorted(names, key=lambda n: (-names[n], len(n), n))[0]
            entity_id = cur.execute(
                """INSERT INTO public.entity (entity_name, entity_type, wikidata_qid)
                   VALUES (%s, 'PERSON', NULL) RETURNING entity_id""",
                (name,),
            ).fetchone()["entity_id"]
            identity_to_id[key] = entity_id
            created_entities += 1
            aliases = {n for r in items for n in [r["author_name"], *r["aliases"]] if n and n != name}
            for alias in sorted(aliases):
                # Do not pretend unreviewed NLK aliases are verified.
                cur.execute(
                    """INSERT INTO public.entity_alias
                       (entity_id, alias_text, normalized_alias, lang, alias_kind, source, status, source_ref)
                       VALUES (%s,%s,%s,'und','synonym','nlk_lod','proposed',%s)
                       ON CONFLICT (entity_id, normalized_alias, lang) DO NOTHING""",
                    (entity_id, alias, normalize_name(alias).casefold(), key),
                )
                created_aliases += cur.rowcount
        # One relation per content and identity, multiple edition-level evidence rows.
        relation_groups = defaultdict(list)
        for key, items in groups.items():
            for r in items:
                relation_groups[(r["content_id"], key)].append(r)
        for (content_id, key), items in sorted(relation_groups.items()):
            direct = any(r["match_method"] == "isbn_direct" for r in items)
            # Direct ISBN link: verified; title fallback: proposed until reviewed.
            fact = "verified" if direct else "proposed"
            ev_status = "checked" if direct else "unchecked"
            eligible = direct
            relation_id = cur.execute(
                """INSERT INTO public.content_entity
                   (content_id, entity_id, link_role, fact_status, evidence_status,
                    relation_basis, next_hop_eligible, policy_version)
                   VALUES (%s,%s,'author',%s,%s,'source_fact',%s,%s)
                   RETURNING content_entity_id""",
                (content_id, identity_to_id[key], fact, ev_status, eligible, VERSION),
            ).fetchone()["content_entity_id"]
            created_relations += 1
            for r in items:
                payload = {
                    "policy_version": VERSION,
                    "identity_key": key,
                    "kac_ids": r["kac_ids"],
                    "isni": r["isni"],
                    "book_edition_id": r["book_edition_id"],
                    "isbn13": r["isbn13"],
                    "match_method": r["match_method"],
                    "title_match": r["title_match"],
                    "matched_books": r["matched_books"],
                    "raw_evidence": r["raw_evidence"],
                }
                cur.execute(
                    """INSERT INTO public.content_entity_evidence
                       (content_entity_id, evidence_type, source_name, source_ref,
                        source_group, evidence_text, evidence_location, quote_valid)
                       VALUES (%s,'metadata','nlk_lod',%s,%s,%s,%s,NULL)""",
                    (relation_id, r["matched_books"][0] if r["matched_books"] else None,
                     VERSION, f"{r['title']} | {r['author_name']} | {r['match_method']}",
                     Jsonb(payload)),
                )
                created_evidence += 1
    return dict(entities=created_entities, aliases=created_aliases,
                relations=created_relations, evidence=created_evidence)

def main():
    parser = argparse.ArgumentParser()
    default_data = ROOT / "structured_relations/data/author_authority"
    parser.add_argument("--data-dir", type=Path, default=default_data)
    parser.add_argument("--apply", action="store_true", help="DB에 실제 적재 (기본값은 미적재)")
    args = parser.parse_args()
    data = args.data_dir.resolve()
    rows02 = read_jsonl(data / "author_candidates.jsonl")
    rows03 = read_jsonl(data / "03_author_candidates.jsonl")
    safe, review, errors = merge_rows(rows02, rows03)
    groups = identity_groups(safe)
    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "input_02": len(rows02),
        "input_03": len(rows03),
        "safe_match_rows": len(safe),
        "unique_author_identities": len(groups),
        "unique_content_author_relations": len({(r["content_id"], key) for key, items in groups.items() for r in items}),
        "review_rows": len(review),
        "error_rows": len(errors),
        "methods": dict(Counter(r["match_method"] for r in safe)),
        "database_changed": False,
    }
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL이 환경변수 또는 .env에 없습니다.")
    with psycopg.connect(url, row_factory=dict_row) as conn:
        counts, missing = load_db_snapshot(conn, safe)
        summary["db_person_before"] = counts["person_count"]
        summary["db_author_relations_before"] = counts["author_count"]
        summary["missing_or_mismatched_editions"] = len(missing)
        if args.apply:
            if review or errors or missing:
                raise RuntimeError(f"검토 {len(review)}, 오류 {len(errors)}, DB 불일치 {len(missing)}건. 전체 적재 중단. dry-run 결과 확인 후 해결하세요.")
            try:
                summary["inserted"] = apply_rows(conn, safe)
                conn.commit()
                summary["database_changed"] = True
            except Exception:
                conn.rollback()
                raise
        else:
            conn.rollback()
    output_files(data, safe, review, errors, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("완료: " + ("DB 적재됨" if args.apply else "미리보기만 수행, DB 변경 없음"))

if __name__ == "__main__":
    main()
